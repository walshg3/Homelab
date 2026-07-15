"""Unit tests for the stdlib-only ntfy-to-Discord bridge."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("bridge.py")
SPEC = importlib.util.spec_from_file_location("discord_bridge", MODULE_PATH)
assert SPEC and SPEC.loader
bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bridge
SPEC.loader.exec_module(bridge)


def config(state_file: Path, attempts: int = 3) -> bridge.Config:
    return bridge.Config(
        ntfy_url="http://ntfy",
        subscriber_token="",
        webhook_url="https://example.invalid/",
        state_file=state_file,
        state_max_age=180,
        subscribe_timeout=75,
        reconnect_max=30,
        discord_timeout=2,
        discord_attempts=attempts,
        retry_max=10,
    )


def event(**changes):
    value = {
        "id": "message-id-1",
        "event": "message",
        "topic": "homelab-critical",
        "priority": 4,
        "title": "Disk alert",
        "message": "Pool is degraded",
        "tags": ["warning"],
    }
    value.update(changes)
    return value


class FakeResponse:
    def __init__(self, status=204, headers=None):
        self.status = status
        self.headers = headers or Message()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status


class FilteringTests(unittest.TestCase):
    def test_only_high_priority_allowed_topics_forward(self):
        self.assertTrue(bridge.should_forward(event()))
        self.assertTrue(bridge.should_forward(event(topic="homelab-ops", priority=5)))
        self.assertFalse(bridge.should_forward(event(topic="homelab-info")))
        self.assertFalse(bridge.should_forward(event(priority=3)))
        self.assertFalse(bridge.should_forward(event(event="keepalive")))

    def test_exact_test_smoke_tags_and_risky_features_are_filtered(self):
        self.assertFalse(bridge.should_forward(event(tags=["test"])))
        self.assertFalse(bridge.should_forward(event(tags=["SMOKE"])))
        self.assertTrue(bridge.should_forward(event(tags=["smoketest"])))
        self.assertFalse(bridge.should_forward(event(attachment={"url": "https://invalid/attachment"})))
        self.assertFalse(bridge.should_forward(event(actions=[{"action": "view"}])))
        self.assertFalse(bridge.should_forward(event(message=f"echo {bridge.ORIGIN_MARKER}")))


class SanitizationTests(unittest.TestCase):
    def test_secrets_authorization_webhooks_and_controls_are_redacted(self):
        raw = (
            "Authorization: Bearer abcdefghijklmnop\n"
            "{\"Authorization\": \"Bearer jsoncredentialvalue\"} "
            "token=abcdefghijklmnop\x00 "
            "https://discord.com/api/webhooks/123456/very-secret-webhook-part "
            "tk_abcdefghijklmnop"
        )
        clean = bridge.sanitize(raw, 500)
        self.assertNotIn("abcdefghijklmnop", clean)
        self.assertNotIn("very-secret-webhook-part", clean)
        self.assertNotIn("jsoncredentialvalue", clean)
        self.assertNotIn("\x00", clean)
        self.assertNotIn("\n", clean)
        self.assertIn("[REDACTED", clean)

    def test_payload_is_bounded_and_disables_mentions(self):
        payload = json.loads(
            bridge.discord_payload(
                event(title="T" * 1_000, message="@everyone " + "M" * 10_000)
            )
        )
        self.assertLessEqual(len(payload["content"]), bridge.MAX_DISCORD_CONTENT_CHARS)
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertIn(bridge.ORIGIN_MARKER, payload["content"])


class DeliveryTests(unittest.TestCase):
    def test_successful_delivery_posts_json(self):
        seen = []

        def opener(request, timeout):
            seen.append((request, timeout))
            return FakeResponse(204)

        with tempfile.TemporaryDirectory() as directory:
            cfg = config(Path(directory) / "state.json")
            bridge.deliver(cfg, b"{}", opener=opener)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0].method, "POST")
        self.assertEqual(seen[0][0].get_header("Content-type"), "application/json")

    def test_429_uses_retry_after_then_succeeds(self):
        calls = 0
        delays = []
        headers = Message()
        headers["Retry-After"] = "2.5"

        def opener(request, timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise urllib.error.HTTPError(request.full_url, 429, "rate limited", headers, None)
            return FakeResponse(204)

        with tempfile.TemporaryDirectory() as directory:
            bridge.deliver(
                config(Path(directory) / "state.json"),
                b"{}",
                opener=opener,
                sleeper=delays.append,
                jitter=lambda: 0,
            )
        self.assertEqual(calls, 2)
        self.assertEqual(delays, [2.5])

    def test_non_429_4xx_is_not_retried(self):
        calls = 0

        def opener(request, timeout):
            nonlocal calls
            calls += 1
            raise urllib.error.HTTPError(request.full_url, 400, "bad request", Message(), None)

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(bridge.PermanentDeliveryError):
                bridge.deliver(config(Path(directory) / "state.json"), b"{}", opener=opener)
        self.assertEqual(calls, 1)


class CursorTests(unittest.TestCase):
    def test_open_control_event_does_not_advance_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            state = bridge.StateStore(Path(directory) / "state.json")
            state.write("previous-id", "running")
            handled = bridge.process_event(
                event(id="open-record-id", event="open"),
                config(state.path),
                state,
            )
            self.assertFalse(handled)
            self.assertEqual(state.cursor(), "previous-id")

    def test_filtered_event_advances_cursor_without_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            state = bridge.StateStore(Path(directory) / "state.json")
            delivered = []
            handled = bridge.process_event(
                event(id="filtered-id", priority=2),
                config(state.path),
                state,
                lambda *_args: delivered.append(True),
            )
            self.assertTrue(handled)
            self.assertEqual(state.cursor(), "filtered-id")
            self.assertEqual(delivered, [])

    def test_successful_delivery_advances_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            state = bridge.StateStore(Path(directory) / "state.json")
            delivered = []
            bridge.process_event(
                event(id="delivered-id"),
                config(state.path),
                state,
                lambda _config, payload: delivered.append(json.loads(payload)),
            )
            self.assertEqual(state.cursor(), "delivered-id")
            self.assertEqual(len(delivered), 1)

    def test_exhausted_delivery_does_not_advance_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            state = bridge.StateStore(Path(directory) / "state.json")
            state.write("previous-id", "running")
            cfg = config(state.path, attempts=2)
            calls = 0

            def unavailable(request, timeout):
                nonlocal calls
                calls += 1
                raise urllib.error.HTTPError(request.full_url, 503, "unavailable", Message(), None)

            def fail(delivery_config, payload):
                bridge.deliver(
                    delivery_config,
                    payload,
                    opener=unavailable,
                    sleeper=lambda _delay: None,
                    jitter=lambda: 0,
                )

            with self.assertRaises(bridge.DeliveryError):
                bridge.process_event(event(id="failed-id"), cfg, state, fail)
            self.assertEqual(state.cursor(), "previous-id")
            self.assertEqual(calls, 2)

    def test_first_start_has_no_since_and_reconnect_uses_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = config(Path(directory) / "state.json")
            self.assertNotIn("since=", bridge.subscription_url(cfg, None))
            self.assertIn("since=last-id", bridge.subscription_url(cfg, "last-id"))


if __name__ == "__main__":
    unittest.main()
