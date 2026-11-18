from datetime import date, datetime, timedelta, timezone
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields
import unittest

from publisher.history import HistoryStats, summarize_buckets, update_daily_buckets
from publisher.schema import COMPONENTS, HISTORY_STATES, SCHEMA_VERSION, validate_history


class DailyBucketTests(unittest.TestCase):
    def test_first_insertion_uses_selected_utc_date(self):
        self.assertEqual(
            update_daily_buckets([], date(2026, 7, 19), "operational"),
            [{"date": "2026-07-19", "state": "operational"}],
        )
        west_of_utc = timezone(-timedelta(hours=7))
        self.assertEqual(
            update_daily_buckets(
                [],
                datetime(2026, 7, 19, 20, 30, tzinfo=west_of_utc),
                "degraded",
            ),
            [{"date": "2026-07-20", "state": "degraded"}],
        )

    def test_same_day_merge_keeps_worst_of_every_public_history_state(self):
        states = ["no-data", "operational", "maintenance", "degraded", "outage"]
        self.assertEqual(set(states), HISTORY_STATES)
        for existing_index, existing in enumerate(states):
            for observed_index, observed in enumerate(states):
                with self.subTest(existing=existing, observed=observed):
                    self.assertEqual(
                        update_daily_buckets(
                            [{"date": "2026-07-19", "state": existing}],
                            date(2026, 7, 19),
                            observed,
                        ),
                        [
                            {
                                "date": "2026-07-19",
                                "state": states[max(existing_index, observed_index)],
                            }
                        ],
                    )

    def test_older_observation_is_inserted_in_date_order(self):
        days = [
            {"date": "2026-07-18", "state": "operational"},
            {"date": "2026-07-20", "state": "outage"},
        ]

        self.assertEqual(
            update_daily_buckets(days, date(2026, 7, 19), "maintenance"),
            [
                {"date": "2026-07-18", "state": "operational"},
                {"date": "2026-07-19", "state": "maintenance"},
                {"date": "2026-07-20", "state": "outage"},
            ],
        )

    def test_update_never_mutates_input_and_returns_fresh_day_objects(self):
        days = [
            {"date": "2026-07-18", "state": "operational"},
            {"date": "2026-07-19", "state": "no-data"},
        ]
        original = deepcopy(days)

        updated = update_daily_buckets(days, date(2026, 7, 19), "degraded")

        self.assertEqual(days, original)
        self.assertIsNot(updated, days)
        for source, result in zip(days, updated):
            self.assertIsNot(source, result)
        updated[0]["state"] = "outage"
        self.assertEqual(days, original)


class HistoryStatisticsTests(unittest.TestCase):
    def test_history_stats_is_immutable_and_exposes_exact_public_fields(self):
        stats = HistoryStats(1, "2026-07-19")

        self.assertEqual(
            [field.name for field in fields(stats)],
            [
                "history_days_available",
                "first_measured_date",
            ],
        )
        with self.assertRaises(FrozenInstanceError):
            stats.history_days_available = 2

    def test_measured_count_and_first_measured_date_exclude_no_data(self):
        stats = summarize_buckets(
            [
                {"date": "2026-07-15", "state": "no-data"},
                {"date": "2026-07-16", "state": "operational"},
                {"date": "2026-07-17", "state": "maintenance"},
                {"date": "2026-07-18", "state": "no-data"},
                {"date": "2026-07-19", "state": "outage"},
            ]
        )

        self.assertIs(type(stats), HistoryStats)
        self.assertEqual(stats.history_days_available, 3)
        self.assertEqual(stats.first_measured_date, "2026-07-16")

    def test_daily_stats_never_infer_duration_percentage(self):
        mixed = [
            {"date": "2026-07-01", "state": "operational"},
            {"date": "2026-07-02", "state": "outage"},
            {"date": "2026-07-03", "state": "no-data"},
        ]
        self.assertEqual(
            summarize_buckets(mixed),
            HistoryStats(
                history_days_available=2,
                first_measured_date="2026-07-01",
            ),
        )

    def test_no_data_only_has_no_measured_statistics(self):
        self.assertEqual(
            summarize_buckets(
                [
                    {"date": "2026-07-18", "state": "no-data"},
                    {"date": "2026-07-19", "state": "no-data"},
                ]
            ),
            HistoryStats(
                history_days_available=0,
                first_measured_date=None,
            ),
        )


