from dataclasses import FrozenInstanceError
from email.message import Message
from io import BytesIO
import json
import math
from pathlib import Path
import secrets
import signal
import ssl
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import publisher.probes as probes
from publisher.probes import (
    HttpResponse,
    MAX_RESPONSE_BYTES,
    ProbeResult,
    StdlibHttpTransport,
    load_check_config,
    parse_check_config,
    probe_check,
    run_probe_cycle,
)
from publisher.schema import COMPONENTS


ROOT = Path(__file__).resolve().parents[1]


class NetworkDeniedTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        for target in ("socket.create_connection", "socket.socket.connect"):
            patcher = patch(
                target, side_effect=AssertionError("unit test attempted network")
            )
            patcher.start()
            self.addCleanup(patcher.stop)


class ManualClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


class FakeTransport:
    def __init__(self, clock, responses):
        self.clock = clock
        self.responses = {key: list(value) for key, value in responses.items()}
        self.calls = []

    def __call__(self, url, *, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        queued = self.responses[url]
        item = queued.pop(0)
        if isinstance(item, tuple):
            item, elapsed = item
        else:
            elapsed = 0.01
        self.clock.value += elapsed
        if isinstance(item, BaseException):
            raise item
        return item


class FakeHandle:
    def __init__(self, *, status=200, headers=None, body=b"{}"):
        self.status = status
        self.headers = headers if headers is not None else {}
        self._body = BytesIO(body)

    def read(self, size=-1):
        return self._body.read(size)

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception, traceback):
        self._body.close()


class FakeOpener:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def response(status=200, content_type="application/json", body=b"{}"):
    return HttpResponse(status=status, content_type=content_type, body=body)


def runtime_environment():
    return {
        "PLEX_TOKEN": secrets.token_hex(24),
        "PLEX_MACHINE_IDENTIFIER": secrets.token_hex(20),
    }


def resources_body(machine_identifier, connections, *, provides="server"):
    return json.dumps(
        [
            {
                "clientIdentifier": machine_identifier,
                "provides": provides,
                "connections": connections,
            }
        ],
        separators=(",", ":"),
    ).encode("utf-8")


def identity_body(machine_identifier):
    return (
        '<MediaContainer machineIdentifier="'
        + machine_identifier
        + '" size="0"/>'
    ).encode("utf-8")


def direct_connection(**overrides):
    connection = {
        "uri": "https://unit-test.plex.direct:32400",
        "local": False,
        "relay": False,
    }
    connection.update(overrides)
    return connection


def config_text(*, checks=None, schema_version="1"):
    if checks is None:
        checks = [
            {
                "component_id": "watch",
                "kind": "plex-composite",
                "web_url": "https://app.plex.tv/desktop/",
                "resources_url": (
                    "https://plex.tv/api/v2/resources?"
                    "includeHttps=1&includeRelay=1&includeIPv6=1"
                ),
                "slow_seconds": 3.0,
                "timeout_seconds": 10.0,
                "follow_redirects": False,
            },
            {
                "component_id": "home",
                "kind": "json-equals",
                "url": "https://home.walshit.com/api/health",
                "slow_seconds": 2.0,
                "timeout_seconds": 10.0,
                "follow_redirects": False,
            },
            {
                "component_id": "requests",
                "kind": "json-requires-string",
                "url": "https://requests.walshit.com/api/v1/status",
                "slow_seconds": 2.0,
                "timeout_seconds": 10.0,
                "follow_redirects": False,
            },
            {
                "component_id": "audiobooks",
                "kind": "text-exact",
                "url": "https://books.walshit.com/healthcheck",
                "slow_seconds": 2.0,
                "timeout_seconds": 10.0,
                "follow_redirects": False,
            },
            {
                "component_id": "site",
                "kind": "text-exact",
                "url": "https://walshit.com/healthz.txt",
                "slow_seconds": 2.0,
                "timeout_seconds": 10.0,
                "follow_redirects": False,
            },
        ]
    lines = [f'schema_version = "{schema_version}"', ""]
    for check in checks:
        lines.append("[[checks]]")
        for key, value in check.items():
            if type(value) is str:
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'{key} = "{escaped}"')
            elif type(value) is bool:
                lines.append(f"{key} = {'true' if value else 'false'}")
            else:
                lines.append(f"{key} = {value}")
        lines.append("")
    return "\n".join(lines)


