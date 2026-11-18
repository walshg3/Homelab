"""Fail-closed loader for operator-authored incident and maintenance content."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tomllib

from publisher.privacy import (
    LeakError,
    assert_public_safe,
    has_disallowed_narrative_location,
)
from publisher.schema import CLOSED_EVENT_STATUSES, COMPONENTS, EVENT_STATUSES


MAX_CANDIDATE_FILES_PER_CATEGORY = 128
MAX_FILE_BYTES = 64 * 1024
MAX_UPDATES_PER_EVENT = 64
MAX_TITLE_LENGTH = 160
MAX_BODY_LENGTH = 2_000

_CATEGORY_KINDS = {"incidents": "incident", "maintenance": "maintenance"}
_SEVERITIES = {"low", "medium", "high"}
_EVENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
_SOURCE_ID_LINE_RE = re.compile(
    r'^[ \t]*id[ \t]*=[ \t]*"([a-z0-9][a-z0-9-]{2,63})"'
    r"[ \t]*(?:#[^\r\n]*)?\r?$"
)
_FILENAME_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*\.toml$")
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_SOURCE_TIMESTAMP_LINE_RE = re.compile(
    r"^[ \t]*(?P<key_quote>[\"']?)(?P<key>started_at|resolved_at|at)"
    r"(?P=key_quote)[ \t]*=[ \t]*(?P<value_quote>[\"']?)"
    r"\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}"
    r"(?:\.(?P<fraction>\d+))?(?:[Zz]|[+-]\d{2}:\d{2})"
    r"(?P=value_quote)[ \t]*(?:#[^\r\n]*)?\r?$"
)


class ContentValidationError(ValueError):
    """Internal marker for a rejected source file; its text is never published."""


@dataclass(frozen=True, slots=True)
class ContentIssue:
    relative_filename: str
    code: str


@dataclass(frozen=True, slots=True)
class ContentUpdate:
    at: str
    status: str
    body: str

    def to_document(self):
        """Return a fresh JSON-shaped public update."""
        return {"at": self.at, "status": self.status, "body": self.body}


@dataclass(frozen=True, slots=True)
class ContentEvent:
    id: str
    kind: str
    status: str
    title: str
    body: str
    started_at: str
    updated_at: str
    affected_components: tuple[str, ...]
    updates: tuple[ContentUpdate, ...]
    resolved_at: str | None = None

    def to_document(self):
        """Return a fresh JSON-shaped public event."""
        document = {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "title": self.title,
            "body": self.body,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "affected_components": list(self.affected_components),
            "updates": [update.to_document() for update in self.updates],
        }
        if self.resolved_at is not None:
            document["resolved_at"] = self.resolved_at
        return document


@dataclass(frozen=True, slots=True)
class ContentLoadResult:
    active_events: tuple[ContentEvent, ...]
    closed_events: tuple[ContentEvent, ...]
    issues: tuple[ContentIssue, ...]

    def active_event_documents(self):
        """Return fresh JSON-shaped active events for a summary document."""
        return [event.to_document() for event in self.active_events]

    def closed_event_documents(self):
        """Return fresh JSON-shaped closed events for an incidents document."""
        return [event.to_document() for event in self.closed_events]


@dataclass(frozen=True, slots=True)
class _LoadedEvent:
    relative_filename: str
    event: ContentEvent


@dataclass(frozen=True, slots=True)
class _CandidateResult:
    relative_filename: str
    recovered_id: str | None
    loaded: _LoadedEvent | None
    issue_code: str | None


def _require_exact_keys(value, expected, field):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ContentValidationError(f"invalid {field}")


def _require_text(value, field, *, narrative=False, max_length):
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ContentValidationError(f"invalid {field}")
    if narrative and has_disallowed_narrative_location(value):
        raise ContentValidationError(f"invalid {field}")
    return value


def _timestamp(value, field):
    if isinstance(value, bool):
        raise ContentValidationError(f"invalid {field}")
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and _RFC3339_RE.fullmatch(value):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContentValidationError(f"invalid {field}") from exc
    else:
        raise ContentValidationError(f"invalid {field}")
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.microsecond != 0:
        raise ContentValidationError(f"invalid {field}")
    return parsed.astimezone(timezone.utc)


def _public_timestamp(value):
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _is_escaped(text, index):
    backslashes = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def _multiline_state_after_line(line, state):
    """Track TOML multiline strings so narrative lines are never assignments."""
    index = 0
    while index < len(line):
        if state is not None:
            closing = line.find(state, index)
            while closing >= 0 and state == '"""' and _is_escaped(line, closing):
                closing = line.find(state, closing + 1)
            if closing < 0:
                return state
            state = None
            index = closing + 3
            continue

        if line[index] == "#":
            return None
        if line.startswith('"""', index) or line.startswith("'''", index):
            state = line[index : index + 3]
            index += 3
            continue
        if line[index] == '"':
            index += 1
            while index < len(line):
                if line[index] == "\\":
                    index += 2
                elif line[index] == '"':
                    index += 1
                    break
                else:
                    index += 1
            continue
        if line[index] == "'":
            closing = line.find("'", index + 1)
            index = len(line) if closing < 0 else closing + 1
            continue
        index += 1
    return state


