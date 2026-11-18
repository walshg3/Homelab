"""Pure interval attribution and duration-weighted availability statistics."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN

from publisher.schema import PUBLIC_STATES


MAX_ATTRIBUTABLE_GAP_SECONDS = 120
MAX_AVAILABILITY_DAYS = 90
MAX_SECONDS_PER_DAY = 86_400
MAX_MEASURED_SECONDS = MAX_AVAILABILITY_DAYS * MAX_SECONDS_PER_DAY
_AVAILABLE_STATES = {"operational", "degraded", "maintenance"}
_PERCENT_QUANTUM = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class AvailabilityStats:
    """Duration totals and the normalized public percentage projection."""

    availability_seconds_measured: int
    availability_seconds_available: int
    availability_90d_pct: float | int | None


def _utc_timestamp(value, path):
    if type(value) is not datetime:
        raise TypeError(f"{path} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{path} must be timezone-aware")
    if value.microsecond != 0:
        raise ValueError(f"{path} must use whole-second precision")
    return value.astimezone(timezone.utc)


def _canonical_date(value, path):
    if type(value) is not str:
        raise TypeError(f"{path} must be a string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{path} must be a canonical date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{path} must be a canonical date")
    return parsed


def validate_availability_days(days, *, generated_at=None):
    """Strictly validate JSON-shaped internal daily duration records."""
    if type(days) is not list:
        raise TypeError("availability_days must be a concrete list")
    if len(days) > MAX_AVAILABILITY_DAYS:
        raise ValueError("availability_days cannot contain more than 90 entries")
    generated_date = None
    earliest_date = None
    if generated_at is not None:
        generated_date = _utc_timestamp(generated_at, "generated_at").date()
        earliest_date = generated_date - timedelta(days=MAX_AVAILABILITY_DAYS - 1)

    previous = None
    for index, item in enumerate(days):
        path = f"availability_days[{index}]"
        if type(item) is not dict:
            raise TypeError(f"{path} must be a dictionary")
        if set(item) != {"date", "measured_seconds", "available_seconds"}:
            raise ValueError(f"{path} has an invalid shape")
        selected_date = _canonical_date(item["date"], f"{path}.date")
        measured = item["measured_seconds"]
        available = item["available_seconds"]
        if type(measured) is not int:
            raise TypeError(f"{path}.measured_seconds must be an exact integer")
        if type(available) is not int:
            raise TypeError(f"{path}.available_seconds must be an exact integer")
        if not 0 <= available <= measured <= MAX_SECONDS_PER_DAY:
            raise ValueError(f"{path} has invalid duration bounds")
        if previous is not None and selected_date <= previous:
            raise ValueError("availability_days must have unique dates in ascending order")
        if generated_date is not None and not earliest_date <= selected_date <= generated_date:
            raise ValueError("availability_days must fall within the generation window")
        previous = selected_date


def _fresh_days(days):
    return [
        {
            "date": item["date"],
            "measured_seconds": item["measured_seconds"],
            "available_seconds": item["available_seconds"],
        }
        for item in days
    ]


def _retain_window(days, window_end, retention_days):
    earliest = window_end.date() - timedelta(days=retention_days - 1)
    latest = window_end.date()
    retained = [
        item
        for item in days
        if earliest <= date.fromisoformat(item["date"]) <= latest
    ][-retention_days:]
    return _fresh_days(retained)


def update_availability_days(
    days,
    interval_start,
    interval_end,
    prior_public_state,
    *,
    retention_days=MAX_AVAILABILITY_DAYS,
):
    """Attribute one prior-state interval into exact UTC calendar-day totals."""
    validate_availability_days(days)
    start = _utc_timestamp(interval_start, "interval_start")
    end = _utc_timestamp(interval_end, "interval_end")
    if end <= start:
        raise ValueError("interval_end must be strictly later than interval_start")
    if type(prior_public_state) is not str:
        raise TypeError("prior_public_state must be a string")
    if prior_public_state not in PUBLIC_STATES:
        raise ValueError("prior_public_state is unsupported")
    if type(retention_days) is not int:
        raise TypeError("retention_days must be an exact integer")
    if not 1 <= retention_days <= MAX_AVAILABILITY_DAYS:
        raise ValueError("retention_days must be between 1 and 90")

    elapsed = int((end - start).total_seconds())
    if elapsed > MAX_ATTRIBUTABLE_GAP_SECONDS or prior_public_state == "no-data":
        retained = _retain_window(days, end, retention_days)
        validate_availability_days(retained, generated_at=end)
        return retained

    by_date = {item["date"]: dict(item) for item in days}
    cursor = start
    while cursor < end:
        midnight = datetime.combine(
            cursor.date() + timedelta(days=1), time.min, tzinfo=timezone.utc
        )
        segment_end = min(end, midnight)
        seconds = int((segment_end - cursor).total_seconds())
        selected = cursor.date().isoformat()
        record = by_date.setdefault(
            selected,
            {"date": selected, "measured_seconds": 0, "available_seconds": 0},
        )
        record["measured_seconds"] += seconds
        if prior_public_state in _AVAILABLE_STATES:
            record["available_seconds"] += seconds
        cursor = segment_end

    ordered = sorted(by_date.values(), key=lambda item: item["date"])
    retained = _retain_window(ordered, end, retention_days)
    validate_availability_days(retained, generated_at=end)
    return retained


def summarize_availability(days):
    """Return duration-weighted 90-day availability with half-even rounding."""
    validate_availability_days(days)
    measured = sum(item["measured_seconds"] for item in days)
    available = sum(item["available_seconds"] for item in days)
    if measured > MAX_MEASURED_SECONDS:
        raise ValueError("availability measurement exceeds the 90-day bound")
    if measured == 0:
        percentage = None
    else:
        rounded = (
            Decimal(available) * Decimal(100) / Decimal(measured)
        ).quantize(_PERCENT_QUANTUM, rounding=ROUND_HALF_EVEN)
        percentage = int(rounded) if rounded == rounded.to_integral() else float(rounded)
    return AvailabilityStats(measured, available, percentage)
