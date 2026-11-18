from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from operations import soak_watchdog
from publisher import schema as public_schema


UTC = timezone.utc
NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)
COMPONENTS = tuple(public_schema.COMPONENTS)


class FakeResponse:
    def __init__(self, body=b"", status=200):
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit=-1):
        if limit < 0:
            return self.body
        return self.body[:limit]


def summary_document(
    *,
    generated_at=None,
    components=COMPONENTS,
    overall_state="operational",
    component_states=None,
):
    if generated_at is None:
        generated_at = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    if component_states is None:
        component_states = {}
    return {
        "schema_version": "2",
        "generated_at": generated_at,
        "overall": {
            "state": overall_state,
            "headline": "All systems are operating normally.",
            "body": "No active incidents or planned maintenance.",
        },
        "components": [
            {
                "id": component_id,
                "name": public_schema.COMPONENTS.get(component_id, "Unknown"),
                "description": public_schema.COMPONENT_DESCRIPTIONS.get(
                    component_id, "Unknown"
                ),
                "state": component_states.get(component_id, "operational"),
                "availability_90d_pct": None,
                "availability_seconds_measured": 0,
                "history_days_available": 0,
            }
            for component_id in components
        ],
        "active_events": [],
    }


def encode_summary(document):
    return json.dumps(document, separators=(",", ":")).encode("utf-8")


def summary_body(**kwargs):
    return encode_summary(summary_document(**kwargs))


def active_incident():
    return {
        "id": "inc-2026-07-22-public-example",
        "kind": "incident",
        "status": "monitoring",
        "title": "Public service interruption",
        "body": "Some visitors may be unable to use this public component.",
        "started_at": "2026-07-22T11:40:00Z",
        "updated_at": "2026-07-22T11:50:00Z",
        "affected_components": ["watch"],
        "updates": [
            {
                "at": "2026-07-22T11:40:00Z",
                "status": "investigating",
                "body": "Public checks indicate an interruption.",
            },
            {
                "at": "2026-07-22T11:50:00Z",
                "status": "monitoring",
                "body": "Public checks are improving.",
            },
        ],
    }


class FakeHttp:
    def __init__(self, summary_responses):
        self.summary_responses = list(summary_responses)
        self.posts = []
        self.requests = []

    def __call__(self, request, *, timeout):
        self.requests.append((request, timeout))
        if request.get_method() == "POST":
            self.posts.append(json.loads(request.data.decode("utf-8")))
            return FakeResponse(status=204)
        response = self.summary_responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class Fixture:
    def __init__(self, root, **overrides):
        self.root = Path(root)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir(mode=0o700)
        self.config = self.root / "watchdog.json"
        self.secret = self.root / "discord.env"
        self.state = self.runtime / "soak-state.json"
        self.lock = self.runtime / "soak.lock"
        self.webhook = "https://discord.com/api/webhooks/123456/test-token"
        self.secret.write_text(
            f"DISCORD_WEBHOOK_URL={self.webhook}\n", encoding="ascii"
        )
        self.secret.chmod(0o600)
        values = {
            "schema_version": "1",
            "summary_url": "https://status.example.test/status/v2/summary.json",
            "webhook_env_file": os.fspath(self.secret),
            "state_file": os.fspath(self.state),
            "lock_file": os.fspath(self.lock),
        }
        values.update(overrides)
        self.write_config(values)

    def write_config(self, values):
        self.config.write_text(
            json.dumps(values, separators=(",", ":")), encoding="utf-8"
        )
        self.config.chmod(0o600)

    def invoke(self, http, *, runner=None, now=NOW):
        if runner is None:
            runner = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("unexpected subprocess")
            )
        stdout = soak_watchdog.StringIO()
        stderr = soak_watchdog.StringIO()
        code = soak_watchdog.main(
            ["run", "--config", os.fspath(self.config)],
            now=lambda: now,
            urlopen=http,
            runner=runner,
            stdout=stdout,
            stderr=stderr,
        )
        return code, stdout.getvalue(), stderr.getvalue()

    def state_document(self):
        return json.loads(self.state.read_text(encoding="utf-8"))


