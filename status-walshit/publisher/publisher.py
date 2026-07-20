"""Pure publication assembly for injected outcomes and bounded local output."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import errno
import fcntl
import json
import os
import secrets
import stat

from publisher.content import ContentEvent, ContentIssue, ContentLoadResult
from publisher.history import summarize_buckets, update_daily_buckets
from publisher.privacy import assert_public_safe, has_disallowed_narrative_location
from publisher.probes import validate_complete_cycle
from publisher.schema import (
    COMPONENTS,
    COMPONENT_DESCRIPTIONS,
    SCHEMA_VERSION,
    validate_history,
    validate_incidents,
    validate_summary,
)
from publisher.statemachine import ComponentState, initial_state, transition


_OVERALL_COPY = {
    "operational": (
        "Everything is on schedule.",
        "All monitored components are operating normally.",
    ),
    "degraded": (
        "Some services are running slowly.",
        "One or more components currently have degraded availability.",
    ),
    "outage": (
        "Service interruption detected.",
        "One or more components are currently interrupted.",
    ),
    "maintenance": (
        "Scheduled maintenance is in progress.",
        "One or more components are currently under planned care.",
    ),
    "no-data": (
        "Current status cannot be verified.",
        "Automated public checks do not currently provide enough data.",
    ),
}
_OVERALL_PRIORITY = {
    "operational": 0,
    "no-data": 1,
    "maintenance": 2,
    "degraded": 3,
    "outage": 4,
}
_GENERIC_TITLE = "Interruption detected"
_GENERIC_BODY = (
    "Automated public checks indicate an interruption. "
    "A narrated update has not yet been posted."
)
_AUTOMATIC_EVENT_ID_PREFIX = "auto-outage-"
_DEFAULT_REACHABLE_STREAKS = {
    "no-data": frozenset(
        {
            (0, 0, 0),
            (1, 0, 0),
            (0, 1, 0),
            (0, 2, 0),
            (0, 0, 1),
            (0, 0, 2),
        }
    ),
    "operational": frozenset(
        {
            (2, 0, 0),
            (0, 1, 0),
            (0, 2, 0),
            (0, 0, 1),
            (0, 0, 2),
        }
    ),
    "outage": frozenset({(0, 0, 3), (1, 0, 0), (0, 1, 0)}),
    "degraded": frozenset(
        {
            (0, 2, 0),
            (0, 3, 0),
            (1, 0, 0),
            (0, 0, 1),
            (0, 0, 2),
        }
    ),
}


def _utc_timestamp(value, field):
    if type(value) is not datetime:
        raise TypeError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    if value.microsecond != 0:
        raise ValueError(f"{field} must have whole-second precision")
    return value.astimezone(timezone.utc)


def _timestamp_text(value):
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_state_timestamp(value, field, *, optional=False):
    if optional and value is None:
        return None
    if type(value) is not str:
        raise TypeError(f"{field} must be a timestamp string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a canonical UTC timestamp") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.microsecond != 0
        or _timestamp_text(parsed.astimezone(timezone.utc)) != value
    ):
        raise ValueError(f"{field} must be a canonical UTC timestamp")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class PublisherComponentState:
    """Recursively immutable internal state for one public component."""

    component_id: str
    health: ComponentState
    auto_outage_started_at: str | None

    def to_document(self):
        return {
            "id": self.component_id,
            "health": {
                "base_state": self.health.base_state,
                "public_state": self.health.public_state,
                "success_streak": self.health.success_streak,
                "slow_streak": self.health.slow_streak,
                "failure_streak": self.health.failure_streak,
            },
            "auto_outage_started_at": self.auto_outage_started_at,
        }


@dataclass(frozen=True, slots=True)
class PublisherState:
    """Internal publisher state; never part of a public document."""

    generated_at: str | None
    components: tuple[PublisherComponentState, ...]

    def to_document(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": self.generated_at,
            "components": [component.to_document() for component in self.components],
        }


@dataclass(frozen=True, slots=True)
class PublishCycle:
    """Immutable result whose document properties return fresh projections."""

    _summary_json: str
    _history_json: str
    _incidents_json: str
    next_state: PublisherState
    issue_count: int

    @property
    def summary_document(self):
        return json.loads(self._summary_json)

    @property
    def history_document(self):
        return json.loads(self._history_json)

    @property
    def incidents_document(self):
        return json.loads(self._incidents_json)


def initial_publisher_state() -> PublisherState:
    """Return a canonical unverified state for every public component."""
    return PublisherState(
        generated_at=None,
        components=tuple(
            PublisherComponentState(component_id, initial_state(), None)
            for component_id in COMPONENTS
        ),
    )


def _exact_dict(value, keys, field):
    if type(value) is not dict:
        raise TypeError(f"{field} must be a dictionary")
    if set(value) != set(keys):
        raise ValueError(f"{field} has an invalid shape")


def _validate_default_reachable_health(health):
    """Reject states unreachable with the publisher's default 3/2 thresholds."""
    streaks = (
        health.success_streak,
        health.slow_streak,
        health.failure_streak,
    )
    if streaks not in _DEFAULT_REACHABLE_STREAKS.get(health.base_state, ()):
        raise ValueError(
            "component health is unreachable with default publisher thresholds"
        )


