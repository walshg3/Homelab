import ast
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from itertools import product
import json
import os
from pathlib import Path
import stat
import subprocess
from tempfile import TemporaryDirectory
import threading
import unittest

from publisher.content import (
    ContentEvent,
    ContentIssue,
    ContentLoadResult,
    ContentUpdate,
)
from publisher.probes import ProbeResult, validate_complete_cycle
from publisher.publisher import (
    PublishCycle,
    build_publish_cycle,
    empty_history_document,
    initial_publisher_state,
    restore_publisher_state,
    write_public_documents,
)
from publisher.schema import (
    COMPONENTS,
    COMPONENT_DESCRIPTIONS,
    validate_history,
    validate_incidents,
    validate_summary,
)
from tests.leakcheck import assert_public_safe


ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def at(day=20, hour=12, minute=0):
    return datetime(2026, 7, day, hour, minute, tzinfo=UTC)


def probes(default="success", **overrides):
    return [
        ProbeResult(component_id, overrides.get(component_id, default))
        for component_id in COMPONENTS
    ]


def no_content(*, active=(), closed=(), issues=()):
    return ContentLoadResult(tuple(active), tuple(closed), tuple(issues))


def event(
    event_id,
    *,
    kind="incident",
    status="investigating",
    affected=("watch",),
    started="2026-07-20T10:00:00Z",
    updated="2026-07-20T10:05:00Z",
    resolved=None,
    title="Public service notice",
    body="Availability information is being shared here.",
    update_body="A public update is available.",
):
    return ContentEvent(
        id=event_id,
        kind=kind,
        status=status,
        title=title,
        body=body,
        started_at=started,
        updated_at=updated,
        affected_components=tuple(affected),
        updates=(
            ContentUpdate(
                at=updated,
                status=status,
                body=update_body,
            ),
        ),
        resolved_at=resolved,
    )


def run_cycle(state, history, generated_at, results=None, content=None):
    return build_publish_cycle(
        state,
        history,
        no_content() if content is None else content,
        probes() if results is None else results,
        generated_at,
    )


class FakeProbeContractTests(unittest.TestCase):
    def test_probe_result_is_immutable_and_accepts_only_exact_contract_values(self):
        result = ProbeResult(component_id="watch", outcome="success")
        self.assertEqual((result.component_id, result.outcome), ("watch", "success"))
        with self.assertRaises(FrozenInstanceError):
            result.outcome = "failure"

        for component_id in (True, 1, None, "unknown", "Watch"):
            with self.subTest(component_id=component_id):
                with self.assertRaises((TypeError, ValueError)):
                    ProbeResult(component_id=component_id, outcome="success")
        for outcome in (True, 1, None, "unknown", "Success"):
            with self.subTest(outcome=outcome):
                with self.assertRaises((TypeError, ValueError)):
                    ProbeResult(component_id="watch", outcome=outcome)

    def test_complete_cycle_is_validated_and_canonicalized(self):
        reversed_results = [
            ProbeResult(component_id, "success")
            for component_id in reversed(tuple(COMPONENTS))
        ]
        validated = validate_complete_cycle(reversed_results)
        self.assertIs(type(validated), tuple)
        self.assertEqual(
            [result.component_id for result in validated],
            list(COMPONENTS),
        )
        self.assertEqual(validate_complete_cycle(tuple(reversed_results)), validated)

        malformed = (
            reversed_results[:-1],
            reversed_results + [ProbeResult("watch", "success")],
            [*reversed_results[:-1], ProbeResult("site", "success")],
            {result.component_id: result for result in reversed_results},
            [*reversed_results[:-1], {"component_id": "watch", "outcome": "success"}],
        )
        for cycle in malformed:
            with self.subTest(cycle=cycle):
                with self.assertRaises((TypeError, ValueError)):
                    validate_complete_cycle(cycle)


