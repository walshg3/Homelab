from copy import deepcopy
from dataclasses import FrozenInstanceError, fields
from datetime import date, datetime, timedelta, timezone
import unittest

from publisher.availability import (
    MAX_ATTRIBUTABLE_GAP_SECONDS,
    AvailabilityStats,
    summarize_availability,
    update_availability_days,
    validate_availability_days,
)


UTC = timezone.utc


def at(day=20, hour=12, minute=0, second=0):
    return datetime(2026, 7, day, hour, minute, second, tzinfo=UTC)


class AvailabilityAttributionTests(unittest.TestCase):
    def test_policy_states_attribute_the_previous_interval_only(self):
        expected = {
            "operational": (60, 60),
            "degraded": (60, 60),
            "maintenance": (60, 60),
            "outage": (60, 0),
            "no-data": (0, 0),
        }
        for state, (measured, available) in expected.items():
            with self.subTest(state=state):
                days = update_availability_days(
                    [], at(), at(minute=1), state
                )
                if measured == 0:
                    self.assertEqual(days, [])
                else:
                    self.assertEqual(
                        days,
                        [{
                            "date": "2026-07-20",
                            "measured_seconds": measured,
                            "available_seconds": available,
                        }],
                    )

    def test_exact_utc_midnight_split_and_same_day_accumulation(self):
        split = update_availability_days(
            [],
            at(day=20, hour=23, minute=59, second=30),
            at(day=21, hour=0, second=30),
            "outage",
        )
        self.assertEqual(
            split,
            [
                {"date": "2026-07-20", "measured_seconds": 30, "available_seconds": 0},
                {"date": "2026-07-21", "measured_seconds": 30, "available_seconds": 0},
            ],
        )
        accumulated = update_availability_days(
            split,
            at(day=21, hour=0, second=30),
            at(day=21, hour=0, minute=1, second=30),
            "operational",
        )
        self.assertEqual(
            accumulated[-1],
            {"date": "2026-07-21", "measured_seconds": 90, "available_seconds": 60},
        )

    def test_retains_newest_ninety_actual_dates_without_backfill(self):
        days = [
            {
                "date": (date(2026, 1, 1) + timedelta(days=offset)).isoformat(),
                "measured_seconds": 1,
                "available_seconds": 1,
            }
            for offset in range(90)
        ]
        updated = update_availability_days(
            days,
            datetime(2026, 4, 1, tzinfo=UTC),
            datetime(2026, 4, 1, 0, 1, tzinfo=UTC),
            "operational",
        )
        self.assertEqual(len(updated), 90)
        self.assertEqual(updated[0]["date"], "2026-01-02")
        self.assertEqual(updated[-1]["date"], "2026-04-01")

    def test_exact_trailing_calendar_window_prunes_sparse_stale_dates(self):
        window_end = datetime(2026, 4, 1, 0, 1, tzinfo=UTC)
        days = [
            {"date": "2026-01-01", "measured_seconds": 1, "available_seconds": 0},
            {"date": "2026-01-02", "measured_seconds": 2, "available_seconds": 1},
            {"date": "2026-03-15", "measured_seconds": 3, "available_seconds": 3},
        ]

        updated = update_availability_days(
            days,
            datetime(2026, 4, 1, tzinfo=UTC),
            window_end,
            "operational",
        )

        self.assertEqual(
            updated,
            [
                {"date": "2026-01-02", "measured_seconds": 2, "available_seconds": 1},
                {"date": "2026-03-15", "measured_seconds": 3, "available_seconds": 3},
                {"date": "2026-04-01", "measured_seconds": 60, "available_seconds": 60},
            ],
        )

    def test_gap_and_no_data_paths_still_prune_to_the_current_window(self):
        end = datetime(2026, 4, 1, 12, tzinfo=UTC)
        days = [
            {"date": "2026-01-01", "measured_seconds": 1, "available_seconds": 0},
            {"date": "2026-01-02", "measured_seconds": 2, "available_seconds": 1},
        ]
        expected = [days[1]]

        for start, state in (
            (end - timedelta(seconds=121), "operational"),
            (end - timedelta(seconds=60), "no-data"),
        ):
            with self.subTest(state=state):
                self.assertEqual(
                    update_availability_days(days, start, end, state),
                    expected,
                )

    def test_gap_limit_is_explicit_and_fail_safe(self):
        self.assertEqual(MAX_ATTRIBUTABLE_GAP_SECONDS, 120)
        exact = update_availability_days([], at(), at(minute=2), "operational")
        self.assertEqual(exact[0]["measured_seconds"], 120)
        too_long = update_availability_days([], at(), at(minute=2, second=1), "outage")
        self.assertEqual(too_long, [])

    def test_equal_reversed_and_non_whole_second_intervals_reject(self):
        invalid = (
            (at(), at()),
            (at(minute=1), at()),
            (at(), at().replace(microsecond=1)),
        )
        for start, end in invalid:
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValueError):
                    update_availability_days([], start, end, "operational")


