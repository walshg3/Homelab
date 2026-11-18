#!/usr/bin/env python3
"""Deterministic loopback Chromium acceptance check for the status renderer."""

from contextlib import contextmanager
from copy import deepcopy
from html.parser import HTMLParser
import json
import mimetypes
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
FIXTURES = ROOT / "fixtures"
CHROMIUM_CANDIDATES = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")
EXPECTED_SCENARIO_COUNT = 12
FONT_REQUESTS = {
    "/fonts/archivo-narrow-italic-variable.woff2",
    "/fonts/archivo-narrow-variable.woff2",
    "/fonts/libre-baskerville-italic-variable.woff2",
    "/fonts/libre-baskerville-variable.woff2",
    "/fonts/source-serif-4-italic-variable.woff2",
    "/fonts/source-serif-4-variable.woff2",
}
EXPECTED_REQUESTS = {
    "/",
    "/status.css",
    "/navigation.js",
    "/status.js",
    "/status/v2/summary.json",
    "/status/v2/history.json",
    "/status/v2/incidents.json",
    "/favicon.ico",
    "/apple-touch-icon.png",
} | FONT_REQUESTS


class NodeCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        node = {
            "tag": tag,
            "attrs": dict(attrs),
            "classes": set(dict(attrs).get("class", "").split()),
            "text": [],
            "parent": self.stack[-1] if self.stack else None,
        }
        self.nodes.append(node)
        self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.stack.pop()

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        for node in self.stack:
            node["text"].append(data)

    def by_id(self, element_id):
        matches = [node for node in self.nodes if node["attrs"].get("id") == element_id]
        assert len(matches) == 1, f"expected exactly one #{element_id}, found {len(matches)}"
        return matches[0]

    def by_class(self, class_name):
        return [node for node in self.nodes if class_name in node["classes"]]

    @staticmethod
    def text(node):
        return " ".join("".join(node["text"]).split())


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@contextmanager
def fixture_server(
    summary,
    history,
    clock_ms,
    fail_history=False,
    malformed_summary=False,
    incidents=None,
    fail_incidents=False,
):
    requests = []
    summary_bytes = json.dumps(summary, separators=(",", ":")).encode()
    history_bytes = json.dumps(history, separators=(",", ":")).encode()
    incidents = incidents if incidents is not None else {"schema_version": "2", "generated_at": summary["generated_at"], "events": []}
    incidents_bytes = json.dumps(incidents, separators=(",", ":")).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            requests.append(path)
            if path == "/status/v2/summary.json":
                body = b"{" if malformed_summary else summary_bytes
                return self.send_bytes(200, "application/json", body)
            if path == "/status/v2/history.json":
                if fail_history:
                    return self.send_bytes(503, "text/plain", b"unavailable")
                return self.send_bytes(200, "application/json", history_bytes)
            if path == "/status/v2/incidents.json":
                return self.send_bytes(503 if fail_incidents else 200, "application/json", incidents_bytes)
            if path == "/":
                return self.send_file(WEB / "index.html")
            if path == "/status.js":
                injected = f"globalThis.__STATUS_NOW_MS__ = {clock_ms};\n".encode()
                return self.send_bytes(
                    200,
                    "text/javascript",
                    injected + (WEB / "status.js").read_bytes(),
                )
            if path in {"/status.css", "/navigation.js", "/favicon.ico", "/apple-touch-icon.png"} | FONT_REQUESTS:
                return self.send_file(WEB / path.removeprefix("/"))
            self.send_bytes(404, "text/plain", b"not found")

        def send_file(self, path):
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_bytes(200, content_type, path.read_bytes())

        def send_bytes(self, status, content_type, body):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    except OSError as error:
        raise SystemExit(
            "render_check prerequisite unavailable: cannot bind an ephemeral "
            f"loopback HTTP server ({error})"
        ) from error
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def chromium_path():
    for candidate in CHROMIUM_CANDIDATES:
        found = shutil.which(candidate)
        if found:
            return found
    raise SystemExit(
        "render_check prerequisite missing: install a system Chromium executable "
        f"({', '.join(CHROMIUM_CANDIDATES)})"
    )


