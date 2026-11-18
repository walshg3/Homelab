"""Pure worst-state daily history bucketing and measured-day statistics."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from publisher.schema import HISTORY_STATES


_SEVERITY = {
    "no-data": 0,
    "operational": 1,
    "maintenance": 2,
    "degraded": 3,
    "outage": 4,
}
MAX_HISTORY_DAYS = 90


@dataclass(frozen=True, slots=True)
class HistoryStats:
    """Non-duration statistics derived from public daily buckets."""

    history_days_available: int
    first_measured_date: str | None


def _validate_state(state, path):
    if type(state) is not str:
        raise TypeError(f"{path} must be a string")
    if state not in HISTORY_STATES:
        raise ValueError(f"{path} is not a supported history state")


def _validate_days(days):
    if type(days) is not list:
        raise TypeError("days must be a concrete list")
    if len(days) > 90:
        raise ValueError("days cannot contain more than 90 entries")
    previous_date = None
    for index, day in enumerate(days):
        path = f"days[{index}]"
        if type(day) is not dict:
            raise TypeError(f"{path} must be a dictionary")
        if set(day) != {"date", "state"}:
            raise ValueError(f"{path} must contain exactly date and state")
        day_date = day["date"]
        if type(day_date) is not str:
            raise TypeError(f"{path}.date must be a string")
        try:
            parsed_date = date.fromisoformat(day_date)
        except ValueError as exc:
            raise ValueError(f"{path}.date must be an ISO 8601 date") from exc
        if parsed_date.isoformat() != day_date:
            raise ValueError(f"{path}.date must be an ISO 8601 date")
        _validate_state(day["state"], f"{path}.state")
        if previous_date is not None and day_date <= previous_date:
            raise ValueError("days must have unique dates in ascending order")
        previous_date = day_date


def _utc_date(observed_at):
    if type(observed_at) is datetime:
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observed_at datetime must be timezone-aware")
        return observed_at.astimezone(timezone.utc).date()
    if type(observed_at) is date:
        return observed_at
    raise TypeError("observed_at must be a date or timezone-aware datetime")


def _window_bounds(window_end, retention_days):
    latest = _utc_date(window_end)
    return latest - timedelta(days=retention_days - 1), latest


def validate_history_window(document):
    """Validate daily buckets against a shape-validated document generation."""
    generated_at = document["generated_at"]
    parsed = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    earliest, latest = _window_bounds(parsed, MAX_HISTORY_DAYS)
    for component in document["components"]:
        _validate_days(component["days"])
        if any(
            not earliest <= date.fromisoformat(day["date"]) <= latest
            for day in component["days"]
        ):
            raise ValueError("history days must fall within the generation window")


def update_daily_buckets(
    days,
    observed_at,
    state,
    *,
    retention_days=MAX_HISTORY_DAYS,
    window_end=None,
) -> list[dict]:
    """Merge one public state into its selected UTC daily bucket."""
    _validate_days(days)
    _validate_state(state, "state")
    if type(retention_days) is not int:
        raise TypeError("retention_days must be an integer")
    if not 1 <= retention_days <= MAX_HISTORY_DAYS:
        raise ValueError("retention_days must be between 1 and 90")
    selected_date = _utc_date(observed_at).isoformat()
    merged = []
    found = False
    for day in days:
        merged_state = day["state"]
        if day["date"] == selected_date:
            merged_state = max((merged_state, state), key=_SEVERITY.__getitem__)
            found = True
        merged.append({"date": day["date"], "state": merged_state})
    if not found:
        merged.append({"date": selected_date, "state": state})
    ordered = sorted(merged, key=lambda day: day["date"])
    if window_end is not None:
        earliest, latest = _window_bounds(window_end, retention_days)
        ordered = [
            day
            for day in ordered
            if earliest <= date.fromisoformat(day["date"]) <= latest
        ]
    return ordered[-retention_days:]


def summarize_buckets(days) -> HistoryStats:
    """Summarize daily-strip coverage without claiming duration availability."""
    _validate_days(days)
    measured = [day for day in days if day["state"] != "no-data"]
    first_measured_date = measured[0]["date"] if measured else None
    return HistoryStats(len(measured), first_measured_date)