class CheckConfigTests(NetworkDeniedTestCase):
    def test_production_and_example_configs_share_the_strict_loader(self):
        production = load_check_config(ROOT / "config" / "checks.toml")
        example = load_check_config(ROOT / "publisher" / "config.example.toml")
        self.assertEqual(production, example)
        self.assertEqual(
            tuple(check.component_id for check in production.checks),
            tuple(COMPONENTS),
        )
        self.assertEqual(
            [check.kind for check in production.checks],
            [
                "plex-composite",
                "json-equals",
                "json-requires-string",
                "text-exact",
                "text-exact",
            ],
        )

    def test_config_is_recursive_immutable_and_projection_is_fresh(self):
        config = parse_check_config(config_text())
        with self.assertRaises(FrozenInstanceError):
            config.schema_version = "2"
        with self.assertRaises(TypeError):
            config.checks[0] = config.checks[0]
        with self.assertRaises(FrozenInstanceError):
            config.checks[0].kind = "text-exact"

        projection = config.to_document()
        projection["checks"][0]["kind"] = "changed"
        self.assertEqual(config.checks[0].kind, "plex-composite")
        self.assertEqual(parse_check_config(config_text()), config)

    def test_registry_and_exact_keys_are_rejected(self):
        canonical = parse_check_config(config_text()).to_document()["checks"]
        malformed = []
        malformed.append(canonical[:-1])
        duplicate = [dict(item) for item in canonical]
        duplicate[-1]["component_id"] = "watch"
        malformed.append(duplicate)
        unknown_component = [dict(item) for item in canonical]
        unknown_component[-1]["component_id"] = "unknown"
        malformed.append(unknown_component)
        unknown_key = [dict(item) for item in canonical]
        unknown_key[1]["operator"] = "private"
        malformed.append(unknown_key)
        missing_key = [dict(item) for item in canonical]
        del missing_key[2]["url"]
        malformed.append(missing_key)
        wrong_kind = [dict(item) for item in canonical]
        wrong_kind[1]["kind"] = "text-exact"
        malformed.append(wrong_kind)

        for checks in malformed:
            with self.subTest(checks=checks):
                with self.assertRaises((TypeError, ValueError)):
                    parse_check_config(config_text(checks=checks))

        for text in (
            config_text(schema_version="2"),
            config_text() + "\nunknown = true\n",
            config_text().replace("[[checks]]", "[[checks]]\nextra = true", 1),
        ):
            with self.subTest(text=text[-80:]):
                with self.assertRaises((TypeError, ValueError)):
                    parse_check_config(text)

    def test_urls_are_exact_public_https_targets(self):
        canonical = parse_check_config(config_text()).to_document()["checks"]
        unsafe_values = (
            "http://home.walshit.com/api/health",
            "https://user:password@home.walshit.com/api/health",
            "https://127.0.0.1/api/health",
            "https://192.168.1.7/api/health",
            "https://[::1]/api/health",
            "https://localhost/api/health",
            "https://internal.local/api/health",
            "https://home.walshit.com/api/health?debug=1",
            "https://home.walshit.com/api/health#fragment",
            "https://other.example/api/health",
        )
        for value in unsafe_values:
            checks = [dict(item) for item in canonical]
            checks[1]["url"] = value
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    parse_check_config(config_text(checks=checks))

        redirected = [dict(item) for item in canonical]
        redirected[1]["follow_redirects"] = True
        with self.assertRaises((TypeError, ValueError)):
            parse_check_config(config_text(checks=redirected))

    def test_numeric_contract_rejects_booleans_nonfinite_and_bad_boundaries(self):
        canonical = parse_check_config(config_text()).to_document()["checks"]
        invalid_pairs = (
            (True, 10.0),
            (2.0, False),
            (0, 10.0),
            (-1, 10.0),
            (2.0, 0),
            (2.0, -1),
            (10.0, 10.0),
            (11.0, 10.0),
            (math.nan, 10.0),
            (2.0, math.inf),
        )
        for slow, timeout in invalid_pairs:
            checks = [dict(item) for item in canonical]
            checks[3]["slow_seconds"] = slow
            checks[3]["timeout_seconds"] = timeout
            with self.subTest(slow=slow, timeout=timeout):
                with self.assertRaises((TypeError, ValueError)):
                    parse_check_config(config_text(checks=checks))

    def test_timing_values_are_the_exact_authoritative_contract(self):
        canonical = parse_check_config(config_text()).to_document()["checks"]
        for index, field, value in (
            (0, "slow_seconds", 2.999),
            (0, "slow_seconds", 3.001),
            (0, "timeout_seconds", 9.999),
            (0, "timeout_seconds", 1e300),
            (1, "slow_seconds", 1.999),
            (1, "slow_seconds", 2.001),
            (4, "timeout_seconds", 10.001),
            (4, "timeout_seconds", 1e300),
        ):
            checks = [dict(item) for item in canonical]
            checks[index][field] = value
            with self.subTest(index=index, field=field, value=value):
                with self.assertRaises(ValueError):
                    parse_check_config(config_text(checks=checks))

    def test_config_file_and_text_are_bounded(self):
        with self.assertRaises(ValueError):
            parse_check_config(config_text() + ("#" * 40_000))
        with self.assertRaises(ValueError):
            parse_check_config("x" * 40_000)


