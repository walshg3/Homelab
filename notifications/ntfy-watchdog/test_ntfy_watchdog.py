import importlib.util
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import fcntl
import http.client
import io
import json
import os
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request


MODULE_PATH = Path(__file__).with_name("ntfy_watchdog.py")
spec = importlib.util.spec_from_file_location("ntfy_watchdog", MODULE_PATH)
assert spec is not None and spec.loader is not None
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


class _Handler(BaseHTTPRequestHandler):
    status = 200
    body = b'{"healthy": true}'
    requests = []
    posts = []
    post_status = 204

    def do_GET(self):
        type(self).requests.append((self.command, self.path, dict(self.headers)))
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(type(self).body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).posts.append((self.path, json.loads(body), dict(self.headers)))
        self.send_response(type(self).post_status)
        self.end_headers()

    def log_message(self, format, *args):
        return


class _Server:
    def __enter__(self):
        _Handler.status = 200
        _Handler.body = b'{"healthy": true}'
        _Handler.requests = []
        _Handler.posts = []
        _Handler.post_status = 204
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        address = self.server.server_address
        self.url = f"http://{address[0]}:{address[1]}/v1/health"
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class DiscordDeliveryTests(unittest.TestCase):
    def test_runtime_error_cannot_escape_network_controlled_text(self):
        secret_marker = "RUNTIME-SECRET-MARKER"
        with patch.object(urllib.request, "urlopen", side_effect=RuntimeError(secret_marker)):
            with self.assertRaises(RuntimeError) as caught:
                watchdog.post_discord(
                    "https://example.invalid/webhook",
                    "outage",
                    {"consecutive_failures": 3, "last_error": "timeout"},
                    timeout=1,
                )

        self.assertNotIn(secret_marker, str(caught.exception))
        self.assertEqual("Discord webhook delivery failed (RuntimeError)", str(caught.exception))

    def test_low_level_http_error_cannot_escape_server_controlled_text(self):
        secret_marker = "SERVER-CONTROLLED-SECRET-MARKER"
        with patch.object(
            urllib.request,
            "urlopen",
            side_effect=http.client.BadStatusLine(secret_marker),
        ):
            with self.assertRaises(RuntimeError) as caught:
                watchdog.post_discord(
                    "https://example.invalid/webhook",
                    "outage",
                    {"consecutive_failures": 3, "last_error": "timeout"},
                    timeout=1,
                )

        self.assertNotIn(secret_marker, str(caught.exception))
        self.assertEqual("Discord webhook delivery failed (BadStatusLine)", str(caught.exception))

    def test_malformed_webhook_url_cannot_escape_sanitized_error(self):
        secret_marker = "WATCHDOG-WEBHOOK-SECRET-MARKER"
        malformed_url = f"https://example.invalid/webhook/{secret_marker}\ninvalid"

        with self.assertRaises(RuntimeError) as caught:
            watchdog.post_discord(
                malformed_url,
                "outage",
                {"consecutive_failures": 3, "last_error": "timeout"},
                timeout=1,
            )

        self.assertNotIn(secret_marker, str(caught.exception))
        self.assertEqual("Discord webhook delivery failed (invalid URL)", str(caught.exception))

    def test_webhook_http_failure_raises_sanitized_error(self):
        with _Server() as server:
            webhook_url = server.url.replace("/v1/health", "/secret-webhook-path")
            _Handler.post_status = 500

            with self.assertRaises(RuntimeError) as caught:
                watchdog.post_discord(
                    webhook_url,
                    "outage",
                    {"consecutive_failures": 3, "last_error": "timeout"},
                    timeout=1,
                )

        self.assertNotIn(webhook_url, str(caught.exception))
        self.assertIn("Discord webhook delivery failed", str(caught.exception))


class MainTests(unittest.TestCase):
    def test_healthy_main_run_is_silent_and_uses_profile_local_paths(self):
        with tempfile.TemporaryDirectory() as directory, _Server() as server:
            home = Path(directory)
            secret = home / "secrets" / "ntfy-watchdog.env"
            secret.parent.mkdir()
            secret.write_text("DISCORD_WEBHOOK_URL=https://example.invalid/webhook\n")
            os.chmod(secret, 0o600)
            output = io.StringIO()

            with redirect_stdout(output):
                result = watchdog.main(
                    environ={
                        "HERMES_HOME": str(home),
                        "NTFY_WATCHDOG_URL": server.url,
                    },
                    now="healthy-main-test",
                )

            self.assertEqual(0, result)
            self.assertEqual("", output.getvalue())
            state_path = home / "runtime" / "ntfy-watchdog" / "state.json"
            state = json.loads(state_path.read_text())
            self.assertEqual("healthy", state["status"])
            self.assertEqual(0o600, stat.S_IMODE(state_path.stat().st_mode))