def _validate_source_timestamp_assignments(raw, parsed):
    """Reject fractional source timestamps before TOML precision can be lost."""
    assignments = []
    multiline_state = None
    for line in raw.splitlines():
        if multiline_state is None:
            match = _SOURCE_TIMESTAMP_LINE_RE.fullmatch(line)
            if match is not None:
                assignments.append(match)
        multiline_state = _multiline_state_after_line(line, multiline_state)

    updates = parsed.get("updates") if isinstance(parsed, dict) else None
    expected = {
        "started_at": int(isinstance(parsed, dict) and "started_at" in parsed),
        "resolved_at": int(isinstance(parsed, dict) and "resolved_at" in parsed),
        "at": len(updates) if isinstance(updates, list) else 0,
    }
    observed = {key: 0 for key in expected}
    for match in assignments:
        observed[match.group("key")] += 1
        fraction = match.group("fraction")
        if fraction is not None and any(digit != "0" for digit in fraction):
            raise ContentValidationError("invalid timestamp precision")
    if observed != expected:
        raise ContentValidationError("invalid timestamp assignments")


def _parse_event(data, expected_kind):
    base_keys = {
        "id",
        "kind",
        "title",
        "body",
        "severity",
        "started_at",
        "affected_components",
        "updates",
    }
    if not isinstance(data, dict) or set(data) not in (base_keys, base_keys | {"resolved_at"}):
        raise ContentValidationError("invalid top-level fields")

    event_id = data["id"]
    if not isinstance(event_id, str) or not _EVENT_ID_RE.fullmatch(event_id):
        raise ContentValidationError("invalid id")
    if data["kind"] != expected_kind:
        raise ContentValidationError("invalid kind")
    if not isinstance(data["severity"], str) or data["severity"] not in _SEVERITIES:
        raise ContentValidationError("invalid severity")
    title = _require_text(
        data["title"], "title", narrative=True, max_length=MAX_TITLE_LENGTH
    )
    body = _require_text(
        data["body"], "body", narrative=True, max_length=MAX_BODY_LENGTH
    )
    started_at = _timestamp(data["started_at"], "started_at")

    affected = data["affected_components"]
    if not isinstance(affected, list) or not affected:
        raise ContentValidationError("invalid affected_components")
    if any(not isinstance(item, str) or item not in COMPONENTS for item in affected):
        raise ContentValidationError("invalid affected_components")
    if len(affected) != len(set(affected)):
        raise ContentValidationError("invalid affected_components")

    updates = data["updates"]
    if (
        not isinstance(updates, list)
        or not updates
        or len(updates) > MAX_UPDATES_PER_EVENT
    ):
        raise ContentValidationError("invalid updates")
    mapped_updates = []
    update_times = []
    phases = []
    allowed_phases = EVENT_STATUSES[expected_kind] | {
        CLOSED_EVENT_STATUSES[expected_kind]
    }
    for update in updates:
        _require_exact_keys(update, {"at", "phase", "body"}, "update fields")
        update_at = _timestamp(update["at"], "updates.at")
        if update_at < started_at:
            raise ContentValidationError("invalid updates.at")
        phase = update["phase"]
        if not isinstance(phase, str) or phase not in allowed_phases:
            raise ContentValidationError("invalid updates.phase")
        update_body = _require_text(
            update["body"],
            "updates.body",
            narrative=True,
            max_length=MAX_BODY_LENGTH,
        )
        update_times.append(update_at)
        phases.append(phase)
        mapped_updates.append(
            ContentUpdate(_public_timestamp(update_at), phase, update_body)
        )
    if update_times != sorted(set(update_times)):
        raise ContentValidationError("invalid update ordering")

    closed_phase = CLOSED_EVENT_STATUSES[expected_kind]
    closed = phases[-1] == closed_phase
    if any(phase == closed_phase for phase in phases[:-1]):
        raise ContentValidationError("invalid lifecycle")
    if closed != ("resolved_at" in data):
        raise ContentValidationError("invalid lifecycle")

    resolved_timestamp = None
    if closed:
        resolved_at = _timestamp(data["resolved_at"], "resolved_at")
        if resolved_at < started_at or resolved_at != update_times[-1]:
            raise ContentValidationError("invalid resolved_at")
        resolved_timestamp = _public_timestamp(resolved_at)

    event = ContentEvent(
        id=event_id,
        kind=expected_kind,
        status=phases[-1],
        title=title,
        body=body,
        started_at=_public_timestamp(started_at),
        updated_at=_public_timestamp(update_times[-1]),
        affected_components=tuple(affected),
        updates=tuple(mapped_updates),
        resolved_at=resolved_timestamp,
    )
    serialized = json.dumps(event.to_document(), sort_keys=True, separators=(",", ":"))
    try:
        assert_public_safe(serialized, source="mapped public event")
    except LeakError as exc:
        raise ContentValidationError("unsafe mapped event") from exc
    return event


