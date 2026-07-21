from datetime import datetime, timezone
from io import StringIO
import json
import os
from pathlib import Path
import secrets
import stat
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

import publisher.publisher as publisher_module
from publisher.probes import load_check_config
from publisher.publisher import main
from publisher.schema import COMPONENTS, SCHEMA_VERSION, validate_history
from tests.test_real_probes import (
    FakeTransport,
    ManualClock,
    direct_connection,
    identity_body,
    resources_body,
    response,
    runtime_environment,
)


ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
CONFIG = ROOT / "config" / "checks.toml"


def at(minute):
    return datetime(2026, 7, 20, 12, minute, tzinfo=UTC)


def make_success_transport(environ):
    config = load_check_config(CONFIG)
    clock = ManualClock()
    machine = environ["PLEX_MACHINE_IDENTIFIER"]
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
    return transport, clock


class CliFixture:
    def __init__(self, root):
        self.root = Path(root)
        self.output = self.root / "output"
        self.state_dir = self.root / "state"
        self.content = self.root / "content"
        self.output.mkdir()
        self.state_dir.mkdir()
        self.content.mkdir()
        (self.content / "incidents").mkdir()
        (self.content / "maintenance").mkdir()
        self.state_file = self.state_dir / "publisher-state.json"

    def args(self, **overrides):
        values = {
            "config": CONFIG,
            "content_root": self.content,
            "output_dir": self.output,
            "state_file": self.state_file,
        }
        values.update(overrides)
        return [
            "--once",
            "--config",
            os.fspath(values["config"]),
            "--content-root",
            os.fspath(values["content_root"]),
            "--output-dir",
            os.fspath(values["output_dir"]),
            "--state-file",
            os.fspath(values["state_file"]),
        ]


def invoke(fixture, generated_at, *, environ=None, args=None):
    if environ is None:
        environ = runtime_environment()
    transport, clock = make_success_transport(environ)
    stdout = StringIO()
    stderr = StringIO()
    code = main(
        fixture.args() if args is None else args,
        now=lambda: generated_at,
        transport=transport,
        probe_clock=clock,
        environ=environ,
        stdout=stdout,
        stderr=stderr,
    )
    return code, stdout.getvalue(), stderr.getvalue(), transport


class PublisherCliTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.socket.connect"):
            patcher = patch(
                target, side_effect=AssertionError("unit test attempted network")
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_two_successful_cycles_persist_thresholds_and_mode(self):
        with TemporaryDirectory() as temporary:
            fixture = CliFixture(temporary)
            code, stdout, stderr, _transport = invoke(fixture, at(0))
            self.assertEqual(code, 0)
            self.assertEqual(stderr, "")
            expected_first = {
                "schema_version": SCHEMA_VERSION,
                "generated_at": "2026-07-20T12:00:00Z",
                "components": [
                    {"component_id": component_id, "outcome": "success"}
                    for component_id in COMPONENTS
                ],
                "issue_count": 0,
                "state_recovered": False,
            }
            self.assertEqual(
                stdout,
                json.dumps(expected_first, sort_keys=True, separators=(",", ":"))
                + "\n",
            )
            summary = json.loads((fixture.output / "summary.json").read_text())
            self.assertEqual(
                [component["state"] for component in summary["components"]],
                ["no-data"] * 5,
            )
            mode = stat.S_IMODE(fixture.state_file.stat().st_mode)
            self.assertEqual(mode, 0o600)

            code, stdout, stderr, _transport = invoke(fixture, at(1))
            self.assertEqual((code, stderr), (0, ""))
            self.assertFalse(json.loads(stdout)["state_recovered"])
            summary = json.loads((fixture.output / "summary.json").read_text())
            self.assertEqual(
                [component["state"] for component in summary["components"]],
                ["operational"] * 5,
            )
            history = json.loads((fixture.output / "history.json").read_text())
            validate_history(history)
            self.assertEqual(history["generated_at"], "2026-07-20T12:01:00Z")
            self.assertEqual(stat.S_IMODE(fixture.state_file.stat().st_mode), 0o600)

    def test_malformed_state_recovers_fresh_while_preserving_valid_history(self):
        with TemporaryDirectory() as temporary:
            fixture = CliFixture(temporary)
            self.assertEqual(invoke(fixture, at(0))[0], 0)
            prior_history = json.loads((fixture.output / "history.json").read_text())
            fixture.state_file.write_bytes(b"not valid state json")

            code, stdout, stderr, _transport = invoke(fixture, at(1))
            self.assertEqual((code, stderr), (0, ""))
            self.assertTrue(json.loads(stdout)["state_recovered"])
            summary = json.loads((fixture.output / "summary.json").read_text())
            self.assertEqual(
                [component["state"] for component in summary["components"]],
                ["no-data"] * 5,
            )
            history = json.loads((fixture.output / "history.json").read_text())
            self.assertEqual(
                [item["days"] for item in history["components"]],
                [item["days"] for item in prior_history["components"]],
            )

    def test_generation_skew_recovers_fresh(self):
        with TemporaryDirectory() as temporary:
            fixture = CliFixture(temporary)
            self.assertEqual(invoke(fixture, at(0))[0], 0)
            state = json.loads(fixture.state_file.read_text())
            state["generated_at"] = "2026-07-20T11:59:00Z"
            fixture.state_file.write_text(json.dumps(state), encoding="utf-8")

            code, stdout, stderr, _transport = invoke(fixture, at(1))
            self.assertEqual((code, stderr), (0, ""))
            self.assertTrue(json.loads(stdout)["state_recovered"])
            summary = json.loads((fixture.output / "summary.json").read_text())
            self.assertEqual(summary["overall"]["state"], "no-data")

    def test_oversized_regular_state_is_recovered_without_disclosure(self):
        with TemporaryDirectory() as temporary:
            fixture = CliFixture(temporary)
            self.assertEqual(invoke(fixture, at(0))[0], 0)
            fixture.state_file.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
            code, stdout, stderr, _transport = invoke(fixture, at(1))
            self.assertEqual((code, stderr), (0, ""))
            self.assertTrue(json.loads(stdout)["state_recovered"])
            self.assertEqual(stat.S_IMODE(fixture.state_file.stat().st_mode), 0o600)

    def test_malformed_history_fails_before_probe_or_write(self):
        with TemporaryDirectory() as temporary:
            fixture = CliFixture(temporary)
            history = fixture.output / "history.json"
            history.write_bytes(b"malformed")
            sentinel = history.read_bytes()
            code, stdout, stderr, transport = invoke(fixture, at(0))
            self.assertNotEqual(code, 0)
            self.assertEqual(stdout, "")
            self.assertEqual(
                stderr,
                '{"error":"history","exception":"JSONDecodeError"}\n',
            )
            self.assertEqual(transport.calls, [])
            self.assertEqual(history.read_bytes(), sentinel)
            self.assertFalse(fixture.state_file.exists())

    def test_local_json_decoder_rejects_duplicates_constants_and_complexity(self):
        invalid = (
            '{"schema_version":"bad","schema_version":"1"}',
            '{"value":NaN}',
            '{"value":Infinity}',
            '{"value":-Infinity}',
            "[" * 80 + "0" + "]" * 80,
            json.dumps("x" * (1024 * 1024 + 1)),
        )
        for raw in invalid:
            with self.subTest(prefix=raw[:32]):
                with self.assertRaises((TypeError, ValueError)):
                    publisher_module._decode_local_json(raw)

    def test_duplicate_and_nonfinite_history_fail_closed_before_probes(self):
        with TemporaryDirectory() as temporary:
            fixture = CliFixture(temporary)
            self.assertEqual(invoke(fixture, at(0))[0], 0)
            history_path = fixture.output / "history.json"
            valid = history_path.read_text(encoding="utf-8").strip()
            malformed_documents = (
                '{"schema_version":"bad",' + valid[1:],
                valid.replace(
                    '"schema_version":"1"',
                    '"nonfinite":NaN,"schema_version":"1"',
                ),
                valid.replace(
                    '"schema_version":"1"',
                    '"nonfinite":Infinity,"schema_version":"1"',
                ),
                valid.replace(
                    '"schema_version":"1"',
                    '"nonfinite":-Infinity,"schema_version":"1"',
                ),
            )
            for malformed in malformed_documents:
                with self.subTest(marker=malformed[-80:]):
                    history_path.write_text(malformed, encoding="utf-8")
                    code, stdout, stderr, transport = invoke(fixture, at(1))
                    self.assertNotEqual(code, 0)
                    self.assertEqual(stdout, "")
                    self.assertIn('"error":"history"', stderr)
                    self.assertEqual(transport.calls, [])

    def test_duplicate_deep_and_oversized_state_recover_with_valid_history(self):
        with TemporaryDirectory() as temporary:
            fixture = CliFixture(temporary)
            self.assertEqual(invoke(fixture, at(0))[0], 0)
            history = (fixture.output / "history.json").read_bytes()
            valid_state = fixture.state_file.read_text(encoding="utf-8").strip()
            malformed_states = (
                '{"schema_version":"bad",' + valid_state[1:],
                "[" * 1000 + "0" + "]" * 1000,
                json.dumps("x" * (1024 * 1024 + 1)),
            )
            for offset, malformed in enumerate(malformed_states, start=1):
                with self.subTest(offset=offset):
                    fixture.state_file.write_text(malformed, encoding="utf-8")
                    code, stdout, stderr, _transport = invoke(
                        fixture, at(offset)
                    )
                    self.assertEqual((code, stderr), (0, ""))
                    self.assertTrue(json.loads(stdout)["state_recovered"])
                    self.assertEqual(
                        [item["days"] for item in json.loads(
                            (fixture.output / "history.json").read_text()
                        )["components"]],
                        [item["days"] for item in json.loads(history)["components"]],
                    )
                    history = (fixture.output / "history.json").read_bytes()

    def test_local_read_detects_regular_file_swap_between_lstat_and_open(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "entry.json"
            replacement = root / "replacement.json"
            target.write_text('{"value":1}', encoding="utf-8")
            replacement.write_text('{"value":2}', encoding="utf-8")
            directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            real_open = os.open
            swapped = False

            def swapping_open(path, flags, *args, **kwargs):
                nonlocal swapped
                if path == "entry.json" and not swapped:
                    replacement.replace(target)
                    swapped = True
                return real_open(path, flags, *args, **kwargs)

            try:
                with patch("publisher.publisher.os.open", side_effect=swapping_open):
                    with self.assertRaises(ValueError):
                        publisher_module._read_bounded_entry(
                            directory_fd, "entry.json"
                        )
            finally:
                os.close(directory_fd)

    def test_equal_generation_is_stale_and_fails_before_probes(self):
        with TemporaryDirectory() as temporary:
            fixture = CliFixture(temporary)
            self.assertEqual(invoke(fixture, at(0))[0], 0)
            before_history = (fixture.output / "history.json").read_bytes()
            before_state = fixture.state_file.read_bytes()
            code, stdout, stderr, transport = invoke(fixture, at(0))
            self.assertNotEqual(code, 0)
            self.assertEqual(stdout, "")
            self.assertIn('"error":"history"', stderr)
            self.assertEqual(transport.calls, [])
            self.assertEqual((fixture.output / "history.json").read_bytes(), before_history)
            self.assertEqual(fixture.state_file.read_bytes(), before_state)

    def test_transaction_lock_loser_calls_no_probes_and_cannot_overwrite(self):
        with TemporaryDirectory() as temporary:
            fixture = CliFixture(temporary)
            environ = runtime_environment()
            self.assertEqual(invoke(fixture, at(0), environ=environ)[0], 0)
            entered = threading.Event()
            release = threading.Event()
            first_base, first_clock = make_success_transport(environ)

            class BlockingTransport:
                def __init__(self):
                    self.calls = first_base.calls
                    self._blocked = False

                def __call__(self, url, *, headers, timeout):
                    if not self._blocked:
                        self._blocked = True
                        entered.set()
                        if not release.wait(timeout=5):
                            raise RuntimeError("test release timed out")
                    return first_base(url, headers=headers, timeout=timeout)

            first_stdout = StringIO()
            first_stderr = StringIO()
            first_result = []

            def first_publisher():
                first_result.append(
                    main(
                        fixture.args(),
                        now=lambda: at(2),
                        transport=BlockingTransport(),
                        probe_clock=first_clock,
                        environ=environ,
                        stdout=first_stdout,
                        stderr=first_stderr,
                    )
                )

            thread = threading.Thread(target=first_publisher)
            thread.start()
            try:
                self.assertTrue(entered.wait(timeout=5))
                before_history = (fixture.output / "history.json").read_bytes()
                before_state = fixture.state_file.read_bytes()
                code, stdout, stderr, losing_transport = invoke(
                    fixture, at(1), environ=environ
                )
                self.assertNotEqual(code, 0)
                self.assertEqual(stdout, "")
                self.assertEqual(
                    stderr,
                    '{"error":"lock","exception":"BlockingIOError"}\n',
                )
                self.assertEqual(losing_transport.calls, [])
                self.assertEqual(
                    (fixture.output / "history.json").read_bytes(), before_history
                )
                self.assertEqual(fixture.state_file.read_bytes(), before_state)
            finally:
                release.set()
                thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(first_result, [0])
            self.assertEqual(first_stderr.getvalue(), "")
            self.assertEqual(
                json.loads((fixture.output / "history.json").read_text())["generated_at"],
                "2026-07-20T12:02:00Z",
            )
            self.assertEqual(
                json.loads(fixture.state_file.read_text())["generated_at"],
                "2026-07-20T12:02:00Z",
            )

    def test_output_and_state_same_parent_use_one_transaction_lock(self):
        with TemporaryDirectory() as temporary:
            fixture = CliFixture(temporary)
            fixture.state_file = fixture.output / "publisher-state.json"
            code, _stdout, stderr, _transport = invoke(fixture, at(0))
            self.assertEqual((code, stderr), (0, ""))
            self.assertEqual(
                sorted(path.name for path in fixture.output.iterdir()),
                [
                    "history.json",
                    "incidents.json",
                    "publisher-state.json",
                    "summary.json",
                ],
            )

    def test_symlinked_output_content_and_state_targets_are_refused(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for target_kind in ("output", "content", "state"):
                with self.subTest(target_kind=target_kind):
                    case = root / target_kind
                    case.mkdir()
                    fixture = CliFixture(case)
                    external = case / "external"
                    if target_kind == "state":
                        external.write_bytes(b"external-state")
                        fixture.state_file.symlink_to(external)
                        args = fixture.args()
                    else:
                        actual = fixture.output if target_kind == "output" else fixture.content
                        moved = case / f"actual-{target_kind}"
                        actual.rename(moved)
                        actual.symlink_to(moved, target_is_directory=True)
                        args = fixture.args()
                    code, stdout, stderr, transport = invoke(
                        fixture, at(0), args=args
                    )
                    self.assertNotEqual(code, 0)
                    self.assertEqual(stdout, "")
                    self.assertIn('"error":"', stderr)
                    self.assertEqual(transport.calls, [])
                    if target_kind == "state":
                        self.assertEqual(external.read_bytes(), b"external-state")

    def test_unsafe_existing_public_entry_is_refused_before_probe(self):
        with TemporaryDirectory() as temporary:
            fixture = CliFixture(temporary)
            external = fixture.root / "external-public"
            external.write_bytes(b"sentinel")
            (fixture.output / "summary.json").symlink_to(external)
            code, stdout, stderr, transport = invoke(fixture, at(0))
            self.assertNotEqual(code, 0)
            self.assertEqual(stdout, "")
            self.assertEqual(
                stderr,
                '{"error":"output","exception":"ValueError"}\n',
            )
            self.assertEqual(transport.calls, [])
            self.assertEqual(external.read_bytes(), b"sentinel")

    def test_failed_public_write_does_not_advance_state(self):
        with TemporaryDirectory() as temporary:
            fixture = CliFixture(temporary)
            with patch(
                "publisher.publisher.write_public_documents",
                side_effect=OSError("sensitive operator detail"),
            ):
                code, stdout, stderr, _transport = invoke(fixture, at(0))
            self.assertNotEqual(code, 0)
            self.assertEqual(stdout, "")
            self.assertEqual(
                stderr,
                '{"error":"public-write","exception":"OSError"}\n',
            )
            self.assertFalse(fixture.state_file.exists())

    def test_state_write_failure_is_safe_and_next_run_recovers_from_history(self):
        with TemporaryDirectory() as temporary:
            fixture = CliFixture(temporary)
            with patch(
                "publisher.publisher.persist_publisher_state",
                side_effect=OSError("sensitive state path and value"),
            ):
                code, stdout, stderr, _transport = invoke(fixture, at(0))
            self.assertNotEqual(code, 0)
            self.assertEqual(stdout, "")
            self.assertEqual(
                stderr,
                '{"error":"state-write","exception":"OSError"}\n',
            )
            self.assertTrue((fixture.output / "history.json").is_file())
            self.assertFalse(fixture.state_file.exists())

            code, stdout, stderr, _transport = invoke(fixture, at(1))
            self.assertEqual((code, stderr), (0, ""))
            self.assertTrue(json.loads(stdout)["state_recovered"])
            summary = json.loads((fixture.output / "summary.json").read_text())
            self.assertEqual(summary["overall"]["state"], "no-data")

    def test_probe_orchestration_error_never_discloses_exception_text(self):
        with TemporaryDirectory() as temporary:
            fixture = CliFixture(temporary)
            canary = secrets.token_hex(32)
            with patch(
                "publisher.publisher.run_probe_cycle",
                side_effect=RuntimeError(canary),
            ):
                code, stdout, stderr, _transport = invoke(fixture, at(0))
            self.assertNotEqual(code, 0)
            self.assertEqual(stdout, "")
            self.assertEqual(
                stderr,
                '{"error":"probe","exception":"RuntimeError"}\n',
            )
            self.assertNotIn(canary, stdout + stderr)
            self.assertFalse(fixture.state_file.exists())

    def test_once_is_required_with_fixed_usage_error(self):
        stdout = StringIO()
        stderr = StringIO()
        code = main([], stdout=stdout, stderr=stderr)
        self.assertNotEqual(code, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(),
            '{"error":"usage","exception":"CliUsageError"}\n',
        )


if __name__ == "__main__":
    unittest.main()