class PublicHttpProbeTests(NetworkDeniedTestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = parse_check_config(config_text())

    def check(self, component_id):
        return next(
            check for check in self.config.checks if check.component_id == component_id
        )

    def run_one(self, component_id, item):
        clock = ManualClock()
        check = self.check(component_id)
        transport = FakeTransport(clock, {check.url: [item]})
        return probe_check(check, transport=transport, clock=clock, environ={}), transport

    def test_json_and_text_contracts_accept_exact_public_health(self):
        cases = (
            (
                "home",
                response(
                    content_type="application/json; charset=utf-8",
                    body=b'{"status":"healthy","timestamp":"changing"}',
                ),
            ),
            (
                "requests",
                response(body=b'{"version":"1.2.3","updateAvailable":true}'),
            ),
            ("audiobooks", response(content_type="text/plain", body=b"  OK\n")),
            (
                "site",
                response(content_type="text/plain; charset=UTF-8", body=b"walshit-landing-hugo-ok\n"),
            ),
        )
        for component_id, item in cases:
            with self.subTest(component_id=component_id):
                result, transport = self.run_one(component_id, item)
                self.assertEqual(result, ProbeResult(component_id, "success"))
                self.assertEqual(len(transport.calls), 1)
                _url, headers, timeout = transport.calls[0]
                self.assertEqual(timeout, 10.0)
                self.assertEqual(set(headers), {"Accept", "User-Agent"})
                self.assertNotIn("\n", headers["User-Agent"])

    def test_latency_above_threshold_is_slow_only_when_body_is_valid(self):
        valid = response(body=b'{"status":"healthy"}')
        result, _transport = self.run_one("home", (valid, 2.01))
        self.assertEqual(result.outcome, "slow")
        invalid = response(body=b'{"status":"unhealthy"}')
        result, _transport = self.run_one("home", (invalid, 2.01))
        self.assertEqual(result.outcome, "failure")

    def test_response_returned_after_component_deadline_is_failure(self):
        valid = response(body=b'{"status":"healthy"}')
        result, transport = self.run_one("home", (valid, 10.001))
        self.assertEqual(result.outcome, "failure")
        self.assertEqual(transport.calls[0][2], 10.0)

    def test_status_redirect_mime_body_malformed_and_oversize_are_failures(self):
        cases = (
            response(status=302, body=b'{"status":"healthy"}'),
            response(status=500, body=b'{"status":"healthy"}'),
            response(content_type="text/plain", body=b'{"status":"healthy"}'),
            response(body=b'{"status":"not-healthy"}'),
            response(body=b"{"),
            response(body=b'{"status":"healthy","timing":NaN}'),
            response(body=b'{"status":"unhealthy","status":"healthy"}'),
            response(body=b"{" + (b"x" * MAX_RESPONSE_BYTES)),
            TimeoutError(),
            ssl.SSLError(),
            OSError(),
        )
        for item in cases:
            with self.subTest(item=type(item).__name__):
                result, _transport = self.run_one("home", item)
                self.assertEqual(result.outcome, "failure")

    def test_requests_requires_a_nonempty_exact_string_field(self):
        for body in (
            b"{}",
            b'{"version":""}',
            b'{"version":"   "}',
            b'{"version":1}',
            b"[]",
        ):
            with self.subTest(body=body):
                result, _transport = self.run_one("requests", response(body=body))
                self.assertEqual(result.outcome, "failure")

    def test_text_contracts_are_component_specific(self):
        result, _ = self.run_one(
            "audiobooks", response(content_type="text/plain", body=b"walshit-landing-hugo-ok")
        )
        self.assertEqual(result.outcome, "failure")
        result, _ = self.run_one(
            "site", response(content_type="text/plain", body=b"OK")
        )
        self.assertEqual(result.outcome, "failure")


class PlexCompositeTests(NetworkDeniedTestCase):
    @classmethod
    def setUpClass(cls):
        config = parse_check_config(config_text())
        cls.check = config.checks[0]

    def run_watch(
        self,
        *,
        environ=None,
        web=None,
        resources=None,
        identities=None,
    ):
        if environ is None:
            environ = runtime_environment()
        if web is None:
            web = response(
                content_type="text/html; charset=utf-8",
                body=b"<!doctype html><title> Plex </title>",
            )
        if resources is None:
            connections = [direct_connection()]
            configured_identifier = environ.get(
                "PLEX_MACHINE_IDENTIFIER", secrets.token_hex(20)
            )
            resources = response(
                body=resources_body(configured_identifier, connections)
            )
        if identities is None:
            configured_identifier = environ.get(
                "PLEX_MACHINE_IDENTIFIER", secrets.token_hex(20)
            )
            identities = {
                "https://unit-test.plex.direct:32400/identity": [
                    response(
                        content_type="text/xml; charset=utf-8",
                        body=identity_body(configured_identifier),
                    )
                ]
            }
        clock = ManualClock()
        mapping = {
            self.check.web_url: [web],
            self.check.resources_url: [resources],
            **identities,
        }
        transport = FakeTransport(clock, mapping)
        result = probe_check(
            self.check,
            transport=transport,
            clock=clock,
            environ=environ,
        )
        return result, transport

    def test_missing_or_malformed_runtime_environment_is_error(self):
        valid = runtime_environment()
        malformed = (
            {},
            {"PLEX_TOKEN": valid["PLEX_TOKEN"]},
            {"PLEX_MACHINE_IDENTIFIER": valid["PLEX_MACHINE_IDENTIFIER"]},
            {"PLEX_TOKEN": "", "PLEX_MACHINE_IDENTIFIER": valid["PLEX_MACHINE_IDENTIFIER"]},
            {"PLEX_TOKEN": valid["PLEX_TOKEN"], "PLEX_MACHINE_IDENTIFIER": " "},
            {"PLEX_TOKEN": "x\nheader", "PLEX_MACHINE_IDENTIFIER": valid["PLEX_MACHINE_IDENTIFIER"]},
        )
        for environ in malformed:
            with self.subTest(keys=tuple(environ)):
                result, transport = self.run_watch(environ=environ)
                self.assertEqual(result.outcome, "error")
                self.assertEqual(transport.calls, [])

    def test_direct_success_and_relay_only_degradation(self):
        result, transport = self.run_watch()
        self.assertEqual(result, ProbeResult("watch", "success"))
        self.assertEqual(len(transport.calls), 3)

        environ = runtime_environment()
        relay_uri = "https://relay-unit-test.plex.direct:443"
        resources = response(
            body=resources_body(
                environ["PLEX_MACHINE_IDENTIFIER"],
                [direct_connection(uri=relay_uri, relay=True)],
            )
        )
        identities = {
            relay_uri + "/identity": [
                response(
                    content_type="text/xml",
                    body=identity_body(environ["PLEX_MACHINE_IDENTIFIER"]),
                )
            ]
        }
        result, _transport = self.run_watch(
            environ=environ, resources=resources, identities=identities
        )
        self.assertEqual(result.outcome, "slow")

    def test_web_failure_wins_and_valid_web_latency_can_degrade(self):
        for web in (
            response(status=302, content_type="text/html", body=b"redirect"),
            response(status=500, content_type="text/html", body=b"error"),
            response(content_type="application/json", body=b"{}"),
            response(content_type="text/html", body=b"generic upstream page"),
            TimeoutError(),
        ):
            with self.subTest(web=type(web).__name__):
                result, transport = self.run_watch(web=web)
                self.assertEqual(result.outcome, "failure")
                self.assertEqual(len(transport.calls), 1)
        valid_slow = (
            response(content_type="text/html", body=b"<TITLE>\nPLeX\t</title>"),
            3.01,
        )
        result, _transport = self.run_watch(web=valid_slow)
        self.assertEqual(result.outcome, "slow")

    def test_composite_uses_one_deadline_and_decreasing_remaining_budgets(self):
        environ = runtime_environment()
        machine = environ["PLEX_MACHINE_IDENTIFIER"]
        identity_uri = "https://unit-test.plex.direct:32400/identity"
        clock = ManualClock()
        transport = FakeTransport(
            clock,
            {
                self.check.web_url: [
                    (response(content_type="text/html", body=b"<title>Plex</title>"), 1.0)
                ],
                self.check.resources_url: [
                    (response(body=resources_body(machine, [direct_connection()])), 2.0)
                ],
                identity_uri: [
                    (
                        response(content_type="application/xml", body=identity_body(machine)),
                        3.1,
                    )
                ],
            },
        )
        result = probe_check(
            self.check, transport=transport, clock=clock, environ=environ
        )
        self.assertEqual(result.outcome, "slow")
        self.assertEqual([call[2] for call in transport.calls], [10.0, 9.0, 7.0])

    def test_composite_slow_threshold_uses_total_validated_elapsed_time(self):
        environ = runtime_environment()
        machine = environ["PLEX_MACHINE_IDENTIFIER"]
        identity_uri = "https://unit-test.plex.direct:32400/identity"
        clock = ManualClock()
        transport = FakeTransport(
            clock,
            {
                self.check.web_url: [
                    (
                        response(
                            content_type="text/html", body=b"<title>Plex</title>"
                        ),
                        2.9,
                    )
                ],
                self.check.resources_url: [
                    (
                        response(
                            body=resources_body(machine, [direct_connection()])
                        ),
                        2.9,
                    )
                ],
                identity_uri: [
                    (
                        response(
                            content_type="application/xml",
                            body=identity_body(machine),
                        ),
                        2.9,
                    )
                ],
            },
        )
        result = probe_check(
            self.check, transport=transport, clock=clock, environ=environ
        )
        self.assertEqual(result.outcome, "slow")
        for actual, expected in zip(
            [call[2] for call in transport.calls], [10.0, 7.1, 4.2]
        ):
            self.assertAlmostEqual(actual, expected)

    def test_validation_time_cannot_escape_the_component_deadline(self):
        environ = runtime_environment()
        machine = environ["PLEX_MACHINE_IDENTIFIER"]
        identity_uri = "https://unit-test.plex.direct:32400/identity"

        clock = ManualClock()
        transport = FakeTransport(
            clock,
            {
                self.check.web_url: [
                    response(content_type="text/html", body=b"<title>Plex</title>")
                ],
                self.check.resources_url: [
                    response(body=resources_body(machine, [direct_connection()]))
                ],
                identity_uri: [
                    response(content_type="text/xml", body=identity_body(machine))
                ],
            },
        )

        def resources_after_deadline(_response, _machine_identifier):
            clock.value += 9.99
            return [("https://unit-test.plex.direct:32400", False)]

        with patch(
            "publisher.probes._parse_resources", side_effect=resources_after_deadline
        ):
            result = probe_check(
                self.check, transport=transport, clock=clock, environ=environ
            )
        self.assertEqual(result.outcome, "error")
        self.assertEqual(len(transport.calls), 2)

        clock = ManualClock()
        transport = FakeTransport(
            clock,
            {
                self.check.web_url: [
                    response(content_type="text/html", body=b"<title>Plex</title>")
                ],
                self.check.resources_url: [
                    response(body=resources_body(machine, [direct_connection()]))
                ],
                identity_uri: [
                    response(content_type="text/xml", body=identity_body(machine))
                ],
            },
        )

        def identity_after_deadline(_response, _machine_identifier):
            clock.value += 9.99
            return True

        with patch(
            "publisher.probes._valid_identity", side_effect=identity_after_deadline
        ):
            result = probe_check(
                self.check, transport=transport, clock=clock, environ=environ
            )
        self.assertEqual(result.outcome, "failure")
        self.assertEqual(len(transport.calls), 3)

    def test_relay_final_classification_rechecks_the_component_deadline(self):
        environ = runtime_environment()
        machine = environ["PLEX_MACHINE_IDENTIFIER"]
        relay_uri = "https://relay-unit-test.plex.direct:443"
        direct_uri = "https://unit-test.plex.direct:32400"
        clock = ManualClock()
        transport = FakeTransport(
            clock,
            {
                self.check.web_url: [
                    response(content_type="text/html", body=b"<title>Plex</title>")
                ],
                self.check.resources_url: [
                    response(
                        body=resources_body(
                            machine,
                            [
                                direct_connection(uri=relay_uri, relay=True),
                                direct_connection(uri=direct_uri),
                            ],
                        )
                    )
                ],
                relay_uri + "/identity": [
                    response(content_type="text/xml", body=identity_body(machine))
                ],
                direct_uri + "/identity": [(TimeoutError(), 9.98)],
            },
        )
        result = probe_check(
            self.check, transport=transport, clock=clock, environ=environ
        )
        self.assertEqual(result.outcome, "failure")
        self.assertEqual(len(transport.calls), 4)

    def test_returned_after_deadline_uses_existing_watch_boundaries(self):
        late_web = (
            response(content_type="text/html", body=b"<title>Plex</title>"),
            10.001,
        )
        result, transport = self.run_watch(web=late_web)
        self.assertEqual(result.outcome, "failure")
        self.assertEqual(len(transport.calls), 1)

        environ = runtime_environment()
        late_resources = (
            response(
                body=resources_body(
                    environ["PLEX_MACHINE_IDENTIFIER"], [direct_connection()]
                )
            ),
            9.5,
        )
        result, transport = self.run_watch(
            environ=environ,
            web=(response(content_type="text/html", body=b"<title>Plex</title>"), 0.6),
            resources=late_resources,
        )
        self.assertEqual(result.outcome, "error")
        self.assertEqual(len(transport.calls), 2)

    def test_remote_connection_work_is_bounded_and_fast_direct_stops_early(self):
        environ = runtime_environment()
        machine = environ["PLEX_MACHINE_IDENTIFIER"]
        connections = [
            direct_connection(uri=f"https://unit-{index}.plex.direct:32400")
            for index in range(20)
        ]
        resources = response(body=resources_body(machine, connections))
        first_identity = "https://unit-0.plex.direct:32400/identity"
        result, transport = self.run_watch(
            environ=environ,
            resources=resources,
            identities={
                first_identity: [
                    response(content_type="text/xml", body=identity_body(machine))
                ]
            },
        )
        self.assertEqual(result.outcome, "success")
        self.assertEqual(len(transport.calls), 3)

        identities = {
            f"https://unit-{index}.plex.direct:32400/identity": [TimeoutError()]
            for index in range(20)
        }
        result, transport = self.run_watch(
            environ=environ, resources=resources, identities=identities
        )
        self.assertEqual(result.outcome, "failure")
        self.assertLessEqual(len(transport.calls), 10)

    def test_resources_trust_boundary_errors_are_no_data_outcomes(self):
        environ = runtime_environment()
        other_identifier = secrets.token_hex(20)
        cases = (
            response(status=401),
            response(status=403),
            response(status=500),
            response(content_type="text/plain", body=b"[]"),
            response(body=b"{"),
            response(body=b"{}"),
            response(body=b"[]"),
            response(body=resources_body(other_identifier, [direct_connection()])),
            response(
                body=resources_body(
                    environ["PLEX_MACHINE_IDENTIFIER"],
                    [direct_connection()],
                    provides="client",
                )
            ),
            TimeoutError(),
        )
        for resources in cases:
            with self.subTest(resources=type(resources).__name__):
                result, _transport = self.run_watch(
                    environ=environ, resources=resources
                )
                self.assertEqual(result.outcome, "error")

    def test_no_remote_or_reachable_valid_connection_is_failure(self):
        environ = runtime_environment()
        no_remote = response(
            body=resources_body(
                environ["PLEX_MACHINE_IDENTIFIER"],
                [direct_connection(local=True)],
            )
        )
        result, _transport = self.run_watch(environ=environ, resources=no_remote, identities={})
        self.assertEqual(result.outcome, "failure")

        identity_uri = "https://unit-test.plex.direct:32400/identity"
        failures = (
            response(status=500, content_type="text/xml", body=b"<MediaContainer/>") ,
            response(content_type="text/plain", body=b"<MediaContainer/>") ,
            response(content_type="text/xml", body=b"<broken"),
            response(
                content_type="text/xml",
                body=identity_body(secrets.token_hex(20)),
            ),
            response(content_type="text/xml", body=b"<NotMediaContainer/>") ,
            response(content_type="text/xml", body=b"x" * (MAX_RESPONSE_BYTES + 1)),
            TimeoutError(),
            ssl.SSLError(),
        )
        for identity in failures:
            with self.subTest(identity=type(identity).__name__):
                result, _transport = self.run_watch(
                    environ=environ, identities={identity_uri: [identity]}
                )
                self.assertEqual(result.outcome, "failure")

    def test_unsafe_dynamic_uris_are_errors_and_never_requested(self):
        environ = runtime_environment()
        unsafe = (
            "http://unit-test.plex.direct:32400",
            "https://user:secret@unit-test.plex.direct:32400",
            "https://unit-test.plex.direct:32400/base",
            "https://unit-test.plex.direct:32400?query=1",
            "https://unit-test.plex.direct:32400#fragment",
            "https://unit-test.example:32400",
            "https://127.0.0.1:32400",
            "https://unit-test.plex.direct:22",
            " https://unit-test.plex.direct:32400",
            "https://unit-test.plex.direct:32400\n",
            "HTTPS://unit-test.plex.direct:32400",
            "https://.plex.direct:32400",
            "https://bad_label.plex.direct:32400",
            "https://-bad.plex.direct:32400",
            "https://bad-.plex.direct:32400",
            "https://unit-test.plex.direct:",
            "https://unit-test.plex.direct:0443",
            "https://unit%2dtest.plex.direct:32400",
            "https://unit-test.plex.direct.:32400",
            "https://t\N{LATIN SMALL LETTER E WITH ACUTE}st.plex.direct:32400",
            "https://" + ("a" * 64) + ".plex.direct:32400",
        )
        for uri in unsafe:
            resources = response(
                body=resources_body(
                    environ["PLEX_MACHINE_IDENTIFIER"],
                    [direct_connection(uri=uri)],
                )
            )
            with self.subTest(uri=uri):
                result, transport = self.run_watch(
                    environ=environ, resources=resources, identities={}
                )
                self.assertEqual(result.outcome, "error")
                self.assertEqual(len(transport.calls), 2)

    def test_identity_accepts_both_registered_xml_media_types(self):
        environ = runtime_environment()
        for content_type in ("text/xml", "application/xml; charset=utf-8"):
            with self.subTest(content_type=content_type):
                result, _transport = self.run_watch(
                    environ=environ,
                    identities={
                        "https://unit-test.plex.direct:32400/identity": [
                            response(
                                content_type=content_type,
                                body=identity_body(
                                    environ["PLEX_MACHINE_IDENTIFIER"]
                                ),
                            )
                        ]
                    },
                )
                self.assertEqual(result.outcome, "success")

    def test_token_and_identity_are_headers_or_validation_only(self):
        environ = runtime_environment()
        result, transport = self.run_watch(environ=environ)
        serialized_result = repr(result)
        self.assertNotIn(environ["PLEX_TOKEN"], serialized_result)
        self.assertNotIn(environ["PLEX_MACHINE_IDENTIFIER"], serialized_result)
        self.assertEqual(
            transport.calls[1][1]["X-Plex-Token"], environ["PLEX_TOKEN"]
        )
        self.assertEqual(
            transport.calls[1][1]["X-Plex-Product"], "Walshit Status Probe"
        )
        self.assertEqual(
            transport.calls[1][1]["X-Plex-Client-Identifier"],
            "walshit-status-public-probe-v1",
        )


class ProbeCycleTests(NetworkDeniedTestCase):
    def test_complete_cycle_is_canonical_and_isolates_one_probe_exception(self):
        config = parse_check_config(config_text())
        environ = runtime_environment()
        machine = environ["PLEX_MACHINE_IDENTIFIER"]
        clock = ManualClock()
        identity_uri = "https://unit-test.plex.direct:32400/identity"
        transport = FakeTransport(
            clock,
            {
                config.checks[1].url: [response(body=b'{"status":"healthy"}')],
                config.checks[2].url: [response(body=b'{"version":"1"}')],
                config.checks[3].url: [response(content_type="text/plain", body=b"OK")],
                config.checks[4].url: [
                    response(content_type="text/plain", body=b"walshit-landing-hugo-ok")
                ],
            },
        )
        results = run_probe_cycle(
            config, transport=transport, clock=clock, environ={}
        )
        self.assertIs(type(results), tuple)
        self.assertEqual([item.component_id for item in results], list(COMPONENTS))
        self.assertEqual(
            [item.outcome for item in results],
            ["error", "success", "success", "success", "success"],
        )
        self.assertTrue(all(type(item) is ProbeResult for item in results))

    def test_programmer_and_invariant_errors_escape_the_cycle(self):
        config = parse_check_config(config_text())

        def broken_transport(url, *, headers, timeout):
            raise TypeError("broken transport contract")

        with self.assertRaises(TypeError):
            run_probe_cycle(
                config,
                transport=broken_transport,
                clock=ManualClock(),
                environ=runtime_environment(),
            )

    def test_recursive_public_json_is_isolated_and_cycle_continues(self):
        config = parse_check_config(config_text())
        environ = runtime_environment()
        machine = environ["PLEX_MACHINE_IDENTIFIER"]
        clock = ManualClock()
        nested = (b"[" * 1_100) + b"0" + (b"]" * 1_100)
        self.assertLess(len(nested), 3_000)
        identity_uri = "https://unit-test.plex.direct:32400/identity"
        transport = FakeTransport(
            clock,
            {
                config.checks[0].web_url: [
                    response(content_type="text/html", body=b"<title>Plex</title>")
                ],
                config.checks[0].resources_url: [
                    response(body=resources_body(machine, [direct_connection()]))
                ],
                identity_uri: [
                    response(content_type="text/xml", body=identity_body(machine))
                ],
                config.checks[1].url: [response(body=nested)],
                config.checks[2].url: [response(body=nested)],
                config.checks[3].url: [
                    response(content_type="text/plain", body=b"OK")
                ],
                config.checks[4].url: [
                    response(
                        content_type="text/plain", body=b"walshit-landing-hugo-ok"
                    )
                ],
            },
        )
        results = run_probe_cycle(
            config, transport=transport, clock=clock, environ=environ
        )
        self.assertEqual(
            [item.outcome for item in results],
            ["success", "failure", "failure", "success", "success"],
        )
        self.assertEqual(len(transport.calls), 7)

    def test_recursive_watch_resources_json_is_error_and_cycle_continues(self):
        config = parse_check_config(config_text())
        environ = runtime_environment()
        clock = ManualClock()
        nested = (b"[" * 1_100) + b"0" + (b"]" * 1_100)
        self.assertLess(len(nested), 3_000)
        transport = FakeTransport(
            clock,
            {
                config.checks[0].web_url: [
                    response(content_type="text/html", body=b"<title>Plex</title>")
                ],
                config.checks[0].resources_url: [response(body=nested)],
                config.checks[1].url: [response(body=b'{"status":"healthy"}')],
                config.checks[2].url: [response(body=b'{"version":"1"}')],
                config.checks[3].url: [
                    response(content_type="text/plain", body=b"OK")
                ],
                config.checks[4].url: [
                    response(
                        content_type="text/plain", body=b"walshit-landing-hugo-ok"
                    )
                ],
            },
        )
        results = run_probe_cycle(
            config, transport=transport, clock=clock, environ=environ
        )
        self.assertEqual(
            [item.outcome for item in results],
            ["error", "success", "success", "success", "success"],
        )
        self.assertEqual(len(transport.calls), 6)

    def test_injected_transport_path_cannot_reach_stdlib_opener(self):
        config = parse_check_config(config_text())
        environ = runtime_environment()
        machine = environ["PLEX_MACHINE_IDENTIFIER"]
        clock = ManualClock()
        identity_uri = "https://unit-test.plex.direct:32400/identity"
        transport = FakeTransport(
            clock,
            {
                config.checks[0].web_url: [response(content_type="text/html", body=b"<title>Plex</title>")],
                config.checks[0].resources_url: [
                    response(body=resources_body(machine, [direct_connection()]))
                ],
                identity_uri: [response(content_type="text/xml", body=identity_body(machine))],
                config.checks[1].url: [response(body=b'{"status":"healthy"}')],
                config.checks[2].url: [response(body=b'{"version":"1"}')],
                config.checks[3].url: [response(content_type="text/plain", body=b"OK")],
                config.checks[4].url: [response(content_type="text/plain", body=b"walshit-landing-hugo-ok")],
            },
        )
        with patch.object(
            urllib.request.OpenerDirector,
            "open",
            side_effect=AssertionError("unit test attempted network"),
        ):
            results = run_probe_cycle(
                config, transport=transport, clock=clock, environ=environ
            )
        self.assertEqual([item.outcome for item in results], ["success"] * 5)


class StdlibHttpTransportTests(NetworkDeniedTestCase):
    def test_response_repr_is_fixed_and_metadata_free(self):
        canary = secrets.token_hex(32)
        response_value = HttpResponse(
            599,
            "application/" + canary,
            ("token-machine-uri-host-" + canary).encode("ascii"),
        )
        self.assertEqual(repr(response_value), "HttpResponse(<redacted>)")
        for forbidden in ("599", "application", "token", "machine", "uri", "host", canary):
            self.assertNotIn(forbidden, repr(response_value))

    def test_get_fixed_headers_timeout_and_bounded_body(self):
        opener = FakeOpener(
            FakeHandle(
                headers={"Content-Type": "text/plain", "Content-Length": "2"},
                body=b"OK",
            )
        )
        transport = StdlibHttpTransport(opener=opener)
        result = transport(
            "https://unit-test.example/health",
            headers={"Accept": "text/plain", "User-Agent": "fixed"},
            timeout=1.25,
        )
        request, timeout = opener.calls[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Accept"), "text/plain")
        self.assertEqual(request.get_header("User-agent"), "fixed")
        self.assertEqual(timeout, 1.25)
        self.assertEqual(result, HttpResponse(200, "text/plain", b"OK"))

    def test_http_error_is_normalized_without_following_redirects(self):
        headers = Message()
        headers["Content-Type"] = "text/plain"
        error = urllib.error.HTTPError(
            "https://unit-test.example/health",
            302,
            "redirect",
            headers,
            BytesIO(b"moved"),
        )
        opener = FakeOpener(error)
        response_value = StdlibHttpTransport(opener=opener)(
            "https://unit-test.example/health",
            headers={},
            timeout=1.0,
        )
        self.assertEqual(response_value.status, 302)
        self.assertEqual(response_value.body, b"moved")

    def test_default_transport_builds_normal_verified_ssl_context(self):
        context = object()
        opener = FakeOpener(FakeHandle())
        with patch("publisher.probes.ssl.create_default_context", return_value=context) as create:
            with patch("publisher.probes.urllib.request.build_opener", return_value=opener) as build:
                StdlibHttpTransport()
        create.assert_called_once_with()
        handlers = build.call_args.args
        self.assertTrue(any(type(handler).__name__ == "_NoRedirectHandler" for handler in handlers))
        https_handler = next(
            handler for handler in handlers if isinstance(handler, urllib.request.HTTPSHandler)
        )
        self.assertIs(https_handler._context, context)

    def test_malformed_or_oversized_response_metadata_and_bodies_are_transport_failures(self):
        cases = (
            FakeHandle(status="200", headers={"Content-Type": "text/plain"}),
            FakeHandle(headers={"Content-Type": "text/plain", "Content-Length": "bad"}),
            FakeHandle(headers={"Content-Type": "text/plain", "Content-Length": "-1"}),
            FakeHandle(
                headers={
                    "Content-Type": "text/plain",
                    "Content-Length": str(MAX_RESPONSE_BYTES + 1),
                }
            ),
            FakeHandle(headers={"Content-Type": "text/plain"}, body=b"x" * (MAX_RESPONSE_BYTES + 1)),
            FakeHandle(headers={"Content-Type": "x" * 257}, body=b"ok"),
            FakeHandle(headers={"Content-Type": object()}, body=b"ok"),
        )
        for handle in cases:
            with self.subTest(headers=repr(handle.headers)):
                with self.assertRaises(OSError):
                    StdlibHttpTransport(opener=FakeOpener(handle))(
                        "https://unit-test.example/health", headers={}, timeout=1.0
                    )

    @unittest.skipUnless(
        hasattr(signal, "setitimer") and hasattr(signal, "SIGALRM"),
        "SIGALRM hard wall is unavailable",
    )
    def test_main_thread_hard_wall_interrupts_hanging_opener(self):
        class HangingOpener:
            def open(self, request, timeout):
                time.sleep(0.2)
                raise TimeoutError()

        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            StdlibHttpTransport(opener=HangingOpener())(
                "https://unit-test.example/health", headers={}, timeout=0.02
            )
        self.assertLess(time.monotonic() - started, 0.1)

    @unittest.skipUnless(
        hasattr(signal, "setitimer") and hasattr(signal, "SIGALRM"),
        "SIGALRM hard wall is unavailable",
    )
    def test_worker_transport_never_reads_or_mutates_process_alarm_state(self):
        prior_handler = signal.getsignal(signal.SIGALRM)
        prior_timer = signal.getitimer(signal.ITIMER_REAL)
        restore_started = time.monotonic()
        worker_signal_calls = []

        def armed_handler(_signal_number, _frame):
            raise AssertionError("test timer unexpectedly fired")

        original_getsignal = signal.getsignal
        original_getitimer = signal.getitimer
        original_setitimer = signal.setitimer
        original_signal = signal.signal

        def record(name, function):
            def wrapper(*args, **kwargs):
                worker_signal_calls.append((name, threading.current_thread()))
                return function(*args, **kwargs)

            return wrapper

        try:
            signal.signal(signal.SIGALRM, armed_handler)
            signal.setitimer(signal.ITIMER_REAL, 30.0)
            armed_timer = signal.getitimer(signal.ITIMER_REAL)
            results = []
            errors = []

            def worker():
                try:
                    results.append(
                        StdlibHttpTransport(
                            opener=FakeOpener(
                                FakeHandle(
                                    headers={"Content-Type": "text/plain"},
                                    body=b"OK",
                                )
                            )
                        )(
                            "https://unit-test.example/health",
                            headers={},
                            timeout=1.0,
                        )
                    )
                except BaseException as exc:
                    errors.append(exc)

            with patch.object(
                probes.signal,
                "getsignal",
                record("getsignal", original_getsignal),
            ), patch.object(
                probes.signal,
                "getitimer",
                record("getitimer", original_getitimer),
            ), patch.object(
                probes.signal,
                "setitimer",
                record("setitimer", original_setitimer),
            ), patch.object(
                probes.signal,
                "signal",
                record("signal", original_signal),
            ):
                thread = threading.Thread(target=worker)
                thread.start()
                thread.join(timeout=2.0)

            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(results, [HttpResponse(200, "text/plain", b"OK")])
            self.assertEqual(worker_signal_calls, [])
            self.assertIs(signal.getsignal(signal.SIGALRM), armed_handler)
            remaining_timer = signal.getitimer(signal.ITIMER_REAL)
            self.assertGreater(remaining_timer[0], 0.0)
            self.assertLessEqual(remaining_timer[0], armed_timer[0])
            self.assertEqual(remaining_timer[1], armed_timer[1])
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0.0)
            signal.signal(signal.SIGALRM, prior_handler)
            prior_delay, prior_interval = prior_timer
            if prior_delay > 0.0:
                prior_delay = max(
                    1e-6, prior_delay - (time.monotonic() - restore_started)
                )
            signal.setitimer(signal.ITIMER_REAL, prior_delay, prior_interval)


if __name__ == "__main__":
    unittest.main()
