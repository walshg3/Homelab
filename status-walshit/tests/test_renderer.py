import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


def run_renderer(expression):
    script = (
        'const renderer = require("./web/status.js");'
        f"const result = ({expression});"
        "Promise.resolve(result).then((value) => process.stdout.write(JSON.stringify(value)))"
        ".catch((error) => { process.stderr.write(String(error)); process.exitCode = 1; });"
    )
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


class RendererPureFunctionTests(unittest.TestCase):
    def test_state_presentations_and_staleness_are_deterministic_with_explicit_clock(self):
        result = run_renderer(
            "({presentations: renderer.STATE_PRESENTATIONS, "
            'fresh: renderer.isStale("2026-07-19T15:00:00Z", Date.parse("2026-07-19T15:03:00Z")), '
            'stale: renderer.isStale("2026-07-19T15:00:00Z", Date.parse("2026-07-19T15:03:00.001Z"))})'
        )
        self.assertEqual(
            result["presentations"],
            {
                "operational": {"label": "Operational", "className": "state-operational"},
                "degraded": {"label": "Degraded", "className": "state-degraded"},
                "outage": {"label": "Outage", "className": "state-outage"},
                "maintenance": {"label": "Maintenance", "className": "state-maintenance"},
                "no-data": {"label": "No data", "className": "state-no-data"},
            },
        )
        self.assertFalse(result["fresh"])
        self.assertTrue(result["stale"])

    def test_runtime_validation_accepts_fixtures_and_rejects_invalid_contract_shapes(self):
        result = run_renderer(
            "(() => {"
            'const summary = require("./fixtures/summary.incident.json");'
            'const history = require("./fixtures/history.sample.json");'
            "const accepts = (fn) => { try { fn(); return true; } catch (_) { return false; } };"
            "const wrongVersion = structuredClone(summary); wrongVersion.schema_version = \"1\";"
            "const oldField = structuredClone(summary);"
            "oldField.components[0].uptime_90d_pct = oldField.components[0].availability_90d_pct;"
            "delete oldField.components[0].availability_90d_pct;"
            "const missingComponent = structuredClone(summary); missingComponent.components.pop();"
            "const unknownHistory = structuredClone(history); unknownHistory.components[0].id = \"other\";"
            "const mismatchedHistory = structuredClone(history); mismatchedHistory.components[0].name = \"Home base\";"
            "const badDay = structuredClone(history); badDay.components[0].days[0].state = \"unknown\";"
            "const unorderedEvent = structuredClone(summary);"
            "const late = unorderedEvent.active_events[0].updates[0];"
            "const early = structuredClone(late); early.at = \"2026-07-19T14:23:00Z\";"
            "unorderedEvent.active_events[0].updates = [late, early];"
            "return ["
            "accepts(() => renderer.validateSummary(summary)),"
            "accepts(() => renderer.validateHistory(history)),"
            "accepts(() => renderer.validateSummary(wrongVersion)),"
            "accepts(() => renderer.validateSummary(oldField)),"
            "accepts(() => renderer.validateSummary(missingComponent)),"
            "accepts(() => renderer.validateHistory(unknownHistory)),"
            "accepts(() => renderer.validateHistory(mismatchedHistory)),"
            "accepts(() => renderer.validateHistory(badDay)),"
            "accepts(() => renderer.validateSummary(unorderedEvent))"
            "]; })()"
        )
        self.assertEqual(result, [True, True, False, False, False, False, False, False, False])

    def test_view_model_rejects_individually_valid_documents_from_unequal_generations(self):
        result = run_renderer(
            "(() => {"
            'const summary = require("./fixtures/summary.operational.json");'
            'const history = require("./fixtures/history.sample.json");'
            "const accepts = (fn) => { try { fn(); return true; } catch (_) { return false; } };"
            "return {"
            "summary: accepts(() => renderer.validateSummary(summary)),"
            "history: accepts(() => renderer.validateHistory(history)),"
            "pair: accepts(() => renderer.buildViewModel(summary, history, Date.parse(history.generated_at)))"
            "};"
            "})()"
        )
        self.assertEqual(result, {"summary": True, "history": True, "pair": False})

    def test_bootstrap_renders_unavailable_for_unequal_generations(self):
        result = run_renderer(
            """(async () => {
            class FakeClassList {
              constructor() { this.values = new Set(); }
              add(...names) { names.forEach((name) => this.values.add(name)); }
              remove(...names) { names.forEach((name) => this.values.delete(name)); }
            }
            class FakeNode {
              constructor(text = "") {
                this.textContent = text;
                this.hidden = true;
                this.children = [];
                this.firstChild = null;
                this.className = "";
                this.classList = new FakeClassList();
                this.attributes = {};
                this.queries = {};
              }
              appendChild(child) {
                this.children.push(child);
                this.firstChild = this.children[0] || null;
                return child;
              }
              removeChild(child) {
                this.children.splice(this.children.indexOf(child), 1);
                this.firstChild = this.children[0] || null;
              }
              setAttribute(name, value) { this.attributes[name] = String(value); }
              querySelector(selector) { return this.queries[selector]; }
            }
            const byId = Object.fromEntries([
              "load-error", "stale-warning", "overall-state-label", "overall-title",
              "generated-at", "active-board",
            ].map((id) => [id, new FakeNode()]));
            const document = {
              readyState: "complete",
              body: new FakeNode(),
              createElement: () => new FakeNode(),
              getElementById: (id) => byId[id],
              queries: {
                ".overall": new FakeNode(),
                ".overall-copy": new FakeNode(),
                ".active-section .section-head > p": new FakeNode(),
              },
              querySelector(selector) { return this.queries[selector]; },
            };
            renderer.COMPONENT_REGISTRY.forEach(({id, name}) => {
              const row = new FakeNode();
              row.queries = {
                "h3": new FakeNode(name),
                ".component-name p": new FakeNode(`${name} description`),
                ".history-placeholder": new FakeNode(),
                ".current-state": new FakeNode(),
              };
              document.queries[`[data-component="${id}"]`] = row;
            });
            const summary = require("./fixtures/summary.operational.json");
            const history = require("./fixtures/history.sample.json");
            const responses = {"/status/v2/summary.json": summary, "/status/v2/history.json": history};
            const windowObject = {
              document,
              AbortController,
              __STATUS_NOW_MS__: Date.parse(history.generated_at),
              fetch: async (path) => ({ok: true, json: async () => structuredClone(responses[path])}),
              setTimeout: () => 1,
              clearTimeout: () => {},
              setInterval: () => 1,
            };
            renderer.bootstrap(windowObject);
            await new Promise((resolve) => setImmediate(resolve));
            return {
              render: document.body.attributes["data-status-render"],
              errorVisible: byId["load-error"].hidden === false,
              label: byId["overall-state-label"].textContent,
              title: byId["overall-title"].textContent,
            };
            })()"""
        )
        self.assertEqual(
            result,
            {
                "render": "unavailable",
                "errorVisible": True,
                "label": "No data",
                "title": "Status unavailable.",
            },
        )

    def test_no_data_summary_builds_unverified_view_model(self):
        result = run_renderer(
            "(() => {"
            'const summary = structuredClone(require("./fixtures/summary.operational.json"));'
            'const history = structuredClone(require("./fixtures/history.sample.json"));'
            'history.generated_at = summary.generated_at;'
            'summary.overall.state = "no-data";'
            'summary.components[0].state = "no-data";'
            "const model = renderer.buildViewModel(summary, history, Date.parse(summary.generated_at));"
            "return {overall: model.overall, component: model.components[0]};"
            "})()"
        )
        self.assertEqual(result["overall"]["state"], "no-data")
        self.assertEqual(
            result["overall"]["presentation"],
            {"label": "No data", "className": "state-no-data"},
        )
        self.assertEqual(result["component"]["state"], "no-data")
        self.assertEqual(
            result["component"]["presentation"],
            {"label": "No data", "className": "state-no-data"},
        )

    def test_runtime_timestamp_validation_rejects_calendar_and_clock_overflow(self):
        result = run_renderer(
            "(() => {"
            'const fixture = require("./fixtures/summary.operational.json");'
            "const accepts = (timestamp) => {"
            "const summary = structuredClone(fixture); summary.generated_at = timestamp;"
            "try { renderer.validateSummary(summary); return true; } catch (_) { return false; }"
            "};"
            'return [accepts("2026-02-30T12:00:00Z"), accepts("2026-07-19T25:00:00Z")];'
            "})()"
        )
        self.assertEqual(result, [False, False])

    def test_runtime_validation_rejects_year_zero_timestamp_and_history_date(self):
        result = run_renderer(
            "(() => {"
            'const summary = structuredClone(require("./fixtures/summary.operational.json"));'
            'const history = structuredClone(require("./fixtures/history.sample.json"));'
            'summary.generated_at = "0000-07-19T14:42:00Z";'
            'history.components[0].days[0].date = "0000-07-15";'
            "const accepts = (fn) => { try { fn(); return true; } catch (_) { return false; } };"
            "return ["
            "accepts(() => renderer.validateSummary(summary)),"
            "accepts(() => renderer.validateHistory(history))"
            "];"
            "})()"
        )
        self.assertEqual(result, [False, False])

    def test_view_model_orders_registry_and_right_aligns_ninety_honest_history_marks(self):
        result = run_renderer(
            "(() => {"
            'const summary = require("./fixtures/summary.incident.json");'
            'const history = require("./fixtures/history.sample.json");'
            'const model = renderer.buildViewModel(summary, history, Date.parse("2026-07-19T15:03:00Z"));'
            "return {"
            "ids: model.components.map((component) => component.id),"
            "lengths: model.components.map((component) => component.marks.length),"
            "watchStates: model.components[0].marks.map((mark) => mark.state),"
            "watchMeta: model.components[0].availabilityLabel,"
            "watchSummary: model.components[0].historySummary,"
            "overall: model.overall.presentation, stale: model.stale"
            "}; })()"
        )
        self.assertEqual(result["ids"], ["watch", "home", "requests", "audiobooks", "site"])
        self.assertEqual(result["lengths"], [90, 90, 90, 90, 90])
        self.assertEqual(result["watchStates"][:85], ["no-data"] * 85)
        self.assertEqual(
            result["watchStates"][-5:],
            ["no-data", "no-data", "operational", "operational", "outage"],
        )
        self.assertEqual(
            result["watchMeta"],
            "99.875% measured availability · 3d 4h measured · 3 days on record",
        )
        self.assertEqual(
            result["watchSummary"],
            "90-day availability strip for Watch: 3 days with recorded states; newest recorded state Outage.",
        )
        self.assertEqual(result["overall"], {"label": "Outage", "className": "state-outage"})
        self.assertEqual(result["stale"], {"summary": False, "history": False, "any": False})

    def test_view_model_aligns_internal_gaps_and_rejects_dates_outside_generated_window(self):
        result = run_renderer(
            "(() => {"
            'const summary = structuredClone(require("./fixtures/summary.incident.json"));'
            'const history = structuredClone(require("./fixtures/history.sample.json"));'
            "history.components[0].days.splice(3, 1);"
            "summary.components[0].history_days_available = 2;"
            'const model = renderer.buildViewModel(summary, history, Date.parse(history.generated_at));'
            "const acceptsDate = (date) => {"
            'const candidateSummary = require("./fixtures/summary.incident.json");'
            'const candidate = structuredClone(require("./fixtures/history.sample.json"));'
            "candidate.components[0].days[0].date = date;"
            "candidate.components[0].days.sort((left, right) => left.date.localeCompare(right.date));"
            "try { renderer.buildViewModel(candidateSummary, candidate, Date.parse(candidate.generated_at)); return true; }"
            "catch (_) { return false; }"
            "};"
            "return {"
            "tail: model.components[0].marks.slice(-3),"
            'tooOld: acceptsDate("2026-04-20"),'
            'afterGeneration: acceptsDate("2026-07-20")'
            "};"
            "})()"
        )
        self.assertEqual(
            result["tail"],
            [
                {"date": "2026-07-17", "state": "operational"},
                {"date": "2026-07-18", "state": "no-data"},
                {"date": "2026-07-19", "state": "outage"},
            ],
        )
        self.assertFalse(result["tooOld"])
        self.assertFalse(result["afterGeneration"])

    def test_fixture_view_models_preserve_distinct_event_kinds_and_ordered_updates(self):
        result = run_renderer(
            "(() => {"
            'const history = require("./fixtures/history.sample.json");'
            'const names = ["operational", "incident", "maintenance"];'
            "return names.map((name) => {"
            "const summary = require(`./fixtures/summary.${name}.json`);"
            "const matchingHistory = structuredClone(history);"
            "matchingHistory.generated_at = summary.generated_at;"
            "const model = renderer.buildViewModel(summary, matchingHistory, Date.parse(summary.generated_at));"
            "return {name, state: model.overall.state, events: model.activeEvents.map((event) => ({"
            "kind: event.kind, status: event.status, title: event.title,"
            "updates: event.updates.map((update) => update.body)"
            "}))};"
            "}); })()"
        )
        self.assertEqual(result[0], {"name": "operational", "state": "operational", "events": []})
        self.assertEqual(result[1]["events"][0]["kind"], "incident")
        self.assertEqual(result[1]["events"][0]["status"], "investigating")
        self.assertEqual(result[2]["events"][0]["kind"], "maintenance")
        self.assertEqual(result[2]["events"][0]["status"], "in-progress")
        self.assertEqual(
            result[1]["events"][0]["updates"],
            ["Public playback checks are not completing consistently."],
        )

    def test_unmeasured_availability_copy_and_matching_generation_staleness(self):
        result = run_renderer(
            "(() => {"
            'const summary = structuredClone(require("./fixtures/summary.operational.json"));'
            'const history = structuredClone(require("./fixtures/history.sample.json"));'
            'history.generated_at = summary.generated_at;'
            "summary.components[0].availability_90d_pct = null;"
            "summary.components[0].availability_seconds_measured = 0;"
            'const model = renderer.buildViewModel(summary, history, Date.parse("2026-07-19T14:45:00.001Z"));'
            "return {label: model.components[0].availabilityLabel, stale: model.stale};"
            "})()"
        )
        self.assertEqual(
            result["label"],
            "3 days on record · measured availability unavailable",
        )
        self.assertEqual(result["stale"], {"summary": True, "history": True, "any": True})

    def test_measured_duration_format_is_deterministic_and_bounded(self):
        result = run_renderer(
            "(() => {"
            'const summary = structuredClone(require("./fixtures/summary.operational.json"));'
            'const history = structuredClone(require("./fixtures/history.sample.json"));'
            'history.generated_at = summary.generated_at;'
            "const label = (seconds) => {"
            "summary.components[0].availability_seconds_measured = seconds;"
            "return renderer.buildViewModel(summary, history, Date.parse(summary.generated_at))"
            ".components[0].availabilityLabel;"
            "};"
            "return [label(60), label(273600), label(7776000)];"
            "})()"
        )
        self.assertEqual(
            result,
            [
                "100% measured availability · 1m measured · 3 days on record",
                "100% measured availability · 3d 4h measured · 3 days on record",
                "100% measured availability · 90d measured · 3 days on record",
            ],
        )

    def test_view_model_fingerprint_deduplicates_equivalent_models_but_not_stale_transitions(self):
        result = run_renderer(
            "(() => {"
            'const summary = require("./fixtures/summary.operational.json");'
            'const history = structuredClone(require("./fixtures/history.sample.json"));'
            'history.generated_at = summary.generated_at;'
            'const fresh = renderer.buildViewModel(summary, history, Date.parse("2026-07-19T14:45:00Z"));'
            "const reorder = (value) => Array.isArray(value) ? value.map(reorder) : "
            "value && typeof value === \"object\" ? "
            "Object.fromEntries(Object.entries(value).reverse().map(([key, item]) => [key, reorder(item)])) : value;"
            "const equivalent = reorder(fresh);"
            'const stale = renderer.buildViewModel(summary, history, Date.parse("2026-07-19T14:45:00.001Z"));'
            "return {"
            "same: renderer.fingerprintViewModel(fresh) === renderer.fingerprintViewModel(equivalent),"
            "staleChanged: renderer.fingerprintViewModel(fresh) !== renderer.fingerprintViewModel(stale)"
            "};"
            "})()"
        )
        self.assertEqual(result, {"same": True, "staleChanged": True})


if __name__ == "__main__":
    unittest.main()