class PublisherStateTests(unittest.TestCase):
    @staticmethod
    def state_document(base_state, streaks, *, maintenance=False):
        document = initial_publisher_state().to_document()
        document["generated_at"] = "2026-07-20T12:00:00Z"
        component = document["components"][0]
        component["health"].update(
            base_state=base_state,
            public_state="maintenance" if maintenance else base_state,
            success_streak=streaks[0],
            slow_streak=streaks[1],
            failure_streak=streaks[2],
        )
        component["auto_outage_started_at"] = (
            "2026-07-20T11:00:00Z" if base_state == "outage" else None
        )
        return document

    def test_initial_state_is_recursive_immutable_and_round_trips_strictly(self):
        state = initial_publisher_state()
        self.assertIsNone(state.generated_at)
        self.assertEqual([item.component_id for item in state.components], list(COMPONENTS))
        for item in state.components:
            self.assertEqual(item.health.base_state, "no-data")
            self.assertIsNone(item.auto_outage_started_at)
            self.assertEqual(item.availability_days, ())
        with self.assertRaises(FrozenInstanceError):
            state.generated_at = "2026-07-20T12:00:00Z"
        with self.assertRaises(TypeError):
            state.components[0] = state.components[0]

        projection = state.to_document()
        restored = restore_publisher_state(projection)
        self.assertEqual(restored, state)
        projection["components"][0]["health"]["base_state"] = "outage"
        projection["components"][0]["availability_days"].append(
            {"date": "2026-07-20", "measured_seconds": 1, "available_seconds": 1}
        )
        self.assertEqual(state.components[0].health.base_state, "no-data")
        self.assertEqual(state.components[0].availability_days, ())
        self.assertEqual(restore_publisher_state(state.to_document()), state)
        reordered = state.to_document()
        reordered["components"].reverse()
        self.assertEqual(restore_publisher_state(reordered), state)

    def test_restore_rejects_registry_shape_health_and_timestamp_corruption(self):
        initial = initial_publisher_state().to_document()
        malformed = []

        unknown_root = deepcopy(initial)
        unknown_root["extra"] = True
        malformed.append(unknown_root)
        missing_component = deepcopy(initial)
        missing_component["components"].pop()
        malformed.append(missing_component)
        duplicate = deepcopy(initial)
        duplicate["components"][-1]["id"] = "watch"
        malformed.append(duplicate)
        unknown_component_key = deepcopy(initial)
        unknown_component_key["components"][0]["extra"] = 1
        malformed.append(unknown_component_key)
        boolean_counter = deepcopy(initial)
        boolean_counter["components"][0]["health"]["success_streak"] = True
        malformed.append(boolean_counter)
        invalid_health = deepcopy(initial)
        invalid_health["components"][0]["health"].update(
            base_state="outage", public_state="operational", failure_streak=3
        )
        malformed.append(invalid_health)
        malformed_timestamp = deepcopy(initial)
        malformed_timestamp["generated_at"] = "today"
        malformed.append(malformed_timestamp)
        state_without_generation = deepcopy(initial)
        state_without_generation["components"][0]["health"].update(
            base_state="operational", public_state="operational", success_streak=2
        )
        malformed.append(state_without_generation)
        future_outage = deepcopy(initial)
        future_outage["generated_at"] = "2026-07-20T12:00:00Z"
        future_outage["components"][0]["health"].update(
            base_state="outage", public_state="outage", failure_streak=3
        )
        future_outage["components"][0]["auto_outage_started_at"] = (
            "2026-07-20T12:00:01Z"
        )
        malformed.append(future_outage)

        availability_valid = self.state_document("operational", (2, 0, 0))
        availability_valid["components"][0]["availability_days"] = [
            {"date": "2026-07-20", "measured_seconds": 60, "available_seconds": 60}
        ]
        availability_mutations = []
        for mutation in (
            lambda days: days[0].update(extra=1),
            lambda days: days[0].update(measured_seconds=True),
            lambda days: days[0].update(available_seconds=False),
            lambda days: days[0].update(date="2026-07-21"),
            lambda days: days.append(deepcopy(days[0])),
            lambda days: days[0].update(measured_seconds=86401),
            lambda days: days[0].update(available_seconds=61),
        ):
            candidate = deepcopy(availability_valid)
            mutation(candidate["components"][0]["availability_days"])
            availability_mutations.append(candidate)
        reversed_days = deepcopy(availability_valid)
        reversed_days["components"][0]["availability_days"] = [
            {"date": "2026-07-19", "measured_seconds": 1, "available_seconds": 1},
            {"date": "2026-07-18", "measured_seconds": 1, "available_seconds": 1},
        ]
        availability_mutations.append(reversed_days)
        too_many_days = deepcopy(availability_valid)
        too_many_days["components"][0]["availability_days"] = [
            {
                "date": (datetime(2026, 1, 1, tzinfo=UTC).date() + timedelta(days=offset)).isoformat(),
                "measured_seconds": 1,
                "available_seconds": 1,
            }
            for offset in range(91)
        ]
        availability_mutations.append(too_many_days)
        malformed.extend(availability_mutations)

        for document in malformed:
            with self.subTest(document=document):
                with self.assertRaises((TypeError, ValueError)):
                    restore_publisher_state(document)

    def test_restore_accepts_exact_default_threshold_reachable_states(self):
        reachable = {
            "no-data": {
                (0, 0, 0),
                (1, 0, 0),
                (0, 1, 0),
                (0, 2, 0),
                (0, 0, 1),
                (0, 0, 2),
            },
            "operational": {
                (2, 0, 0),
                (0, 1, 0),
                (0, 2, 0),
                (0, 0, 1),
                (0, 0, 2),
            },
            "outage": {(0, 0, 3), (1, 0, 0), (0, 1, 0)},
            "degraded": {
                (0, 2, 0),
                (0, 3, 0),
                (1, 0, 0),
                (0, 0, 1),
                (0, 0, 2),
            },
        }
        candidates = set(product(range(5), repeat=3))
        for base_state, expected in reachable.items():
            for maintenance in (False, True):
                accepted = set()
                for streaks in candidates:
                    document = self.state_document(
                        base_state, streaks, maintenance=maintenance
                    )
                    try:
                        restore_publisher_state(document)
                    except (TypeError, ValueError):
                        continue
                    accepted.add(streaks)
                with self.subTest(base_state=base_state, maintenance=maintenance):
                    self.assertEqual(accepted, expected)

    def test_restore_rejects_review_examples_and_unbounded_streaks(self):
        impossible = (
            ("no-data", (2, 0, 0)),
            ("operational", (0, 0, 3)),
            ("outage", (2, 0, 0)),
            ("degraded", (0, 0, 3)),
            ("no-data", (0, 999, 0)),
        )
        for base_state, streaks in impossible:
            for maintenance in (False, True):
                with self.subTest(
                    base_state=base_state,
                    streaks=streaks,
                    maintenance=maintenance,
                ):
                    with self.assertRaises(ValueError):
                        restore_publisher_state(
                            self.state_document(
                                base_state, streaks, maintenance=maintenance
                            )
                        )

    def test_restored_availability_is_recursive_immutable_with_fresh_projections(self):
        document = self.state_document("operational", (2, 0, 0))
        document["components"][0]["availability_days"] = [
            {"date": "2026-07-20", "measured_seconds": 60, "available_seconds": 59}
        ]
        restored = restore_publisher_state(document)
        self.assertEqual(
            restored.components[0].availability_days,
            (("2026-07-20", 60, 59),),
        )
        with self.assertRaises(TypeError):
            restored.components[0].availability_days[0][1] = 0

        first = restored.to_document()
        first["components"][0]["availability_days"][0]["measured_seconds"] = 0
        second = restored.to_document()
        self.assertEqual(
            second["components"][0]["availability_days"],
            [{"date": "2026-07-20", "measured_seconds": 60, "available_seconds": 59}],
        )

    def test_restore_accepts_exact_cutoff_and_rejects_out_of_window_availability(self):
        document = self.state_document("operational", (2, 0, 0))
        record = {"date": "2026-04-22", "measured_seconds": 1, "available_seconds": 1}
        document["components"][0]["availability_days"] = [record]
        restored = restore_publisher_state(document)
        self.assertEqual(restored.components[0].availability_days[0][0], "2026-04-22")

        document["components"][0]["availability_days"] = [
            {**record, "date": "2026-04-21"}
        ]
        with self.assertRaises(ValueError):
            restore_publisher_state(document)


