"""Pure per-component anti-flap state transitions."""

from dataclasses import dataclass


PROBE_OUTCOMES = frozenset({"success", "slow", "failure", "error"})
BASE_STATES = frozenset({"operational", "degraded", "outage", "no-data"})


@dataclass(frozen=True, slots=True)
class ComponentState:
    """Serializable state carried between deterministic probe transitions."""

    base_state: str
    public_state: str
    success_streak: int
    slow_streak: int
    failure_streak: int


def _state(
    base_state: str,
    *,
    maintenance: bool = False,
    success: int = 0,
    slow: int = 0,
    failure: int = 0,
) -> ComponentState:
    return ComponentState(
        base_state=base_state,
        public_state="maintenance" if maintenance else base_state,
        success_streak=success,
        slow_streak=slow,
        failure_streak=failure,
    )


def initial_state() -> ComponentState:
    """Return the unverified state used before any probe samples arrive."""
    return _state("no-data")


def _validate_threshold(value, name: str) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def _validate_outcome(outcome) -> None:
    if not isinstance(outcome, str):
        raise TypeError("outcome must be a string")
    if outcome not in PROBE_OUTCOMES:
        raise ValueError(f"unsupported probe outcome: {outcome!r}")


def _validate_prior(prior) -> None:
    if type(prior) is not ComponentState:
        raise TypeError("prior must be a ComponentState")
    if prior.base_state not in BASE_STATES:
        raise ValueError(f"invalid base state: {prior.base_state!r}")
    if prior.public_state not in {prior.base_state, "maintenance"}:
        raise ValueError("public_state must match base_state or be maintenance")
    streaks = (prior.success_streak, prior.slow_streak, prior.failure_streak)
    if any(type(streak) is not int for streak in streaks):
        raise TypeError("streaks must be integers")
    if any(streak < 0 for streak in streaks):
        raise ValueError("streaks cannot be negative")
    if sum(streak > 0 for streak in streaks) > 1:
        raise ValueError("only one streak may be active")
    if prior.base_state != "no-data" and not any(streaks):
        raise ValueError("non-no-data states require an active streak")


def _advance_streak(streak: int, threshold: int) -> int:
    return min(streak + 1, threshold)


def transition(
    prior: ComponentState,
    outcome: str,
    *,
    maintenance: bool = False,
    trip_threshold: int = 3,
    recover_threshold: int = 2,
) -> ComponentState:
    """Advance one probe sample."""
    _validate_prior(prior)
    _validate_outcome(outcome)
    if type(maintenance) is not bool:
        raise TypeError("maintenance must be a boolean")
    _validate_threshold(trip_threshold, "trip_threshold")
    _validate_threshold(recover_threshold, "recover_threshold")
    if outcome == "error":
        return _state("no-data", maintenance=maintenance)
    if prior.base_state == "no-data" and outcome == "success":
        success_streak = _advance_streak(
            prior.success_streak,
            recover_threshold,
        )
        base_state = (
            "operational" if success_streak >= recover_threshold else "no-data"
        )
        return _state(
            base_state,
            maintenance=maintenance,
            success=success_streak,
        )
    if prior.base_state == "no-data" and outcome == "failure":
        failure_streak = _advance_streak(
            prior.failure_streak,
            trip_threshold,
        )
        base_state = "outage" if failure_streak >= trip_threshold else "no-data"
        return _state(
            base_state,
            maintenance=maintenance,
            failure=failure_streak,
        )
    if prior.base_state == "no-data" and outcome == "slow":
        slow_streak = _advance_streak(prior.slow_streak, trip_threshold)
        base_state = "degraded" if slow_streak >= trip_threshold else "no-data"
        return _state(
            base_state,
            maintenance=maintenance,
            slow=slow_streak,
        )
    if prior.base_state == "operational" and outcome == "failure":
        failure_streak = _advance_streak(
            prior.failure_streak,
            trip_threshold,
        )
        base_state = "outage" if failure_streak >= trip_threshold else "operational"
        return _state(
            base_state,
            maintenance=maintenance,
            failure=failure_streak,
        )
    if prior.base_state == "operational" and outcome == "slow":
        slow_streak = _advance_streak(prior.slow_streak, trip_threshold)
        base_state = "degraded" if slow_streak >= trip_threshold else "operational"
        return _state(
            base_state,
            maintenance=maintenance,
            slow=slow_streak,
        )
    if prior.base_state == "operational" and outcome == "success":
        return _state(
            "operational",
            maintenance=maintenance,
            success=_advance_streak(prior.success_streak, recover_threshold),
        )
    if prior.base_state in {"outage", "degraded"} and outcome == "success":
        success_streak = _advance_streak(
            prior.success_streak,
            recover_threshold,
        )
        base_state = (
            "operational"
            if success_streak >= recover_threshold
            else prior.base_state
        )
        return _state(
            base_state,
            maintenance=maintenance,
            success=success_streak,
        )
    if prior.base_state == "outage" and outcome == "slow":
        slow_streak = _advance_streak(prior.slow_streak, recover_threshold)
        base_state = "degraded" if slow_streak >= recover_threshold else "outage"
        return _state(
            base_state,
            maintenance=maintenance,
            slow=slow_streak,
        )
    if prior.base_state == "outage" and outcome == "failure":
        return _state(
            "outage",
            maintenance=maintenance,
            failure=_advance_streak(prior.failure_streak, trip_threshold),
        )
    if prior.base_state == "degraded" and outcome == "failure":
        failure_streak = _advance_streak(
            prior.failure_streak,
            trip_threshold,
        )
        base_state = "outage" if failure_streak >= trip_threshold else "degraded"
        return _state(
            base_state,
            maintenance=maintenance,
            failure=failure_streak,
        )
    if prior.base_state == "degraded" and outcome == "slow":
        return _state(
            "degraded",
            maintenance=maintenance,
            slow=_advance_streak(prior.slow_streak, trip_threshold),
        )
    raise AssertionError("validated state/outcome matrix is incomplete")