class HistoryInputValidationTests(unittest.TestCase):
    def test_both_apis_reject_malformed_days_states_dates_and_order(self):
        too_many = [
            {
                "date": (date(2026, 1, 1) + timedelta(days=offset)).isoformat(),
                "state": "operational",
            }
            for offset in range(91)
        ]
        malformed = [
            (({"date": "2026-07-19", "state": "operational"},), TypeError),
            ([[]], TypeError),
            ([{"date": "2026-07-19"}], ValueError),
            (
                [
                    {
                        "date": "2026-07-19",
                        "state": "operational",
                        "detail": "extra",
                    }
                ],
                ValueError,
            ),
            ([{"date": 20260719, "state": "operational"}], TypeError),
            ([{"date": "2026-02-30", "state": "operational"}], ValueError),
            ([{"date": "2026-07-19", "state": False}], TypeError),
            ([{"date": "2026-07-19", "state": "unknown"}], ValueError),
            (
                [
                    {"date": "2026-07-19", "state": "operational"},
                    {"date": "2026-07-19", "state": "outage"},
                ],
                ValueError,
            ),
            (
                [
                    {"date": "2026-07-20", "state": "operational"},
                    {"date": "2026-07-19", "state": "outage"},
                ],
                ValueError,
            ),
            (too_many, ValueError),
        ]

        for days, exception in malformed:
            for function in (
                summarize_buckets,
                lambda value: update_daily_buckets(
                    value, date(2026, 7, 20), "operational"
                ),
            ):
                with self.subTest(days=days, function=function):
                    with self.assertRaises(exception):
                        function(days)

        for invalid_state, exception in ((False, TypeError), ("unknown", ValueError)):
            with self.subTest(observed_state=invalid_state):
                with self.assertRaises(exception):
                    update_daily_buckets([], date(2026, 7, 20), invalid_state)

    def test_naive_and_invalid_observed_at_values_are_rejected(self):
        invalid_values = [
            (datetime(2026, 7, 20, 12, 0), ValueError),
            ("2026-07-20T12:00:00Z", TypeError),
            (1784548800, TypeError),
            (None, TypeError),
        ]
        for observed_at, exception in invalid_values:
            with self.subTest(observed_at=observed_at):
                with self.assertRaises(exception):
                    update_daily_buckets([], observed_at, "operational")

    def test_invalid_retention_values_are_rejected(self):
        invalid_values = [
            (True, TypeError),
            (False, TypeError),
            (1.0, TypeError),
            ("90", TypeError),
            (None, TypeError),
            (0, ValueError),
            (-1, ValueError),
            (91, ValueError),
        ]
        for retention_days, exception in invalid_values:
            with self.subTest(retention_days=retention_days):
                with self.assertRaises(exception):
                    update_daily_buckets(
                        [],
                        date(2026, 7, 20),
                        "operational",
                        retention_days=retention_days,
                    )


class HistoryRetentionAndSchemaTests(unittest.TestCase):
    def test_sparse_dates_use_exact_trailing_calendar_window_without_backfill(self):
        days = [
            {"date": "2026-01-01", "state": "outage"},
            {"date": "2026-01-02", "state": "maintenance"},
            {"date": "2026-03-15", "state": "operational"},
        ]

        retained = update_daily_buckets(
            days,
            datetime(2026, 4, 1, 23, 0, tzinfo=timezone.utc),
            "degraded",
            window_end=datetime(2026, 4, 1, 23, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(
            retained,
            [
                {"date": "2026-01-02", "state": "maintenance"},
                {"date": "2026-03-15", "state": "operational"},
                {"date": "2026-04-01", "state": "degraded"},
            ],
        )

    def test_output_days_form_valid_history_for_all_public_components(self):
        components = []
        for component_id, name in COMPONENTS.items():
            days = update_daily_buckets(
                [{"date": "2026-07-19", "state": "no-data"}],
                datetime(2026, 7, 20, 1, 0, tzinfo=timezone.utc),
                "operational",
            )
            components.append({"id": component_id, "name": name, "days": days})

        validate_history(
            {
                "schema_version": SCHEMA_VERSION,
                "generated_at": "2026-07-20T01:00:00Z",
                "components": components,
            }
        )

    def test_default_retention_keeps_90_entries_and_drops_oldest_on_91st(self):
        start = date(2026, 1, 1)
        days = [
            {
                "date": (start + timedelta(days=offset)).isoformat(),
                "state": "operational",
            }
            for offset in range(90)
        ]

        retained = update_daily_buckets(days, start + timedelta(days=90), "outage")

        self.assertEqual(len(retained), 90)
        self.assertEqual(retained[0]["date"], "2026-01-02")
        self.assertEqual(
            retained[-1], {"date": "2026-04-01", "state": "outage"}
        )

    def test_too_old_observation_cannot_displace_retained_window(self):
        days = [
            {"date": "2026-07-18", "state": "operational"},
            {"date": "2026-07-19", "state": "degraded"},
            {"date": "2026-07-20", "state": "outage"},
        ]

        self.assertEqual(
            update_daily_buckets(
                days, date(2026, 7, 1), "outage", retention_days=3
            ),
            days,
        )

    def test_missing_dates_are_not_synthesized(self):
        self.assertEqual(
            update_daily_buckets(
                [{"date": "2026-07-01", "state": "operational"}],
                date(2026, 7, 20),
                "degraded",
            ),
            [
                {"date": "2026-07-01", "state": "operational"},
                {"date": "2026-07-20", "state": "degraded"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