class AvailabilityStatisticsTests(unittest.TestCase):
    def test_empty_measurement_has_null_percentage(self):
        self.assertEqual(
            summarize_availability([]),
            AvailabilityStats(0, 0, None),
        )

    def test_duration_weighting_and_exact_half_even_ties(self):
        def days_with_available(available):
            measured_chunks = (80000,) * 5
            available_chunks = (available, 0, 0, 0, 0)
            return [
                {
                    "date": f"2026-07-{index:02d}",
                    "measured_seconds": measured,
                    "available_seconds": portion,
                }
                for index, (measured, portion) in enumerate(
                    zip(measured_chunks, available_chunks, strict=True), start=1
                )
            ]

        weighted = summarize_availability(days_with_available(12498))
        self.assertEqual(weighted, AvailabilityStats(400000, 12498, 3.124))
        rounded_up = summarize_availability(days_with_available(12502))
        self.assertEqual(rounded_up.availability_90d_pct, 3.126)
        self.assertIs(type(rounded_up.availability_90d_pct), float)

    def test_stats_are_immutable_and_have_exact_fields(self):
        stats = AvailabilityStats(60, 60, 100)
        self.assertEqual(
            [field.name for field in fields(stats)],
            [
                "availability_seconds_measured",
                "availability_seconds_available",
                "availability_90d_pct",
            ],
        )
        with self.assertRaises(FrozenInstanceError):
            stats.availability_seconds_measured = 0


class AvailabilityValidationTests(unittest.TestCase):
    def test_generation_window_keeps_day_89_and_rejects_day_90(self):
        generated_at = datetime(2026, 4, 1, 23, 59, tzinfo=UTC)
        cutoff = [
            {"date": "2026-01-02", "measured_seconds": 1, "available_seconds": 1}
        ]
        validate_availability_days(cutoff, generated_at=generated_at)

        too_old = [{**cutoff[0], "date": "2026-01-01"}]
        with self.assertRaises(ValueError):
            validate_availability_days(too_old, generated_at=generated_at)

    def test_rejects_wrong_shapes_types_dates_bounds_order_and_future(self):
        valid = [
            {"date": "2026-07-19", "measured_seconds": 60, "available_seconds": 59}
        ]
        too_many = [
            {
                "date": (date(2026, 1, 1) + timedelta(days=offset)).isoformat(),
                "measured_seconds": 1,
                "available_seconds": 1,
            }
            for offset in range(91)
        ]
        malformed = [
            tuple(valid),
            [[]],
            [{"date": "2026-07-19", "measured_seconds": 60}],
            [{**valid[0], "extra": 1}],
            [{**valid[0], "date": 20260719}],
            [{**valid[0], "date": "2026-02-30"}],
            [{**valid[0], "measured_seconds": True}],
            [{**valid[0], "available_seconds": False}],
            [{**valid[0], "measured_seconds": 86401}],
            [{**valid[0], "available_seconds": 61}],
            [valid[0], deepcopy(valid[0])],
            [
                {**valid[0], "date": "2026-07-20"},
                {**valid[0], "date": "2026-07-19"},
            ],
            too_many,
        ]
        for days in malformed:
            with self.subTest(days=days):
                with self.assertRaises((TypeError, ValueError)):
                    validate_availability_days(days, generated_at=at())

        future = [{**valid[0], "date": "2026-07-21"}]
        with self.assertRaises(ValueError):
            validate_availability_days(future, generated_at=at())

    def test_update_does_not_mutate_input_and_returns_fresh_objects(self):
        days = [
            {"date": "2026-07-20", "measured_seconds": 60, "available_seconds": 60}
        ]
        original = deepcopy(days)
        updated = update_availability_days(
            days, at(minute=1), at(minute=2), "outage"
        )
        self.assertEqual(days, original)
        self.assertIsNot(updated, days)
        self.assertIsNot(updated[0], days[0])
        updated[0]["available_seconds"] = 0
        self.assertEqual(days, original)


if __name__ == "__main__":
    unittest.main()
