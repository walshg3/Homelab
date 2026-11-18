from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
import fcntl
import importlib
import io
import ipaddress
import json
import os
from pathlib import Path
import signal
import ssl
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from operations import public_status_watchdog as watchdog
from publisher import schema as public_schema


UTC = timezone.utc
NOW = datetime(2026, 7, 23, 12, 0, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]
OPERATIONS = ROOT / "operations"
HEALTH_URL = "https://status.walshit.com/healthz.txt"
SUMMARY_URL = "https://status.walshit.com/status/v2/summary.json"
HTTP_ROOT_URL = "http://status.walshit.com/"
HTTPS_ROOT_URL = "https://status.walshit.com/"
RESOLVERS = ("1.1.1.1", "8.8.8.8", "9.9.9.9")


def summary_document(*, generated_at=None, state="operational", components=None):
    if generated_at is None:
        generated_at = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    component_states = components or {}
    return {
        "schema_version": "2",
        "generated_at": generated_at,
        "overall": {
            "state": state,
            "headline": "Public status is available.",
            "body": "See components for current public state.",
        },
        "components": [
            {
                "id": component_id,
                "name": public_schema.COMPONENTS[component_id],
                "description": public_schema.COMPONENT_DESCRIPTIONS[component_id],
                "state": component_states.get(component_id, "operational"),
                "availability_90d_pct": None,
                "availability_seconds_measured": 0,
                "history_days_available": 0,
            }
            for component_id in public_schema.COMPONENTS
        ],
        "active_events": [],
    }


def encode_summary(document=None):
    return json.dumps(
        document or summary_document(), separators=(",", ":")
    ).encode("utf-8")


def encode_state(document):
    return (
        json.dumps(document, ensure_ascii=True, separators=(",", ":")) + "\n"
    ).encode("ascii")


class Headers:
    def __init__(self, values=None):
        self.values = {key.lower(): value for key, value in (values or {}).items()}

    def get(self, key, default=None):
        return self.values.get(key.lower(), default)


class FakeResponse:
    def __init__(self, body=b"", status=200, headers=None, url=None):
        self.body = body
        self.status = status
        self.headers = Headers(headers)
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit=-1):
        return self.body if limit < 0 else self.body[:limit]

    def geturl(self):
        return self.url


class DripResponse(FakeResponse):
    def read(self, limit=-1):
        del limit
        chunks = []
        for _index in range(100):
            time.sleep(0.1)
            chunks.append(b"x")
        return b"".join(chunks)


def healthy_responses():
    return [
        FakeResponse(
            b"status-walshit-web-ok\n",
            headers={"Content-Type": "text/plain"},
            url=HEALTH_URL,
        ),
        FakeResponse(
            encode_summary(),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Cache-Control": "no-store, max-age=0",
                "Server": "cloudflare",
                "CF-Ray": "public-edge-proof",
            },
            url=SUMMARY_URL,
        ),
        FakeResponse(
            b"",
            status=301,
            headers={"Location": HTTPS_ROOT_URL},
            url=HTTP_ROOT_URL,
        ),
    ]


class FakeHttp:
    def __init__(self, responses, *, clock=None, advance=0):
        self.responses = list(responses)
        self.requests = []
        self.posts = []
        self.clock = clock
        self.advance = advance

    def __call__(self, request, *, timeout):
        self.requests.append((request, timeout))
        if self.clock is not None:
            self.clock.value += self.advance
        if request.get_method() == "POST":
            if self.responses and isinstance(self.responses[0], BaseException):
                raise self.responses.pop(0)
            payload = json.loads(request.data.decode("utf-8"))
            self.posts.append(payload)
            return FakeResponse(b"", status=204, url=request.full_url)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeDns:
    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.answers:
            answer = self.answers.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer
        query_type = argv[-1]
        output = b"93.184.216.34\n" if query_type == "A" else b""
        return SimpleNamespace(returncode=0, stdout=output)


class Clock:
    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value


class Fixture:
    def __init__(self, root, **overrides):
        self.root = Path(root)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir(mode=0o700)
        self.config = self.root / "watchdog.json"
        self.secret = self.root / "discord.env"
        self.state = self.runtime / "public-watchdog-state.json"
        self.lock = self.runtime / "public-watchdog.lock"
        self.webhook = "https://discord.com/api/webhooks/123456789/test-token"
        self.secret.write_text(
            f"DISCORD_WEBHOOK_URL={self.webhook}\n", encoding="ascii"
        )
        self.secret.chmod(0o600)
        values = {
            "schema_version": "1",
            "health_url": HEALTH_URL,
            "summary_url": SUMMARY_URL,
            "http_root_url": HTTP_ROOT_URL,
            "https_root_url": HTTPS_ROOT_URL,
            "webhook_env_file": os.fspath(self.secret),
            "state_file": os.fspath(self.state),
            "lock_file": os.fspath(self.lock),
            "resolvers": list(RESOLVERS),
            "operation_timeout_seconds": 10,
            "cycle_timeout_seconds": 45,
            "max_health_body_bytes": 128,
            "max_summary_body_bytes": 262144,
            "max_dns_output_bytes": 8192,
            "max_age_seconds": 180,
            "future_tolerance_seconds": 5,
        }
        values.update(overrides)
        self.write_config(values)

    def write_config(self, values):
        self.config.write_text(
            json.dumps(values, separators=(",", ":")), encoding="utf-8"
        )
        self.config.chmod(0o600)

    def invoke(self, http=None, dns=None, *, now=NOW, monotonic=None):
        http = http or FakeHttp(healthy_responses())
        dns = dns or FakeDns()
        stdout = io.StringIO()
        stderr = io.StringIO()
        now_provider = now if callable(now) else lambda: now
        code = watchdog.main(
            ["--config", os.fspath(self.config)],
            now=now_provider,
            monotonic=monotonic or Clock(),
            open_url=http,
            dns_runner=dns,
            stdout=stdout,
            stderr=stderr,
        )
        return code, stdout.getvalue(), stderr.getvalue(), http, dns

    def state_document(self):
        return json.loads(self.state.read_text(encoding="ascii"))


