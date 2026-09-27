import json
from pathlib import Path
import subprocess
import unittest
from copy import deepcopy

from publisher.schema import validate_incidents


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "incidents.ups-maintenance.json"


def node_result(script):
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class IncidentFrontendTests(unittest.TestCase):
    def test_archive_refresh_runtime_states(self):
        result = subprocess.run(
            ["node", "tests/incident_runtime_harness.js"], cwd=ROOT,
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "loaded": True, "retained": True, "unchanged": True, "newGenerationPending": True, "newerWins": True,
            "malformed": True, "incoherent": True, "stale": True,
            "error": True, "recovered": True,
        })

    def test_fixture_matches_publisher_schema_and_supplied_facts(self):
        document = json.loads(FIXTURE.read_text())
        validate_incidents(document)
        event = document["events"][0]
        self.assertEqual(event["id"], "maint-2026-09-26-ups-battery")
        self.assertEqual(event["title"], "Planned maintenance: UPS battery replacement")
        self.assertEqual(event["started_at"], "2026-09-26T22:40:33Z")
        self.assertEqual(event["resolved_at"], "2026-09-26T23:25:16Z")
        self.assertEqual(event["affected_components"], ["watch", "home", "requests", "audiobooks", "site"])
        self.assertEqual(event["body"], "Walshit services were temporarily unavailable during planned maintenance to replace the UPS battery. Maintenance is complete and services have been restored. This entry was added after the maintenance window.")
        self.assertEqual(event["updates"], [
            {"at": "2026-09-26T22:40:33Z", "status": "in-progress", "body": "Services were taken offline for the planned UPS battery replacement."},
            {"at": "2026-09-26T23:25:16Z", "status": "completed", "body": "The UPS battery replacement maintenance is complete. Services have been restored and recovery checks have passed."},
        ])

    def test_validator_accepts_empty_and_rejects_contract_mutations(self):
        result = node_result('''
          const renderer = require("./web/status.js");
          const base = require("./fixtures/incidents.ups-maintenance.json");
          const accepts = (value) => { try { renderer.validateIncidents(value); return true; } catch (_) { return false; } };
          const cases = [];
          const mutate = (fn) => { const item = structuredClone(base); fn(item); cases.push(accepts(item)); };
          mutate(d => d.events = []);
          mutate(d => d.extra = "x");
          mutate(d => d.events[0].extra = "x");
          mutate(d => d.events[0].id = "Bad ID");
          mutate(d => d.events[0].status = "resolved");
          mutate(d => d.events[0].affected_components = ["private"]);
          mutate(d => d.events[0].resolved_at = "2026-09-26T22:00:00Z");
          mutate(d => d.events[0].updated_at = "2026-09-26T23:24:00Z");
          mutate(d => d.events[0].updates[0].status = "completed");
          mutate(d => d.events[0].updates[1].at = d.events[0].updates[0].at);
          mutate(d => d.events[0].updates[1].body = "See http://example.com/private");
          mutate(d => d.events[0].body = "Password: hidden");
          mutate(d => d.events[0].title = "Host 192.168.1.4");
          mutate(d => { d.events.push(structuredClone(d.events[0])); });
          process.stdout.write(JSON.stringify([accepts(base), ...cases]));
        ''')
        self.assertEqual(result, [True, True] + [False] * 13)

    def test_archive_generation_must_match_summary_and_not_be_future(self):
        result = node_result('''
          const renderer = require("./web/status.js");
          const base = require("./fixtures/incidents.ups-maintenance.json");
          const accepts = (value, summary, now) => { try { renderer.validateIncidents(value, summary, now); return true; } catch (_) { return false; } };
          const old = structuredClone(base); old.generated_at = "2026-09-26T23:25:59Z";
          const future = structuredClone(base); future.generated_at = "2026-09-26T23:27:00Z";
          process.stdout.write(JSON.stringify([
            accepts(base, base.generated_at, Date.parse(base.generated_at)),
            accepts(old, base.generated_at, Date.parse(base.generated_at)),
            accepts(future, base.generated_at, Date.parse(base.generated_at))
          ]));
        ''')
        self.assertEqual(result, [True, False, False])

    def test_archive_rejects_stale_future_and_accepts_equivalent_timestamp_instants(self):
        result = node_result('''
          const renderer = require("./web/status.js");
          const base = require("./fixtures/incidents.ups-maintenance.json");
          const now = Date.parse(base.generated_at);
          const check = (value, summary, clock) => { try { renderer.validateIncidents(value, summary, clock); return true; } catch (_) { return false; } };
          const equivalent = new Date(now).toISOString().replace("Z", "+00:00");
          process.stdout.write(JSON.stringify([
            check(base, equivalent, now), check(base, base.generated_at, now + 180000),
            check(base, base.generated_at, now + 180001), check(base, base.generated_at, now - 1)
          ]));
        ''')
        self.assertEqual(result, [False, True, False, False])

    def test_archive_microsecond_chronology_and_exact_generation(self):
        result = node_result('''
          const r = require("./web/status.js");
          const base = require("./fixtures/incidents.ups-maintenance.json");
          const check = (edit, summary) => { const d = structuredClone(base); edit(d); try { r.validateIncidents(d, summary); return true; } catch (_) { return false; } };
          const at = "2026-09-26T23:25:16";
          const exact = base.generated_at;
          const future = exact.replace("Z", ".000001Z");
          process.stdout.write(JSON.stringify([
            check(d => { d.events[0].updated_at = at + ".000001Z"; }, exact),
            check(d => { d.events[0].started_at = at + "Z"; d.events[0].updates[0].at = at + "Z"; d.events[0].updates[1].at = at + ".000001Z"; d.events[0].updated_at = at + ".000001Z"; d.events[0].resolved_at = at + ".000001Z"; }, exact),
            check(d => { d.events[0].updates[1].at = at + ".0000009Z"; }, exact),
            check(d => {}, exact.replace("Z", ".000001Z")),
            check(d => { d.events[0].updates[1].at = "2026-09-26T19:25:16-04:00"; }, exact),
            (() => { const d = structuredClone(base); d.generated_at = future; try { r.validateIncidents(d, future, Date.parse(exact)); return true; } catch (_) { return false; } })(),
            (() => { const d = structuredClone(base); const later = structuredClone(d.events[0]); later.id += "-later"; later.resolved_at = "2026-09-26T23:25:16.000001Z"; later.updated_at = later.resolved_at; later.updates[1].at = later.resolved_at; d.events.push(later); try { r.validateIncidents(d); return true; } catch (_) { return false; } })()
          ]));
        ''')
        self.assertEqual(result, [False, True, True, False, True, False, False])

    def test_archive_kind_requires_string(self):
        result = node_result('''
          const r = require("./web/status.js"); const base = require("./fixtures/incidents.ups-maintenance.json");
          const d = structuredClone(base); d.events[0].kind = ["maintenance"];
          try { r.validateIncidents(d); process.stdout.write("true"); } catch (_) { process.stdout.write("false"); }
        ''')
        self.assertFalse(result)

    def test_archive_rejects_private_addresses_urls_and_secret_markers(self):
        result = node_result('''
          const renderer = require("./web/status.js");
          const base = require("./fixtures/incidents.ups-maintenance.json");
          const values = ["10.0.0.1", "172.20.1.2", "192.168.1.2", "127.0.0.1", "169.254.1.2", "fc00::1", "fd12::1", "fe80::1", "::1", "//walshit.com/path", "walshit.com/path", "walshit.com?key=x", "walshit.com#part", "walshit.com%2fpath", "secret: value", "Bearer abcdefghijklmnop", "admin.local"];
          const check = (text) => { const d = structuredClone(base); d.events[0].updates[0].body = text; try { renderer.validateIncidents(d); return true; } catch (_) { return false; } };
          process.stdout.write(JSON.stringify([check("Walshit.com status is restored."), ...values.map(check)]));
        ''')
        self.assertEqual(result, [True] + [False] * 17)

    def test_archive_ipv6_range_forms_and_boundaries(self):
        result = node_result('''
          const r = require("./web/status.js"); const base = require("./fixtures/incidents.ups-maintenance.json");
          const check = text => { const d = structuredClone(base); d.events[0].body = text; try { r.validateIncidents(d); return true; } catch (_) { return false; } };
          process.stdout.write(JSON.stringify([
            ...["fc::1", "fd::1", "fc00::1", "fdff::1", "fe80::1", "febf::1", "::1", "0:0:0:0:0:0:0:1", "0000:0000:0000:0000:0000:0000:0000:0001", "[fd12::1]:443", "IP fd12::1.", "fe90:0:0:0:0:0:0:1"].map(check),
            ...["2001:db8::1", "fe7f::1", "fec0::1", "port 443", "fc is a label", "::2"].map(check)
          ]));
        ''')
        self.assertEqual(result, [True, True] + [False] * 10 + [True] * 6)

    def test_archive_request_deadline_covers_response_and_json(self):
        result = node_result('''
          const renderer = require("./web/status.js");
          const checks = [];
          async function run(fetch) {
            let aborted = false;
            const timers = new Set();
            const windowObject = {
              AbortController: class { constructor() { this.signal = {}; } abort() { aborted = true; } },
              setTimeout(fn) { const id = setTimeout(fn, 1); timers.add(id); return id; },
              clearTimeout(id) { clearTimeout(id); timers.delete(id); },
              fetch,
            };
            try { await renderer.fetchJsonWithDeadline(windowObject, "/status/v2/incidents.json"); checks.push("accepted"); }
            catch (_) { checks.push(aborted && timers.size === 0 ? "timed-out" : "bad-timeout"); }
          }
          (async () => {
            await run(() => new Promise(() => {}));
            await run(() => Promise.resolve({ok: true, json: () => new Promise(() => {})}));
            process.stdout.write(JSON.stringify(checks));
          })();
        ''')
        self.assertEqual(result, ["timed-out", "timed-out"])

    def test_archive_ordering_uses_instants_and_active_ids_cannot_overlap(self):
        result = node_result('''
          const renderer = require("./web/status.js");
          const base = require("./fixtures/incidents.ups-maintenance.json");
          const check = (d, summary) => { try { renderer.validateIncidents(d, summary, Date.parse(base.generated_at)); return true; } catch (_) { return false; } };
          const d = structuredClone(base);
          const second = structuredClone(d.events[0]); second.id = "maint-2026-09-26-ups-battery-z";
          second.resolved_at = "2026-09-26T19:25:16-04:00";
          second.updated_at = second.resolved_at;
          second.updates[1].at = second.resolved_at;
          d.events.push(second);
          const ordered = check(d, {generated_at: base.generated_at, active_events: []});
          d.events.reverse();
          const reverse = check(d, {generated_at: base.generated_at, active_events: []});
          const overlap = check(base, {generated_at: base.generated_at, active_events: [{id: base.events[0].id}]});
          process.stdout.write(JSON.stringify([ordered, reverse, overlap]));
        ''')
        self.assertEqual(result, [True, False, False])


if __name__ == "__main__":
    unittest.main()