def restore_publisher_state(document) -> PublisherState:
    """Strictly restore one fresh JSON-shaped internal state projection."""
    _exact_dict(document, {"schema_version", "generated_at", "components"}, "state")
    if document["schema_version"] != SCHEMA_VERSION:
        raise ValueError("state has an unsupported schema version")
    generated = _parse_state_timestamp(
        document["generated_at"], "state.generated_at", optional=True
    )
    components = document["components"]
    if type(components) is not list:
        raise TypeError("state.components must be a concrete list")

    restored = []
    component_ids = []
    for index, component in enumerate(components):
        path = f"state.components[{index}]"
        _exact_dict(component, {"id", "health", "auto_outage_started_at"}, path)
        component_id = component["id"]
        if type(component_id) is not str:
            raise TypeError(f"{path}.id must be a string")
        component_ids.append(component_id)
        health_document = component["health"]
        _exact_dict(
            health_document,
            {
                "base_state",
                "public_state",
                "success_streak",
                "slow_streak",
                "failure_streak",
            },
            f"{path}.health",
        )
        health = ComponentState(
            base_state=health_document["base_state"],
            public_state=health_document["public_state"],
            success_streak=health_document["success_streak"],
            slow_streak=health_document["slow_streak"],
            failure_streak=health_document["failure_streak"],
        )
        transition(health, "error")
        _validate_default_reachable_health(health)
        outage_started = _parse_state_timestamp(
            component["auto_outage_started_at"],
            f"{path}.auto_outage_started_at",
            optional=True,
        )
        if (health.base_state == "outage") != (outage_started is not None):
            raise ValueError("outage start must exist exactly while base state is outage")
        if generated is None and outage_started is not None:
            raise ValueError("an outage start requires state.generated_at")
        if generated is not None and outage_started is not None and outage_started > generated:
            raise ValueError("outage start cannot follow state.generated_at")
        restored.append(
            PublisherComponentState(
                component_id,
                health,
                None if outage_started is None else _timestamp_text(outage_started),
            )
        )

    if len(component_ids) != len(set(component_ids)):
        raise ValueError("state.components contains duplicate ids")
    if set(component_ids) != set(COMPONENTS):
        raise ValueError("state.components must contain the exact component registry")
    if generated is None and any(
        component.health != initial_state() for component in restored
    ):
        raise ValueError("state without generated_at must be the initial state")
    by_component = {component.component_id: component for component in restored}
    return PublisherState(
        None if generated is None else _timestamp_text(generated),
        tuple(by_component[component_id] for component_id in COMPONENTS),
    )


def empty_history_document(generated_at):
    """Return a canonical empty five-component public history document."""
    generated = _utc_timestamp(generated_at, "generated_at")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _timestamp_text(generated),
        "components": [
            {"id": component_id, "name": name, "days": []}
            for component_id, name in COMPONENTS.items()
        ],
    }