class PublicStatusWatchdogTests(unittest.TestCase):
    def test_healthy_complete_transaction_uses_exact_public_paths_headers_and_ua(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            result = fixture.invoke()

            self.assertEqual(result[:3], (0, "", ""))
            http, dns = result[3:]
            self.assertEqual(
                [request.full_url for request, _timeout in http.requests],
                [HEALTH_URL, SUMMARY_URL, HTTP_ROOT_URL],
            )
            self.assertEqual(
                [request.get_method() for request, _timeout in http.requests],
                ["GET", "GET", "GET"],
            )
            for request, timeout in http.requests:
                self.assertEqual(request.headers["User-agent"], watchdog.USER_AGENT)
                self.assertGreater(timeout, 0)
                self.assertLessEqual(timeout, 10)
            self.assertEqual(http.requests[0][0].headers["Accept"], "text/plain")
            self.assertEqual(
                http.requests[1][0].headers["Accept"], "application/json"
            )
            self.assertEqual(
                http.requests[2][0].headers["Accept"], "text/html,*/*;q=0.8"
            )
            self.assertEqual(len(dns.calls), len(RESOLVERS) * 2)
            state = fixture.state_document()
            self.assertEqual(state["total_runs"], 1)
            self.assertEqual(state["healthy_runs"], 1)
            self.assertEqual(state["failed_runs"], 0)
            self.assertEqual(state["reason_counts"]["ok"], 1)
            self.assertEqual(state["maximum_generation_age_seconds"], 0)
            self.assertEqual(state["last_success"], "2026-07-23T12:00:00Z")
            self.assertIsNone(state["last_failure"])

    def test_valid_nonoperational_summary_states_are_watchdog_success(self):
        cases = (
            ("degraded", {"watch": "degraded"}),
            ("outage", {"watch": "outage", "home": "no-data"}),
            ("maintenance", {"requests": "maintenance"}),
            ("no-data", {"site": "no-data"}),
        )
        for overall, components in cases:
            with self.subTest(overall=overall), TemporaryDirectory() as temporary:
                fixture = Fixture(temporary)
                responses = healthy_responses()
                responses[1].body = encode_summary(
                    summary_document(state=overall, components=components)
                )

                result = fixture.invoke(FakeHttp(responses))

                self.assertEqual(result[:3], (0, "", ""))
                self.assertEqual(fixture.state_document()["reason_counts"]["ok"], 1)

    def test_summary_freshness_uses_wall_clock_sampled_after_body_transfer(self):
        cases = (
            (
                NOW,
                (NOW, NOW + timedelta(seconds=181)),
                "summary_generation_stale",
            ),
            (
                NOW + timedelta(seconds=6),
                (NOW, NOW + timedelta(seconds=6)),
                "ok",
            ),
        )
        for generated_at, samples, reason in cases:
            with self.subTest(reason=reason), TemporaryDirectory() as temporary:
                fixture = Fixture(temporary)
                responses = healthy_responses()
                responses[1].body = encode_summary(
                    summary_document(generated_at=generated_at.isoformat())
                )
                values = iter(samples)

                result = fixture.invoke(FakeHttp(responses), now=lambda: next(values))

                self.assertEqual(result[:3], (0, "", ""))
                self.assertEqual(
                    fixture.state_document()["last_reason_code"], reason
                )

    def test_health_failures_map_to_fixed_reasons(self):
        cases = (
            (
                FakeResponse(b"ok", status=503, url=HEALTH_URL),
                "health_http_status",
            ),
            (
                FakeResponse(b"", status=301, url=HEALTH_URL),
                "health_redirect",
            ),
            (
                FakeResponse(b"x" * 129, status=200, url=HEALTH_URL),
                "health_body_oversized",
            ),
            (
                FakeResponse(b"wrong private body", status=200, url=HEALTH_URL),
                "health_body_invalid",
            ),
            (
                urllib.error.URLError("PRIVATE TRANSPORT DETAIL"),
                "health_transport_error",
            ),
        )
        for response, reason in cases:
            with self.subTest(reason=reason), TemporaryDirectory() as temporary:
                fixture = Fixture(temporary)
                result = fixture.invoke(FakeHttp([response]))
                self.assertEqual(result[:3], (0, "", ""))
                state_text = fixture.state.read_text(encoding="ascii")
                self.assertEqual(fixture.state_document()["last_reason_code"], reason)
                self.assertNotIn("PRIVATE", state_text)

    def test_summary_contract_failures_map_to_fixed_reasons(self):
        stale = summary_document(
            generated_at=(NOW - timedelta(seconds=181)).isoformat()
        )
        future = summary_document(
            generated_at=(NOW + timedelta(seconds=6)).isoformat()
        )
        invalid_time = summary_document()
        invalid_time["generated_at"] = "not-a-time"
        invalid_schema = summary_document()
        del invalid_schema["active_events"]
        invalid_version = summary_document()
        invalid_version["schema_version"] = "1"
        invalid_components = summary_document()
        invalid_components["components"].pop()
        cases = (
            (
                {"body": encode_summary(stale)},
                "summary_generation_stale",
            ),
            (
                {"body": encode_summary(future)},
                "summary_generation_future",
            ),
            (
                {"body": encode_summary(invalid_time)},
                "summary_generated_at_invalid",
            ),
            (
                {"body": encode_summary(invalid_schema)},
                "summary_schema_invalid",
            ),
            (
                {"body": encode_summary(invalid_version)},
                "summary_schema_invalid",
            ),
            (
                {"body": encode_summary(invalid_components)},
                "summary_components_invalid",
            ),
            (
                {"headers": {"Content-Type": "text/plain"}},
                "summary_content_type_invalid",
            ),
            (
                {"headers": {"Cache-Control": "max-age=60"}},
                "summary_cache_control_invalid",
            ),
            (
                {"headers": {"Server": "origin"}},
                "summary_edge_invalid",
            ),
            (
                {"headers": {"CF-Ray": ""}},
                "summary_edge_invalid",
            ),
            (
                {"body": b"not json PRIVATE BODY"},
                "summary_json_invalid",
            ),
            (
                {"body": b"x" * 262145},
                "summary_body_oversized",
            ),
            (
                {"status": 503},
                "summary_http_status",
            ),
            (
                {"status": 302, "url": SUMMARY_URL},
                "summary_redirect",
            ),
            (
                {"status": 200, "url": HTTPS_ROOT_URL},
                "summary_redirect",
            ),
        )
        defaults = {
            "body": encode_summary(),
            "status": 200,
            "headers": {
                "Content-Type": "application/json",
                "Cache-Control": "private, no-store",
                "Server": "cloudflare",
                "CF-Ray": "edge-proof",
            },
            "url": SUMMARY_URL,
        }
        for changes, reason in cases:
            with self.subTest(reason=reason), TemporaryDirectory() as temporary:
                fixture = Fixture(temporary)
                values = dict(defaults)
                values.update(changes)
                if "headers" in changes:
                    values["headers"] = {**defaults["headers"], **changes["headers"]}
                responses = healthy_responses()
                responses[1] = FakeResponse(**values)
                result = fixture.invoke(FakeHttp(responses[:2]))
                self.assertEqual(result[:3], (0, "", ""))
                serialized = fixture.state.read_text(encoding="ascii")
                self.assertEqual(fixture.state_document()["last_reason_code"], reason)
                self.assertNotIn("PRIVATE", serialized)

    def test_http_root_redirect_contract(self):
        cases = (
            (200, HTTPS_ROOT_URL, "http_redirect_status"),
            (302, HTTPS_ROOT_URL, "http_redirect_status"),
            (301, "https://status.walshit.com/wrong", "http_redirect_location"),
            (308, None, "http_redirect_location"),
        )
        for status_code, location, reason in cases:
            with self.subTest(reason=reason), TemporaryDirectory() as temporary:
                fixture = Fixture(temporary)
                responses = healthy_responses()
                responses[2] = FakeResponse(
                    status=status_code,
                    headers={"Location": location} if location else {},
                    url=HTTP_ROOT_URL,
                )
                result = fixture.invoke(FakeHttp(responses))
                self.assertEqual(result[:3], (0, "", ""))
                self.assertEqual(fixture.state_document()["last_reason_code"], reason)

    def test_three_failures_post_once_and_two_successes_recover_once(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            all_posts = []

            for second in range(4):
                bad = FakeHttp(
                    [
                        FakeResponse(
                            b"wrong", status=200, url=HEALTH_URL
                        )
                    ]
                )
                result = fixture.invoke(
                    bad, now=NOW + timedelta(seconds=second * 5)
                )
                self.assertEqual(result[:3], (0, "", ""))
                all_posts.extend(bad.posts)

            self.assertEqual(len(all_posts), 1)
            self.assertIn("status.walshit.com", all_posts[0]["content"])
            self.assertIn("OUTAGE", all_posts[0]["content"])
            self.assertIn("health_body_invalid", all_posts[0]["content"])
            self.assertIn("consecutive=3", all_posts[0]["content"])
            self.assertEqual(
                all_posts[0]["allowed_mentions"], {"parse": [], "users": [], "roles": []}
            )
            state = fixture.state_document()
            self.assertEqual(state["total_runs"], 4)
            self.assertEqual(state["failed_runs"], 4)
            self.assertEqual(state["consecutive_failures"], 4)
            self.assertEqual(state["status"], "outage")
            self.assertTrue(state["alerted"])
            self.assertEqual(state["last_transition"], "2026-07-23T12:00:10Z")

            for second in (20, 25, 30):
                good = FakeHttp(healthy_responses())
                result = fixture.invoke(
                    good, now=NOW + timedelta(seconds=second)
                )
                self.assertEqual(result[:3], (0, "", ""))
                all_posts.extend(good.posts)

            self.assertEqual(len(all_posts), 2)
            self.assertIn("RECOVERY", all_posts[1]["content"])
            self.assertIn("ok", all_posts[1]["content"])
            self.assertIn("consecutive=2", all_posts[1]["content"])
            state = fixture.state_document()
            self.assertEqual(state["status"], "healthy")
            self.assertFalse(state["alerted"])
            self.assertEqual(state["consecutive_successes"], 3)
            self.assertEqual(state["last_transition"], "2026-07-23T12:00:25Z")

    def test_reason_counts_and_success_failure_timestamps_are_aggregated(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            first = fixture.invoke(
                FakeHttp([FakeResponse(b"bad", url=HEALTH_URL)]), now=NOW
            )
            second = fixture.invoke(
                FakeHttp(healthy_responses()), now=NOW + timedelta(seconds=5)
            )
            third = fixture.invoke(
                FakeHttp([FakeResponse(b"bad", url=HEALTH_URL)]),
                now=NOW + timedelta(seconds=10),
            )
            self.assertEqual(first[:3], second[:3])
            self.assertEqual(second[:3], third[:3])

            state = fixture.state_document()
            self.assertEqual(state["total_runs"], 3)
            self.assertEqual(state["healthy_runs"], 1)
            self.assertEqual(state["failed_runs"], 2)
            self.assertEqual(state["reason_counts"]["health_body_invalid"], 2)
            self.assertEqual(state["reason_counts"]["ok"], 1)
            self.assertEqual(state["last_run"], "2026-07-23T12:00:10Z")
            self.assertEqual(state["last_success"], "2026-07-23T12:00:05Z")
            self.assertEqual(state["last_failure"], "2026-07-23T12:00:10Z")

    def test_lock_contention_is_silent_and_does_not_probe_or_mutate(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            fixture.lock.touch(mode=0o600)
            before = set(fixture.runtime.iterdir())
            lock_fd = os.open(fixture.lock, os.O_RDWR)
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                http = FakeHttp([])
                dns = FakeDns()
                result = fixture.invoke(http, dns)
            finally:
                os.close(lock_fd)

            self.assertEqual(result[:3], (0, "", ""))
            self.assertEqual(http.requests, [])
            self.assertEqual(dns.calls, [])
            self.assertEqual(set(fixture.runtime.iterdir()), before)
            self.assertFalse(fixture.state.exists())

    def test_config_secret_and_state_symlinks_or_loose_modes_are_rejected(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            real_config = fixture.root / "real-config.json"
            fixture.config.replace(real_config)
            fixture.config.symlink_to(real_config)
            result = fixture.invoke()
            self.assertEqual(result, (2, "", "watchdog-config-invalid\n", result[3], result[4]))

        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            fixture.config.chmod(0o644)
            result = fixture.invoke()
            self.assertEqual(result[:3], (2, "", "watchdog-config-invalid\n"))

        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            target = fixture.root / "secret-target"
            fixture.secret.replace(target)
            fixture.secret.symlink_to(target)
            bad = FakeHttp([FakeResponse(b"bad", url=HEALTH_URL)])
            fixture.invoke(bad, now=NOW)
            fixture.invoke(FakeHttp([FakeResponse(b"bad", url=HEALTH_URL)]), now=NOW)
            result = fixture.invoke(
                FakeHttp([FakeResponse(b"bad", url=HEALTH_URL)]), now=NOW
            )
            self.assertEqual(result[:3], (2, "", "watchdog-secret-invalid\n"))

        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            fixture.secret.chmod(0o640)
            for _index in range(2):
                fixture.invoke(FakeHttp([FakeResponse(b"bad", url=HEALTH_URL)]))
            result = fixture.invoke(FakeHttp([FakeResponse(b"bad", url=HEALTH_URL)]))
            self.assertEqual(result[:3], (2, "", "watchdog-secret-invalid\n"))

        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            target = fixture.root / "state-target"
            target.write_text("{}", encoding="ascii")
            target.chmod(0o600)
            fixture.state.symlink_to(target)
            result = fixture.invoke()
            self.assertEqual(result[:3], (2, "", "watchdog-state-invalid\n"))

        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            fixture.invoke()
            fixture.state.chmod(0o644)
            result = fixture.invoke()
            self.assertEqual(result[:3], (2, "", "watchdog-state-invalid\n"))

    def test_descriptor_reads_survive_safe_pathname_swap_after_open(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            original_open = os.open
            swapped = False

            def swapping_open(path, flags, *args, **kwargs):
                nonlocal swapped
                descriptor = original_open(path, flags, *args, **kwargs)
                if os.fspath(path) == os.fspath(fixture.config) and not swapped:
                    swapped = True
                    replacement = fixture.root / "replacement.json"
                    replacement.write_text('{"private":"SENTINEL"}', encoding="ascii")
                    replacement.chmod(0o600)
                    os.replace(replacement, fixture.config)
                return descriptor

            with patch.object(watchdog.os, "open", side_effect=swapping_open):
                loaded = watchdog.load_config(fixture.config)
            self.assertEqual(loaded.health_url, HEALTH_URL)

        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            original_open = os.open
            swapped = False

            def swapping_open(path, flags, *args, **kwargs):
                nonlocal swapped
                descriptor = original_open(path, flags, *args, **kwargs)
                if os.fspath(path) == os.fspath(fixture.secret) and not swapped:
                    swapped = True
                    replacement = fixture.root / "replacement.env"
                    replacement.write_text(
                        "DISCORD_WEBHOOK_URL=https://evil.invalid/api/webhooks/1/x\n",
                        encoding="ascii",
                    )
                    replacement.chmod(0o600)
                    os.replace(replacement, fixture.secret)
                return descriptor

            with patch.object(watchdog.os, "open", side_effect=swapping_open):
                loaded = watchdog.load_webhook(fixture.secret)
            self.assertEqual(loaded, fixture.webhook)

    def test_discord_url_allowlist_path_and_mention_suppression(self):
        rejected = (
            "http://discord.com/api/webhooks/1/token",
            "https://evil.invalid/api/webhooks/1/token",
            "https://discord.com.evil.invalid/api/webhooks/1/token",
            "https://discord.com/not-webhooks/1/token",
            "https://discord.com/api/webhooks/1",
            "https://discord.com:444/api/webhooks/1/token",
            "https://user@discord.com/api/webhooks/1/token",
            "https://discord.com/api/webhooks/1/token?wait=true",
        )
        for url in rejected:
            with self.subTest(url=url), TemporaryDirectory() as temporary:
                fixture = Fixture(temporary)
                fixture.secret.write_text(
                    f"DISCORD_WEBHOOK_URL={url}\n", encoding="ascii"
                )
                fixture.secret.chmod(0o600)
                with self.assertRaises(watchdog.LocalFailure) as raised:
                    watchdog.load_webhook(fixture.secret)
                self.assertEqual(raised.exception.code, "watchdog-secret-invalid")

        payload = watchdog.alert_payload("outage", "health_body_invalid", 3)
        self.assertEqual(payload["allowed_mentions"]["parse"], [])
        self.assertNotIn("@", payload["content"])
        self.assertEqual(
            set(payload), {"content", "allowed_mentions"}
        )

    def test_dns_requires_each_resolver_and_uses_fixed_argv_without_shell(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            result = fixture.invoke()
            self.assertEqual(result[:3], (0, "", ""))
            dns = result[4]
            for index, (argv, kwargs) in enumerate(dns.calls):
                resolver = RESOLVERS[index // 2]
                query_type = ("A", "AAAA")[index % 2]
                self.assertEqual(
                    argv,
                    [
                        "/usr/bin/dig",
                        "+short",
                        "+tries=1",
                        "+time=10",
                        f"@{resolver}",
                        "status.walshit.com",
                        query_type,
                    ],
                )
                self.assertNotIn("shell", kwargs)
                self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
                self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
                self.assertEqual(kwargs["stdout"], subprocess.PIPE)
                self.assertFalse(kwargs["check"])
                self.assertGreater(kwargs["timeout"], 0)

    def test_dns_private_malformed_timeout_error_and_missing_answers_are_failures(self):
        cases = (
            (
                [SimpleNamespace(returncode=0, stdout=b"10.0.0.1\n")],
                "dns_address_invalid",
            ),
            (
                [SimpleNamespace(returncode=0, stdout=b"not-an-address\n")],
                "dns_address_invalid",
            ),
            (
                [subprocess.TimeoutExpired(["dig"], 1)],
                "dns_timeout",
            ),
            (
                [SimpleNamespace(returncode=2, stdout=b"PRIVATE ERROR")],
                "dns_command_error",
            ),
            (
                [
                    SimpleNamespace(returncode=0, stdout=b""),
                    SimpleNamespace(returncode=0, stdout=b""),
                ],
                "dns_no_address",
            ),
            (
                [SimpleNamespace(returncode=0, stdout=b"x" * 8193)],
                "dns_output_oversized",
            ),
        )
        for answers, reason in cases:
            with self.subTest(reason=reason), TemporaryDirectory() as temporary:
                fixture = Fixture(temporary)
                result = fixture.invoke(dns=FakeDns(answers))
                self.assertEqual(result[:3], (0, "", ""))
                text = fixture.state.read_text(encoding="ascii")
                self.assertEqual(fixture.state_document()["last_reason_code"], reason)
                self.assertNotIn("PRIVATE", text)

    def test_all_accepted_dns_addresses_are_syntactically_public(self):
        accepted = (
            "93.184.216.34",
            "2606:2800:220:1:248:1893:25c8:1946",
        )
        rejected = (
            "127.0.0.1",
            "169.254.1.1",
            "224.0.0.1",
            "0.0.0.0",
            "255.255.255.255",
            "::",
            "::1",
            "fe80::1",
            "ff00::1",
            "2001:db8::1",
        )
        for value in accepted:
            self.assertTrue(watchdog._is_public_address(ipaddress.ip_address(value)))
        for value in rejected:
            self.assertFalse(watchdog._is_public_address(ipaddress.ip_address(value)))

    def test_dns_accepts_bounded_cname_and_typed_a_or_aaaa_per_resolver(self):
        valid_cases = (
            [
                SimpleNamespace(
                    returncode=0,
                    stdout=b"edge.example.net.\n93.184.216.34\n",
                ),
                SimpleNamespace(returncode=0, stdout=b""),
            ],
            [
                SimpleNamespace(returncode=0, stdout=b"93.184.216.34\n"),
                SimpleNamespace(returncode=0, stdout=b""),
            ],
            [
                SimpleNamespace(returncode=0, stdout=b""),
                SimpleNamespace(
                    returncode=0,
                    stdout=b"2606:2800:220:1:248:1893:25c8:1946\n",
                ),
            ],
        )
        for pair in valid_cases:
            with self.subTest(pair=pair), TemporaryDirectory() as temporary:
                fixture = Fixture(temporary)
                answers = pair * len(RESOLVERS)
                result = fixture.invoke(dns=FakeDns(answers))
                self.assertEqual(result[:3], (0, "", ""))
                self.assertEqual(fixture.state_document()["last_reason_code"], "ok")

        invalid_cases = (
            b"bad..name.\n93.184.216.34\n",
            b"edge.example.net.\n10.0.0.1\n",
            b"2606:2800:220:1:248:1893:25c8:1946\n",
        )
        for output in invalid_cases:
            with self.subTest(output=output), TemporaryDirectory() as temporary:
                fixture = Fixture(temporary)
                result = fixture.invoke(
                    dns=FakeDns([SimpleNamespace(returncode=0, stdout=output)])
                )
                self.assertEqual(result[:3], (0, "", ""))
                self.assertEqual(
                    fixture.state_document()["last_reason_code"],
                    "dns_address_invalid",
                )

    def test_production_dns_runner_bounds_timeout_reaps_overflow_and_exit(self):
        original_popen = subprocess.Popen
        processes = []

        def capturing_popen(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            processes.append(process)
            return process

        with patch.object(watchdog.subprocess, "Popen", side_effect=capturing_popen):
            started = time.monotonic()
            timed_out = watchdog._run_dns_bounded(
                [
                    sys.executable,
                    "-c",
                    "import sys,time; sys.stdout.write('a.\\n'); "
                    "sys.stdout.flush(); time.sleep(10)",
                ],
                0.25,
                128,
            )
            elapsed = time.monotonic() - started
            overflow = watchdog._run_dns_bounded(
                [sys.executable, "-c", "import sys; sys.stdout.write('x' * 1025)"],
                1,
                1024,
            )
            exited = watchdog._run_dns_bounded(
                [sys.executable, "-c", "raise SystemExit(7)"], 1, 128
            )

        self.assertTrue(timed_out.timed_out)
        self.assertLess(elapsed, 1.0)
        self.assertTrue(overflow.output_oversized)
        self.assertEqual(exited.returncode, 7)
        self.assertTrue(processes)
        self.assertTrue(all(process.poll() is not None for process in processes))

    def test_cycle_deadline_and_remaining_budget_bound_each_operation(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(
                temporary, cycle_timeout_seconds=12, operation_timeout_seconds=10
            )
            clock = Clock()
            http = FakeHttp(healthy_responses(), clock=clock, advance=4)
            result = fixture.invoke(http, monotonic=clock)

            self.assertEqual(result[:3], (0, "", ""))
            self.assertEqual(
                [timeout for _request, timeout in http.requests], [10, 8, 4]
            )
            self.assertEqual(result[4].calls, [])
            self.assertEqual(
                fixture.state_document()["last_reason_code"], "cycle_deadline_exceeded"
            )

    def test_hard_cycle_timeout_interrupts_drip_and_restores_signal_state(self):
        if not hasattr(signal, "setitimer"):
            self.skipTest("POSIX interval timers are required")
        with TemporaryDirectory() as temporary:
            fixture = Fixture(
                temporary, cycle_timeout_seconds=1, operation_timeout_seconds=1
            )
            prior_handler = signal.getsignal(signal.SIGALRM)

            def preserved_handler(_signum, _frame):
                return None

            signal.signal(signal.SIGALRM, preserved_handler)
            signal.setitimer(signal.ITIMER_REAL, 20.0, 0.0)
            started = time.monotonic()
            try:
                result = fixture.invoke(
                    FakeHttp(
                        [
                            DripResponse(
                                url=HEALTH_URL,
                                headers={"Content-Type": "text/plain"},
                            )
                        ]
                    )
                )
                elapsed = time.monotonic() - started
                restored_timer = signal.getitimer(signal.ITIMER_REAL)
                self.assertEqual(signal.getsignal(signal.SIGALRM), preserved_handler)
                self.assertGreater(restored_timer[0], 15.0)
                self.assertEqual(restored_timer[1], 0.0)
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0.0, 0.0)
                signal.signal(signal.SIGALRM, prior_handler)

            self.assertEqual(
                result[:3], (2, "", "watchdog-cycle-hard-timeout\n")
            )
            self.assertLess(elapsed, 2.0)
            self.assertFalse(fixture.state.exists())

    def test_hard_deadline_setup_baseexception_restores_exact_signal_state(self):
        if not hasattr(signal, "setitimer"):
            self.skipTest("POSIX interval timers are required")
        prior_handler = signal.getsignal(signal.SIGALRM)
        original_setitimer = signal.setitimer

        def preserved_handler(_signum, _frame):
            return None

        signal.signal(signal.SIGALRM, preserved_handler)
        original_setitimer(signal.ITIMER_REAL, 20.0, 3.0)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)
        calls = 0

        def mutate_then_interrupt(which, seconds, interval=0.0):
            nonlocal calls
            calls += 1
            result = original_setitimer(which, seconds, interval)
            if calls == 1:
                raise watchdog.HardCycleTimeout from None
            return result

        try:
            with patch.object(
                watchdog.signal,
                "setitimer",
                side_effect=mutate_then_interrupt,
            ):
                with self.assertRaises(watchdog.HardCycleTimeout):
                    watchdog._HardCycleDeadline(10).__enter__()
                restored_handler = signal.getsignal(signal.SIGALRM)
                restored_timer = signal.getitimer(signal.ITIMER_REAL)
        finally:
            original_setitimer(signal.ITIMER_REAL, 0.0, 0.0)
            signal.signal(signal.SIGALRM, prior_handler)

        self.assertIs(restored_handler, preserved_handler)
        self.assertAlmostEqual(restored_timer[0], previous_timer[0], delta=0.1)
        self.assertEqual(restored_timer[1], previous_timer[1])

    def test_hard_timeout_during_production_dns_reaps_child_and_releases_lock(self):
        if not hasattr(signal, "setitimer"):
            self.skipTest("POSIX interval timers are required")
        with TemporaryDirectory() as temporary:
            fixture = Fixture(
                temporary, cycle_timeout_seconds=10, operation_timeout_seconds=10
            )
            processes = []
            original_popen = subprocess.Popen
            original_setitimer = signal.setitimer

            def capturing_popen(*args, **kwargs):
                process = original_popen(*args, **kwargs)
                processes.append(process)
                return process

            def shortened_timer(which, seconds, interval=0.0):
                if seconds == 10 and interval == 0.0:
                    seconds = 0.25
                return original_setitimer(which, seconds, interval)

            with (
                patch.object(
                    watchdog,
                    "_dns_argv",
                    return_value=[
                        sys.executable,
                        "-c",
                        "import time; time.sleep(10)",
                    ],
                ),
                patch.object(
                    watchdog.subprocess,
                    "Popen",
                    side_effect=capturing_popen,
                ),
                patch.object(
                    watchdog.signal,
                    "setitimer",
                    side_effect=shortened_timer,
                ),
            ):
                stdout = io.StringIO()
                stderr = io.StringIO()
                code = watchdog.main(
                    ["--config", os.fspath(fixture.config)],
                    now=lambda: NOW,
                    monotonic=Clock(),
                    open_url=FakeHttp(healthy_responses()),
                    dns_runner=None,
                    stdout=stdout,
                    stderr=stderr,
                )
                result = (code, stdout.getvalue(), stderr.getvalue())

            self.assertEqual(
                result[:3], (2, "", "watchdog-cycle-hard-timeout\n")
            )
            self.assertTrue(processes)
            self.assertTrue(all(process.poll() is not None for process in processes))
            self.assertFalse(fixture.state.exists())

            later = fixture.invoke(
                FakeHttp([FakeResponse(b"bad", url=HEALTH_URL)])
            )
            self.assertEqual(later[:3], (0, "", ""))
            self.assertEqual(fixture.state_document()["total_runs"], 1)

    def test_hard_timeout_during_alert_response_releases_lock_and_preserves_state(self):
        if not hasattr(signal, "setitimer"):
            self.skipTest("POSIX interval timers are required")
        with TemporaryDirectory() as temporary:
            fixture = Fixture(
                temporary, cycle_timeout_seconds=10, operation_timeout_seconds=10
            )
            for _index in range(2):
                fixture.invoke(FakeHttp([FakeResponse(b"bad", url=HEALTH_URL)]))
            before = fixture.state.read_bytes()
            original_setitimer = signal.setitimer

            class DripPostHttp(FakeHttp):
                def __call__(self, request, *, timeout):
                    if request.get_method() == "POST":
                        self.requests.append((request, timeout))
                        self.posts.append(json.loads(request.data.decode("utf-8")))
                        return DripResponse(status=204, url=request.full_url)
                    return super().__call__(request, timeout=timeout)

            def shortened_timer(which, seconds, interval=0.0):
                if seconds == 10 and interval == 0.0:
                    seconds = 0.25
                return original_setitimer(which, seconds, interval)

            http = DripPostHttp(
                [FakeResponse(b"bad", url=HEALTH_URL)]
            )
            with patch.object(
                watchdog.signal,
                "setitimer",
                side_effect=shortened_timer,
            ):
                result = fixture.invoke(http)

            self.assertEqual(
                result[:3], (2, "", "watchdog-cycle-hard-timeout\n")
            )
            self.assertEqual(len(http.posts), 1)
            self.assertEqual(fixture.state.read_bytes(), before)

            later = fixture.invoke(
                FakeHttp([FakeResponse(b"bad", url=HEALTH_URL)])
            )
            self.assertEqual(later[:3], (0, "", ""))
            self.assertEqual(fixture.state_document()["total_runs"], 3)

    def test_hard_timeout_during_persistence_releases_lock_and_preserves_state(self):
        if not hasattr(signal, "setitimer"):
            self.skipTest("POSIX interval timers are required")
        with TemporaryDirectory() as temporary:
            fixture = Fixture(
                temporary, cycle_timeout_seconds=10, operation_timeout_seconds=10
            )
            original_setitimer = signal.setitimer

            def shortened_timer(which, seconds, interval=0.0):
                if seconds == 10 and interval == 0.0:
                    seconds = 0.25
                return original_setitimer(which, seconds, interval)

            with (
                patch.object(
                    watchdog.signal,
                    "setitimer",
                    side_effect=shortened_timer,
                ),
                patch.object(
                    watchdog,
                    "_atomic_save_state",
                    side_effect=lambda *_args: time.sleep(10),
                ),
            ):
                result = fixture.invoke()

            self.assertEqual(
                result[:3], (2, "", "watchdog-cycle-hard-timeout\n")
            )
            self.assertFalse(fixture.state.exists())

            later = fixture.invoke()
            self.assertEqual(later[:3], (0, "", ""))
            self.assertEqual(fixture.state_document()["total_runs"], 1)

    def test_atomic_save_faults_before_replace_preserve_prior_state_and_unlock(self):
        fault_cases = (
            (
                "write",
                watchdog.HardCycleTimeout(),
                "watchdog-cycle-hard-timeout\n",
            ),
            (
                "write",
                OSError("PRIVATE TEMP WRITE"),
                "watchdog-state-invalid\n",
            ),
            (
                "fsync",
                watchdog.HardCycleTimeout(),
                "watchdog-cycle-hard-timeout\n",
            ),
            (
                "fsync",
                OSError("PRIVATE TEMP FSYNC"),
                "watchdog-state-invalid\n",
            ),
        )
        for boundary, fault, expected_error in fault_cases:
            with self.subTest(
                boundary=boundary, expected_error=expected_error
            ), TemporaryDirectory() as temporary:
                fixture = Fixture(temporary)
                fixture.invoke(now=NOW)
                prior = fixture.state.read_bytes()
                original_fsync = os.fsync

                def inject_fault(*arguments):
                    descriptor = arguments[0]
                    if boundary == "write":
                        raise fault
                    if stat.S_ISREG(os.fstat(descriptor).st_mode):
                        raise fault
                    return original_fsync(descriptor)

                with patch.object(
                    watchdog.os, boundary, side_effect=inject_fault
                ):
                    result = fixture.invoke(now=NOW + timedelta(seconds=1))

                self.assertEqual(result[:3], (2, "", expected_error))
                self.assertEqual(fixture.state.read_bytes(), prior)
                lock_descriptor = os.open(fixture.lock, os.O_RDWR)
                try:
                    fcntl.flock(
                        lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB
                    )
                finally:
                    os.close(lock_descriptor)
                later = fixture.invoke(now=NOW + timedelta(seconds=2))
                self.assertEqual(later[:3], (0, "", ""))
                self.assertEqual(fixture.state_document()["total_runs"], 2)

    def test_interrupted_replace_commits_only_when_exact_new_state_is_visible(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            fixture.invoke(now=NOW)
            prior_document = fixture.state_document()
            prior = fixture.state.read_bytes()
            intended_document, transition = watchdog._apply_result(
                prior_document,
                watchdog.ProbeResult(True, "ok", 1),
                NOW + timedelta(seconds=1),
            )
            self.assertIsNone(transition)
            intended = encode_state(intended_document)
            original_replace = os.replace

            def replace_then_interrupt(source, destination):
                original_replace(source, destination)
                raise watchdog.HardCycleTimeout from None

            with patch.object(
                watchdog.os, "replace", side_effect=replace_then_interrupt
            ):
                result = fixture.invoke(now=NOW + timedelta(seconds=1))

            self.assertEqual(result[:3], (0, "", ""))
            self.assertNotEqual(fixture.state.read_bytes(), prior)
            self.assertEqual(fixture.state.read_bytes(), intended)
            self.assertEqual(stat.S_IMODE(fixture.state.stat().st_mode), 0o600)
            later = fixture.invoke(now=NOW + timedelta(seconds=2))
            self.assertEqual(later[:3], (0, "", ""))
            self.assertEqual(fixture.state_document()["total_runs"], 3)

        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            fixture.invoke(now=NOW)
            prior = fixture.state.read_bytes()
            original_replace = os.replace

            def replace_with_nonmatching_state_then_fail(source, destination):
                Path(source).write_bytes(b"{}\n")
                os.chmod(source, 0o600)
                original_replace(source, destination)
                raise OSError("PRIVATE AMBIGUOUS REPLACE")

            with patch.object(
                watchdog.os,
                "replace",
                side_effect=replace_with_nonmatching_state_then_fail,
            ):
                result = fixture.invoke(now=NOW + timedelta(seconds=1))

            self.assertEqual(
                result[:3], (2, "", "watchdog-state-invalid\n")
            )
            self.assertNotEqual(fixture.state.read_bytes(), prior)
            self.assertEqual(fixture.state.read_bytes(), b"{}\n")
            lock_descriptor = os.open(fixture.lock, os.O_RDWR)
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(lock_descriptor)
            self.assertEqual(
                fixture.invoke(now=NOW + timedelta(seconds=2))[:3],
                (2, "", "watchdog-state-invalid\n"),
            )

    def test_directory_fsync_interruption_reconciles_exact_committed_state(self):
        faults = (
            watchdog.HardCycleTimeout(),
            OSError("PRIVATE DIRECTORY FSYNC"),
        )
        for fault in faults:
            with self.subTest(fault=type(fault).__name__), TemporaryDirectory() as temporary:
                fixture = Fixture(temporary)
                fixture.invoke(now=NOW)
                prior_document = fixture.state_document()
                intended_document, transition = watchdog._apply_result(
                    prior_document,
                    watchdog.ProbeResult(True, "ok", 1),
                    NOW + timedelta(seconds=1),
                )
                self.assertIsNone(transition)
                intended = encode_state(intended_document)
                original_fsync = os.fsync

                def fail_directory_fsync(descriptor):
                    if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                        raise fault
                    return original_fsync(descriptor)

                with patch.object(
                    watchdog.os, "fsync", side_effect=fail_directory_fsync
                ):
                    result = fixture.invoke(now=NOW + timedelta(seconds=1))

                self.assertEqual(result[:3], (0, "", ""))
                self.assertEqual(fixture.state.read_bytes(), intended)
                self.assertEqual(
                    stat.S_IMODE(fixture.state.stat().st_mode), 0o600
                )
                later = fixture.invoke(now=NOW + timedelta(seconds=2))
                self.assertEqual(later[:3], (0, "", ""))
                self.assertEqual(fixture.state_document()["total_runs"], 3)

    def test_unexpected_probe_exception_is_local_failure_unless_budget_expired(self):
        sentinel = RuntimeError("PRIVATE UNEXPECTED")
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            with patch.object(watchdog, "probe_health", side_effect=sentinel):
                result = fixture.invoke()
            self.assertEqual(result[:3], (2, "", "watchdog-local-failure\n"))
            self.assertFalse(fixture.state.exists())

        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            clock = Clock()

            def expired_probe(*_args, **_kwargs):
                clock.value += 46
                raise sentinel

            with patch.object(watchdog, "probe_health", side_effect=expired_probe):
                result = fixture.invoke(monotonic=clock)
            self.assertEqual(result[:3], (0, "", ""))
            self.assertEqual(
                fixture.state_document()["last_reason_code"],
                "cycle_deadline_exceeded",
            )

    def test_exception_text_never_reaches_state_output_or_alert(self):
        sentinel = "PRIVATE_EXCEPTION_URL_SECRET_CERTIFICATE"
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            posts = []
            for _index in range(3):
                http = FakeHttp([RuntimeError(sentinel)])
                result = fixture.invoke(http)
                posts.extend(http.posts)
                self.assertEqual(result[:3], (0, "", ""))
            combined = (
                fixture.state.read_text(encoding="ascii")
                + json.dumps(posts)
                + result[1]
                + result[2]
            )
            self.assertNotIn(sentinel, combined)
            self.assertNotIn(fixture.webhook, combined)
            self.assertEqual(len(posts), 1)

        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            result = fixture.invoke(dns=FakeDns([RuntimeError(sentinel)]))
            combined = (
                fixture.state.read_text(encoding="ascii") + result[1] + result[2]
            )
            self.assertNotIn(sentinel, combined)
            self.assertEqual(
                fixture.state_document()["last_reason_code"], "dns_command_error"
            )

    def test_atomic_state_modes_and_no_healthy_stdout(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            result = fixture.invoke()
            self.assertEqual(result[:3], (0, "", ""))
            self.assertEqual(stat.S_IMODE(fixture.runtime.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(fixture.state.stat().st_mode), 0o600)
            self.assertTrue(stat.S_ISREG(fixture.state.stat().st_mode))
            self.assertFalse(fixture.state.is_symlink())

    def test_webhook_delivery_is_at_least_once_post_then_persist(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            for _index in range(2):
                fixture.invoke(FakeHttp([FakeResponse(b"bad", url=HEALTH_URL)]))
            failing = FakeHttp(
                [
                    FakeResponse(b"bad", url=HEALTH_URL),
                    RuntimeError("PRIVATE POST FAILURE"),
                ]
            )
            result = fixture.invoke(failing)
            self.assertEqual(result[:3], (2, "", "watchdog-alert-failed\n"))
            state = fixture.state_document()
            self.assertEqual(state["total_runs"], 2)
            self.assertEqual(state["consecutive_failures"], 2)

    def test_successful_post_then_persist_failure_can_duplicate_on_retry(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            for _index in range(2):
                fixture.invoke(FakeHttp([FakeResponse(b"bad", url=HEALTH_URL)]))

            first_http = FakeHttp([FakeResponse(b"bad", url=HEALTH_URL)])
            with patch.object(
                watchdog,
                "_atomic_save_state",
                side_effect=watchdog.LocalFailure("watchdog-state-invalid"),
            ):
                first = fixture.invoke(first_http)
            self.assertEqual(first[:3], (2, "", "watchdog-state-invalid\n"))
            self.assertEqual(len(first_http.posts), 1)
            self.assertEqual(fixture.state_document()["total_runs"], 2)

            retry_http = FakeHttp([FakeResponse(b"bad", url=HEALTH_URL)])
            retry = fixture.invoke(retry_http)
            self.assertEqual(retry[:3], (0, "", ""))
            self.assertEqual(len(retry_http.posts), 1)
            self.assertEqual(first_http.posts, retry_http.posts)

    def test_strict_loaded_state_rejects_unknown_or_inconsistent_values(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            fixture.invoke()
            state = fixture.state_document()
            state["private_unknown"] = "SENTINEL"
            fixture.state.write_text(json.dumps(state), encoding="ascii")
            fixture.state.chmod(0o600)
            result = fixture.invoke()
            self.assertEqual(result[:3], (2, "", "watchdog-state-invalid\n"))
            self.assertNotIn("SENTINEL", result[2])

    def test_state_rejects_threshold_streak_and_reason_timestamp_invariants(self):
        def fail(fixture):
            return fixture.invoke(FakeHttp([FakeResponse(b"bad", url=HEALTH_URL)]))

        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            for _index in range(3):
                fail(fixture)
            state = fixture.state_document()
            state.update(status="healthy", alerted=False)
            fixture.state.write_text(json.dumps(state), encoding="ascii")
            fixture.state.chmod(0o600)
            self.assertEqual(
                fixture.invoke()[:3], (2, "", "watchdog-state-invalid\n")
            )

        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            for _index in range(3):
                fail(fixture)
            state = fixture.state_document()
            state["consecutive_failures"] = 2
            fixture.state.write_text(json.dumps(state), encoding="ascii")
            fixture.state.chmod(0o600)
            self.assertEqual(
                fixture.invoke()[:3], (2, "", "watchdog-state-invalid\n")
            )

        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            for _index in range(3):
                fail(fixture)
            fixture.invoke(now=NOW + timedelta(seconds=1))
            fixture.invoke(now=NOW + timedelta(seconds=2))
            state = fixture.state_document()
            state.update(status="outage", alerted=True)
            fixture.state.write_text(json.dumps(state), encoding="ascii")
            fixture.state.chmod(0o600)
            self.assertEqual(
                fixture.invoke(now=NOW + timedelta(seconds=3))[:3],
                (2, "", "watchdog-state-invalid\n"),
            )

        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            fixture.invoke()
            state = fixture.state_document()
            state["last_success"] = "2026-07-23T11:59:59Z"
            fixture.state.write_text(json.dumps(state), encoding="ascii")
            fixture.state.chmod(0o600)
            self.assertEqual(
                fixture.invoke()[:3], (2, "", "watchdog-state-invalid\n")
            )

    def test_loaded_state_rejects_unreachable_reason_transition_and_age_values(self):
        def write_state(fixture, state):
            fixture.state.write_text(json.dumps(state), encoding="ascii")
            fixture.state.chmod(0o600)

        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            fixture.invoke(FakeHttp([FakeResponse(b"bad", url=HEALTH_URL)]))
            state = fixture.state_document()
            state["reason_counts"]["health_body_invalid"] = 0
            state["reason_counts"]["health_http_status"] = 1
            write_state(fixture, state)
            self.assertEqual(
                fixture.invoke()[:3], (2, "", "watchdog-state-invalid\n")
            )

        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            fixture.invoke(FakeHttp([FakeResponse(b"bad", url=HEALTH_URL)]))
            state = fixture.state_document()
            state["last_transition"] = state["last_run"]
            write_state(fixture, state)
            self.assertEqual(
                fixture.invoke()[:3], (2, "", "watchdog-state-invalid\n")
            )

        age_cases = (
            ("ok", NOW),
            ("summary_generation_future", NOW + timedelta(seconds=6)),
            ("summary_generation_stale", NOW - timedelta(seconds=181)),
        )
        for reason, generated_at in age_cases:
            with self.subTest(reason=reason), TemporaryDirectory() as temporary:
                fixture = Fixture(temporary)
                responses = healthy_responses()
                responses[1].body = encode_summary(
                    summary_document(generated_at=generated_at.isoformat())
                )
                fixture.invoke(FakeHttp(responses))
                state = fixture.state_document()
                self.assertEqual(state["last_reason_code"], reason)
                state["maximum_generation_age_seconds"] = None
                write_state(fixture, state)
                self.assertEqual(
                    fixture.invoke()[:3],
                    (2, "", "watchdog-state-invalid\n"),
                )

    def test_recovered_healthy_state_requires_reachable_outage_and_recovery_totals(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            for second in range(3):
                fixture.invoke(
                    FakeHttp([FakeResponse(b"bad", url=HEALTH_URL)]),
                    now=NOW + timedelta(seconds=second),
                )
            for second in (3, 4):
                fixture.invoke(now=NOW + timedelta(seconds=second))
            recovered = fixture.state_document()
            self.assertEqual(recovered["status"], "healthy")
            self.assertIsNotNone(recovered["last_transition"])
            self.assertEqual(
                fixture.invoke(now=NOW + timedelta(seconds=5))[:3],
                (0, "", ""),
            )

            unreachable = recovered
            unreachable["total_runs"] -= 1
            unreachable["healthy_runs"] -= 1
            unreachable["reason_counts"]["ok"] -= 1
            unreachable["consecutive_successes"] = 1
            fixture.state.write_bytes(encode_state(unreachable))
            fixture.state.chmod(0o600)
            self.assertEqual(
                fixture.invoke(now=NOW + timedelta(seconds=6))[:3],
                (2, "", "watchdog-state-invalid\n"),
            )

        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            for second in range(3):
                fixture.invoke(
                    FakeHttp([FakeResponse(b"bad", url=HEALTH_URL)]),
                    now=NOW + timedelta(seconds=second),
                )
            for second in (3, 4):
                fixture.invoke(now=NOW + timedelta(seconds=second))
            unreachable = fixture.state_document()
            unreachable["total_runs"] -= 1
            unreachable["failed_runs"] -= 1
            unreachable["reason_counts"]["health_body_invalid"] -= 1
            fixture.state.write_bytes(encode_state(unreachable))
            fixture.state.chmod(0o600)
            self.assertEqual(
                fixture.invoke(now=NOW + timedelta(seconds=6))[:3],
                (2, "", "watchdog-state-invalid\n"),
            )

    def test_recovered_and_never_transitioned_healthy_history_boundaries_are_valid(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            for second in range(3):
                fixture.invoke(
                    FakeHttp([FakeResponse(b"bad", url=HEALTH_URL)]),
                    now=NOW + timedelta(seconds=second),
                )
            for second in (3, 4):
                fixture.invoke(now=NOW + timedelta(seconds=second))
            fixture.invoke(now=NOW + timedelta(seconds=5))
            fixture.invoke(
                FakeHttp([FakeResponse(b"bad", url=HEALTH_URL)]),
                now=NOW + timedelta(seconds=6),
            )
            state = fixture.state_document()
            self.assertEqual(state["status"], "healthy")
            self.assertEqual(state["healthy_runs"], 3)
            self.assertEqual(state["failed_runs"], 4)
            self.assertIsNotNone(state["last_transition"])
            self.assertEqual(
                fixture.invoke(now=NOW + timedelta(seconds=7))[:3],
                (0, "", ""),
            )

        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            for second in range(8):
                http = (
                    FakeHttp([FakeResponse(b"bad", url=HEALTH_URL)])
                    if second % 2 == 0
                    else FakeHttp(healthy_responses())
                )
                self.assertEqual(
                    fixture.invoke(
                        http, now=NOW + timedelta(seconds=second)
                    )[:3],
                    (0, "", ""),
                )
            state = fixture.state_document()
            self.assertEqual(state["status"], "healthy")
            self.assertEqual(state["failed_runs"], 4)
            self.assertIsNone(state["last_transition"])

    def test_invalid_updated_state_is_validated_before_alert_or_save(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            invalid = watchdog._initial_state()
            invalid["total_runs"] = 1
            invalid["healthy_runs"] = 1
            invalid["reason_counts"]["ok"] = 1
            invalid["last_reason_code"] = "ok"
            invalid["last_run"] = "2026-07-23T12:00:00Z"
            invalid["last_success"] = "2026-07-23T11:59:59Z"
            invalid["consecutive_successes"] = 1
            invalid["maximum_generation_age_seconds"] = 0
            with (
                patch.object(watchdog, "_apply_result", return_value=(invalid, None)),
                patch.object(watchdog, "_atomic_save_state") as save,
            ):
                result = fixture.invoke()
            self.assertEqual(result[:3], (2, "", "watchdog-state-invalid\n"))
            save.assert_not_called()
            self.assertFalse(fixture.state.exists())

    def test_backward_wall_clock_preserves_state_and_skips_probe_and_alert(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            fixture.invoke(now=NOW)
            before = fixture.state.read_bytes()
            http = FakeHttp(healthy_responses())
            result = fixture.invoke(http, now=NOW - timedelta(seconds=1))
            self.assertEqual(result[:3], (2, "", "watchdog-clock-backward\n"))
            self.assertEqual(fixture.state.read_bytes(), before)
            self.assertEqual(http.requests, [])
            self.assertEqual(http.posts, [])

    def test_post_summary_backward_wall_clock_preserves_state_and_stops_probes(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            fixture.invoke(now=NOW)
            before = fixture.state.read_bytes()
            http = FakeHttp(healthy_responses())
            samples = iter(
                (NOW + timedelta(seconds=10), NOW - timedelta(seconds=1))
            )

            result = fixture.invoke(http, now=lambda: next(samples))

            self.assertEqual(result[:3], (2, "", "watchdog-clock-backward\n"))
            self.assertEqual(fixture.state.read_bytes(), before)
            self.assertEqual(
                [request.full_url for request, _timeout in http.requests],
                [HEALTH_URL, SUMMARY_URL],
            )
            self.assertEqual(http.posts, [])
            self.assertEqual(result[4].calls, [])

    def test_production_opener_disables_proxies_redirects_and_verifies_tls(self):
        captured = {}

        class FakeOpener:
            def open(self, request, *, timeout):
                captured["open"] = (request, timeout)
                return "response"

        def fake_build_opener(*handlers):
            captured["handlers"] = handlers
            return FakeOpener()

        request = urllib.request.Request(HEALTH_URL)
        with (
            patch.dict(
                os.environ,
                {
                    "HTTPS_PROXY": "http://proxy.invalid:8080",
                    "https_proxy": "http://proxy.invalid:8080",
                },
                clear=False,
            ),
            patch.object(
                watchdog.urllib.request,
                "build_opener",
                side_effect=fake_build_opener,
            ),
        ):
            self.assertEqual(
                watchdog._default_open_url(request, timeout=3), "response"
            )

        handlers = captured["handlers"]
        proxy = next(
            handler
            for handler in handlers
            if isinstance(handler, urllib.request.ProxyHandler)
        )
        redirect = next(
            handler
            for handler in handlers
            if isinstance(handler, watchdog._NoRedirectHandler)
        )
        https = next(
            handler
            for handler in handlers
            if isinstance(handler, urllib.request.HTTPSHandler)
        )
        self.assertEqual(proxy.proxies, {})
        self.assertIsNone(
            redirect.redirect_request(request, None, 302, "", Headers(), HTTPS_ROOT_URL)
        )
        self.assertEqual(https._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(https._context.check_hostname)


class PublicStatusArtifactsTests(unittest.TestCase):
    def test_wrapper_uses_only_normalized_absolute_hermes_config_path(self):
        wrapper = importlib.import_module(
            "operations.run_public_status_watchdog"
        )
        with TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            with (
                patch.dict(os.environ, {"HERMES_HOME": os.fspath(home)}, clear=True),
                patch.object(wrapper, "watchdog_main", return_value=17) as main,
            ):
                self.assertEqual(wrapper.main(), 17)
            main.assert_called_once_with(
                [
                    "--config",
                    os.fspath(
                        home / "config" / "status-public-watchdog" / "watchdog.json"
                    ),
                ]
            )

        for environment in (
            {},
            {"HERMES_HOME": "relative"},
            {"HERMES_HOME": "/"},
            {"HERMES_HOME": "/tmp/profile/../other"},
        ):
            stderr = io.StringIO()
            with (
                patch.dict(os.environ, environment, clear=True),
                redirect_stderr(stderr),
            ):
                self.assertEqual(wrapper.main(), 2)
            self.assertEqual(
                stderr.getvalue(), "status-public-watchdog-wrapper-invalid\n"
            )

    def test_cron_manifest_is_exact_and_local_silent(self):
        manifest = json.loads(
            (OPERATIONS / "status-public-watchdog-cron-job.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            manifest,
            {
                "name": "Independent public status watchdog",
                "schedule": "every 5m",
                "script": "run_public_status_watchdog.py",
                "no_agent": True,
                "deliver": "local",
            },
        )

    def test_fresh_profile_install_layout_imports_without_network_or_dependencies(self):
        with TemporaryDirectory() as temporary:
            profile = Path(temporary).resolve()
            scripts = profile / "scripts"
            package = scripts / "publisher"
            config_directory = profile / "config" / "status-public-watchdog"
            runtime = profile / "state" / "status-public-watchdog"
            secret_directory = profile / "secrets" / "discord"
            for directory in (
                scripts,
                package,
                config_directory,
                runtime,
                secret_directory,
            ):
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                directory.chmod(0o700)

            artifacts = (
                (
                    OPERATIONS / "public_status_watchdog.py",
                    scripts / "public_status_watchdog.py",
                    0o700,
                ),
                (
                    OPERATIONS / "run_public_status_watchdog.py",
                    scripts / "run_public_status_watchdog.py",
                    0o700,
                ),
                (
                    ROOT / "publisher" / "__init__.py",
                    package / "__init__.py",
                    0o600,
                ),
                (
                    ROOT / "publisher" / "schema.py",
                    package / "schema.py",
                    0o600,
                ),
            )
            for source, destination, mode in artifacts:
                self.assertTrue(stat.S_ISREG(source.stat().st_mode))
                self.assertIn(
                    stat.S_IMODE(source.stat().st_mode), {0o644, 0o664}
                )
                destination.write_bytes(source.read_bytes())
                destination.chmod(mode)
                self.assertEqual(stat.S_IMODE(destination.stat().st_mode), mode)
            secret = secret_directory / "public-status-watchdog.env"
            secret.touch(mode=0o600)

            environment = {
                "HERMES_HOME": os.fspath(profile),
                "PATH": os.environ.get("PATH", ""),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
            }
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    os.fspath(scripts / "run_public_status_watchdog.py"),
                ],
                cwd=profile,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stdout, b"")
            self.assertEqual(
                completed.stderr, b"watchdog-config-invalid\n"
            )
            self.assertFalse((profile / "publisher").exists())
            self.assertFalse(any(profile.rglob("__pycache__")))
            self.assertEqual(stat.S_IMODE(config_directory.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(runtime.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(secret_directory.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(secret.stat().st_mode), 0o600)

    def test_runbook_documents_config_contract_at_least_once_and_rollback(self):
        runbook = (ROOT / "runbooks" / "operations.md").read_text(encoding="utf-8")
        self.assertIn("status-public-watchdog", runbook)
        self.assertIn("at-least-once", runbook)
        self.assertIn("duplicate", runbook)
        self.assertIn("pause", runbook.lower())
        self.assertIn("preserve", runbook.lower())
        self.assertNotIn("DISCORD_WEBHOOK_URL=https://", runbook)
        for command in (
            'install -m 700 operations/public_status_watchdog.py "$HERMES_HOME/scripts/public_status_watchdog.py"',
            'install -m 700 operations/run_public_status_watchdog.py "$HERMES_HOME/scripts/run_public_status_watchdog.py"',
            'install -m 600 publisher/__init__.py "$HERMES_HOME/scripts/publisher/__init__.py"',
            'install -m 600 publisher/schema.py "$HERMES_HOME/scripts/publisher/schema.py"',
        ):
            self.assertIn(command, runbook)
        self.assertIn("directory fsync", runbook)
        self.assertIn("commit boundary", runbook)


if __name__ == "__main__":
    unittest.main()