def render(
    chromium,
    summary,
    history,
    clock_ms,
    fail_history=False,
    malformed_summary=False,
    incidents=None,
    fail_incidents=False,
):
    with tempfile.TemporaryDirectory(prefix="status-render-check-") as profile:
        with fixture_server(
            summary,
            history,
            clock_ms,
            fail_history,
            malformed_summary,
            incidents,
            fail_incidents,
        ) as (port, requests):
            command = [
                chromium,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-default-apps",
                "--disable-extensions",
                "--disable-sync",
                "--metrics-recording-only",
                "--no-first-run",
                "--run-all-compositor-stages-before-draw",
                "--virtual-time-budget=1500",
                f"--user-data-dir={profile}",
                "--dump-dom",
                f"http://127.0.0.1:{port}/",
            ]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
            recorded = list(requests)
    if completed.returncode:
        raise AssertionError(
            f"Chromium exited {completed.returncode}: {completed.stderr[-1200:]}"
        )
    if "Uncaught" in completed.stderr:
        raise AssertionError(f"page exception reported by Chromium: {completed.stderr[-1200:]}")
    unexpected = sorted(set(recorded) - EXPECTED_REQUESTS)
    assert not unexpected, f"unexpected same-origin requests: {unexpected}"
    assert set(recorded) >= {
        "/status/v2/summary.json",
        "/status/v2/history.json",
        "/status/v2/incidents.json",
    }, f"status endpoints were not both requested: {recorded}"
    document = NodeCollector()
    document.feed(completed.stdout)
    return document


def assert_visible(document, element_id):
    node = document.by_id(element_id)
    assert "hidden" not in node["attrs"], f"#{element_id} should be visible"
    return node


def assert_hidden(document, element_id):
    node = document.by_id(element_id)
    assert "hidden" in node["attrs"], f"#{element_id} should be hidden"


def assert_common_valid(document, summary):
    body = next(node for node in document.nodes if node["tag"] == "body")
    assert body["attrs"].get("data-status-render") == "ready"
    assert_hidden(document, "load-error")
    component_rows = [
        node for node in document.nodes if node["attrs"].get("data-component")
    ]
    assert [node["attrs"]["data-component"] for node in component_rows] == [
        "watch", "home", "requests", "audiobooks", "site"
    ]
    marks = document.by_class("history-mark")
    assert len(marks) == 450, f"expected 450 history marks, found {len(marks)}"
    for row in component_rows:
        row_marks = []
        for mark in marks:
            ancestor = mark["parent"]
            while ancestor and ancestor is not row:
                ancestor = ancestor["parent"]
            if ancestor is row:
                row_marks.append(mark)
        assert len(row_marks) == 90, (
            f"{row['attrs']['data-component']} has {len(row_marks)} history marks"
        )
    assert all("tabindex" not in mark["attrs"] for mark in marks)
    strip_containers = document.by_class("history-strip-container")
    assert len(strip_containers) == 5
    strip_labels = [node["attrs"].get("aria-label", "") for node in strip_containers]
    assert all("3 days with recorded states" in label for label in strip_labels)
    assert all("days measured" not in label for label in strip_labels)
    assert len(document.by_class("state-stamp")) == 5
    text = NodeCollector.text(body)
    assert "3 days on record" in text
    assert "90 days on record" not in text
    assert "Measured availability and missing data are shown separately" in text
    assert "missing periods are never filled with invented history" in text
    if "hidden" in document.by_id("stale-warning")["attrs"]:
        assert "No resolved incidents or completed maintenance." in text
    else:
        assert "Incident archive unavailable." in text
    assert "This report covers public availability and incidents" in text
    assert "No current condition is asserted here." not in text
    assert "No past availability or incident record is claimed" not in text
    assert "Future reports cover" not in text
    assert not document.by_class("filed-event")
    overall = document.by_id("overall-state-label")
    expected_label = {
        "operational": "Operational",
        "outage": "Outage",
        "maintenance": "Maintenance",
        "no-data": "No data",
    }[summary["overall"]["state"]]
    assert NodeCollector.text(overall) == expected_label
    mast = document.by_class("overall")[0]
    assert f"state-{summary['overall']['state']}" in mast["classes"]