def _canonical_json(document):
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _validated_public_json(summary, history, incidents):
    validate_summary(summary)
    validate_history(history)
    validate_incidents(incidents)
    generated_values = {
        summary["generated_at"], history["generated_at"], incidents["generated_at"]
    }
    if len(generated_values) != 1:
        raise ValueError("public documents must share one generation timestamp")
    serialized = tuple(_canonical_json(item) for item in (summary, history, incidents))
    for text in serialized:
        assert_public_safe(text, source="public status document")
    return serialized


def _content_documents(content_result):
    if type(content_result) is not ContentLoadResult:
        raise TypeError("content_result must be a ContentLoadResult")
    if any(
        type(items) is not tuple
        for items in (
            content_result.active_events,
            content_result.closed_events,
            content_result.issues,
        )
    ):
        raise TypeError("content result collections must be immutable tuples")
    if any(type(event) is not ContentEvent for event in content_result.active_events):
        raise TypeError("active content must contain ContentEvent values")
    if any(type(event) is not ContentEvent for event in content_result.closed_events):
        raise TypeError("closed content must contain ContentEvent values")
    if any(type(issue) is not ContentIssue for issue in content_result.issues):
        raise TypeError("content issues must contain ContentIssue values")
    for event in content_result.active_events + content_result.closed_events:
        narrative = (event.title, event.body) + tuple(
            update.body for update in event.updates
        )
        if any(has_disallowed_narrative_location(text) for text in narrative):
            raise ValueError("operator content contains a disallowed narrative location")
    active = content_result.active_event_documents()
    closed = content_result.closed_event_documents()
    for document in active + closed:
        assert_public_safe(
            _canonical_json(document), source="operator public event projection"
        )
    ids = [item["id"] for item in active + closed]
    if len(ids) != len(set(ids)):
        raise ValueError("operator content contains duplicate event ids")
    if any(event_id.startswith(_AUTOMATIC_EVENT_ID_PREFIX) for event_id in ids):
        raise ValueError("operator content uses a publisher-reserved event id")
    return active, closed


def _generic_event(component_id, started_at):
    event_id = f"auto-outage-{component_id}"
    return {
        "id": event_id,
        "kind": "incident",
        "status": "investigating",
        "title": _GENERIC_TITLE,
        "body": _GENERIC_BODY,
        "started_at": started_at,
        "updated_at": started_at,
        "affected_components": [component_id],
        "updates": [
            {"at": started_at, "status": "investigating", "body": _GENERIC_BODY}
        ],
    }