class SoakWatchdogTests(unittest.TestCase):
    def test_healthy_run_is_silent_and_uses_stable_bounded_http_probe(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            http = FakeHttp([FakeResponse(summary_body())])

            result = fixture.invoke(http)

            self.assertEqual(result, (0, "", ""))
            request, timeout = http.requests[0]
            self.assertEqual(timeout, 10)
            self.assertEqual(request.headers["User-agent"], soak_watchdog.USER_AGENT)
            self.assertEqual(request.headers["Accept"], "application/json")
            state = fixture.state_document()
            self.assertEqual(state["total_runs"], 1)
            self.assertEqual(state["healthy_runs"], 1)
            self.assertEqual(state["failed_runs"], 0)
            self.assertEqual(state["last_reason_code"], "ok")
            serialized = fixture.state.read_text(encoding="utf-8")
            self.assertNotIn("example.test", serialized)
            self.assertNotIn(fixture.webhook, serialized)

    def test_stale_generation_is_an_unhealthy_completed_transaction(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary, max_age_seconds=180)
            stale = NOW - timedelta(seconds=181)
            http = FakeHttp(
                [FakeResponse(summary_body(generated_at=stale.isoformat()))]
            )

            result = fixture.invoke(http)

            self.assertEqual(result, (0, "", ""))
            state = fixture.state_document()
            self.assertEqual(state["failed_runs"], 1)
            self.assertEqual(state["last_reason_code"], "summary_generation_stale")
            self.assertEqual(state["maximum_generation_age_seconds"], 181)

    def test_malformed_and_oversized_json_are_sanitized_failures(self):
        cases = (
            (b"not-json PRIVATE_SENTINEL", {}, "summary_json_invalid"),
            (
                b"{" + b"x" * 64,
                {"max_body_bytes": 32},
                "summary_body_oversized",
            ),
        )
        for body, config, expected in cases:
            with self.subTest(expected=expected), TemporaryDirectory() as temporary:
                fixture = Fixture(temporary, **config)
                result = fixture.invoke(FakeHttp([FakeResponse(body)]))
                self.assertEqual(result, (0, "", ""))
                serialized = fixture.state.read_text(encoding="utf-8")
                self.assertEqual(fixture.state_document()["last_reason_code"], expected)
                self.assertNotIn("PRIVATE_SENTINEL", serialized)

    def test_components_must_be_the_exact_public_set_once_each(self):
        cases = (
            COMPONENTS[:-1],
            COMPONENTS + ("watch",),
            COMPONENTS[:-1] + ("private-component",),
        )
        for component_ids in cases:
            with self.subTest(component_ids=component_ids), TemporaryDirectory() as temporary:
                fixture = Fixture(temporary)
                result = fixture.invoke(
                    FakeHttp([FakeResponse(summary_body(components=component_ids))])
                )
                self.assertEqual(result, (0, "", ""))
                self.assertEqual(
                    fixture.state_document()["last_reason_code"],
                    "summary_schema_invalid",
                )

    def test_complete_public_summary_schema_is_applied_before_classification(self):
        cases = {}

        missing_root = summary_document()
        del missing_root["active_events"]
        cases["missing-root"] = missing_root

        unknown_root = summary_document()
        unknown_root["private-root-sentinel"] = True
        cases["unknown-root"] = unknown_root

        missing_overall = summary_document()
        del missing_overall["overall"]["headline"]
        cases["missing-overall"] = missing_overall

        unknown_overall = summary_document()
        unknown_overall["overall"]["private-overall-sentinel"] = True
        cases["unknown-overall"] = unknown_overall

        missing_component = summary_document()
        del missing_component["components"][0]["name"]
        cases["missing-component"] = missing_component

        unknown_component = summary_document()
        unknown_component["components"][0]["private-component-sentinel"] = True
        cases["unknown-component"] = unknown_component

        invalid_availability = summary_document()
        invalid_availability["components"][0]["availability_90d_pct"] = 99.1234
        invalid_availability["components"][0]["availability_seconds_measured"] = 1
        cases["availability"] = invalid_availability

        missing_active_event = summary_document()
        missing_active_event["active_events"] = [active_incident()]
        del missing_active_event["active_events"][0]["body"]
        cases["missing-active-event"] = missing_active_event

        unknown_active_event = summary_document()
        unknown_active_event["active_events"] = [active_incident()]
        unknown_active_event["active_events"][0]["private-event-sentinel"] = True
        cases["unknown-active-event"] = unknown_active_event

        invalid_timestamp_order = summary_document()
        invalid_timestamp_order["active_events"] = [active_incident()]
        invalid_timestamp_order["active_events"][0]["updates"].reverse()
        cases["timestamp-order"] = invalid_timestamp_order

        invalid_other_field = summary_document()
        invalid_other_field["components"][0]["description"] = "PRIVATE_SCHEMA_TEXT"
        cases["other-schema-field"] = invalid_other_field

        for label, document in cases.items():
            with self.subTest(label=label), TemporaryDirectory() as temporary:
                fixture = Fixture(temporary)

                result = fixture.invoke(
                    FakeHttp([FakeResponse(encode_summary(document))])
                )

                self.assertEqual(result, (0, "", ""))
                self.assertEqual(
                    fixture.state_document()["last_reason_code"],
                    "summary_schema_invalid",
                )
                self.assertNotIn("PRIVATE", fixture.state.read_text(encoding="utf-8"))

    def test_schema_validator_is_called_and_exception_text_is_never_projected(self):
        sentinel = "PRIVATE_SCHEMA_EXCEPTION_TEXT"
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary, failure_threshold=1)
            http = FakeHttp([FakeResponse(summary_body())])

            with patch.object(
                public_schema,
                "validate_summary",
                side_effect=public_schema.SchemaError(sentinel),
            ) as validate:
                result = fixture.invoke(http)

            self.assertEqual(result, (0, "", ""))
            validate.assert_called_once()
            persisted_and_delivered = (
                fixture.state.read_text(encoding="utf-8") + json.dumps(http.posts)
            )
            self.assertEqual(
                fixture.state_document()["last_reason_code"],
                "summary_schema_invalid",
            )
            self.assertNotIn(sentinel, persisted_and_delivered)

    def test_public_state_vocabulary_matches_the_checked_in_schema(self):
        self.assertEqual(
            soak_watchdog.PUBLIC_STATES,
            frozenset(public_schema.PUBLIC_STATES),
        )

    def test_overall_state_is_required_and_exact(self):
        missing = summary_document()
        del missing["overall"]["state"]
        cases = (
            missing,
            {**summary_document(), "overall": None},
            {**summary_document(), "overall": {"state": None}},
            {**summary_document(), "overall": {"state": "Operational"}},
            {**summary_document(), "overall": {"state": " operational "}},
        )
        for document in cases:
            with self.subTest(overall=document["overall"]), TemporaryDirectory() as temporary:
                fixture = Fixture(temporary)

                result = fixture.invoke(
                    FakeHttp([FakeResponse(encode_summary(document))])
                )

                self.assertEqual(result, (0, "", ""))
                self.assertEqual(
                    fixture.state_document()["last_reason_code"],
                    "summary_schema_invalid",
                )

    def test_component_states_are_required_and_exact(self):
        missing = summary_document()
        del missing["components"][0]["state"]
        null_state = summary_document()
        null_state["components"][0]["state"] = None
        wrong_case = summary_document()
        wrong_case["components"][0]["state"] = "Operational"
        padded = summary_document()
        padded["components"][0]["state"] = " operational "
        alias = summary_document()
        alias["components"][0]["state"] = "healthy"
        for document in (missing, null_state, wrong_case, padded, alias):
            with self.subTest(
                component=document["components"][0]
            ), TemporaryDirectory() as temporary:
                fixture = Fixture(temporary)

                result = fixture.invoke(
                    FakeHttp([FakeResponse(encode_summary(document))])
                )

                self.assertEqual(result, (0, "", ""))
                self.assertEqual(
                    fixture.state_document()["last_reason_code"],
                    "summary_schema_invalid",
                )

    def test_unhealthy_public_states_drive_alert_and_recovery_transitions(self):
        maintenance_states = {component_id: "maintenance" for component_id in COMPONENTS}
        recovery = summary_body(
            overall_state="maintenance", component_states=maintenance_states
        )
        for target in ("overall", "component"):
            for public_state in ("degraded", "outage", "no-data"):
                with self.subTest(
                    target=target, public_state=public_state
                ), TemporaryDirectory() as temporary:
                    fixture = Fixture(temporary, max_age_seconds=1000)
                    unhealthy = (
                        summary_body(overall_state=public_state)
                        if target == "overall"
                        else summary_body(
                            component_states={COMPONENTS[0]: public_state}
                        )
                    )
                    http = FakeHttp(
                        [FakeResponse(unhealthy) for _ in range(3)]
                        + [FakeResponse(recovery) for _ in range(2)]
                    )

                    for index in range(5):
                        self.assertEqual(
                            fixture.invoke(
                                http, now=NOW + timedelta(minutes=index)
                            ),
                            (0, "", ""),
                        )

                    state = fixture.state_document()
                    self.assertEqual(
                        (state["total_runs"], state["healthy_runs"], state["failed_runs"]),
                        (5, 2, 3),
                    )
                    self.assertEqual(
                        (state["outage_transitions"], state["recovery_transitions"]),
                        (1, 1),
                    )
                    self.assertEqual(len(http.posts), 2)
                    self.assertIn(
                        "Reason code: summary_public_state_unhealthy.",
                        http.posts[0]["content"],
                    )

    def test_maintenance_states_are_healthy(self):
        cases = (
            summary_body(overall_state="maintenance"),
            summary_body(component_states={COMPONENTS[0]: "maintenance"}),
            summary_body(
                overall_state="maintenance",
                component_states={
                    component_id: "maintenance" for component_id in COMPONENTS
                },
            ),
        )
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            http = FakeHttp([FakeResponse(body) for body in cases])

            for index in range(len(cases)):
                self.assertEqual(
                    fixture.invoke(http, now=NOW + timedelta(seconds=index)),
                    (0, "", ""),
                )

            state = fixture.state_document()
            self.assertEqual(
                (state["total_runs"], state["healthy_runs"], state["failed_runs"]),
                (3, 3, 0),
            )
            self.assertEqual(state["last_reason_code"], "ok")
            self.assertEqual(http.posts, [])

    def test_public_payload_content_never_reaches_state_or_discord(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            sentinels = (
                "PRIVATE_COMPONENT_TITLE",
                "PRIVATE_COMPONENT_DESCRIPTION",
                "PRIVATE_RAW_STATE_PAYLOAD",
                "private-host.example.test",
                "https://private-host.example.test/private-path",
                "PRIVATE_PUBLIC_BODY",
            )
            document = summary_document()
            document["overall"]["body"] = sentinels[5]
            document["components"][0].update(
                {
                    "title": sentinels[0],
                    "description": sentinels[1],
                    "state": sentinels[2],
                    "hostname": sentinels[3],
                    "url": sentinels[4],
                    "body": sentinels[5],
                }
            )
            body = encode_summary(document)
            http = FakeHttp([FakeResponse(body) for _ in range(3)])

            for index in range(3):
                self.assertEqual(
                    fixture.invoke(http, now=NOW + timedelta(seconds=index)),
                    (0, "", ""),
                )

            persisted_and_delivered = (
                fixture.state.read_text(encoding="utf-8") + json.dumps(http.posts)
            )
            self.assertEqual(
                fixture.state_document()["last_reason_code"],
                "summary_schema_invalid",
            )
            self.assertEqual(
                http.posts[0]["content"],
                "Status soak outage: consecutive checks crossed the failure threshold. "
                "Reason code: summary_schema_invalid.",
            )
            for sentinel in sentinels + COMPONENTS + (
                fixture.webhook,
                "status.example.test",
            ):
                self.assertNotIn(sentinel, persisted_and_delivered)

    def test_docker_uses_fixed_argv_and_rejects_name_or_health_mismatch(self):
        docker_cases = (
            (
                b'{"name":"/public-web","running":true,"health":"healthy"}\n',
                "docker_container_mismatch",
            ),
            (
                b'{"name":"/public-web","running":false,"health":"healthy"}\n'
                b'{"name":"/publisher","running":true,"health":"healthy"}\n',
                "docker_container_not_running",
            ),
            (
                b'{"name":"/public-web","running":true,"health":"starting"}\n'
                b'{"name":"/publisher","running":true,"health":"healthy"}\n',
                "docker_container_unhealthy",
            ),
        )
        for output, expected in docker_cases:
            with self.subTest(expected=expected), TemporaryDirectory() as temporary:
                fixture = Fixture(
                    temporary,
                    ssh_alias="status-soak",
                    container_names=["public-web", "publisher"],
                )
                calls = []

                def runner(argv, **kwargs):
                    calls.append((argv, kwargs))
                    return SimpleNamespace(returncode=0, stdout=output)

                result = fixture.invoke(
                    FakeHttp([FakeResponse(summary_body())]), runner=runner
                )
                self.assertEqual(result, (0, "", ""))
                argv, kwargs = calls[0]
                self.assertEqual(argv, soak_watchdog.docker_inspect_argv(
                    "status-soak", ("public-web", "publisher")
                ))
                self.assertNotIn("shell", kwargs)
                self.assertEqual(kwargs["timeout"], 10)
                self.assertEqual(
                    fixture.state_document()["last_reason_code"], expected
                )

    def test_default_docker_runner_bounds_stream_before_capture_and_reaps(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(
                temporary,
                ssh_alias="status-soak",
                container_names=["public-web"],
            )
            spawned = []
            real_popen = subprocess.Popen

            def recording_popen(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                spawned.append(process)
                return process

            local_argv = [
                sys.executable,
                "-c",
                "import os; os.write(1, b'x' * 4096)",
            ]
            with (
                patch.object(soak_watchdog, "MAX_DOCKER_OUTPUT_BYTES", 1024),
                patch.object(
                    soak_watchdog,
                    "docker_inspect_argv",
                    return_value=local_argv,
                ),
                patch.object(
                    soak_watchdog.subprocess,
                    "run",
                    side_effect=AssertionError("unbounded capture is forbidden"),
                ),
                patch.object(soak_watchdog.subprocess, "Popen", recording_popen),
            ):
                result = soak_watchdog.probe_docker(
                    soak_watchdog.load_config(fixture.config)
                )

            self.assertEqual(result.reason_code, "docker_output_oversized")
            self.assertEqual(len(spawned), 1)
            self.assertIsNotNone(spawned[0].poll())

    def test_three_failures_alert_once_and_two_successes_recover_once(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary, max_age_seconds=1000)
            responses = [FakeResponse(b"nope")] * 4 + [
                FakeResponse(summary_body()),
                FakeResponse(summary_body()),
                FakeResponse(summary_body()),
            ]
            http = FakeHttp(responses)

            for index in range(7):
                self.assertEqual(
                    fixture.invoke(http, now=NOW + timedelta(minutes=index)),
                    (0, "", ""),
                )

            self.assertEqual(len(http.posts), 2)
            self.assertIn("outage", http.posts[0]["content"].lower())
            self.assertIn("recovery", http.posts[1]["content"].lower())
            for payload in http.posts:
                self.assertEqual(
                    payload["allowed_mentions"],
                    {"parse": [], "roles": [], "users": [], "replied_user": False},
                )
                self.assertNotIn("example.test", json.dumps(payload))
                self.assertNotIn(fixture.webhook, json.dumps(payload))
            state = fixture.state_document()
            self.assertEqual(state["outage_transitions"], 1)
            self.assertEqual(state["recovery_transitions"], 1)
            self.assertEqual(state["alert_state"], "normal")
            self.assertEqual((state["total_runs"], state["healthy_runs"]), (7, 3))

    def test_nonblocking_lock_contention_fails_with_fixed_stderr(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            fixture.lock.touch(mode=0o600)
            with fixture.lock.open("rb+") as held:
                fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = fixture.invoke(FakeHttp([FakeResponse(summary_body())]))

            self.assertEqual(result, (2, "", "watchdog-lock-busy\n"))
            self.assertFalse(fixture.state.exists())

    def test_state_save_is_atomic_and_runtime_modes_are_restricted(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            real_replace = os.replace
            replacements = []

            def recording_replace(source, target):
                replacements.append((Path(source), Path(target)))
                return real_replace(source, target)

            with patch.object(soak_watchdog.os, "replace", recording_replace):
                result = fixture.invoke(FakeHttp([FakeResponse(summary_body())]))

            self.assertEqual(result, (0, "", ""))
            self.assertEqual(stat.S_IMODE(fixture.runtime.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(fixture.state.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(fixture.lock.stat().st_mode), 0o600)
            self.assertEqual(replacements[-1][1], fixture.state)
            self.assertEqual(list(fixture.runtime.glob(".soak-state-*")), [])

    def test_runtime_parent_must_already_be_owned_restricted_and_non_symlink(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            fixture.runtime.chmod(0o755)

            result = fixture.invoke(FakeHttp([FakeResponse(summary_body())]))

            self.assertEqual(result, (2, "", "watchdog-state-invalid\n"))
            self.assertEqual(stat.S_IMODE(fixture.runtime.stat().st_mode), 0o755)
            self.assertFalse(fixture.state.exists())
            self.assertFalse(fixture.lock.exists())

    def test_report_rejects_a_broad_runtime_parent(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            self.assertEqual(
                fixture.invoke(FakeHttp([FakeResponse(summary_body())])),
                (0, "", ""),
            )
            fixture.runtime.chmod(0o755)
            stdout = soak_watchdog.StringIO()
            stderr = soak_watchdog.StringIO()

            code = soak_watchdog.main(
                ["report", "--state-file", os.fspath(fixture.state)],
                stdout=stdout,
                stderr=stderr,
            )

            self.assertEqual(
                (code, stdout.getvalue(), stderr.getvalue()),
                (2, "", "watchdog-state-invalid\n"),
            )

    def test_transition_delivery_is_at_least_once_across_save_crash_window(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary, failure_threshold=1)
            http = FakeHttp([FakeResponse(b"not-json"), FakeResponse(b"not-json")])

            with patch.object(
                soak_watchdog,
                "_atomic_save_state",
                side_effect=soak_watchdog.LocalFailure("watchdog-state-invalid"),
            ):
                first = fixture.invoke(http)

            self.assertEqual(first, (2, "", "watchdog-state-invalid\n"))
            self.assertFalse(fixture.state.exists())
            self.assertEqual(len(http.posts), 1)

            second = fixture.invoke(http, now=NOW + timedelta(seconds=1))

            self.assertEqual(second, (0, "", ""))
            self.assertEqual(len(http.posts), 2)
            self.assertEqual(fixture.state_document()["outage_transitions"], 1)

    def test_config_rejects_unknown_keys_broad_mode_and_symlinks(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            values = json.loads(fixture.config.read_text(encoding="utf-8"))
            values["arbitrary_command"] = "print-private-data"
            fixture.write_config(values)
            bad_key = fixture.invoke(FakeHttp([]))
            self.assertEqual(bad_key, (2, "", "watchdog-config-invalid\n"))

            values.pop("arbitrary_command")
            fixture.write_config(values)
            fixture.config.chmod(0o640)
            broad = fixture.invoke(FakeHttp([]))
            self.assertEqual(broad, (2, "", "watchdog-config-invalid\n"))

            target = fixture.root / "real-config.json"
            fixture.config.rename(target)
            fixture.config.symlink_to(target)
            symlink = fixture.invoke(FakeHttp([]))
            self.assertEqual(symlink, (2, "", "watchdog-config-invalid\n"))

    def test_secret_rejects_broad_mode_and_symlinks_without_projecting_values(self):
        for as_symlink in (False, True):
            with self.subTest(as_symlink=as_symlink), TemporaryDirectory() as temporary:
                fixture = Fixture(temporary, failure_threshold=1)
                if as_symlink:
                    target = fixture.root / "real-secret.env"
                    fixture.secret.rename(target)
                    fixture.secret.symlink_to(target)
                else:
                    fixture.secret.chmod(0o640)

                result = fixture.invoke(FakeHttp([FakeResponse(b"not-json")]))

                self.assertEqual(result, (2, "", "watchdog-secret-invalid\n"))
                combined = "".join(str(item) for item in result)
                self.assertNotIn(fixture.webhook, combined)

    def test_exception_text_is_redacted_from_all_outputs_and_state(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            sentinel = "SECRET_SENTINEL https://private.example.invalid/path"

            result = fixture.invoke(FakeHttp([RuntimeError(sentinel)]))

            self.assertEqual(result, (0, "", ""))
            serialized = fixture.state.read_text(encoding="utf-8")
            self.assertNotIn(sentinel, serialized)
            self.assertEqual(
                fixture.state_document()["last_reason_code"],
                "summary_transport_error",
            )

    def test_report_is_fixed_and_fails_safely_for_absent_or_corrupt_state(self):
        with TemporaryDirectory() as temporary:
            fixture = Fixture(temporary)
            stdout = soak_watchdog.StringIO()
            stderr = soak_watchdog.StringIO()
            missing = soak_watchdog.main(
                ["report", "--state-file", os.fspath(fixture.state)],
                stdout=stdout,
                stderr=stderr,
            )
            self.assertEqual((missing, stdout.getvalue(), stderr.getvalue()), (
                2, "", "watchdog-state-invalid\n"
            ))

            fixture.state.write_text("private raw corrupt state", encoding="utf-8")
            fixture.state.chmod(0o600)
            stdout = soak_watchdog.StringIO()
            stderr = soak_watchdog.StringIO()
            corrupt = soak_watchdog.main(
                ["report", "--state-file", os.fspath(fixture.state)],
                stdout=stdout,
                stderr=stderr,
            )
            self.assertEqual((corrupt, stdout.getvalue(), stderr.getvalue()), (
                2, "", "watchdog-state-invalid\n"
            ))

            fixture.state.unlink()
            self.assertEqual(
                fixture.invoke(FakeHttp([FakeResponse(summary_body())])),
                (0, "", ""),
            )
            stdout = soak_watchdog.StringIO()
            stderr = soak_watchdog.StringIO()
            good = soak_watchdog.main(
                ["report", "--state-file", os.fspath(fixture.state)],
                stdout=stdout,
                stderr=stderr,
            )
            report = json.loads(stdout.getvalue())
            self.assertEqual((good, stderr.getvalue()), (0, ""))
            self.assertEqual(
                set(report),
                {
                    "window_start",
                    "last_run",
                    "total_runs",
                    "healthy_runs",
                    "failed_runs",
                    "outage_transitions",
                    "recovery_transitions",
                    "maximum_generation_age_seconds",
                    "last_reason_code",
                    "alert_state",
                },
            )
            self.assertNotIn("example.test", stdout.getvalue())
            self.assertNotIn(fixture.webhook, stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