class PurePublishCycleTests(unittest.TestCase):
    def setUp(self):
        self.state = initial_publisher_state()
        self.history = empty_history_document(at(day=19))

    def advance(self, outcomes, count, *, start=at()):
        cycle = None
        for offset in range(count):
            cycle = run_cycle(
                self.state,
                self.history,
                start + timedelta(minutes=offset),
                outcomes,
            )
            self.state = cycle.next_state
            self.history = cycle.history_document
        return cycle

    def component(self, cycle, component_id="watch"):
        return next(
            item
            for item in cycle.summary_document["components"]
            if item["id"] == component_id
        )

    def test_two_successes_operational_then_trip_and_recover_thresholds(self):
        first = self.advance(probes(), 1)
        self.assertEqual(self.component(first)["state"], "no-data")
        second = self.advance(probes(), 1, start=at(minute=1))
        self.assertTrue(
            all(item["state"] == "operational" for item in second.summary_document["components"])
        )

        one_failure = self.advance(probes(watch="failure"), 1, start=at(minute=2))
        self.assertEqual(self.component(one_failure)["state"], "operational")
        outage = self.advance(probes(watch="failure"), 2, start=at(minute=3))
        self.assertEqual(self.component(outage)["state"], "outage")
        still_outage = self.advance(probes(), 1, start=at(minute=5))
        self.assertEqual(self.component(still_outage)["state"], "outage")
        recovered = self.advance(probes(), 1, start=at(minute=6))
        self.assertEqual(self.component(recovered)["state"], "operational")

    def test_error_becomes_no_data_and_slow_streak_becomes_degraded(self):
        self.advance(probes(), 2)
        errored = self.advance(probes(watch="error"), 1, start=at(minute=2))
        self.assertEqual(self.component(errored)["state"], "no-data")

        self.state = initial_publisher_state()
        self.history = empty_history_document(at(day=19))
        degraded = self.advance(probes(watch="slow"), 3)
        self.assertEqual(self.component(degraded)["state"], "degraded")
        recovered = self.advance(probes(), 2, start=at(minute=3))
        self.assertEqual(self.component(recovered)["state"], "operational")

    def test_maintenance_override_advances_base_and_planned_does_not_override(self):
        maintenance = event(
            "maint-2026-07-20-watch",
            kind="maintenance",
            status="in-progress",
        )
        for minute in range(3):
            cycle = run_cycle(
                self.state,
                self.history,
                at(minute=minute),
                probes(watch="failure"),
                no_content(active=(maintenance,)),
            )
            self.state, self.history = cycle.next_state, cycle.history_document
        self.assertEqual(self.component(cycle)["state"], "maintenance")
        watch_state = cycle.next_state.components[0]
        self.assertEqual(watch_state.health.base_state, "outage")
        self.assertEqual(watch_state.health.public_state, "maintenance")
        self.assertIsNotNone(watch_state.auto_outage_started_at)

        planned = event(
            "maint-2026-07-20-planned",
            kind="maintenance",
            status="planned",
        )
        revealed = run_cycle(
            self.state,
            self.history,
            at(minute=3),
            probes(watch="failure"),
            no_content(active=(planned,)),
        )
        self.assertEqual(self.component(revealed)["state"], "outage")

    def test_manual_incident_is_visible_without_changing_healthy_state(self):
        self.advance(probes(), 2)
        narrative = event("inc-2026-07-20-watch")
        cycle = run_cycle(
            self.state,
            self.history,
            at(minute=2),
            probes(),
            no_content(active=(narrative,)),
        )
        self.assertEqual(self.component(cycle)["state"], "operational")
        self.assertEqual(
            [item["id"] for item in cycle.summary_document["active_events"]],
            [narrative.id],
        )

    def test_generic_ticket_is_stable_hidden_by_narrative_and_clears_after_recovery(self):
        outage = self.advance(probes(watch="failure"), 3)
        generic = outage.summary_document["active_events"][0]
        self.assertEqual(
            generic,
            {
                "id": "auto-outage-watch",
                "kind": "incident",
                "status": "investigating",
                "title": "Interruption detected",
                "body": "Automated public checks indicate an interruption. A narrated update has not yet been posted.",
                "started_at": "2026-07-20T12:02:00Z",
                "updated_at": "2026-07-20T12:02:00Z",
                "affected_components": ["watch"],
                "updates": [
                    {
                        "at": "2026-07-20T12:02:00Z",
                        "status": "investigating",
                        "body": "Automated public checks indicate an interruption. A narrated update has not yet been posted.",
                    }
                ],
            },
        )

        held = self.advance(probes(watch="failure"), 1, start=at(minute=3))
        self.assertEqual(
            held.summary_document["active_events"][0]["started_at"],
            generic["started_at"],
        )
        narrative = event(
            "inc-2026-07-20-narrated",
            started="2026-07-20T12:03:00Z",
            updated="2026-07-20T12:03:00Z",
        )
        hidden = run_cycle(
            self.state,
            self.history,
            at(minute=4),
            probes(watch="failure"),
            no_content(active=(narrative,)),
        )
        self.assertEqual(
            [item["id"] for item in hidden.summary_document["active_events"]],
            [narrative.id],
        )
        self.assertEqual(
            hidden.next_state.components[0].auto_outage_started_at,
            generic["started_at"],
        )

        self.state, self.history = hidden.next_state, hidden.history_document
        first_success = self.advance(probes(), 1, start=at(minute=5))
        self.assertEqual(
            first_success.next_state.components[0].auto_outage_started_at,
            generic["started_at"],
        )
        recovered = self.advance(probes(), 1, start=at(minute=6))
        self.assertIsNone(recovered.next_state.components[0].auto_outage_started_at)
        self.assertEqual(recovered.summary_document["active_events"], [])

    def test_operator_collision_with_generic_id_fails_closed(self):
        collision = event("auto-outage-watch", affected=("home",))
        with self.assertRaises(ValueError):
            for minute in range(3):
                cycle = run_cycle(
                    self.state,
                    self.history,
                    at(minute=minute),
                    probes(watch="failure"),
                    no_content(active=(collision,)),
                )
                self.state, self.history = cycle.next_state, cycle.history_document

    def test_overall_priority_and_canonical_component_order_copy(self):
        operational = self.advance(probes(), 2)
        self.assertEqual(operational.summary_document["overall"]["state"], "operational")
        self.assertEqual(
            operational.summary_document["overall"]["headline"],
            "Everything is on schedule.",
        )
        components = operational.summary_document["components"]
        self.assertEqual([item["id"] for item in components], list(COMPONENTS))
        self.assertEqual(
            {item["id"]: item["description"] for item in components},
            COMPONENT_DESCRIPTIONS,
        )

        maintained = run_cycle(
            initial_publisher_state(),
            empty_history_document(at(day=19)),
            at(),
            probes(default="error"),
            no_content(
                active=(
                    event(
                        "maint-2026-07-20-home",
                        kind="maintenance",
                        status="in-progress",
                        affected=("home",),
                    ),
                )
            ),
        )
        self.assertEqual(maintained.summary_document["overall"]["state"], "maintenance")

        self.state, self.history = operational.next_state, operational.history_document
        degraded = self.advance(probes(watch="slow"), 3, start=at(minute=2))
        self.assertEqual(degraded.summary_document["overall"]["state"], "degraded")
        outage = self.advance(probes(home="failure"), 3, start=at(minute=5))
        self.assertEqual(outage.summary_document["overall"]["state"], "outage")

    def test_history_updates_all_components_stats_retention_and_monotonicity(self):
        cycle = run_cycle(self.state, self.history, at(), probes(default="error"))
        validate_history(cycle.history_document)
        for component in cycle.history_document["components"]:
            self.assertEqual(component["days"], [{"date": "2026-07-20", "state": "no-data"}])
        for component in cycle.summary_document["components"]:
            self.assertIsNone(component["availability_90d_pct"])
            self.assertEqual(component["availability_seconds_measured"], 0)
            self.assertEqual(component["history_days_available"], 0)

        days = [
            {
                "date": (at().date() - timedelta(days=offset)).isoformat(),
                "state": "outage" if offset == 1 else "operational",
            }
            for offset in reversed(range(90))
        ]
        existing = empty_history_document(at())
        for component in existing["components"]:
            component["days"] = deepcopy(days)
        cycle = run_cycle(initial_publisher_state(), existing, at(), probes(default="error"))
        for component in cycle.history_document["components"]:
            self.assertEqual(len(component["days"]), 90)
            self.assertEqual(component["days"][0]["date"], days[0]["date"])
        self.assertIsNone(cycle.summary_document["components"][0]["availability_90d_pct"])
        self.assertEqual(
            cycle.summary_document["components"][0]["availability_seconds_measured"], 0
        )
        self.assertEqual(cycle.summary_document["components"][0]["history_days_available"], 90)

        future = empty_history_document(at() + timedelta(seconds=1))
        with self.assertRaises(ValueError):
            run_cycle(initial_publisher_state(), future, at())

    def test_first_cycle_is_unmeasured_and_second_attributes_previous_public_state(self):
        maintenance = event(
            "maint-2026-07-20-watch-measured",
            kind="maintenance",
            status="in-progress",
        )
        first = run_cycle(
            self.state,
            self.history,
            at(),
            probes(default="error"),
            no_content(active=(maintenance,)),
        )
        first_watch = self.component(first)
        self.assertEqual(first_watch["state"], "maintenance")
        self.assertEqual(first_watch["availability_seconds_measured"], 0)
        self.assertIsNone(first_watch["availability_90d_pct"])

        second = run_cycle(
            first.next_state,
            first.history_document,
            at(minute=1),
            probes(default="error"),
        )
        second_watch = self.component(second)
        self.assertEqual(second_watch["state"], "no-data")
        self.assertEqual(second_watch["availability_seconds_measured"], 60)
        self.assertEqual(second_watch["availability_90d_pct"], 100)

    def test_summary_duration_comes_from_internal_days_not_daily_strip_counts(self):
        state_document = initial_publisher_state().to_document()
        state_document["generated_at"] = "2026-07-20T11:59:00Z"
        for component in state_document["components"]:
            component["availability_days"] = [
                {
                    "date": "2026-07-20",
                    "measured_seconds": 60,
                    "available_seconds": 30,
                }
            ]
        state = restore_publisher_state(state_document)
        history = empty_history_document(at(hour=11, minute=59))
        days = [
            {
                "date": (at().date() - timedelta(days=offset)).isoformat(),
                "state": "operational",
            }
            for offset in reversed(range(90))
        ]
        for component in history["components"]:
            component["days"] = deepcopy(days)

        cycle = run_cycle(state, history, at(), probes(default="error"))
        watch = self.component(cycle)
        self.assertEqual(watch["availability_seconds_measured"], 60)
        self.assertEqual(watch["availability_90d_pct"], 50)
        self.assertEqual(watch["history_days_available"], 90)

    def test_non_initial_state_and_history_must_share_the_same_generation(self):
        first = run_cycle(self.state, self.history, at())

        older_history = first.history_document
        older_history["generated_at"] = "2026-07-20T11:59:59Z"
        with self.assertRaises(ValueError):
            run_cycle(first.next_state, older_history, at(minute=1))

        newer_history = first.history_document
        newer_history["generated_at"] = "2026-07-20T12:00:01Z"
        with self.assertRaises(ValueError):
            run_cycle(first.next_state, newer_history, at(minute=1))

        equal = run_cycle(
            first.next_state,
            first.history_document,
            at(minute=1),
        )
        self.assertEqual(equal.next_state.generated_at, "2026-07-20T12:01:00Z")

    def test_null_state_recovery_preserves_older_history_and_resets_counters(self):
        older_history = empty_history_document(at(day=18))
        older_history["components"][0]["days"] = [
            {"date": "2026-07-18", "state": "outage"}
        ]
        cycle = run_cycle(initial_publisher_state(), older_history, at(), probes())
        self.assertEqual(
            cycle.history_document["components"][0]["days"],
            [
                {"date": "2026-07-18", "state": "outage"},
                {"date": "2026-07-20", "state": "no-data"},
            ],
        )
        self.assertEqual(cycle.next_state.components[0].health.success_streak, 1)

    def test_sparse_prior_evidence_is_pruned_and_renderer_accepts_python_output(self):
        prior_generated = datetime(2026, 1, 1, 12, tzinfo=UTC)
        generated = datetime(2026, 4, 1, 12, tzinfo=UTC)
        state_document = initial_publisher_state().to_document()
        state_document["generated_at"] = "2026-01-01T12:00:00Z"
        for component in state_document["components"]:
            component["availability_days"] = [
                {"date": "2025-10-04", "measured_seconds": 60, "available_seconds": 60}
            ]
        state = restore_publisher_state(state_document)
        history = empty_history_document(prior_generated)
        for component in history["components"]:
            component["days"] = [
                {"date": "2025-10-04", "state": "operational"},
                {"date": "2026-01-01", "state": "no-data"},
            ]

        cycle = run_cycle(state, history, generated)

        for component in cycle.summary_document["components"]:
            self.assertEqual(component["availability_seconds_measured"], 0)
            self.assertIsNone(component["availability_90d_pct"])
        for component in cycle.history_document["components"]:
            self.assertEqual(
                component["days"],
                [{"date": "2026-04-01", "state": "no-data"}],
            )
        validate_history(cycle.history_document)

        script = (
            'const fs = require("fs");'
            'const renderer = require("./web/status.js");'
            'const input = JSON.parse(fs.readFileSync(0, "utf8"));'
            'const model = renderer.buildViewModel('
            'input.summary, input.history, Date.parse(input.summary.generated_at));'
            'process.stdout.write(JSON.stringify(model.components.map((item) => item.marks.length)));'
        )
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            input=json.dumps(
                {
                    "summary": cycle.summary_document,
                    "history": cycle.history_document,
                }
            ),
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(json.loads(completed.stdout), [90] * len(COMPONENTS))

    def test_closed_archive_includes_exact_90_day_boundary_and_orders(self):
        boundary = at() - timedelta(days=90)
        boundary_text = boundary.isoformat(timespec="seconds").replace("+00:00", "Z")
        just_old = (boundary - timedelta(seconds=1)).isoformat(timespec="seconds").replace("+00:00", "Z")
        newest = "2026-07-20T11:00:00Z"
        closed = (
            event("inc-boundary", status="resolved", started=boundary_text, updated=boundary_text, resolved=boundary_text),
            event("inc-too-old", status="resolved", started=just_old, updated=just_old, resolved=just_old),
            event("inc-zulu", status="resolved", started=newest, updated=newest, resolved=newest),
            event("inc-alpha", status="resolved", started=newest, updated=newest, resolved=newest),
        )
        cycle = run_cycle(
            self.state,
            self.history,
            at(),
            content=no_content(closed=closed),
        )
        self.assertEqual(
            [item["id"] for item in cycle.incidents_document["events"]],
            ["inc-alpha", "inc-zulu", "inc-boundary"],
        )

    def test_future_and_invalid_closed_events_fail_before_archive_filtering(self):
        future_time = "2026-07-20T12:00:01Z"
        future = event(
            "inc-future",
            status="resolved",
            updated=future_time,
            resolved=future_time,
        )
        invalid = ContentEvent(
            id="inc-invalid-lifecycle",
            kind="incident",
            status="resolved",
            title="Public service notice",
            body="Availability information is being shared here.",
            started_at="2026-01-01T10:00:00Z",
            updated_at="2026-01-01T11:00:00Z",
            affected_components=("watch",),
            updates=(
                ContentUpdate(
                    at="2026-01-01T11:00:00Z",
                    status="monitoring",
                    body="A public update is available.",
                ),
            ),
            resolved_at="2026-01-01T11:00:00Z",
        )
        for closed_event in (future, invalid):
            with self.subTest(event_id=closed_event.id):
                with self.assertRaises(ValueError):
                    run_cycle(
                        self.state,
                        self.history,
                        at(),
                        content=no_content(closed=(closed_event,)),
                    )

    def test_automatic_event_id_prefix_is_reserved_for_all_operator_content(self):
        resolved_at = "2026-07-20T11:00:00Z"
        reserved_cases = (
            no_content(active=(event("auto-outage-watch"),)),
            no_content(
                active=(event("auto-outage-watch", affected=("home",)),)
            ),
            no_content(
                closed=(
                    event(
                        "auto-outage-watch",
                        status="resolved",
                        updated=resolved_at,
                        resolved=resolved_at,
                    ),
                )
            ),
            no_content(active=(event("auto-outage-site"),)),
        )
        for content in reserved_cases:
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    run_cycle(
                        self.state,
                        self.history,
                        at(),
                        probes(default="success"),
                        content,
                    )

        allowed = event("operator-auto-outage-watch")
        cycle = run_cycle(
            self.state,
            self.history,
            at(),
            probes(default="success"),
            no_content(active=(allowed,)),
        )
        self.assertEqual(
            [item["id"] for item in cycle.summary_document["active_events"]],
            [allowed.id],
        )

    def test_direct_content_rechecks_narrative_locations_before_writing(self):
        resolved_at = "2026-07-20T11:00:00Z"
        unsafe = (
            (
                "active-title",
                no_content(
                    active=(event("inc-active-title", title="See status.example.net"),)
                ),
            ),
            (
                "active-body",
                no_content(
                    active=(event("inc-active-body", body="See https://walshit.com"),)
                ),
            ),
            (
                "active-update",
                no_content(
                    active=(
                        event("inc-active-update", update_body="See walshit.com/path"),
                    )
                ),
            ),
            (
                "closed-title",
                no_content(
                    closed=(
                        event(
                            "inc-closed-title",
                            status="resolved",
                            updated=resolved_at,
                            resolved=resolved_at,
                            title="See history.example.org",
                        ),
                    )
                ),
            ),
            (
                "closed-body",
                no_content(
                    closed=(
                        event(
                            "inc-closed-body",
                            status="resolved",
                            updated=resolved_at,
                            resolved=resolved_at,
                            body="See https://walshit.com",
                        ),
                    )
                ),
            ),
            (
                "closed-update",
                no_content(
                    closed=(
                        event(
                            "inc-closed-update",
                            status="resolved",
                            updated=resolved_at,
                            resolved=resolved_at,
                            update_body="See walshit.com/path",
                        ),
                    )
                ),
            ),
        )
        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            for label, content in unsafe:
                with self.subTest(label=label):
                    with self.assertRaises(ValueError):
                        cycle = run_cycle(
                            self.state,
                            self.history,
                            at(),
                            content=content,
                        )
                        write_public_documents(output, cycle)
                    self.assertEqual(list(output.iterdir()), [])

        allowed = event(
            "inc-bare-public-host",
            title="Walshit.com status",
            body="Updates remain available at walshit.com",
            update_body="The walshit.com notice is current.",
        )
        cycle = run_cycle(
            self.state,
            self.history,
            at(),
            content=no_content(active=(allowed,)),
        )
        self.assertEqual(cycle.summary_document["active_events"][0]["id"], allowed.id)

    def test_documents_validate_are_safe_share_time_and_are_fresh_projections(self):
        cycle = run_cycle(
            self.state,
            self.history,
            datetime(2026, 7, 20, 8, 0, tzinfo=timezone(timedelta(hours=-4))),
            content=no_content(issues=(ContentIssue("hidden", "invalid_content"),)),
        )
        self.assertIsInstance(cycle, PublishCycle)
        self.assertEqual(cycle.issue_count, 1)
        documents = (
            cycle.summary_document,
            cycle.history_document,
            cycle.incidents_document,
        )
        validate_summary(documents[0])
        validate_history(documents[1])
        validate_incidents(documents[2])
        self.assertEqual({document["generated_at"] for document in documents}, {"2026-07-20T12:00:00Z"})
        for document in documents:
            serialized = json.dumps(document, sort_keys=True, separators=(",", ":"))
            assert_public_safe(serialized)
            self.assertNotIn("hidden", serialized)
            self.assertNotIn("success_streak", serialized)

        changed = cycle.summary_document
        changed["overall"]["state"] = "outage"
        self.assertNotEqual(changed, cycle.summary_document)

    def test_invalid_inputs_fail_and_identical_inputs_are_deterministic(self):
        valid_args = (self.state, self.history, no_content(), probes(), at())
        first = build_publish_cycle(*valid_args)
        second = build_publish_cycle(*valid_args)
        self.assertEqual(first, second)

        invalid_calls = (
            lambda: build_publish_cycle({}, *valid_args[1:]),
            lambda: build_publish_cycle(self.state, {}, *valid_args[2:]),
            lambda: build_publish_cycle(self.state, self.history, object(), *valid_args[3:]),
            lambda: build_publish_cycle(self.state, self.history, no_content(), probes()[:-1], at()),
            lambda: build_publish_cycle(self.state, self.history, no_content(), probes(), datetime(2026, 7, 20, 12)),
            lambda: build_publish_cycle(self.state, self.history, no_content(), probes(), at().replace(microsecond=1)),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises((TypeError, ValueError)):
                    call()

    def test_later_injected_cycle_changes_only_expected_freshness_value(self):
        first = run_cycle(self.state, self.history, at())
        second = run_cycle(first.next_state, first.history_document, at(minute=1))
        self.assertEqual(first.summary_document["generated_at"], "2026-07-20T12:00:00Z")
        self.assertEqual(second.summary_document["generated_at"], "2026-07-20T12:01:00Z")


class AtomicWriterTests(unittest.TestCase):
    def setUp(self):
        self.cycle = run_cycle(
            initial_publisher_state(),
            empty_history_document(at(day=19)),
            at(),
        )

    @staticmethod
    def expected_bytes(document):
        return (
            json.dumps(
                document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")

    def test_success_writes_exact_deterministic_bytes_and_public_read_only_mode(self):
        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            previous_umask = os.umask(0o077)
            try:
                write_public_documents(output, self.cycle)
            finally:
                os.umask(previous_umask)

            self.assertEqual(
                sorted(path.name for path in output.iterdir()),
                ["history.json", "incidents.json", "summary.json"],
            )
            expected = {
                "summary.json": self.cycle.summary_document,
                "history.json": self.cycle.history_document,
                "incidents.json": self.cycle.incidents_document,
            }
            first_bytes = {}
            for filename, document in expected.items():
                path = output / filename
                first_bytes[filename] = path.read_bytes()
                self.assertEqual(first_bytes[filename], self.expected_bytes(document))
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o022, 0)
                file_fd = os.open(path, os.O_RDONLY)
                try:
                    self.assertEqual(os.read(file_fd, 1_000_000), first_bytes[filename])
                finally:
                    os.close(file_fd)

            write_public_documents(output, self.cycle)
            self.assertEqual(
                {name: (output / name).read_bytes() for name in expected},
                first_bytes,
            )

    def test_pre_replace_failure_preserves_every_sentinel_and_cleans_own_temps(self):
        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            sentinels = {
                "summary.json": b"old summary\n",
                "history.json": b"old history\n",
                "incidents.json": b"old incidents\n",
            }
            for filename, content in sentinels.items():
                (output / filename).write_bytes(content)

            def fail_after_prepare():
                names = sorted(path.name for path in output.iterdir())
                prepared = [name for name in names if name.endswith(".tmp")]
                self.assertEqual(len(prepared), 3)
                self.assertEqual(
                    sorted(name.split(".publisher.")[0] for name in prepared),
                    [".history.json", ".incidents.json", ".summary.json"],
                )
                for filename, content in sentinels.items():
                    self.assertEqual((output / filename).read_bytes(), content)
                raise RuntimeError("injected")

            with self.assertRaises(RuntimeError):
                write_public_documents(output, self.cycle, fault_hook=fail_after_prepare)

            self.assertEqual(
                {filename: (output / filename).read_bytes() for filename in sentinels},
                sentinels,
            )
            self.assertEqual(sorted(path.name for path in output.iterdir()), sorted(sentinels))

    def test_cooperating_concurrent_writer_fails_closed_while_lock_is_held(self):
        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            entered_hook = threading.Event()
            release_hook = threading.Event()
            first_errors = []

            def hold_lock():
                entered_hook.set()
                if not release_hook.wait(timeout=5):
                    raise RuntimeError("test timed out waiting to release writer")

            def first_writer():
                try:
                    write_public_documents(output, self.cycle, fault_hook=hold_lock)
                except Exception as exc:  # pragma: no cover - asserted below
                    first_errors.append(exc)

            thread = threading.Thread(target=first_writer)
            thread.start()
            try:
                self.assertTrue(entered_hook.wait(timeout=5))
                with self.assertRaises((BlockingIOError, RuntimeError)):
                    write_public_documents(output, self.cycle)
            finally:
                release_hook.set()
                thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(first_errors, [])

    def test_stale_unowned_temp_like_file_does_not_block_and_is_not_removed(self):
        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            stale = output / ".summary.json.publisher.tmp"
            stale.write_bytes(b"stale unrelated file\n")
            write_public_documents(output, self.cycle)
            self.assertEqual(stale.read_bytes(), b"stale unrelated file\n")
            self.assertTrue((output / "summary.json").is_file())

    def test_temp_substitution_fails_closed_and_never_removes_substitute(self):
        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            sentinels = {}
            for filename in ("history.json", "incidents.json", "summary.json"):
                sentinels[filename] = f"old {filename}\n".encode()
                (output / filename).write_bytes(sentinels[filename])
            substituted = []

            def substitute_one_temp():
                prepared = sorted(
                    path for path in output.iterdir() if path.name.endswith(".tmp")
                )
                self.assertEqual(len(prepared), 3)
                victim = prepared[0]
                victim.unlink()
                victim.write_bytes(b"unowned substitute\n")
                substituted.append(victim)

            with self.assertRaises(ValueError):
                write_public_documents(output, self.cycle, fault_hook=substitute_one_temp)

            self.assertEqual(
                {filename: (output / filename).read_bytes() for filename in sentinels},
                sentinels,
            )
            self.assertEqual(substituted[0].read_bytes(), b"unowned substitute\n")
            remaining_temps = [
                path for path in output.iterdir() if path.name.endswith(".tmp")
            ]
            self.assertEqual(remaining_temps, substituted)

    def test_injected_symlink_target_fails_before_any_publisher_replacement(self):
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            output.mkdir()
            external = Path(temporary) / "external"
            external.write_bytes(b"external sentinel\n")
            sentinels = {}
            for filename in ("history.json", "incidents.json", "summary.json"):
                sentinels[filename] = f"old {filename}\n".encode()
                (output / filename).write_bytes(sentinels[filename])

            def substitute_target():
                summary = output / "summary.json"
                summary.unlink()
                summary.symlink_to(external)

            with self.assertRaises(ValueError):
                write_public_documents(output, self.cycle, fault_hook=substitute_target)

            self.assertEqual((output / "history.json").read_bytes(), sentinels["history.json"])
            self.assertEqual(
                (output / "incidents.json").read_bytes(), sentinels["incidents.json"]
            )
            self.assertTrue((output / "summary.json").is_symlink())
            self.assertEqual(external.read_bytes(), b"external sentinel\n")
            self.assertEqual(
                [path for path in output.iterdir() if path.name.endswith(".tmp")],
                [],
            )

    def test_invalid_cycle_is_rejected_before_any_temp_is_created(self):
        invalid = PublishCycle(
            "{}",
            self.cycle._history_json,
            self.cycle._incidents_json,
            self.cycle.next_state,
            0,
        )
        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            with self.assertRaises((TypeError, ValueError)):
                write_public_documents(output, invalid)
            self.assertEqual(list(output.iterdir()), [])

    def test_rejects_non_directory_symlinked_directory_and_symlink_target(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            regular_file = root / "not-a-directory"
            regular_file.write_text("sentinel", encoding="utf-8")
            with self.assertRaises((NotADirectoryError, ValueError)):
                write_public_documents(regular_file, self.cycle)

            actual = root / "actual"
            actual.mkdir()
            linked = root / "linked"
            linked.symlink_to(actual, target_is_directory=True)
            with self.assertRaises(ValueError):
                write_public_documents(linked, self.cycle)
            self.assertEqual(list(actual.iterdir()), [])

            target = actual / "summary.json"
            target.symlink_to(regular_file)
            with self.assertRaises(ValueError):
                write_public_documents(actual, self.cycle)
            self.assertEqual(regular_file.read_text(encoding="utf-8"), "sentinel")
            self.assertTrue(target.is_symlink())
            self.assertEqual(
                sorted(path.name for path in actual.iterdir()), ["summary.json"]
            )

            target.unlink()

            ancestor = root / "ancestor"
            ancestor.symlink_to(actual, target_is_directory=True)
            nested = ancestor / "nested"
            (actual / "nested").mkdir()
            with self.assertRaises((NotADirectoryError, ValueError)):
                write_public_documents(nested, self.cycle)
            self.assertEqual(list((actual / "nested").iterdir()), [])


class LocalOnlySourceTests(unittest.TestCase):
    def test_probe_network_surface_is_exact_and_publisher_remains_network_free(self):
        probes = (ROOT / "publisher" / "probes.py").read_text(encoding="utf-8")
        publisher = (ROOT / "publisher" / "publisher.py").read_text(
            encoding="utf-8"
        )
        expected_public_urls = {
            "https://app.plex.tv/desktop/",
            (
                "https://plex.tv/api/v2/resources?"
                "includeHttps=1&includeRelay=1&includeIPv6=1"
            ),
            "https://home.walshit.com/api/health",
            "https://requests.walshit.com/api/v1/status",
            "https://books.walshit.com/healthcheck",
            "https://walshit.com/healthz.txt",
        }
        probe_tree = ast.parse(probes)
        https_literals = {
            node.value
            for node in ast.walk(probe_tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("https://")
        }
        self.assertEqual(https_literals, expected_public_urls)
        self.assertNotIn("http://", probes.lower())
        self.assertNotIn("_create_unverified_context", probes.lower())
        self.assertNotIn("cert_none", probes.lower())
        self.assertNotIn("check_hostname = false", probes.lower())
        for token in ("subprocess", "importlib", "ctypes", "socket", "getaddrinfo"):
            with self.subTest(source="probes.py", token=token):
                self.assertNotIn(token, probes.lower())

        for token in (
            "http://",
            "https://",
            "urllib",
            "requests.",
            "socket",
            "urlopen",
            "getaddrinfo",
            "subprocess",
        ):
            with self.subTest(source="publisher.py", token=token):
                self.assertNotIn(token, publisher.lower())

    def test_new_source_ast_restricts_imports_dynamic_loading_and_execution(self):
        paths = (
            ROOT / "publisher" / "availability.py",
            ROOT / "publisher" / "probes.py",
            ROOT / "publisher" / "publisher.py",
        )
        common_imports = {
            "__future__", "dataclasses", "errno", "json", "os", "stat",
            "publisher.privacy", "publisher.schema", "publisher.statemachine",
        }
        allowed_by_file = {
            "availability.py": {
                "dataclasses", "datetime", "decimal", "publisher.schema"
            },
            "probes.py": common_imports
            | {
                "ipaddress", "math", "re", "signal", "ssl", "threading", "time", "tomllib",
                "urllib.error", "urllib.parse", "urllib.request",
                "xml.etree.ElementTree",
            },
            "publisher.py": common_imports
            | {
                "argparse", "datetime", "fcntl", "pathlib", "secrets", "sys",
                "time", "publisher.content", "publisher.history",
                "publisher.probes", "publisher.availability",
            },
        }
        forbidden_import_roots = {
            "aiohttp",
            "asyncio",
            "ctypes",
            "ftplib",
            "httpx",
            "httplib",
            "imaplib",
            "importlib",
            "multiprocessing",
            "paramiko",
            "poplib",
            "requests",
            "smtplib",
            "socket",
            "socketserver",
            "subprocess",
            "telnetlib",
            "urllib3",
            "websockets",
            "xmlrpc",
        }
        forbidden_calls = {
            "__import__",
            "Popen",
            "compile",
            "connect",
            "create_connection",
            "eval",
            "exec",
            "execl",
            "execle",
            "execlp",
            "execlpe",
            "execv",
            "execve",
            "execvp",
            "execvpe",
            "fork",
            "forkpty",
            "getaddrinfo",
            "gethostbyaddr",
            "gethostbyname",
            "gethostbyname_ex",
            "open_connection",
            "popen",
            "posix_spawn",
            "posix_spawnp",
            "spawnl",
            "spawnle",
            "spawnlp",
            "spawnlpe",
            "spawnv",
            "spawnve",
            "spawnvp",
            "spawnvpe",
            "startfile",
            "system",
            "urlopen",
        }
        for path in paths:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            imports = []
            calls = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.append(node.module or "")
                elif isinstance(node, ast.Call):
                    function = node.func
                    if isinstance(function, ast.Name):
                        calls.append(function.id)
                    elif isinstance(function, ast.Attribute):
                        calls.append(function.attr)
            with self.subTest(path=path.name, check="allowlist"):
                self.assertEqual(set(imports) - allowed_by_file[path.name], set())
            with self.subTest(path=path.name, check="forbidden imports"):
                self.assertFalse(
                    {name.split(".")[0] for name in imports} & forbidden_import_roots
                )
            with self.subTest(path=path.name, check="forbidden calls"):
                self.assertFalse(set(calls) & forbidden_calls)
            if path.name == "publisher.py":
                self.assertFalse(
                    {name.split(".")[0] for name in imports}
                    & {"http", "ssl", "urllib", "xml"}
                )
            if path.name == "probes.py":
                self.assertFalse(
                    set(calls)
                    & {"debug", "error", "exception", "info", "log", "print", "warning"}
                )


if __name__ == "__main__":
    unittest.main()