def _event_timestamp(document, field):
    return datetime.fromisoformat(document[field].replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def build_publish_cycle(
    prior_state,
    prior_history_document,
    content_result,
    probe_results,
    generated_at,
) -> PublishCycle:
    """Build one deterministic publication cycle without clocks or external I/O."""
    if type(prior_state) is not PublisherState:
        raise TypeError("prior_state must be a PublisherState")
    restored_prior = restore_publisher_state(prior_state.to_document())
    generated = _utc_timestamp(generated_at, "generated_at")
    generated_text = _timestamp_text(generated)
    if restored_prior.generated_at is not None:
        prior_generated = _parse_state_timestamp(
            restored_prior.generated_at, "prior_state.generated_at"
        )
        if prior_generated > generated:
            raise ValueError("prior state cannot be newer than this cycle")

    validate_history(prior_history_document)
    history = json.loads(_canonical_json(prior_history_document))
    history_generated = datetime.fromisoformat(
        history["generated_at"].replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    if history_generated > generated:
        raise ValueError("prior history cannot be newer than this cycle")
    if (
        restored_prior.generated_at is not None
        and prior_generated != history_generated
    ):
        raise ValueError("prior state and history must share one generation")
    for component in history["components"]:
        if any(
            datetime.fromisoformat(day["date"]).date() > history_generated.date()
            for day in component["days"]
        ):
            raise ValueError("prior history cannot contain future daily buckets")

    results = validate_complete_cycle(probe_results)
    active_events, closed_events = _content_documents(content_result)
    active_maintenance = {
        component_id
        for item in active_events
        if item["kind"] == "maintenance" and item["status"] == "in-progress"
        for component_id in item["affected_components"]
    }
    narrated_incidents = {
        component_id
        for item in active_events
        if item["kind"] == "incident"
        for component_id in item["affected_components"]
    }

    prior_by_id = {item.component_id: item for item in restored_prior.components}
    next_components = []
    public_states = {}
    for result in results:
        prior_component = prior_by_id[result.component_id]
        health = transition(
            prior_component.health,
            result.outcome,
            maintenance=result.component_id in active_maintenance,
        )
        if health.base_state == "outage":
            outage_started = (
                prior_component.auto_outage_started_at
                if prior_component.health.base_state == "outage"
                else generated_text
            )
        else:
            outage_started = None
        next_components.append(
            PublisherComponentState(result.component_id, health, outage_started)
        )
        public_states[result.component_id] = health.public_state

    for component in next_components:
        if (
            component.health.public_state == "outage"
            and component.component_id not in narrated_incidents
        ):
            generic = _generic_event(
                component.component_id, component.auto_outage_started_at
            )
            active_events.append(generic)
    active_events.sort(key=lambda item: (_event_timestamp(item, "started_at"), item["id"]))

    history_by_id = {item["id"]: item for item in history["components"]}
    history_components = []
    summary_components = []
    for component_id, name in COMPONENTS.items():
        days = update_daily_buckets(
            history_by_id[component_id]["days"],
            generated,
            public_states[component_id],
        )
        stats = summarize_buckets(days)
        history_components.append({"id": component_id, "name": name, "days": days})
        summary_components.append(
            {
                "id": component_id,
                "name": name,
                "description": COMPONENT_DESCRIPTIONS[component_id],
                "state": public_states[component_id],
                "uptime_90d_pct": stats.uptime_90d_pct,
                "history_days_available": stats.history_days_available,
            }
        )

    overall_state = max(public_states.values(), key=_OVERALL_PRIORITY.__getitem__)
    headline, body = _OVERALL_COPY[overall_state]
    summary = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_text,
        "overall": {"state": overall_state, "headline": headline, "body": body},
        "components": summary_components,
        "active_events": active_events,
    }
    new_history = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_text,
        "components": history_components,
    }

    closed_events.sort(key=lambda item: item["id"])
    closed_events.sort(
        key=lambda item: _event_timestamp(item, "resolved_at"), reverse=True
    )
    all_closed = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_text,
        "events": closed_events,
    }
    validate_incidents(all_closed)
    archive_start = generated - timedelta(days=90)
    retained_closed = [
        item
        for item in closed_events
        if _event_timestamp(item, "resolved_at") >= archive_start
    ]
    incidents = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_text,
        "events": retained_closed,
    }

    summary_json, history_json, incidents_json = _validated_public_json(
        summary, new_history, incidents
    )
    next_state = PublisherState(generated_text, tuple(next_components))
    restore_publisher_state(next_state.to_document())
    return PublishCycle(
        summary_json,
        history_json,
        incidents_json,
        next_state,
        len(content_result.issues),
    )


_PUBLIC_FILES = (
    ("history.json", "history_document"),
    ("incidents.json", "incidents_document"),
    ("summary.json", "summary_document"),
)


def _safe_output_directory(output_dir):
    try:
        supplied = os.fspath(output_dir)
    except TypeError as exc:
        raise TypeError("output_dir must be path-like") from exc
    if type(supplied) is not str:
        raise TypeError("output_dir must resolve to a text path")
    absolute = os.path.abspath(supplied)
    no_follow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    close_on_exec = getattr(os, "O_CLOEXEC", None)
    if no_follow is None or directory_flag is None or close_on_exec is None:
        raise ValueError("safe directory operations are unavailable")
    flags = os.O_RDONLY | directory_flag | no_follow | close_on_exec

    directory_fd = os.open(os.path.sep, flags)
    try:
        for part in (item for item in absolute.split(os.path.sep) if item):
            try:
                child_fd = os.open(part, flags, dir_fd=directory_fd)
            except FileNotFoundError as exc:
                raise NotADirectoryError(
                    "output directory must already exist"
                ) from exc
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise ValueError(
                        "output directory path must contain only real directories"
                    ) from exc
                raise
            os.close(directory_fd)
            directory_fd = child_fd
        return absolute, directory_fd
    except BaseException:
        try:
            os.close(directory_fd)
        except OSError:
            pass
        raise