def _safe_relative(category, filename):
    digest = hashlib.sha256(
        f"{category}\0{filename}".encode("utf-8", errors="replace")
    ).hexdigest()[:12]
    return f"{category}/file-{digest}.toml"


def _recover_id(raw, parsed):
    if isinstance(parsed, dict):
        parsed_id = parsed.get("id")
        if isinstance(parsed_id, str) and _EVENT_ID_RE.fullmatch(parsed_id):
            return parsed_id
    for line in raw.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _SOURCE_ID_LINE_RE.fullmatch(line)
        return match.group(1) if match is not None else None
    return None


def _read_candidate(directory_fd, filename):
    """Read at most MAX_FILE_BYTES without following a candidate symlink."""
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        return None, "file_unavailable"
    try:
        metadata = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
    except OSError:
        return None, "file_unavailable"
    if stat.S_ISLNK(metadata.st_mode):
        return None, None
    if not stat.S_ISREG(metadata.st_mode):
        return None, "file_unavailable"

    flags = os.O_RDONLY | no_follow
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        file_fd = os.open(filename, flags, dir_fd=directory_fd)
    except OSError:
        return None, "file_unavailable"
    try:
        try:
            opened_metadata = os.fstat(file_fd)
        except OSError:
            return None, "file_unavailable"
        if not stat.S_ISREG(opened_metadata.st_mode):
            return None, "file_unavailable"
        if opened_metadata.st_size > MAX_FILE_BYTES:
            return None, "file_too_large"

        chunks = []
        bytes_read = 0
        while bytes_read < MAX_FILE_BYTES:
            try:
                chunk = os.read(file_fd, min(8192, MAX_FILE_BYTES - bytes_read))
            except OSError:
                return None, "file_unavailable"
            if not chunk:
                break
            chunks.append(chunk)
            bytes_read += len(chunk)
        try:
            final_metadata = os.fstat(file_fd)
        except OSError:
            return None, "file_unavailable"
        if final_metadata.st_size > MAX_FILE_BYTES:
            return None, "file_too_large"
        if (
            final_metadata.st_size != opened_metadata.st_size
            or final_metadata.st_mtime_ns != opened_metadata.st_mtime_ns
        ):
            return None, "file_unavailable"
        try:
            return b"".join(chunks).decode("utf-8"), None
        except UnicodeError:
            return None, "file_unavailable"
    finally:
        try:
            os.close(file_fd)
        except OSError:
            pass


