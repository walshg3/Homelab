from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from publisher import runner


ROOT = Path(__file__).resolve().parents[1]
CANARY_TOKEN = "FAKE_CANARY_TOKEN_7Q2K9"
CANARY_MACHINE = "FAKE_CANARY_MACHINE_4X8P"
UTC = timezone.utc


def secret_text():
    return (
        f"PLEX_TOKEN={CANARY_TOKEN}\n"
        f"PLEX_MACHINE_IDENTIFIER={CANARY_MACHINE}\n"
    )


def write_secret(root, text=None, *, mode=0o600):
    path = Path(root) / "plex-probe.env"
    path.write_bytes((secret_text() if text is None else text).encode("utf-8"))
    path.chmod(mode)
    return path


class SecretLoaderTests(unittest.TestCase):
    def test_accepts_mode_0600_file_owned_by_current_effective_uid(self):
        with TemporaryDirectory() as temporary:
            path = write_secret(temporary)
            self.assertEqual(path.stat().st_uid, os.geteuid())
            loaded = runner.load_secret_file(path)
        self.assertEqual(dict(loaded), {
            "PLEX_TOKEN": CANARY_TOKEN,
            "PLEX_MACHINE_IDENTIFIER": CANARY_MACHINE,
        })
        with self.assertRaises(TypeError):
            loaded["PLEX_TOKEN"] = "replacement"
        with self.assertRaises(TypeError):
            loaded._values["PLEX_TOKEN"] = "replacement"
        self.assertNotIn(CANARY_TOKEN, repr(loaded))
        self.assertNotIn(CANARY_MACHINE, repr(loaded))

    def test_rejects_format_and_value_classes_without_projecting_canaries(self):
        invalid = (
            "",
            "PLEX_TOKEN=x\n",
            "PLEX_TOKEN=x\nPLEX_TOKEN=y\nPLEX_MACHINE_IDENTIFIER=z\n",
            "UNKNOWN=x\nPLEX_TOKEN=y\nPLEX_MACHINE_IDENTIFIER=z\n",
            "PLEX_TOKEN=\nPLEX_MACHINE_IDENTIFIER=z\n",
            "PLEX_TOKEN='quoted'\nPLEX_MACHINE_IDENTIFIER=z\n",
            "PLEX_TOKEN=${EXPAND}\nPLEX_MACHINE_IDENTIFIER=z\n",
            "PLEX_TOKEN=$(command)\nPLEX_MACHINE_IDENTIFIER=z\n",
            "# comment\nPLEX_TOKEN=x\nPLEX_MACHINE_IDENTIFIER=z\n",
            "PLEX_TOKEN=x y\nPLEX_MACHINE_IDENTIFIER=z\n",
            "PLEX_TOKEN=x\x00y\nPLEX_MACHINE_IDENTIFIER=z\n",
            "PLEX_TOKEN=x\x07y\nPLEX_MACHINE_IDENTIFIER=z\n",
            "PLEX_TOKEN=caf\N{LATIN SMALL LETTER E WITH ACUTE}\nPLEX_MACHINE_IDENTIFIER=z\n",
            f"PLEX_TOKEN={'x' * 1025}\nPLEX_MACHINE_IDENTIFIER=z\n",
        )
        with TemporaryDirectory() as temporary:
            for index, text in enumerate(invalid):
                with self.subTest(index=index):
                    path = write_secret(temporary, text)
                    stdout, stderr = StringIO(), StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        with self.assertRaises(runner.SecretFileError) as raised:
                            runner.load_secret_file(path)
                    projection = stdout.getvalue() + stderr.getvalue() + repr(raised.exception) + str(raised.exception)
                    self.assertNotIn(CANARY_TOKEN, projection)
                    self.assertNotIn(CANARY_MACHINE, projection)
                    self.assertNotIn(os.fspath(path), projection)
                    self.assertLessEqual(len(projection), 160)

    def test_rejects_unsafe_filesystem_metadata_and_oversize(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = write_secret(root)
            for mode in (0o640, 0o604, 0o666):
                with self.subTest(mode=oct(mode)):
                    path.chmod(mode)
                    with self.assertRaises(runner.SecretFileError):
                        runner.load_secret_file(path)
            path.chmod(0o600)
            hardlink = root / "hardlink.env"
            os.link(path, hardlink)
            with self.assertRaises(runner.SecretFileError):
                runner.load_secret_file(path)
            hardlink.unlink()
            path.unlink()
            path.mkdir()
            with self.assertRaises(runner.SecretFileError):
                runner.load_secret_file(path)
            path.rmdir()
            target = write_secret(root)
            symlink = root / "symlink.env"
            symlink.symlink_to(target)
            with self.assertRaises(runner.SecretFileError):
                runner.load_secret_file(symlink)
            target.write_bytes(b"x" * 4097)
            target.chmod(0o600)
            with self.assertRaises(runner.SecretFileError):
                runner.load_secret_file(target)

    def test_rejects_ancestor_symlink_directory_and_fifo(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            actual_parent = root / "actual"
            actual_parent.mkdir()
            write_secret(actual_parent)
            linked_parent = root / "linked"
            linked_parent.symlink_to(actual_parent, target_is_directory=True)
            with self.assertRaises(runner.SecretFileError):
                runner.load_secret_file(linked_parent / "plex-probe.env")

            directory = root / "directory.env"
            directory.mkdir()
            with self.assertRaises(runner.SecretFileError):
                runner.load_secret_file(directory)

            fifo = root / "fifo.env"
            os.mkfifo(fifo, 0o600)
            with self.assertRaises(runner.SecretFileError):
                runner.load_secret_file(fifo)

    def test_rejects_owner_mismatch_and_mutation_during_read(self):
        with TemporaryDirectory() as temporary:
            path = write_secret(temporary)
            actual = path.stat()
            fake_owner = os.geteuid() + 17 if os.geteuid() != 0 else 17
            with patch("publisher.runner.os.fstat") as fstat:
                fstat.side_effect = [
                    actual,
                    os.stat_result((actual.st_mode, actual.st_ino, actual.st_dev,
                                    actual.st_nlink, fake_owner, actual.st_gid,
                                    actual.st_size, actual.st_atime,
                                    actual.st_mtime, actual.st_ctime)),
                ]
                with self.assertRaises(runner.SecretFileError):
                    runner.load_secret_file(path)

            original = runner._read_secret_bytes
            def mutate(fd):
                data = original(fd)
                os.fchmod(fd, 0o400)
                return data
            with patch("publisher.runner._read_secret_bytes", side_effect=mutate):
                with self.assertRaises(runner.SecretFileError):
                    runner.load_secret_file(path)


class ManualClock:
    def __init__(self):
        self.value = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


class ServeLoopTests(unittest.TestCase):
    def options(self, secret):
        return runner.ServeOptions("cfg", "content", "output", "state", os.fspath(secret), 60.0)

    def test_immediate_nonoverlapping_cycles_use_exact_once_arguments(self):
        with TemporaryDirectory() as temporary:
            secret = write_secret(temporary)
            clock = ManualClock()
            calls = []
            def publish(argv, **kwargs):
                calls.append((tuple(argv), kwargs["environ"], clock.value))
                clock.value += 5
                if len(calls) == 3:
                    raise KeyboardInterrupt
                return 0
            code = runner.run_serve(self.options(secret), publish=publish,
                                    monotonic=clock.monotonic, sleep=clock.sleep)
        self.assertEqual(code, 0)
        expected = ("--once", "--config", "cfg", "--content-root", "content",
                    "--output-dir", "output", "--state-file", "state")
        self.assertEqual([call[0] for call in calls], [expected] * 3)
        self.assertEqual([call[2] for call in calls], [0, 60, 120])
        self.assertEqual(clock.sleeps, [55, 55])
        self.assertTrue(all(repr(call[1]) == "<secret mapping>" for call in calls))

    def test_exact_boundaries_and_overruns_skip_only_missed_ticks(self):
        cases = (
            (0, 3, [0, 60, 120], [60, 60]),
            (60, 2, [0, 60], []),
            (120, 2, [0, 120], []),
            (61, 2, [0, 120], [59]),
            (125, 2, [0, 180], [55]),
        )
        for duration, cycle_count, expected_starts, expected_sleeps in cases:
            with self.subTest(duration=duration), TemporaryDirectory() as temporary:
                secret = write_secret(temporary)
                clock = ManualClock()
                starts = []

                def publish(_argv, **_kwargs):
                    starts.append(clock.value)
                    if len(starts) == cycle_count:
                        raise KeyboardInterrupt
                    clock.value += duration
                    return 0

                self.assertEqual(
                    runner.run_serve(
                        self.options(secret), publish=publish,
                        monotonic=clock.monotonic, sleep=clock.sleep,
                    ),
                    0,
                )
            self.assertEqual(starts, expected_starts)
            self.assertEqual(clock.sleeps, expected_sleeps)
            self.assertTrue(all(delay > 0 for delay in clock.sleeps))

    def test_nonzero_and_exception_are_sanitized_and_continue(self):
        with TemporaryDirectory() as temporary:
            secret = write_secret(temporary)
            clock = ManualClock()
            outcomes = iter((2, RuntimeError(CANARY_TOKEN), KeyboardInterrupt()))
            def publish(_argv, **_kwargs):
                outcome = next(outcomes)
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome
            stdout, stderr = StringIO(), StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = runner.run_serve(self.options(secret), publish=publish,
                                        monotonic=clock.monotonic, sleep=clock.sleep)
        self.assertEqual(code, 0)
        output = stdout.getvalue() + stderr.getvalue()
        self.assertEqual(output, "publisher-cycle-failed\npublisher-cycle-failed\n")
        self.assertNotIn(CANARY_TOKEN, output)

    def test_success_output_is_discarded_at_the_runner_boundary(self):
        with TemporaryDirectory() as temporary:
            secret = write_secret(temporary)
            clock = ManualClock()
            calls = 0
            def publish(_argv, **kwargs):
                nonlocal calls
                calls += 1
                kwargs["stdout"].write(CANARY_TOKEN)
                if calls == 2:
                    raise KeyboardInterrupt
                return 0
            stdout = StringIO()
            with redirect_stdout(stdout):
                runner.run_serve(self.options(secret), publish=publish,
                                 monotonic=clock.monotonic, sleep=clock.sleep)
        self.assertEqual(stdout.getvalue(), "")

    def test_does_not_spawn_or_mutate_process_environment(self):
        with TemporaryDirectory() as temporary:
            secret = write_secret(temporary)
            before = dict(os.environ)
            with patch("subprocess.run", side_effect=AssertionError("spawned")), \
                 patch("subprocess.Popen", side_effect=AssertionError("spawned")):
                def publish(_argv, **_kwargs):
                    raise KeyboardInterrupt
                runner.run_serve(self.options(secret), publish=publish)
        self.assertEqual(dict(os.environ), before)
        self.assertNotIn(CANARY_TOKEN, os.environ.values())


class HealthcheckTests(unittest.TestCase):
    def documents(self, generated):
        summary = json.loads((ROOT / "fixtures" / "summary.operational.json").read_text())
        history = json.loads((ROOT / "fixtures" / "history.sample.json").read_text())
        stamp = generated.isoformat().replace("+00:00", "Z")
        summary["generated_at"] = stamp
        history["generated_at"] = stamp
        incidents = {"schema_version": "2", "generated_at": stamp, "events": []}
        return summary, history, incidents

    def write_documents(self, root, generated):
        for name, document in zip(runner.PUBLIC_DOCUMENTS, self.documents(generated)):
            (Path(root) / name).write_text(json.dumps(document))

    def assert_fixed_health_failure(self, root, now):
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            self.assertEqual(runner.health_main(root, now=lambda: now), 1)
        self.assertEqual(output.getvalue(), "publisher-health-failed\n")

    def assert_public_document_rejected(self, raw):
        self.assertLessEqual(len(raw), runner.MAX_PUBLIC_JSON_BYTES)
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "document.json"
            path.write_bytes(raw)
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_fd = os.open(temporary, flags)
            try:
                with self.assertRaises(ValueError):
                    runner._read_public_document(directory_fd, path.name)
            finally:
                os.close(directory_fd)

    def test_success_is_fixed_and_no_secret_is_loaded(self):
        now = datetime(2026, 7, 21, 12, tzinfo=UTC)
        with TemporaryDirectory() as temporary:
            self.write_documents(temporary, now - timedelta(seconds=30))
            stdout, stderr = StringIO(), StringIO()
            with patch("publisher.runner.load_secret_file", side_effect=AssertionError("secret loaded")), \
                 redirect_stdout(stdout), redirect_stderr(stderr):
                code = runner.health_main(temporary, now=lambda: now)
        self.assertEqual((code, stdout.getvalue(), stderr.getvalue()),
                         (0, "publisher-health-ok\n", ""))

    def test_rejects_stale_future_and_generation_mismatch(self):
        now = datetime(2026, 7, 21, 12, tzinfo=UTC)
        for delta in (timedelta(seconds=181), timedelta(seconds=-31)):
            with self.subTest(delta=delta), TemporaryDirectory() as temporary:
                self.write_documents(temporary, now - delta)
                self.assertEqual(runner.health_main(temporary, now=lambda: now), 1)
        with TemporaryDirectory() as temporary:
            self.write_documents(temporary, now)
            path = Path(temporary) / "history.json"
            history = json.loads(path.read_text())
            history["generated_at"] = (now - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
            path.write_text(json.dumps(history))
            self.assertEqual(runner.health_main(temporary, now=lambda: now), 1)

    def test_rejects_missing_symlink_malformed_oversize_schema_and_privacy(self):
        now = datetime(2026, 7, 21, 12, tzinfo=UTC)
        mutations = (b"{", b"x" * (runner.MAX_PUBLIC_JSON_BYTES + 1))
        for content in mutations:
            with self.subTest(size=len(content)), TemporaryDirectory() as temporary:
                self.write_documents(temporary, now)
                (Path(temporary) / "summary.json").write_bytes(content)
                self.assertEqual(runner.health_main(temporary, now=lambda: now), 1)
        with TemporaryDirectory() as temporary:
            self.write_documents(temporary, now)
            (Path(temporary) / "summary.json").unlink()
            self.assertEqual(runner.health_main(temporary, now=lambda: now), 1)
        with TemporaryDirectory() as temporary:
            self.write_documents(temporary, now)
            target = Path(temporary) / "actual.json"
            target.write_text((Path(temporary) / "summary.json").read_text())
            (Path(temporary) / "summary.json").unlink()
            (Path(temporary) / "summary.json").symlink_to(target)
            self.assertEqual(runner.health_main(temporary, now=lambda: now), 1)
        for mutate in (
            lambda d: d[0].update(schema_version="1"),
            lambda d: d[0]["overall"].update(body="token=FAKE_CANARY"),
        ):
            with TemporaryDirectory() as temporary:
                documents = list(self.documents(now))
                mutate(documents)
                for name, document in zip(runner.PUBLIC_DOCUMENTS, documents):
                    (Path(temporary) / name).write_text(json.dumps(document))
                output = StringIO()
                with redirect_stdout(output), redirect_stderr(output):
                    self.assertEqual(runner.health_main(temporary, now=lambda: now), 1)
                self.assertEqual(output.getvalue(), "publisher-health-failed\n")
                self.assertNotIn("FAKE_CANARY", output.getvalue())

    def test_structural_json_limits_apply_before_schema_or_serialization(self):
        self.assertEqual(runner.MAX_PUBLIC_JSON_DEPTH, 64)
        self.assertEqual(runner.MAX_PUBLIC_JSON_NODES, 20_000)
        self.assertEqual(runner.MAX_PUBLIC_JSON_TEXT_BYTES, 1024 * 1024)

        too_many_nodes = json.dumps(
            [None] * runner.MAX_PUBLIC_JSON_NODES,
            separators=(",", ":"),
        ).encode("utf-8")
        too_deep = ("[" * 65 + "null" + "]" * 65).encode("utf-8")
        too_much_text = json.dumps(
            "x" * (runner.MAX_PUBLIC_JSON_TEXT_BYTES + 1)
        ).encode("utf-8")
        for name, raw in (
            ("nodes", too_many_nodes),
            ("depth", too_deep),
            ("text", too_much_text),
        ):
            with self.subTest(bound=name):
                self.assert_public_document_rejected(raw)
                with TemporaryDirectory() as temporary:
                    now = datetime(2026, 7, 21, 12, tzinfo=UTC)
                    self.write_documents(temporary, now)
                    (Path(temporary) / "summary.json").write_bytes(raw)
                    with patch("publisher.runner.json.dumps", wraps=json.dumps) as serialize:
                        self.assert_fixed_health_failure(temporary, now)
                    serialize.assert_not_called()

    def test_recursion_error_is_normalized_to_a_bounded_rejection(self):
        deeply_nested = ("[" * 1100 + "null" + "]" * 1100).encode("utf-8")
        self.assert_public_document_rejected(deeply_nested)

    def test_strict_decode_failures_have_fixed_sanitized_output(self):
        now = datetime(2026, 7, 21, 12, tzinfo=UTC)
        invalid_documents = {
            "duplicate": b'{"schema_version":"2","schema_version":"2"}',
            "nonfinite": b'{"value":NaN}',
            "trailing": b'{} {}',
            "invalid-utf8": b'{"value":"\xff"}',
        }
        for name, raw in invalid_documents.items():
            with self.subTest(case=name), TemporaryDirectory() as temporary:
                self.write_documents(temporary, now)
                (Path(temporary) / "summary.json").write_bytes(raw)
                self.assert_fixed_health_failure(temporary, now)

    def test_history_window_accepts_exact_edges_and_rejects_outside_them(self):
        now = datetime(2026, 7, 21, 12, tzinfo=UTC)
        earliest = (now.date() - timedelta(days=89)).isoformat()
        latest = now.date().isoformat()

        with TemporaryDirectory() as temporary:
            documents = list(self.documents(now))
            for component in documents[1]["components"]:
                component["days"] = [
                    {"date": earliest, "state": "no-data"},
                    {"date": latest, "state": "operational"},
                ]
            for name, document in zip(runner.PUBLIC_DOCUMENTS, documents):
                (Path(temporary) / name).write_text(json.dumps(document))
            output = StringIO()
            with redirect_stdout(output), redirect_stderr(output):
                self.assertEqual(runner.health_main(temporary, now=lambda: now), 0)
            self.assertEqual(output.getvalue(), "publisher-health-ok\n")

        for outside in (
            (now.date() - timedelta(days=90)).isoformat(),
            (now.date() + timedelta(days=1)).isoformat(),
        ):
            with self.subTest(outside=outside), TemporaryDirectory() as temporary:
                documents = list(self.documents(now))
                documents[1]["components"][0]["days"] = [
                    {"date": outside, "state": "no-data"},
                ]
                for name, document in zip(runner.PUBLIC_DOCUMENTS, documents):
                    (Path(temporary) / name).write_text(json.dumps(document))
                self.assert_fixed_health_failure(temporary, now)


if __name__ == "__main__":
    unittest.main()