def _entry_metadata(directory_fd, filename):
    try:
        return os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return None
        raise


def _validate_output_entries(directory_fd):
    for target, _attribute in _PUBLIC_FILES:
        target_metadata = _entry_metadata(directory_fd, target)
        if target_metadata is not None and not stat.S_ISREG(target_metadata.st_mode):
            raise ValueError("public output targets must be regular files or absent")


def _same_regular_entry(directory_fd, filename, identity):
    metadata = _entry_metadata(directory_fd, filename)
    return (
        metadata is not None
        and stat.S_ISREG(metadata.st_mode)
        and (metadata.st_dev, metadata.st_ino) == identity
    )


def _open_unique_temporary(directory_fd, target, open_flags):
    for _attempt in range(16):
        temporary = f".{target}.publisher.{secrets.token_hex(16)}.tmp"
        try:
            return temporary, os.open(
                temporary,
                open_flags,
                0o644,
                dir_fd=directory_fd,
            )
        except FileExistsError:
            continue
    raise FileExistsError("could not allocate a unique publisher temporary file")


def _validate_replacement_entry(directory_fd, target, temporary, identity):
    target_metadata = _entry_metadata(directory_fd, target)
    if target_metadata is not None and not stat.S_ISREG(target_metadata.st_mode):
        raise ValueError("public output target changed to an unsafe entry")
    if not _same_regular_entry(directory_fd, temporary, identity):
        raise ValueError("publisher temporary output changed unexpectedly")


def write_public_documents(output_dir, cycle, fault_hook=None):
    """Write three public files atomically, with summary replaced last.

    Each replacement is filesystem-atomic.  The three replacements are not a
    cross-file transaction; summary is the final freshness signal.  A later
    generation-pointer design can strengthen that runtime property.

    Runtime assumes this publisher is the only output-directory writer.  The
    directory lock coordinates cooperating publisher processes; Phase 4 must
    keep the directory non-writable by the web container and other users.
    """
    if type(cycle) is not PublishCycle:
        raise TypeError("cycle must be a PublishCycle")
    if fault_hook is not None and not callable(fault_hook):
        raise TypeError("fault_hook must be callable")

    summary = cycle.summary_document
    history = cycle.history_document
    incidents = cycle.incidents_document
    serialized = _validated_public_json(summary, history, incidents)
    text_by_attribute = dict(
        zip(
            ("summary_document", "history_document", "incidents_document"),
            serialized,
        )
    )
    bytes_by_attribute = {
        attribute: (text + "\n").encode("utf-8")
        for attribute, text in text_by_attribute.items()
    }

    _absolute, directory_fd = _safe_output_directory(output_dir)
    created = {}
    try:
        try:
            fcntl.flock(directory_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise BlockingIOError(
                    errno.EWOULDBLOCK, "public output directory is locked"
                ) from exc
            raise
        _validate_output_entries(directory_fd)
        open_flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        prepared = []
        for target, attribute in _PUBLIC_FILES:
            temporary, file_fd = _open_unique_temporary(
                directory_fd, target, open_flags
            )
            try:
                metadata = os.fstat(file_fd)
                created[temporary] = (metadata.st_dev, metadata.st_ino)
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError("publisher temporary output must be a regular file")
                os.fchmod(file_fd, 0o644)
                with os.fdopen(file_fd, "wb") as handle:
                    file_fd = -1
                    handle.write(bytes_by_attribute[attribute])
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                if file_fd >= 0:
                    os.close(file_fd)
            prepared.append((target, temporary))

        if fault_hook is not None:
            fault_hook()

        for target, temporary in prepared:
            _validate_replacement_entry(
                directory_fd, target, temporary, created[temporary]
            )
        for target, temporary in prepared:
            _validate_replacement_entry(
                directory_fd, target, temporary, created[temporary]
            )
            os.replace(
                temporary,
                target,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            del created[temporary]
        os.fsync(directory_fd)
    finally:
        for temporary, identity in tuple(created.items()):
            try:
                if _same_regular_entry(directory_fd, temporary, identity):
                    os.unlink(temporary, dir_fd=directory_fd)
            except OSError:
                pass
        os.close(directory_fd)