def assert_event(document, summary):
    event = summary["active_events"][0]
    tickets = [node for node in document.by_class("ticket-event") if "active-event" in node["classes"]]
    assert len(tickets) == 1
    ticket_text = NodeCollector.text(tickets[0])
    for expected in (event["title"], event["body"], "Severity not specified by v2 contract"):
        assert expected in ticket_text
    assert event["kind"].title() in ticket_text
    expected_status = event["status"].replace("-", " ")
    assert expected_status.casefold() in ticket_text.casefold()
    for component_id in event["affected_components"]:
        component = next(item for item in summary["components"] if item["id"] == component_id)
        assert component["name"] in ticket_text
    times = [node["attrs"].get("datetime") for node in document.nodes if node["tag"] == "time"]
    assert event["started_at"] in times and event["updated_at"] in times
    updates = document.by_class("event-update")
    assert len(updates) == len(event["updates"])
    for expected, rendered in zip(event["updates"], updates, strict=True):
        assert expected["body"] in NodeCollector.text(rendered)


def run_checks():
    chromium = chromium_path()
    history = load_fixture("history.sample.json")
    scenarios = []
    for fixture, clock in (
        ("summary.operational.json", "2026-07-19T14:45:00Z"),
        ("summary.incident.json", "2026-07-19T15:03:00Z"),
        ("summary.maintenance.json", "2026-07-19T14:33:00Z"),
    ):
        summary = load_fixture(fixture)
        matching_history = deepcopy(history)
        matching_history["generated_at"] = summary["generated_at"]
        document = render(chromium, summary, matching_history, int(__import__("datetime").datetime.fromisoformat(clock.replace("Z", "+00:00")).timestamp() * 1000))
        assert_common_valid(document, summary)
        assert_hidden(document, "stale-warning")
        if summary["active_events"]:
            assert_event(document, summary)
        else:
            quiet = document.by_class("event-quiet")
            assert len(quiet) == 1
            assert "No active incidents or planned maintenance are on the board." in NodeCollector.text(quiet[0])
        scenarios.append(fixture.removeprefix("summary.").removesuffix(".json"))

    incident = load_fixture("summary.incident.json")
    stale_clock = 1784473380001
    stale = render(chromium, incident, history, stale_clock)
    assert_common_valid(stale, incident)
    assert_visible(stale, "stale-warning")
    scenarios.append("stale")

    no_data_summary = deepcopy(load_fixture("summary.operational.json"))
    no_data_history = deepcopy(history)
    no_data_history["generated_at"] = no_data_summary["generated_at"]
    no_data_summary["overall"] = {
        "state": "no-data",
        "headline": "Public status is not verified yet.",
        "body": "Fresh probe results are required before availability can be confirmed.",
    }
    for component in no_data_summary["components"]:
        component["state"] = "no-data"
    no_data = render(chromium, no_data_summary, no_data_history, 1784472300000)
    assert_common_valid(no_data, no_data_summary)
    assert_hidden(no_data, "stale-warning")
    assert NodeCollector.text(no_data.by_id("overall-state-label")) == "No data"
    stamps = no_data.by_class("state-stamp")
    assert len(stamps) == 5
    assert all(NodeCollector.text(stamp) == "No data" for stamp in stamps)
    quiet = no_data.by_class("event-quiet")
    assert len(quiet) == 1
    assert (
        "No active incidents or planned maintenance are on the board."
        in NodeCollector.text(quiet[0])
    )
    scenarios.append("no-data")

    invalid = load_fixture("summary.operational.json")
    invalid["schema_version"] = "1"
    unavailable = render(chromium, invalid, history, 1784472300000)
    assert_visible(unavailable, "load-error")
    body = next(node for node in unavailable.nodes if node["tag"] == "body")
    assert body["attrs"].get("data-status-render") == "unavailable"
    assert "Status unavailable." in NodeCollector.text(body)
    assert NodeCollector.text(unavailable.by_id("overall-state-label")) == "No data"
    assert len(unavailable.by_class("history-mark")) == 450
    assert all(
        "state-no-data" in mark["classes"]
        for mark in unavailable.by_class("history-mark")
    )
    scenarios.append("invalid-schema")

    mismatched_summary = load_fixture("summary.operational.json")
    mismatched = render(chromium, mismatched_summary, history, 1784472300000)
    assert_visible(mismatched, "load-error")
    mismatched_body = next(node for node in mismatched.nodes if node["tag"] == "body")
    assert mismatched_body["attrs"].get("data-status-render") == "unavailable"
    assert NodeCollector.text(mismatched.by_id("overall-state-label")) == "No data"
    assert "Operational" not in NodeCollector.text(mismatched_body)
    scenarios.append("generation-mismatch")

    failed = render(
        chromium,
        load_fixture("summary.operational.json"),
        history,
        1784472300000,
        fail_history=True,
    )
    assert_visible(failed, "load-error")
    failed_body = next(node for node in failed.nodes if node["tag"] == "body")
    assert failed_body["attrs"].get("data-status-render") == "unavailable"
    scenarios.append("load-failure")

    malformed = render(
        chromium,
        load_fixture("summary.operational.json"),
        history,
        1784472300000,
        malformed_summary=True,
    )
    assert_visible(malformed, "load-error")
    malformed_body = next(node for node in malformed.nodes if node["tag"] == "body")
    assert malformed_body["attrs"].get("data-status-render") == "unavailable"
    scenarios.append("malformed-json")

    archive = load_fixture("incidents.ups-maintenance.json")
    archived_summary = deepcopy(load_fixture("summary.operational.json"))
    archived_summary["generated_at"] = archive["generated_at"]
    archived_history = deepcopy(history)
    archived_history["generated_at"] = archive["generated_at"]
    for component in archived_history["components"]:
        component["days"] = []
    for component in archived_summary["components"]:
        component["history_days_available"] = 0
    archive_clock = int(__import__("datetime").datetime.fromisoformat(archive["generated_at"].replace("Z", "+00:00")).timestamp() * 1000)
    filed = render(chromium, archived_summary, archived_history, archive_clock, incidents=archive)
    cards = filed.by_class("history-card")
    assert len(cards) == 1
    card_text = NodeCollector.text(cards[0])
    for expected in (archive["events"][0]["title"], "Maintenance", "completed", "Watch", "Home base", "Requests", "Audiobooks", "Walshit.com"):
        assert expected.casefold() in card_text.casefold(), expected
    assert len(filed.by_class("history-update")) == 2
    scenarios.append("filed-maintenance")

    empty = render(chromium, archived_summary, archived_history, archive_clock)
    assert "No resolved incidents or completed maintenance." in NodeCollector.text(empty.by_class("history-empty")[0])
    scenarios.append("empty-archive")

    failed_archive = render(chromium, archived_summary, archived_history, archive_clock, fail_incidents=True)
    assert "Incident archive unavailable." in NodeCollector.text(failed_archive.by_class("history-empty")[0])
    body = next(node for node in failed_archive.nodes if node["tag"] == "body")
    assert body["attrs"].get("data-status-render") == "ready"
    scenarios.append("archive-failure-independent")
    assert len(scenarios) == EXPECTED_SCENARIO_COUNT
    return scenarios


if __name__ == "__main__":
    checked = run_checks()
    print(f"render_check: PASS ({len(checked)} scenarios: {', '.join(checked)})")
