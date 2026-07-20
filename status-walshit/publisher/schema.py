"""Executable, dependency-free contract for public status v1 JSON."""

from datetime import date, datetime
import re


SCHEMA_VERSION = "1"
COMPONENTS = {
    "watch": "Watch",
    "home": "Home base",
    "requests": "Requests",
    "audiobooks": "Audiobooks",
    "site": "Walshit.com",
}
PUBLIC_STATES = {"operational", "degraded", "outage", "maintenance"}
HISTORY_STATES = PUBLIC_STATES | {"no-data"}
EVENT_STATUSES = {
    "incident": {"investigating", "identified", "monitoring"},
    "maintenance": {"planned", "in-progress"},
}

_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EVENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")


class SchemaError(ValueError):
    """Raised when a public document does not match the v1 contract."""


def _require_object(value, path):
    if not isinstance(value, dict):
        raise SchemaError(f"{path} must be an object")


def _require_exact_keys(value, expected, path):
    _require_object(value, path)
    actual = set(value)
    if actual != set(expected):
        missing = sorted(set(expected) - actual)
        unknown = sorted(actual - set(expected))
        raise SchemaError(f"{path} has missing keys {missing} and unknown keys {unknown}")


def _require_string(value, path):
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{path} must be a non-empty string")


def _require_timestamp(value, path):
    if not isinstance(value, str) or not _RFC3339_RE.fullmatch(value):
        raise SchemaError(f"{path} must be an RFC3339 timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SchemaError(f"{path} must be an RFC3339 timestamp") from exc


def _require_date(value, path):
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise SchemaError(f"{path} must be an ISO 8601 date")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise SchemaError(f"{path} must be an ISO 8601 date") from exc


def _require_enum(value, allowed, path):
    if not isinstance(value, str) or value not in allowed:
        raise SchemaError(f"{path} must be one of {sorted(allowed)}")


def _validate_component(component, index):
    path = f"summary.components[{index}]"
    _require_exact_keys(
        component,
        {
            "id",
            "name",
            "description",
            "state",
            "uptime_90d_pct",
            "history_days_available",
        },
        path,
    )
    component_id = component["id"]
    _require_string(component_id, f"{path}.id")
    if component_id not in COMPONENTS:
        raise SchemaError(f"{path}.id is not a public component")
    if component["name"] != COMPONENTS[component_id]:
        raise SchemaError(f"{path}.name does not match {component_id!r}")
    _require_string(component["description"], f"{path}.description")
    _require_enum(component["state"], PUBLIC_STATES, f"{path}.state")

    percentage = component["uptime_90d_pct"]
    if percentage is not None and (
        isinstance(percentage, bool)
        or not isinstance(percentage, (int, float))
        or not 0 <= percentage <= 100
    ):
        raise SchemaError(f"{path}.uptime_90d_pct must be null or between 0 and 100")

    days = component["history_days_available"]
    if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= 90:
        raise SchemaError(f"{path}.history_days_available must be between 0 and 90")


def _validate_event(event, index):
    path = f"summary.active_events[{index}]"
    _require_exact_keys(
        event,
        {
            "id",
            "kind",
            "status",
            "title",
            "body",
            "started_at",
            "updated_at",
            "affected_components",
            "updates",
        },
        path,
    )
    event_id = event["id"]
    if not isinstance(event_id, str) or not _EVENT_ID_RE.fullmatch(event_id):
        raise SchemaError(f"{path}.id must be a lowercase public event id")
    _require_enum(event["kind"], set(EVENT_STATUSES), f"{path}.kind")
    allowed_statuses = EVENT_STATUSES[event["kind"]]
    _require_enum(event["status"], allowed_statuses, f"{path}.status")
    _require_string(event["title"], f"{path}.title")
    _require_string(event["body"], f"{path}.body")
    started_at = _require_timestamp(event["started_at"], f"{path}.started_at")
    updated_at = _require_timestamp(event["updated_at"], f"{path}.updated_at")
    if updated_at < started_at:
        raise SchemaError(f"{path}.updated_at cannot precede started_at")

    affected = event["affected_components"]
    if not isinstance(affected, list) or not affected:
        raise SchemaError(f"{path}.affected_components must be a non-empty array")
    if any(not isinstance(item, str) or item not in COMPONENTS for item in affected):
        raise SchemaError(f"{path}.affected_components contains an unknown component")
    if len(affected) != len(set(affected)):
        raise SchemaError(f"{path}.affected_components contains duplicate ids")

    updates = event["updates"]
    if not isinstance(updates, list) or not updates:
        raise SchemaError(f"{path}.updates must be a non-empty array")
    update_times = []
    for update_index, update in enumerate(updates):
        update_path = f"{path}.updates[{update_index}]"
        _require_exact_keys(update, {"at", "status", "body"}, update_path)
        update_at = _require_timestamp(update["at"], f"{update_path}.at")
        if not started_at <= update_at <= updated_at:
            raise SchemaError(f"{update_path}.at must fall within the event window")
        update_times.append(update_at)
        _require_enum(update["status"], allowed_statuses, f"{update_path}.status")
        _require_string(update["body"], f"{update_path}.body")
    if update_times != sorted(set(update_times)):
        raise SchemaError(f"{path}.updates must have unique times in ascending order")
    if update_times[-1] != updated_at:
        raise SchemaError(f"{path}.updated_at must match the latest update")


def validate_summary(document):
    """Validate a public v1 summary document."""
    _require_exact_keys(
        document,
        {"schema_version", "generated_at", "overall", "components", "active_events"},
        "summary",
    )
    if document["schema_version"] != SCHEMA_VERSION:
        raise SchemaError(f"summary.schema_version must be {SCHEMA_VERSION!r}")
    _require_timestamp(document["generated_at"], "summary.generated_at")

    overall = document["overall"]
    _require_exact_keys(overall, {"state", "headline", "body"}, "summary.overall")
    _require_enum(overall["state"], PUBLIC_STATES, "summary.overall.state")
    _require_string(overall["headline"], "summary.overall.headline")
    _require_string(overall["body"], "summary.overall.body")

    components = document["components"]
    if not isinstance(components, list):
        raise SchemaError("summary.components must be an array")
    for index, component in enumerate(components):
        _validate_component(component, index)
    component_ids = [component["id"] for component in components]
    if len(component_ids) != len(set(component_ids)):
        raise SchemaError("summary.components contains duplicate ids")
    if set(component_ids) != set(COMPONENTS):
        raise SchemaError("summary.components must contain every public component exactly once")

    active_events = document["active_events"]
    if not isinstance(active_events, list):
        raise SchemaError("summary.active_events must be an array")
    for index, event in enumerate(active_events):
        _validate_event(event, index)
    event_ids = [event["id"] for event in active_events]
    if len(event_ids) != len(set(event_ids)):
        raise SchemaError("summary.active_events contains duplicate ids")


def validate_history(document):
    """Validate a public v1 history document."""
    _require_exact_keys(
        document, {"schema_version", "generated_at", "components"}, "history"
    )
    if document["schema_version"] != SCHEMA_VERSION:
        raise SchemaError(f"history.schema_version must be {SCHEMA_VERSION!r}")
    _require_timestamp(document["generated_at"], "history.generated_at")

    components = document["components"]
    if not isinstance(components, list):
        raise SchemaError("history.components must be an array")

    component_ids = []
    for component_index, component in enumerate(components):
        path = f"history.components[{component_index}]"
        _require_exact_keys(component, {"id", "name", "days"}, path)
        component_id = component["id"]
        _require_string(component_id, f"{path}.id")
        if component_id not in COMPONENTS:
            raise SchemaError(f"{path}.id is not a public component")
        if component["name"] != COMPONENTS[component_id]:
            raise SchemaError(f"{path}.name does not match {component_id!r}")
        component_ids.append(component_id)

        days = component["days"]
        if not isinstance(days, list):
            raise SchemaError(f"{path}.days must be an array")
        if len(days) > 90:
            raise SchemaError(f"{path}.days cannot contain more than 90 entries")

        dates = []
        for day_index, day in enumerate(days):
            day_path = f"{path}.days[{day_index}]"
            _require_exact_keys(day, {"date", "state"}, day_path)
            _require_date(day["date"], f"{day_path}.date")
            _require_enum(day["state"], HISTORY_STATES, f"{day_path}.state")
            dates.append(day["date"])
        if dates != sorted(set(dates)):
            raise SchemaError(f"{path}.days must have unique dates in ascending order")

    if len(component_ids) != len(set(component_ids)):
        raise SchemaError("history.components contains duplicate ids")
    if set(component_ids) != set(COMPONENTS):
        raise SchemaError("history.components must contain every public component exactly once")
