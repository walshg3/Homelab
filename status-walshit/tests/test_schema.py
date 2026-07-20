import json
from copy import deepcopy
from pathlib import Path
import unittest

from publisher.schema import (
    COMPONENT_DESCRIPTIONS,
    SchemaError,
    validate_history,
    validate_incidents,
    validate_summary,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


class SummaryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.operational = json.loads(
            (FIXTURES / "summary.operational.json").read_text(encoding="utf-8")
        )

    def test_operational_fixture_validates(self):
        validate_summary(deepcopy(self.operational))

    def test_component_descriptions_are_canonical(self):
        self.assertEqual(
            COMPONENT_DESCRIPTIONS,
            {
                "watch": "Plex playback and sign-in",
                "home": "Dashboard and links",
                "requests": "Film and series requests",
                "audiobooks": "Listening and library access",
                "site": "Updates and guides",
            },
        )
        for component in self.operational["components"]:
            self.assertEqual(
                component["description"],
                COMPONENT_DESCRIPTIONS[component["id"]],
            )

        changed = deepcopy(self.operational)
        changed["components"][0]["description"] = "A drifting description"
        with self.assertRaises(SchemaError):
            validate_summary(changed)

    def test_summary_accepts_no_data_states(self):
        document = deepcopy(self.operational)
        document["overall"]["state"] = "no-data"
        document["components"][0]["state"] = "no-data"

        validate_summary(document)

    def test_summary_rejects_unknown_missing_and_incorrect_types(self):
        mutations = []

        unknown_root = deepcopy(self.operational)
        unknown_root["private_note"] = "not public"
        mutations.append(unknown_root)

        missing_root = deepcopy(self.operational)
        del missing_root["generated_at"]
        mutations.append(missing_root)

        wrong_root_type = deepcopy(self.operational)
        wrong_root_type["components"] = {}
        mutations.append(wrong_root_type)

        unknown_nested = deepcopy(self.operational)
        unknown_nested["overall"]["detail"] = "extra"
        mutations.append(unknown_nested)

        missing_nested = deepcopy(self.operational)
        del missing_nested["components"][0]["description"]
        mutations.append(missing_nested)

        wrong_nested_type = deepcopy(self.operational)
        wrong_nested_type["components"][0]["history_days_available"] = True
        mutations.append(wrong_nested_type)

        for document in mutations:
            with self.subTest(document=document):
                with self.assertRaises(SchemaError):
                    validate_summary(document)

    @staticmethod
    def valid_incident_event():
        return {
            "id": "inc-2026-07-19-watch",
            "kind": "incident",
            "status": "investigating",
            "title": "Playback interruptions under investigation",
            "body": "Some viewers may be unable to begin or resume playback.",
            "started_at": "2026-07-19T14:18:00Z",
            "updated_at": "2026-07-19T14:24:00Z",
            "affected_components": ["watch"],
            "updates": [
                {
                    "at": "2026-07-19T14:24:00Z",
                    "status": "investigating",
                    "body": "Playback checks are not completing consistently.",
                }
            ],
        }

    def test_summary_accepts_strict_active_event(self):
        document = deepcopy(self.operational)
        document["active_events"] = [self.valid_incident_event()]

        validate_summary(document)

    def test_summary_rejects_malformed_active_events(self):
        base = deepcopy(self.operational)
        base["active_events"] = [self.valid_incident_event()]
        mutations = []

        unknown_event_key = deepcopy(base)
        unknown_event_key["active_events"][0]["private_note"] = "extra"
        mutations.append(unknown_event_key)

        missing_event_key = deepcopy(base)
        del missing_event_key["active_events"][0]["title"]
        mutations.append(missing_event_key)

        invalid_id = deepcopy(base)
        invalid_id["active_events"][0]["id"] = "Incident 1"
        mutations.append(invalid_id)

        invalid_kind = deepcopy(base)
        invalid_kind["active_events"][0]["kind"] = "notice"
        mutations.append(invalid_kind)

        invalid_incident_status = deepcopy(base)
        invalid_incident_status["active_events"][0]["status"] = "in-progress"
        mutations.append(invalid_incident_status)

        invalid_timestamp = deepcopy(base)
        invalid_timestamp["active_events"][0]["started_at"] = "today"
        mutations.append(invalid_timestamp)

        reversed_timestamps = deepcopy(base)
        reversed_timestamps["active_events"][0]["updated_at"] = (
            "2026-07-19T14:17:00Z"
        )
        mutations.append(reversed_timestamps)

        duplicate_component = deepcopy(base)
        duplicate_component["active_events"][0]["affected_components"] = [
            "watch",
            "watch",
        ]
        mutations.append(duplicate_component)

        unknown_component = deepcopy(base)
        unknown_component["active_events"][0]["affected_components"] = ["server"]
        mutations.append(unknown_component)

        unknown_update_key = deepcopy(base)
        unknown_update_key["active_events"][0]["updates"][0]["detail"] = "extra"
        mutations.append(unknown_update_key)

        invalid_update_status = deepcopy(base)
        invalid_update_status["active_events"][0]["updates"][0]["status"] = "planned"
        mutations.append(invalid_update_status)

        mismatched_latest_status = deepcopy(base)
        mismatched_latest_status["active_events"][0]["status"] = "monitoring"
        mutations.append(mismatched_latest_status)

        generated_before_update = deepcopy(base)
        generated_before_update["generated_at"] = "2026-07-19T14:23:59Z"
        mutations.append(generated_before_update)

        duplicate_id = deepcopy(base)
        duplicate_id["active_events"].append(
            deepcopy(duplicate_id["active_events"][0])
        )
        mutations.append(duplicate_id)

        for document in mutations:
            with self.subTest(document=document):
                with self.assertRaises(SchemaError):
                    validate_summary(document)


class HistoryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.operational = json.loads(
            (FIXTURES / "summary.operational.json").read_text(encoding="utf-8")
        )

    @staticmethod
    def valid_history():
        names = {
            "watch": "Watch",
            "home": "Home base",
            "requests": "Requests",
            "audiobooks": "Audiobooks",
            "site": "Walshit.com",
        }
        return {
            "schema_version": "1",
            "generated_at": "2026-07-19T14:42:00Z",
            "components": [
                {
                    "id": component_id,
                    "name": name,
                    "days": [
                        {"date": "2026-07-17", "state": "operational"},
                        {"date": "2026-07-18", "state": "no-data"},
                        {"date": "2026-07-19", "state": "degraded"},
                    ],
                }
                for component_id, name in names.items()
            ],
        }

    def test_history_document_validates(self):
        validate_history(self.valid_history())

    def test_history_rejects_malformed_data_and_invalid_states(self):
        valid = self.valid_history()
        mutations = []

        unknown_root = deepcopy(valid)
        unknown_root["range"] = 90
        mutations.append(unknown_root)

        invalid_timestamp = deepcopy(valid)
        invalid_timestamp["generated_at"] = "2026-07-19"
        mutations.append(invalid_timestamp)

        unknown_component_key = deepcopy(valid)
        unknown_component_key["components"][0]["note"] = "extra"
        mutations.append(unknown_component_key)

        wrong_days_type = deepcopy(valid)
        wrong_days_type["components"][0]["days"] = {}
        mutations.append(wrong_days_type)

        unknown_day_key = deepcopy(valid)
        unknown_day_key["components"][0]["days"][0]["detail"] = "extra"
        mutations.append(unknown_day_key)

        invalid_date = deepcopy(valid)
        invalid_date["components"][0]["days"][0]["date"] = "2026-02-30"
        mutations.append(invalid_date)

        invalid_state = deepcopy(valid)
        invalid_state["components"][0]["days"][0]["state"] = "unknown"
        mutations.append(invalid_state)

        duplicate_date = deepcopy(valid)
        duplicate_date["components"][0]["days"][1]["date"] = "2026-07-17"
        mutations.append(duplicate_date)

        unordered_dates = deepcopy(valid)
        unordered_dates["components"][0]["days"].reverse()
        mutations.append(unordered_dates)

        too_many_days = deepcopy(valid)
        too_many_days["components"][0]["days"] = [
            {"date": f"2026-04-{day:02d}", "state": "no-data"}
            for day in range(1, 92)
        ]
        mutations.append(too_many_days)

        missing_component = deepcopy(valid)
        missing_component["components"].pop()
        mutations.append(missing_component)

        duplicate_component = deepcopy(valid)
        duplicate_component["components"][-1] = deepcopy(
            duplicate_component["components"][0]
        )
        mutations.append(duplicate_component)

        wrong_name = deepcopy(valid)
        wrong_name["components"][0]["name"] = "Streaming"
        mutations.append(wrong_name)

        for document in mutations:
            with self.subTest(document=document):
                with self.assertRaises(SchemaError):
                    validate_history(document)


class IncidentsContractTests(unittest.TestCase):
    @staticmethod
    def valid_document():
        return {
            "schema_version": "1",
            "generated_at": "2026-07-20T13:00:00Z",
            "events": [
                {
                    "id": "inc-2026-07-20-example",
                    "kind": "incident",
                    "status": "resolved",
                    "title": "Public service interruption",
                    "body": "Some visitors were unable to use this public component.",
                    "started_at": "2026-07-20T12:00:00Z",
                    "updated_at": "2026-07-20T12:30:00Z",
                    "resolved_at": "2026-07-20T12:30:00Z",
                    "affected_components": ["watch"],
                    "updates": [
                        {
                            "at": "2026-07-20T12:05:00Z",
                            "status": "investigating",
                            "body": "Public checks were not completing consistently.",
                        },
                        {
                            "at": "2026-07-20T12:30:00Z",
                            "status": "resolved",
                            "body": "Public checks are completing consistently again.",
                        },
                    ],
                }
            ],
        }

    def test_closed_incidents_document_validates(self):
        validate_incidents(self.valid_document())

    def test_completed_maintenance_document_validates(self):
        document = self.valid_document()
        event = document["events"][0]
        event["id"] = "maint-2026-07-20-example"
        event["kind"] = "maintenance"
        event["status"] = "completed"
        event["updates"] = [
            {
                "at": "2026-07-20T12:05:00Z",
                "status": "planned",
                "body": "Public maintenance was scheduled.",
            },
            {
                "at": "2026-07-20T12:15:00Z",
                "status": "in-progress",
                "body": "Public maintenance was in progress.",
            },
            {
                "at": "2026-07-20T12:30:00Z",
                "status": "completed",
                "body": "Public maintenance completed.",
            },
        ]

        validate_incidents(document)

    def test_incidents_rejects_malformed_lifecycle_and_ordering(self):
        valid = self.valid_document()
        mutations = []

        unknown_root = deepcopy(valid)
        unknown_root["private_note"] = "extra"
        mutations.append(unknown_root)

        missing_root = deepcopy(valid)
        del missing_root["generated_at"]
        mutations.append(missing_root)

        bad_version = deepcopy(valid)
        bad_version["schema_version"] = "2"
        mutations.append(bad_version)

        bad_generated = deepcopy(valid)
        bad_generated["generated_at"] = "2026-07-20"
        mutations.append(bad_generated)

        generated_before_resolution = deepcopy(valid)
        generated_before_resolution["generated_at"] = "2026-07-20T12:29:59Z"
        mutations.append(generated_before_resolution)

        unknown_event = deepcopy(valid)
        unknown_event["events"][0]["severity"] = "high"
        mutations.append(unknown_event)

        missing_event = deepcopy(valid)
        del missing_event["events"][0]["title"]
        mutations.append(missing_event)

        bad_kind = deepcopy(valid)
        bad_kind["events"][0]["kind"] = "notice"
        mutations.append(bad_kind)

        bad_closed_status = deepcopy(valid)
        bad_closed_status["events"][0]["status"] = "monitoring"
        mutations.append(bad_closed_status)

        mismatched_updated = deepcopy(valid)
        mismatched_updated["events"][0]["updated_at"] = "2026-07-20T12:29:00Z"
        mutations.append(mismatched_updated)

        resolved_before_start = deepcopy(valid)
        resolved_before_start["events"][0]["resolved_at"] = "2026-07-20T11:59:00Z"
        resolved_before_start["events"][0]["updated_at"] = "2026-07-20T11:59:00Z"
        mutations.append(resolved_before_start)

        duplicate_component = deepcopy(valid)
        duplicate_component["events"][0]["affected_components"] = ["watch", "watch"]
        mutations.append(duplicate_component)

        unknown_component = deepcopy(valid)
        unknown_component["events"][0]["affected_components"] = ["database"]
        mutations.append(unknown_component)

        unknown_update = deepcopy(valid)
        unknown_update["events"][0]["updates"][0]["phase"] = "investigating"
        mutations.append(unknown_update)

        closed_before_final = deepcopy(valid)
        closed_before_final["events"][0]["updates"][0]["status"] = "resolved"
        mutations.append(closed_before_final)

        duplicate_update_time = deepcopy(valid)
        duplicate_update_time["events"][0]["updates"][0]["at"] = (
            "2026-07-20T12:30:00Z"
        )
        mutations.append(duplicate_update_time)

        wrong_latest_time = deepcopy(valid)
        wrong_latest_time["events"][0]["updates"][-1]["at"] = (
            "2026-07-20T12:29:00Z"
        )
        mutations.append(wrong_latest_time)

        duplicate_id = deepcopy(valid)
        duplicate_id["events"].append(deepcopy(duplicate_id["events"][0]))
        mutations.append(duplicate_id)

        newest_not_first = deepcopy(valid)
        older = newest_not_first["events"][0]
        newer = deepcopy(older)
        newer["id"] = "inc-2026-07-20-newer"
        newer["updated_at"] = "2026-07-20T12:40:00Z"
        newer["resolved_at"] = "2026-07-20T12:40:00Z"
        newer["updates"][-1]["at"] = "2026-07-20T12:40:00Z"
        newest_not_first["events"].append(newer)
        mutations.append(newest_not_first)

        wrong_id_tie_break = deepcopy(valid)
        first = wrong_id_tie_break["events"][0]
        first["id"] = "inc-2026-07-20-z"
        second = deepcopy(first)
        second["id"] = "inc-2026-07-20-a"
        wrong_id_tie_break["events"].append(second)
        mutations.append(wrong_id_tie_break)

        for document in mutations:
            with self.subTest(document=document):
                with self.assertRaises(SchemaError):
                    validate_incidents(document)


class FixtureSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.operational = json.loads(
            (FIXTURES / "summary.operational.json").read_text(encoding="utf-8")
        )

    def test_incident_maintenance_and_history_fixtures_validate(self):
        for filename in ("summary.incident.json", "summary.maintenance.json"):
            with self.subTest(filename=filename):
                document = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
                validate_summary(document)

        history = json.loads(
            (FIXTURES / "history.sample.json").read_text(encoding="utf-8")
        )
        validate_history(history)

    def test_all_fixtures_free_of_private_patterns(self):
        from tests.leakcheck import assert_public_safe

        fixture_paths = sorted(FIXTURES.glob("*.json"))
        self.assertEqual(
            {path.name for path in fixture_paths},
            {
                "history.sample.json",
                "summary.incident.json",
                "summary.maintenance.json",
                "summary.operational.json",
            },
        )
        for path in fixture_paths:
            with self.subTest(path=path.name):
                assert_public_safe(path.read_text(encoding="utf-8"), source=path.name)

    def test_leak_guard_covers_required_private_categories(self):
        from tests.leakcheck import LeakError, assert_public_safe

        unsafe_samples = {
            "secret": "api_key = abcdef123456",
            "private address": "192.168.50.12",
            "private hostname": "media-box.local",
            "filesystem path": "/srv/private/status.json",
            "credential": "https://operator:hunter2@example.test",
            "token": "token: abcdefghijklmnop",
            "media title": "media_title: Private Film",
            "current media": "now_playing: Private Film",
            "identity": "operator_name: A Person",
            "contact identity": "contact_person: A Person",
            "recovery detail": "restore_command: switch-primary",
            "recovery procedure": "recovery_procedure: switch-primary",
            "IPv4 loopback": "127.2.3.4",
            "IPv4 link-local": "169.254.44.8",
            "IPv6 loopback": "::1",
            "IPv6 unique-local": "fc00::1234",
            "IPv6 link-local full range": "fe90::1",
            "usr local path": "/usr/local/private/status.json",
            "temporary path": "/tmp/private-status.json",
            "Windows raw TOML path": r'C:\Users\Public\status.json',
            "Windows escaped TOML path": r'C:\\Users\\Public\\status.json',
            "UNC path": r'\\private-host\share\status.json',
            "client secret": "client_secret = syntheticvalue",
            "authorization": "authorization: Basic c3ludGhldGlj",
            "bearer authorization": "authorization: Bearer syntheticvalue",
            "access token": "access_token = syntheticvalue",
            "refresh token": "refresh-token: syntheticvalue",
            "passphrase": "passphrase = syntheticvalue",
            "credential marker": "credential: syntheticvalue",
            "natural identity": "operator Jane Example",
            "natural recovery command": "recovery command is synthetic-switch",
            "natural restore steps": "restore steps are documented here",
            "natural failover procedure": "failover procedure uses synthetic-switch",
            "natural backup path": "backup path is synthetic-path",
            "GitHub provider token": "ghp_AAAAAAAAAAAAAAAAAAAA",
            "Slack provider token": "xoxb-AAAAAAAAAA",
        }
        for category, text in unsafe_samples.items():
            with self.subTest(category=category):
                with self.assertRaises(LeakError):
                    assert_public_safe(text, source=category)

        for text in (
            "This is operator-authored public prose.",
            "The operator is investigating a public interruption.",
            "Visitors may retry the public component.",
        ):
            with self.subTest(safe=text):
                assert_public_safe(text, source="ordinary prose")

    def test_history_fixture_is_honest_about_measured_days(self):
        history = json.loads(
            (FIXTURES / "history.sample.json").read_text(encoding="utf-8")
        )
        measured_by_id = {}
        for component in history["components"]:
            states = [day["state"] for day in component["days"]]
            self.assertLess(len(states), 90)
            self.assertIn("no-data", states)
            measured_by_id[component["id"]] = sum(
                state != "no-data" for state in states
            )

        for filename in (
            "summary.operational.json",
            "summary.incident.json",
            "summary.maintenance.json",
        ):
            summary = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
            with self.subTest(filename=filename):
                self.assertEqual(
                    {
                        component["id"]: component["history_days_available"]
                        for component in summary["components"]
                    },
                    measured_by_id,
                )

    def test_summary_requires_exact_component_registry(self):
        mutations = []

        duplicate = deepcopy(self.operational)
        duplicate["components"][-1] = deepcopy(duplicate["components"][0])
        mutations.append(duplicate)

        missing = deepcopy(self.operational)
        missing["components"].pop()
        mutations.append(missing)

        unknown_id = deepcopy(self.operational)
        unknown_id["components"][0]["id"] = "stream"
        mutations.append(unknown_id)

        wrong_name = deepcopy(self.operational)
        wrong_name["components"][0]["name"] = "Streaming"
        mutations.append(wrong_name)

        for document in mutations:
            with self.subTest(document=document):
                with self.assertRaises(SchemaError):
                    validate_summary(document)

    def test_summary_rejects_invalid_states_timestamps_and_percentages(self):
        mutations = []

        invalid_timestamp = deepcopy(self.operational)
        invalid_timestamp["generated_at"] = "19 July 2026"
        mutations.append(invalid_timestamp)

        invalid_overall_state = deepcopy(self.operational)
        invalid_overall_state["overall"]["state"] = "fine"
        mutations.append(invalid_overall_state)

        invalid_component_state = deepcopy(self.operational)
        invalid_component_state["components"][0]["state"] = "offline"
        mutations.append(invalid_component_state)

        percentage_too_high = deepcopy(self.operational)
        percentage_too_high["components"][0]["uptime_90d_pct"] = 100.01
        mutations.append(percentage_too_high)

        negative_percentage = deepcopy(self.operational)
        negative_percentage["components"][0]["uptime_90d_pct"] = -0.01
        mutations.append(negative_percentage)

        too_many_days = deepcopy(self.operational)
        too_many_days["components"][0]["history_days_available"] = 91
        mutations.append(too_many_days)

        for document in mutations:
            with self.subTest(document=document):
                with self.assertRaises(SchemaError):
                    validate_summary(document)


if __name__ == "__main__":
    unittest.main()
