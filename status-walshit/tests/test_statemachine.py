from dataclasses import FrozenInstanceError
import unittest

from publisher.statemachine import ComponentState, initial_state, transition


class StateMachineTests(unittest.TestCase):
    @staticmethod
    def advance(prior, outcome, count, **thresholds):
        state = prior
        for _ in range(count):
            state = transition(state, outcome, **thresholds)
        return state

    @staticmethod
    def operational_state():
        return transition(transition(initial_state(), "success"), "success")

    @classmethod
    def state_after(cls, outcome, count):
        return cls.advance(cls.operational_state(), outcome, count)

    def test_initial_state_has_no_data_and_zero_streaks(self):
        self.assertEqual(
            initial_state(),
            ComponentState(
                base_state="no-data",
                public_state="no-data",
                success_streak=0,
                slow_streak=0,
                failure_streak=0,
            ),
        )

    def test_no_data_requires_two_successes_to_become_operational(self):
        first = transition(initial_state(), "success")
        second = transition(first, "success")

        self.assertEqual(first.base_state, "no-data")
        self.assertEqual(first.public_state, "no-data")
        self.assertEqual(first.success_streak, 1)
        self.assertEqual(second.base_state, "operational")
        self.assertEqual(second.public_state, "operational")
        self.assertEqual(second.success_streak, 2)

    def test_operational_trips_on_third_consecutive_failure(self):
        first = transition(self.operational_state(), "failure")
        second = transition(first, "failure")
        third = transition(second, "failure")

        self.assertEqual(
            [first.public_state, second.public_state, third.public_state],
            ["operational", "operational", "outage"],
        )
        self.assertEqual(
            [first.failure_streak, second.failure_streak, third.failure_streak],
            [1, 2, 3],
        )

    def test_operational_degrades_on_third_consecutive_slow_sample(self):
        first = transition(self.operational_state(), "slow")
        second = transition(first, "slow")
        third = transition(second, "slow")

        self.assertEqual(
            [first.public_state, second.public_state, third.public_state],
            ["operational", "operational", "degraded"],
        )
        self.assertEqual(
            [first.slow_streak, second.slow_streak, third.slow_streak],
            [1, 2, 3],
        )

    def test_outage_and_degraded_require_two_successes_to_recover(self):
        for base_state, adverse in (("outage", "failure"), ("degraded", "slow")):
            with self.subTest(base_state=base_state):
                adverse_state = self.state_after(adverse, 3)
                first = transition(adverse_state, "success")
                second = transition(first, "success")

                self.assertEqual(first.public_state, base_state)
                self.assertEqual(first.success_streak, 1)
                self.assertEqual(second.public_state, "operational")
                self.assertEqual(second.success_streak, 2)

    def test_outage_improves_only_to_degraded_after_two_slow_samples(self):
        outage = self.state_after("failure", 3)
        first = transition(outage, "slow")
        second = transition(first, "slow")

        self.assertEqual(first.public_state, "outage")
        self.assertEqual(first.slow_streak, 1)
        self.assertEqual(second.public_state, "degraded")
        self.assertEqual(second.slow_streak, 2)

    def test_degraded_worsens_on_third_consecutive_failure(self):
        degraded = self.state_after("slow", 3)
        first = transition(degraded, "failure")
        second = transition(first, "failure")
        third = transition(second, "failure")

        self.assertEqual(
            [first.public_state, second.public_state, third.public_state],
            ["degraded", "degraded", "outage"],
        )
        self.assertEqual(third.failure_streak, 3)

    def test_different_outcomes_reset_other_streaks(self):
        failure = transition(self.operational_state(), "failure")
        slow = transition(failure, "slow")
        success = transition(slow, "success")

        self.assertEqual(
            (failure.success_streak, failure.slow_streak, failure.failure_streak),
            (0, 0, 1),
        )
        self.assertEqual(
            (slow.success_streak, slow.slow_streak, slow.failure_streak),
            (0, 1, 0),
        )
        self.assertEqual(
            (success.success_streak, success.slow_streak, success.failure_streak),
            (1, 0, 0),
        )

    def test_probe_error_immediately_becomes_no_data_and_resets_streaks(self):
        outage = self.state_after("failure", 3)

        result = transition(outage, "error")

        self.assertEqual(
            result,
            ComponentState(
                base_state="no-data",
                public_state="no-data",
                success_streak=0,
                slow_streak=0,
                failure_streak=0,
            ),
        )

    def test_maintenance_overrides_public_state_while_base_state_advances(self):
        maintained = self.operational_state()
        for _ in range(3):
            maintained = transition(maintained, "failure", maintenance=True)

        self.assertEqual(maintained.base_state, "outage")
        self.assertEqual(maintained.public_state, "maintenance")
        self.assertEqual(maintained.failure_streak, 3)

        error = transition(maintained, "error", maintenance=True)
        self.assertEqual(error.base_state, "no-data")
        self.assertEqual(error.public_state, "maintenance")
        self.assertEqual(
            (error.success_streak, error.slow_streak, error.failure_streak),
            (0, 0, 0),
        )

        revealed = transition(maintained, "success", maintenance=False)
        self.assertEqual(revealed.base_state, "outage")
        self.assertEqual(revealed.public_state, "outage")

    def test_thresholds_require_positive_non_boolean_integers(self):
        invalid_by_error = {
            TypeError: [True, False, 1.0, "2", None],
            ValueError: [0, -1],
        }
        for keyword in ("trip_threshold", "recover_threshold"):
            for error_type, values in invalid_by_error.items():
                for value in values:
                    with self.subTest(keyword=keyword, value=value):
                        with self.assertRaises(error_type):
                            transition(initial_state(), "success", **{keyword: value})

    def test_custom_thresholds_apply_to_every_state_change(self):
        operational = transition(initial_state(), "success", recover_threshold=1)
        degraded = transition(initial_state(), "slow", trip_threshold=1)
        outage = transition(initial_state(), "failure", trip_threshold=1)
        cases = (
            (
                "no-data to operational",
                initial_state(),
                "success",
                {"recover_threshold": 1},
                "operational",
            ),
            (
                "operational to outage",
                operational,
                "failure",
                {"trip_threshold": 1},
                "outage",
            ),
            (
                "operational to degraded",
                operational,
                "slow",
                {"trip_threshold": 1},
                "degraded",
            ),
            (
                "degraded to outage",
                degraded,
                "failure",
                {"trip_threshold": 1},
                "outage",
            ),
            (
                "outage to degraded",
                outage,
                "slow",
                {"recover_threshold": 1},
                "degraded",
            ),
            (
                "outage to operational",
                outage,
                "success",
                {"recover_threshold": 1},
                "operational",
            ),
            (
                "degraded to operational",
                degraded,
                "success",
                {"recover_threshold": 1},
                "operational",
            ),
        )
        for name, prior, outcome, thresholds, expected in cases:
            with self.subTest(transition=name):
                result = transition(prior, outcome, **thresholds)

                self.assertEqual(result.base_state, expected)

    def test_outcomes_must_be_exact_supported_strings(self):
        for outcome in (None, 1, True):
            with self.subTest(outcome=outcome):
                with self.assertRaises(TypeError):
                    transition(initial_state(), outcome)

        for outcome in ("Success", "unknown", ""):
            with self.subTest(outcome=outcome):
                with self.assertRaises(ValueError):
                    transition(initial_state(), outcome)

    def test_malformed_prior_state_and_maintenance_flag_fail_closed(self):
        with self.assertRaises(TypeError):
            transition({}, "error")

        invalid_value_states = [
            ComponentState("unknown", "unknown", 0, 0, 0),
            ComponentState("maintenance", "maintenance", 0, 0, 0),
            ComponentState("operational", "unknown", 0, 0, 0),
            ComponentState("outage", "operational", 0, 0, 0),
            ComponentState("operational", "operational", -1, 0, 0),
            ComponentState("operational", "operational", 1, 1, 0),
        ]
        for prior in invalid_value_states:
            with self.subTest(prior=prior):
                with self.assertRaises(ValueError):
                    transition(prior, "success")

        invalid_type_states = [
            ComponentState("operational", "operational", True, 0, 0),
            ComponentState("operational", "operational", 1.0, 0, 0),
        ]
        for prior in invalid_type_states:
            with self.subTest(prior=prior):
                with self.assertRaises(TypeError):
                    transition(prior, "success")

        for maintenance in (1, "yes", None):
            with self.subTest(maintenance=maintenance):
                with self.assertRaises(TypeError):
                    transition(initial_state(), "success", maintenance=maintenance)

    def test_restored_non_no_data_state_requires_an_active_streak(self):
        for base_state in ("operational", "degraded", "outage"):
            with self.subTest(base_state=base_state):
                prior = ComponentState(base_state, base_state, 0, 0, 0)

                with self.assertRaises(ValueError):
                    transition(prior, "success")

    def test_success_streak_saturates_at_recover_threshold(self):
        priors = (
            ("no-data", initial_state()),
            ("outage", self.state_after("failure", 3)),
            ("degraded", self.state_after("slow", 3)),
        )
        for name, prior in priors:
            with self.subTest(prior=name):
                result = self.advance(
                    prior,
                    "success",
                    5,
                    recover_threshold=2,
                )

                self.assertEqual(result.success_streak, 2)

    def test_failure_streak_saturates_at_trip_threshold(self):
        priors = (
            ("no-data", initial_state()),
            ("operational", self.operational_state()),
            ("degraded", self.state_after("slow", 3)),
        )
        for name, prior in priors:
            with self.subTest(prior=name):
                result = self.advance(prior, "failure", 5, trip_threshold=3)

                self.assertEqual(result.failure_streak, 3)

    def test_slow_streak_saturates_at_trip_threshold_when_degrading(self):
        priors = (
            ("no-data", initial_state()),
            ("operational", self.operational_state()),
            ("degraded", self.state_after("slow", 3)),
        )
        for name, prior in priors:
            with self.subTest(prior=name):
                result = self.advance(prior, "slow", 5, trip_threshold=3)

                self.assertEqual(result.slow_streak, 3)

    def test_outage_slow_streak_saturates_at_recover_threshold(self):
        outage = ComponentState("outage", "outage", 0, 4, 0)

        result = transition(outage, "slow", recover_threshold=2)

        self.assertEqual(result.base_state, "degraded")
        self.assertEqual(result.slow_streak, 2)

    def test_repeated_adverse_outcomes_preserve_saturated_adverse_state(self):
        outage = transition(self.state_after("failure", 3), "failure")
        degraded = transition(self.state_after("slow", 3), "slow")

        self.assertEqual(outage.public_state, "outage")
        self.assertEqual(outage.failure_streak, 3)
        self.assertEqual(degraded.public_state, "degraded")
        self.assertEqual(degraded.slow_streak, 3)

    def test_transition_does_not_mutate_input_and_states_are_immutable(self):
        prior = self.operational_state()
        snapshot = ComponentState(
            prior.base_state,
            prior.public_state,
            prior.success_streak,
            prior.slow_streak,
            prior.failure_streak,
        )

        result = transition(prior, "failure")

        self.assertEqual(prior, snapshot)
        self.assertIsNot(result, prior)
        with self.assertRaises(FrozenInstanceError):
            prior.base_state = "outage"


if __name__ == "__main__":
    unittest.main()