class ProbeTests(unittest.TestCase):
    def test_protocol_error_counts_as_failed_probe_without_exposing_response_text(self):
        secret_marker = "UPSTREAM-RESPONSE-SECRET-MARKER"
        with patch.object(
            urllib.request,
            "urlopen",
            side_effect=http.client.BadStatusLine(secret_marker),
        ):
            healthy, detail = watchdog.probe_health(
                "https://notify.walshit.com/v1/health",
                timeout=1,
            )

        self.assertFalse(healthy)
        self.assertNotIn(secret_marker, detail)
        self.assertEqual("health probe failed: BadStatusLine", detail)

    def test_probe_accepts_ntfy_healthy_json(self):
        with _Server() as server:
            healthy, detail = watchdog.probe_health(server.url, timeout=1)

        self.assertTrue(healthy)
        self.assertEqual("", detail)
        self.assertEqual("/v1/health", _Handler.requests[0][1])
        user_agent = _Handler.requests[0][2]["User-Agent"]
        self.assertTrue(user_agent.startswith("Mozilla/5.0"))
        self.assertIn("Hermes-Ntfy-Watchdog/1.0", user_agent)


class RunOnceTests(unittest.TestCase):
    def test_overlapping_run_exits_without_probe_or_post(self):
        with tempfile.TemporaryDirectory() as directory, _Server() as server:
            state_path = Path(directory) / "state.json"
            lock_path = watchdog.lock_path_for(state_path)
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                event = watchdog.run_once(
                    probe_url=server.url,
                    webhook_url=server.url.replace("/v1/health", "/webhook"),
                    state_path=state_path,
                    now="overlap",
                )
            finally:
                os.close(descriptor)

            self.assertIsNone(event)
            self.assertEqual([], _Handler.requests)
            self.assertEqual([], _Handler.posts)
            self.assertFalse(state_path.exists())

    def test_mock_outage_and_recovery_post_once_and_persist_restricted_state(self):
        with tempfile.TemporaryDirectory() as directory, _Server() as server:
            state_path = Path(directory) / "state.json"
            webhook_url = server.url.replace("/v1/health", "/webhook")
            _Handler.body = b'{"healthy": false}'

            for index in range(3):
                watchdog.run_once(
                    probe_url=server.url,
                    webhook_url=webhook_url,
                    state_path=state_path,
                    now=f"failure-{index}",
                )

            self.assertEqual(1, len(_Handler.posts))
            self.assertIn("ntfy outage detected", _Handler.posts[0][1]["content"])
            self.assertEqual({"parse": []}, _Handler.posts[0][1]["allowed_mentions"])

            _Handler.body = b'{"healthy": true}'
            for index in range(2):
                watchdog.run_once(
                    probe_url=server.url,
                    webhook_url=webhook_url,
                    state_path=state_path,
                    now=f"recovery-{index}",
                )

            self.assertEqual(2, len(_Handler.posts))
            self.assertIn("ntfy service recovered", _Handler.posts[1][1]["content"])
            final_state = json.loads(state_path.read_text())
            self.assertFalse(final_state["alerted"])
            self.assertEqual(0o600, stat.S_IMODE(state_path.stat().st_mode))


class SecretTests(unittest.TestCase):
    def test_webhook_secret_must_not_be_group_or_world_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watchdog.env"
            path.write_text("DISCORD_WEBHOOK_URL=https://example.invalid/webhook\n")
            os.chmod(path, 0o644)

            with self.assertRaises(PermissionError):
                watchdog.load_webhook_url(path)

            os.chmod(path, 0o600)
            with patch.object(Path, "read_text", side_effect=AssertionError("path reopened")):
                self.assertEqual("https://example.invalid/webhook", watchdog.load_webhook_url(path))


class StateMachineTests(unittest.TestCase):
    def test_thresholds_must_be_positive(self):
        with self.assertRaises(ValueError):
            watchdog.evaluate_result(
                watchdog.initial_state(),
                healthy=False,
                now="invalid",
                failure_threshold=0,
            )
        with self.assertRaises(ValueError):
            watchdog.evaluate_result(
                watchdog.initial_state(),
                healthy=True,
                now="invalid",
                recovery_threshold=-1,
            )

    def test_outage_and_recovery_require_consecutive_results_and_emit_once(self):
        state = watchdog.initial_state()

        state, event = watchdog.evaluate_result(state, healthy=True, now="t0")
        self.assertIsNone(event)

        state, event = watchdog.evaluate_result(state, healthy=False, now="t1", detail="timeout")
        self.assertIsNone(event)
        state, event = watchdog.evaluate_result(state, healthy=False, now="t2", detail="timeout")
        self.assertIsNone(event)
        state, event = watchdog.evaluate_result(state, healthy=False, now="t3", detail="timeout")
        self.assertEqual("outage", event)
        self.assertTrue(state["alerted"])

        state, event = watchdog.evaluate_result(state, healthy=False, now="t4", detail="timeout")
        self.assertIsNone(event)

        state, event = watchdog.evaluate_result(state, healthy=True, now="t5")
        self.assertIsNone(event)
        state, event = watchdog.evaluate_result(state, healthy=True, now="t6")
        self.assertEqual("recovery", event)
        self.assertFalse(state["alerted"])


if __name__ == "__main__":
    unittest.main()
