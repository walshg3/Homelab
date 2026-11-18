from contextlib import redirect_stderr
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
OPERATIONS = ROOT / "operations"
RUN_WRAPPER_NAME = "run_status_soak_watchdog.py"
REPORT_WRAPPER_NAME = "report_status_soak_watchdog.py"


class SoakCronArtifactTests(unittest.TestCase):
    def test_repository_package_wrappers_supply_profile_paths_without_cli_arguments(self):
        run_wrapper = importlib.import_module("operations.run_status_soak_watchdog")
        report_wrapper = importlib.import_module(
            "operations.report_status_soak_watchdog"
        )
        with TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            with (
                patch.dict(os.environ, {"HERMES_HOME": os.fspath(home)}, clear=True),
                patch.object(run_wrapper, "watchdog_main", return_value=17) as run,
                patch.object(report_wrapper, "watchdog_main", return_value=23) as report,
            ):
                self.assertEqual(run_wrapper.main(), 17)
                self.assertEqual(report_wrapper.main(), 23)

        run.assert_called_once_with(
            [
                "run",
                "--config",
                os.fspath(home / "config" / "status-soak" / "watchdog.json"),
            ]
        )
        report.assert_called_once_with(
            [
                "report",
                "--state-file",
                os.fspath(home / "runtime" / "status-soak" / "soak-state.json"),
            ]
        )

    def test_colocated_profile_wrappers_execute_with_no_arguments(self):
        fake_entrypoint = """\
import json
def main(arguments):
    print(json.dumps(arguments, separators=(\",\", \":\")))
    return 0
"""
        with TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            scripts = home / "scripts"
            scripts.mkdir()
            (scripts / "soak_watchdog.py").write_text(
                fake_entrypoint, encoding="utf-8"
            )
            environment = dict(os.environ)
            environment["HERMES_HOME"] = os.fspath(home)

            expected = {
                RUN_WRAPPER_NAME: [
                    "run",
                    "--config",
                    os.fspath(home / "config" / "status-soak" / "watchdog.json"),
                ],
                REPORT_WRAPPER_NAME: [
                    "report",
                    "--state-file",
                    os.fspath(home / "runtime" / "status-soak" / "soak-state.json"),
                ],
            }
            for wrapper_name, arguments in expected.items():
                with self.subTest(wrapper=wrapper_name):
                    shutil.copyfile(OPERATIONS / wrapper_name, scripts / wrapper_name)
                    completed = subprocess.run(
                        [sys.executable, os.fspath(scripts / wrapper_name)],
                        cwd=scripts,
                        env=environment,
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        text=True,
                        check=False,
                    )

                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(json.loads(completed.stdout), arguments)
                    self.assertEqual(completed.stderr, "")

    def test_colocated_real_report_is_self_contained_with_copied_schema_package(self):
        state = {
            "schema_version": "1",
            "window_start": "2026-07-22T12:00:00Z",
            "last_run": "2026-07-22T12:00:00Z",
            "total_runs": 1,
            "healthy_runs": 1,
            "failed_runs": 0,
            "outage_transitions": 0,
            "recovery_transitions": 0,
            "maximum_generation_age_seconds": 0,
            "last_reason_code": "ok",
            "alert_state": "normal",
            "consecutive_failures": 0,
            "consecutive_successes": 1,
        }
        with TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            scripts = home / "scripts"
            publisher = scripts / "publisher"
            runtime = home / "runtime" / "status-soak"
            publisher.mkdir(parents=True)
            runtime.mkdir(parents=True, mode=0o700)
            for source, destination in (
                (OPERATIONS / REPORT_WRAPPER_NAME, scripts / REPORT_WRAPPER_NAME),
                (OPERATIONS / "soak_watchdog.py", scripts / "soak_watchdog.py"),
                (ROOT / "publisher" / "schema.py", publisher / "schema.py"),
                (ROOT / "publisher" / "__init__.py", publisher / "__init__.py"),
            ):
                shutil.copyfile(source, destination)
            state_file = runtime / "soak-state.json"
            state_file.write_text(
                json.dumps(state, separators=(",", ":")) + "\n",
                encoding="ascii",
            )
            state_file.chmod(0o600)
            environment = dict(os.environ)
            environment.pop("PYTHONPATH", None)
            environment["HERMES_HOME"] = os.fspath(home)

            completed = subprocess.run(
                [sys.executable, os.fspath(scripts / REPORT_WRAPPER_NAME)],
                cwd=scripts,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        report = json.loads(completed.stdout)
        self.assertEqual(report["total_runs"], 1)
        self.assertNotIn("consecutive_failures", report)

    def test_wrappers_require_an_explicit_absolute_hermes_home(self):
        wrappers = (
            importlib.import_module("operations.run_status_soak_watchdog"),
            importlib.import_module("operations.report_status_soak_watchdog"),
        )
        for environment in (
            {},
            {"HERMES_HOME": "relative/profile"},
            {"HERMES_HOME": "/"},
            {"HERMES_HOME": "/tmp/profile/../other"},
        ):
            for wrapper in wrappers:
                with self.subTest(environment=environment, wrapper=wrapper.__name__):
                    stderr = io.StringIO()
                    with (
                        patch.dict(os.environ, environment, clear=True),
                        redirect_stderr(stderr),
                    ):
                        self.assertEqual(wrapper.main(), 2)
                    self.assertEqual(stderr.getvalue(), "status-soak-wrapper-invalid\n")

    def test_logical_manifests_use_no_argument_profile_scripts_and_delivery_intent(self):
        run = json.loads(
            (OPERATIONS / "status-soak-cron-job.json").read_text(encoding="utf-8")
        )
        report = json.loads(
            (OPERATIONS / "status-soak-report-cron-job.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            run,
            {
                "name": "Independent status soak watchdog",
                "schedule": "every 5m",
                "script": RUN_WRAPPER_NAME,
                "no_agent": True,
                "deliver": "local",
            },
        )
        self.assertEqual(
            report,
            {
                "name": "Status soak one-shot report",
                "schedule": "at <reviewed-utc-timestamp>",
                "script": REPORT_WRAPPER_NAME,
                "no_agent": True,
                "deliver": "announce",
            },
        )
        for manifest in (run, report):
            self.assertEqual(Path(manifest["script"]).name, manifest["script"])
            self.assertNotIn(" ", manifest["script"])


if __name__ == "__main__":
    unittest.main()