def _process_candidate(directory_fd, category, filename):
    relative = _safe_relative(category, filename)
    raw, read_code = _read_candidate(directory_fd, filename)
    if raw is None:
        if read_code is None:
            return None
        return _CandidateResult(relative, None, None, read_code)

    try:
        assert_public_safe(raw, source=relative)
        unsafe = False
    except LeakError:
        unsafe = True
    try:
        parsed = tomllib.loads(raw)
        toml_valid = True
    except tomllib.TOMLDecodeError:
        parsed = None
        toml_valid = False
    recovered_id = _recover_id(raw, parsed)

    if not _FILENAME_RE.fullmatch(filename):
        return _CandidateResult(relative, recovered_id, None, "invalid_filename")
    if unsafe:
        return _CandidateResult(relative, recovered_id, None, "unsafe_content")
    if not toml_valid:
        return _CandidateResult(relative, recovered_id, None, "invalid_toml")
    try:
        _validate_source_timestamp_assignments(raw, parsed)
        event = _parse_event(parsed, _CATEGORY_KINDS[category])
    except ContentValidationError:
        return _CandidateResult(relative, recovered_id, None, "invalid_content")
    return _CandidateResult(relative, recovered_id, _LoadedEvent(relative, event), None)


def _load_category(content_root, category):
    no_follow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if no_follow is None or directory_flag is None:
        return [], [ContentIssue(category, "category_unavailable")]
    flags = os.O_RDONLY | no_follow | directory_flag | getattr(os, "O_CLOEXEC", 0)
    try:
        directory_fd = os.open(content_root / category, flags)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return [], []
        return [], [ContentIssue(category, "category_unavailable")]

    try:
        try:
            candidate_names = []
            with os.scandir(directory_fd) as entries:
                for entry in entries:
                    if not entry.name.endswith(".toml"):
                        continue
                    candidate_names.append(entry.name)
                    if len(candidate_names) > MAX_CANDIDATE_FILES_PER_CATEGORY:
                        return [], [
                            ContentIssue(category, "candidate_limit_exceeded")
                        ]
        except OSError:
            return [], [ContentIssue(category, "category_unavailable")]

        candidates = []
        for filename in sorted(candidate_names):
            candidate = _process_candidate(directory_fd, category, filename)
            if candidate is not None:
                candidates.append(candidate)
        return candidates, []
    finally:
        try:
            os.close(directory_fd)
        except OSError:
            pass


def load_content(content_root: Path) -> ContentLoadResult:
    """Load bounded public-safe content while rejecting each invalid source."""
    content_root = Path(content_root)
    candidates = []
    issues = []
    for category in _CATEGORY_KINDS:
        category_candidates, category_issues = _load_category(content_root, category)
        candidates.extend(category_candidates)
        issues.extend(category_issues)

    id_counts = {}
    for candidate in candidates:
        if candidate.recovered_id is not None:
            id_counts[candidate.recovered_id] = id_counts.get(candidate.recovered_id, 0) + 1
        if candidate.issue_code is not None:
            issues.append(
                ContentIssue(candidate.relative_filename, candidate.issue_code)
            )
    duplicate_ids = {event_id for event_id, count in id_counts.items() if count > 1}
    for candidate in candidates:
        if candidate.recovered_id in duplicate_ids:
            issues.append(ContentIssue(candidate.relative_filename, "duplicate_id"))

    loaded = [
        candidate.loaded
        for candidate in candidates
        if candidate.loaded is not None and candidate.loaded.event.id not in duplicate_ids
    ]
    active = [item.event for item in loaded if item.event.resolved_at is None]
    active.sort(key=lambda event: (event.started_at, event.id))
    closed = [item.event for item in loaded if item.event.resolved_at is not None]
    closed.sort(key=lambda event: event.id)
    closed.sort(key=lambda event: event.resolved_at, reverse=True)
    issues.sort(key=lambda issue: (issue.relative_filename, issue.code))
    return ContentLoadResult(tuple(active), tuple(closed), tuple(issues))
