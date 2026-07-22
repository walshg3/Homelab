#!/usr/bin/env python3
"""Independent, privacy-safe status soak watchdog using only the stdlib."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
from io import StringIO
import json
import math
import os
import re
import selectors
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from publisher import schema as public_schema


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) StatusSoakWatchdog/1.0"
)
HTTP_TIMEOUT_SECONDS = 10
DEFAULT_MAX_AGE_SECONDS = 180
DEFAULT_MAX_BODY_BYTES = 256 * 1024
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_RECOVERY_THRESHOLD = 2
MAX_CONFIG_BYTES = 16 * 1024
MAX_SECRET_BYTES = 8 * 1024
MAX_STATE_BYTES = 16 * 1024
MAX_BODY_BYTES = 1024 * 1024
MAX_DOCKER_OUTPUT_BYTES = 64 * 1024
MAX_CONTAINERS = 32
PUBLIC_COMPONENT_IDS = frozenset(public_schema.COMPONENTS)
PUBLIC_STATES = frozenset(public_schema.PUBLIC_STATES)
_HEALTHY_PUBLIC_STATES = frozenset(("operational", "maintenance"))

_CONFIG_KEYS = frozenset(
    (
        "schema_version",
        "summary_url",
        "webhook_env_file",
        "state_file",
        "lock_file",
        "max_age_seconds",
        "max_body_bytes",
        "failure_threshold",
        "recovery_threshold",
        "ssh_alias",
        "container_names",
    )
)
_REQUIRED_CONFIG_KEYS = frozenset(
    (
        "schema_version",
        "summary_url",
        "webhook_env_file",
        "state_file",
        "lock_file",
    )
)
_SSH_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_DISCORD_PATH_RE = re.compile(
    r"^/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9._-]{1,512}$"
)
_DISCORD_HOSTS = frozenset(
    (
        "discord.com",
        "www.discord.com",
        "discordapp.com",
        "canary.discord.com",
        "ptb.discord.com",
    )
)
_REASON_CODES = frozenset(
    (
        "ok",
        "summary_transport_error",
        "summary_http_status",
        "summary_body_oversized",
        "summary_json_invalid",
        "summary_schema_version",
        "summary_generated_at_invalid",
        "summary_generation_future",
        "summary_generation_stale",
        "summary_components_invalid",
        "summary_public_state_invalid",
        "summary_schema_invalid",
        "summary_public_state_unhealthy",
        "docker_exec_error",
        "docker_output_oversized",
        "docker_output_invalid",
        "docker_container_mismatch",
        "docker_container_not_running",
        "docker_container_unhealthy",
    )
)
_STATE_KEYS = frozenset(
    (
        "schema_version",
        "window_start",
        "last_run",
        "total_runs",
        "healthy_runs",
        "failed_runs",
        "outage_transitions",
        "recovery_transitions",
        "maximum_generation_age_seconds",
        "last_reason_code",
        "alert_state",
        "consecutive_failures",
        "consecutive_successes",
    )
)
_REPORT_KEYS = (
    "window_start",
    "last_run",
    "total_runs",
    "healthy_runs",
    "failed_runs",
    "outage_transitions",
    "recovery_transitions",
    "maximum_generation_age_seconds",
    "last_reason_code",
    "alert_state",
)
_DOCKER_FORMAT = (
    '{"name":{{json .Name}},"running":{{json .State.Running}},'
    '"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}}}'
)
_DOCKER_FORMAT_REMOTE_ARGUMENT = "'" + _DOCKER_FORMAT + "'"


class LocalFailure(Exception):
    """A local failure represented only by its fixed public-safe code."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class Config:
    summary_url: str
    webhook_env_file: str
    state_file: str
    lock_file: str
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD
    recovery_threshold: int = DEFAULT_RECOVERY_THRESHOLD
    ssh_alias: str | None = None
    container_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProbeResult:
    healthy: bool
    reason_code: str
    generation_age_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class _DockerCommandResult:
    returncode: int | None
    stdout: bytes
    output_oversized: bool = False
    timed_out: bool = False


def _fail(code):
    raise LocalFailure(code) from None


def _path_text(path, code):
    try:
        value = os.fspath(path)
    except TypeError:
        _fail(code)
    if type(value) is not str or not value or "\x00" in value:
        _fail(code)
    return value


def _restricted_metadata(metadata, maximum, code):
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or metadata.st_uid != os.geteuid()
        or metadata.st_size > maximum
    ):
        _fail(code)


def _metadata_identity(metadata):
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_restricted_file(path, maximum, code):
    supplied = _path_text(path, code)
    file_fd = -1
    try:
        entry = os.lstat(supplied)
        _restricted_metadata(entry, maximum, code)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        file_fd = os.open(supplied, flags)
        opened = os.fstat(file_fd)
        _restricted_metadata(opened, maximum, code)
        if (entry.st_dev, entry.st_ino) != (opened.st_dev, opened.st_ino):
            _fail(code)
        chunks = []
        size = 0
        while size <= maximum:
            chunk = os.read(file_fd, min(4096, maximum + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        if size > maximum:
            _fail(code)
        final = os.fstat(file_fd)
        _restricted_metadata(final, maximum, code)
        if _metadata_identity(opened) != _metadata_identity(final):
            _fail(code)
        return b"".join(chunks)
    except LocalFailure:
        raise
    except Exception:
        _fail(code)
    finally:
        if file_fd >= 0:
            try:
                os.close(file_fd)
            except OSError:
                pass


def _json_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(_value):
    raise ValueError("non-finite JSON number")


def _decode_json(raw):
    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_json_no_duplicates,
        parse_constant=_reject_json_constant,
    )


def _positive_integer(value, *, maximum, code):
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _absolute_path(value, code):
    if type(value) is not str or not value or "\x00" in value:
        _fail(code)
    if not os.path.isabs(value):
        _fail(code)
    return os.path.normpath(value)


def _summary_url(value):
    if type(value) is not str or not value or len(value) > 4096:
        _fail("watchdog-config-invalid")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        _fail("watchdog-config-invalid")
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port is not None and not 1 <= port <= 65535
        or any(ord(character) < 33 for character in value)
    ):
        _fail("watchdog-config-invalid")
    return value


def load_config(path):
    """Load a strict, restricted local watchdog configuration."""
    try:
        document = _decode_json(
            _read_restricted_file(path, MAX_CONFIG_BYTES, "watchdog-config-invalid")
        )
    except LocalFailure:
        raise
    except Exception:
        _fail("watchdog-config-invalid")
    if (
        type(document) is not dict
        or not _REQUIRED_CONFIG_KEYS <= set(document)
        or not set(document) <= _CONFIG_KEYS
        or document["schema_version"] != "1"
    ):
        _fail("watchdog-config-invalid")

    summary_url = _summary_url(document["summary_url"])
    webhook_env_file = _absolute_path(
        document["webhook_env_file"], "watchdog-config-invalid"
    )
    state_file = _absolute_path(document["state_file"], "watchdog-config-invalid")
    lock_file = _absolute_path(document["lock_file"], "watchdog-config-invalid")
    if (
        os.path.dirname(state_file) != os.path.dirname(lock_file)
        or state_file == lock_file
    ):
        _fail("watchdog-config-invalid")

    max_age = _positive_integer(
        document.get("max_age_seconds", DEFAULT_MAX_AGE_SECONDS),
        maximum=86400,
        code="watchdog-config-invalid",
    )
    max_body = _positive_integer(
        document.get("max_body_bytes", DEFAULT_MAX_BODY_BYTES),
        maximum=MAX_BODY_BYTES,
        code="watchdog-config-invalid",
    )
    failures = _positive_integer(
        document.get("failure_threshold", DEFAULT_FAILURE_THRESHOLD),
        maximum=100,
        code="watchdog-config-invalid",
    )
    recoveries = _positive_integer(
        document.get("recovery_threshold", DEFAULT_RECOVERY_THRESHOLD),
        maximum=100,
        code="watchdog-config-invalid",
    )

    has_alias = "ssh_alias" in document
    has_containers = "container_names" in document
    if has_alias != has_containers:
        _fail("watchdog-config-invalid")
    alias = None
    names = ()
    if has_alias:
        alias = document["ssh_alias"]
        configured_names = document["container_names"]
        if (
            type(alias) is not str
            or _SSH_ALIAS_RE.fullmatch(alias) is None
            or type(configured_names) is not list
            or not configured_names
            or len(configured_names) > MAX_CONTAINERS
            or any(
                type(name) is not str or _CONTAINER_NAME_RE.fullmatch(name) is None
                for name in configured_names
            )
            or len(configured_names) != len(set(configured_names))
        ):
            _fail("watchdog-config-invalid")
        names = tuple(configured_names)

    return Config(
        summary_url=summary_url,
        webhook_env_file=webhook_env_file,
        state_file=state_file,
        lock_file=lock_file,
        max_age_seconds=max_age,
        max_body_bytes=max_body,
        failure_threshold=failures,
        recovery_threshold=recoveries,
        ssh_alias=alias,
        container_names=names,
    )


def _parse_timestamp(value):
    if type(value) is not str or _RFC3339_RE.fullmatch(value) is None:
        raise ValueError("timestamp rejected")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp rejected")
    return parsed.astimezone(timezone.utc)


def _now_utc(now):
    value = now()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail("watchdog-local-failure")
    return value.astimezone(timezone.utc)


def _public_timestamp(value):
    return value.replace(microsecond=0).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def probe_summary(config, current_time, *, urlopen=urllib.request.urlopen):
    """Probe and fully validate the public v2 summary without retaining it."""
    request = urllib.request.Request(
        config.summary_url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            if type(response.status) is not int or response.status != 200:
                return ProbeResult(False, "summary_http_status")
            raw = response.read(config.max_body_bytes + 1)
    except urllib.error.HTTPError:
        return ProbeResult(False, "summary_http_status")
    except Exception:
        return ProbeResult(False, "summary_transport_error")
    if type(raw) is not bytes:
        return ProbeResult(False, "summary_transport_error")
    if len(raw) > config.max_body_bytes:
        return ProbeResult(False, "summary_body_oversized")
    try:
        document = _decode_json(raw)
    except Exception:
        return ProbeResult(False, "summary_json_invalid")
    try:
        public_schema.validate_summary(document)
    except public_schema.SchemaError:
        return ProbeResult(False, "summary_schema_invalid")
    try:
        generated_at = _parse_timestamp(document["generated_at"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return ProbeResult(False, "summary_schema_invalid")
    age = (current_time - generated_at).total_seconds()
    observed_age = max(0, math.ceil(age))
    if age < 0:
        return ProbeResult(False, "summary_generation_future", observed_age)
    if age > config.max_age_seconds:
        return ProbeResult(False, "summary_generation_stale", observed_age)
    if document["overall"]["state"] not in _HEALTHY_PUBLIC_STATES or any(
        component["state"] not in _HEALTHY_PUBLIC_STATES
        for component in document["components"]
    ):
        return ProbeResult(False, "summary_public_state_unhealthy", observed_age)
    return ProbeResult(True, "ok", observed_age)


def docker_inspect_argv(ssh_alias, container_names):
    """Return the sole fixed SSH/Docker command shape used by this watchdog."""
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ConnectionAttempts=1",
        "--",
        ssh_alias,
        "docker",
        "inspect",
        "--type",
        "container",
        "--format",
        _DOCKER_FORMAT_REMOTE_ARGUMENT,
        *container_names,
    ]


def _terminate_and_reap(process):
    try:
        if process.poll() is None:
            process.kill()
    except Exception:
        pass
    try:
        process.wait()
    except Exception:
        pass


def _run_docker_bounded(argv):
    """Run fixed argv while retaining no more than the configured byte bound."""
    process = None
    output_stream = None
    selector = None
    output = bytearray()
    deadline = time.monotonic() + HTTP_TIMEOUT_SECONDS
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        output_stream = process.stdout
        if output_stream is None:
            raise OSError("stdout pipe unavailable")
        descriptor = output_stream.fileno()
        os.set_blocking(descriptor, False)
        selector = selectors.DefaultSelector()
        selector.register(descriptor, selectors.EVENT_READ)
        reached_eof = False
        while not reached_eof:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_and_reap(process)
                return _DockerCommandResult(None, bytes(output), timed_out=True)
            events = selector.select(remaining)
            if not events:
                if process.poll() is None:
                    _terminate_and_reap(process)
                    return _DockerCommandResult(None, bytes(output), timed_out=True)
                events = ((None, None),)
            for _key, _mask in events:
                try:
                    chunk = os.read(
                        descriptor,
                        min(4096, MAX_DOCKER_OUTPUT_BYTES + 1 - len(output)),
                    )
                except BlockingIOError:
                    continue
                if not chunk:
                    reached_eof = True
                    break
                output.extend(chunk)
                if len(output) > MAX_DOCKER_OUTPUT_BYTES:
                    _terminate_and_reap(process)
                    return _DockerCommandResult(
                        process.returncode,
                        bytes(output),
                        output_oversized=True,
                    )
        remaining = max(0, deadline - time.monotonic())
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _terminate_and_reap(process)
            return _DockerCommandResult(None, bytes(output), timed_out=True)
        return _DockerCommandResult(returncode, bytes(output))
    finally:
        if selector is not None:
            try:
                selector.close()
            except Exception:
                pass
        if output_stream is not None:
            try:
                output_stream.close()
            except Exception:
                pass
        if process is not None and process.poll() is None:
            _terminate_and_reap(process)


def probe_docker(config, *, runner=None):
    """Check only exact allowlisted names using bounded machine-readable output."""
    if not config.container_names:
        return ProbeResult(True, "ok")
    try:
        argv = docker_inspect_argv(config.ssh_alias, config.container_names)
        if runner is None:
            completed = _run_docker_bounded(argv)
        else:
            completed = runner(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=HTTP_TIMEOUT_SECONDS,
                check=False,
            )
    except Exception:
        return ProbeResult(False, "docker_exec_error")
    try:
        if getattr(completed, "output_oversized", False):
            return ProbeResult(False, "docker_output_oversized")
        if getattr(completed, "timed_out", False):
            return ProbeResult(False, "docker_exec_error")
        returncode = completed.returncode
        output = completed.stdout
        if returncode != 0 or type(output) is not bytes:
            return ProbeResult(False, "docker_exec_error")
        if len(output) > MAX_DOCKER_OUTPUT_BYTES:
            return ProbeResult(False, "docker_output_oversized")
        lines = output.splitlines()
        documents = [_decode_json(line) for line in lines]
    except Exception:
        return ProbeResult(False, "docker_output_invalid")
    if len(documents) != len(config.container_names):
        return ProbeResult(False, "docker_container_mismatch")
    mapped = {}
    for document in documents:
        if (
            type(document) is not dict
            or set(document) != {"name", "running", "health"}
            or type(document["name"]) is not str
            or type(document["running"]) is not bool
            or document["health"] is not None
            and type(document["health"]) is not str
            or document["name"] in mapped
        ):
            return ProbeResult(False, "docker_output_invalid")
        mapped[document["name"]] = document
    expected = {f"/{name}" for name in config.container_names}
    if set(mapped) != expected:
        return ProbeResult(False, "docker_container_mismatch")
    if any(not document["running"] for document in mapped.values()):
        return ProbeResult(False, "docker_container_not_running")
    if any(document["health"] != "healthy" for document in mapped.values()):
        return ProbeResult(False, "docker_container_unhealthy")
    return ProbeResult(True, "ok")


def _validate_runtime_parent(path):
    supplied = _path_text(path, "watchdog-state-invalid")
    if not os.path.isabs(supplied):
        _fail("watchdog-state-invalid")
    parent = os.path.dirname(supplied)
    directory_fd = -1
    try:
        metadata = os.lstat(parent)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.geteuid()
        ):
            _fail("watchdog-state-invalid")
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        directory_fd = os.open(parent, flags)
        opened = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or stat.S_IMODE(opened.st_mode) != 0o700
            or opened.st_uid != os.geteuid()
            or (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            _fail("watchdog-state-invalid")
    except LocalFailure:
        raise
    except Exception:
        _fail("watchdog-state-invalid")
    finally:
        if directory_fd >= 0:
            try:
                os.close(directory_fd)
            except OSError:
                pass


def _prepare_runtime_directory(config):
    _validate_runtime_parent(config.state_file)


class _HeldLock:
    def __init__(self, path):
        self.path = path
        self.file_fd = -1

    def __enter__(self):
        try:
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            self.file_fd = os.open(self.path, flags, 0o600)
            metadata = os.fstat(self.file_fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
                or metadata.st_uid != os.geteuid()
            ):
                _fail("watchdog-lock-invalid")
            fcntl.flock(self.file_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self
        except BlockingIOError:
            self._close()
            _fail("watchdog-lock-busy")
        except LocalFailure:
            self._close()
            raise
        except Exception:
            self._close()
            _fail("watchdog-lock-invalid")

    def _close(self):
        if self.file_fd >= 0:
            try:
                os.close(self.file_fd)
            except OSError:
                pass
            self.file_fd = -1

    def __exit__(self, *_args):
        self._close()
        return False


def _nonnegative_integer(value):
    return type(value) is int and value >= 0


def _validate_state(document):
    if (
        type(document) is not dict
        or set(document) != _STATE_KEYS
        or document["schema_version"] != "1"
        or document["alert_state"] not in {"normal", "outage"}
        or document["last_reason_code"] not in _REASON_CODES
    ):
        _fail("watchdog-state-invalid")
    integer_keys = (
        "total_runs",
        "healthy_runs",
        "failed_runs",
        "outage_transitions",
        "recovery_transitions",
        "consecutive_failures",
        "consecutive_successes",
    )
    if any(not _nonnegative_integer(document[key]) for key in integer_keys):
        _fail("watchdog-state-invalid")
    maximum_age = document["maximum_generation_age_seconds"]
    if maximum_age is not None and not _nonnegative_integer(maximum_age):
        _fail("watchdog-state-invalid")
    try:
        window_start = _parse_timestamp(document["window_start"])
        last_run = _parse_timestamp(document["last_run"])
    except Exception:
        _fail("watchdog-state-invalid")
    if (
        document["total_runs"] < 1
        or _public_timestamp(window_start) != document["window_start"]
        or _public_timestamp(last_run) != document["last_run"]
        or window_start > last_run
        or document["total_runs"]
        != document["healthy_runs"] + document["failed_runs"]
        or document["consecutive_failures"] > document["failed_runs"]
        or document["consecutive_successes"] > document["healthy_runs"]
        or (document["last_reason_code"] == "ok")
        != (document["consecutive_successes"] > 0)
        or document["healthy_runs"] > 0
        and maximum_age is None
        or document["outage_transitions"] < document["recovery_transitions"]
        or document["outage_transitions"]
        > document["recovery_transitions"] + 1
        or (document["alert_state"] == "normal")
        != (document["outage_transitions"] == document["recovery_transitions"])
        or document["consecutive_failures"]
        and document["consecutive_successes"]
    ):
        _fail("watchdog-state-invalid")
    return document


def _load_state(path, *, absent_allowed):
    try:
        os.lstat(path)
    except FileNotFoundError:
        if absent_allowed:
            return None
        _fail("watchdog-state-invalid")
    except Exception:
        _fail("watchdog-state-invalid")
    try:
        document = _decode_json(
            _read_restricted_file(path, MAX_STATE_BYTES, "watchdog-state-invalid")
        )
    except LocalFailure:
        raise
    except Exception:
        _fail("watchdog-state-invalid")
    return _validate_state(document)


def _initial_state(timestamp):
    return {
        "schema_version": "1",
        "window_start": timestamp,
        "last_run": timestamp,
        "total_runs": 0,
        "healthy_runs": 0,
        "failed_runs": 0,
        "outage_transitions": 0,
        "recovery_transitions": 0,
        "maximum_generation_age_seconds": None,
        "last_reason_code": "ok",
        "alert_state": "normal",
        "consecutive_failures": 0,
        "consecutive_successes": 0,
    }


def _atomic_save_state(path, document):
    raw = (
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("ascii")
    if len(raw) > MAX_STATE_BYTES:
        _fail("watchdog-state-invalid")
    parent = os.path.dirname(path)
    temporary_path = None
    file_fd = -1
    directory_fd = -1
    try:
        file_fd, temporary_path = tempfile.mkstemp(prefix=".soak-state-", dir=parent)
        os.fchmod(file_fd, 0o600)
        offset = 0
        while offset < len(raw):
            offset += os.write(file_fd, raw[offset:])
        os.fsync(file_fd)
        os.close(file_fd)
        file_fd = -1
        os.replace(temporary_path, path)
        temporary_path = None
        directory_fd = os.open(
            parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        os.fsync(directory_fd)
    except LocalFailure:
        raise
    except Exception:
        _fail("watchdog-state-invalid")
    finally:
        if file_fd >= 0:
            try:
                os.close(file_fd)
            except OSError:
                pass
        if directory_fd >= 0:
            try:
                os.close(directory_fd)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def load_webhook_url(path):
    """Load the one exact webhook entry without projecting its value."""
    raw = _read_restricted_file(path, MAX_SECRET_BYTES, "watchdog-secret-invalid")
    try:
        text = raw.decode("ascii")
    except UnicodeError:
        _fail("watchdog-secret-invalid")
    prefix = "DISCORD_WEBHOOK_URL="
    if not text.startswith(prefix) or not text.endswith("\n") or "\r" in text:
        _fail("watchdog-secret-invalid")
    if text.count("\n") != 1:
        _fail("watchdog-secret-invalid")
    value = text[len(prefix) : -1]
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        _fail("watchdog-secret-invalid")
    if (
        not value
        or len(value) > 2048
        or parsed.scheme != "https"
        or parsed.hostname not in _DISCORD_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or _DISCORD_PATH_RE.fullmatch(parsed.path) is None
    ):
        _fail("watchdog-secret-invalid")
    return value


def _deliver_transition(config, transition, reason_code, *, urlopen):
    webhook_url = load_webhook_url(config.webhook_env_file)
    if transition == "outage":
        content = (
            "Status soak outage: consecutive checks crossed the failure threshold. "
            f"Reason code: {reason_code}."
        )
    else:
        content = "Status soak recovery: consecutive checks crossed the recovery threshold."
    payload = {
        "content": content,
        "allowed_mentions": {
            "parse": [],
            "roles": [],
            "users": [],
            "replied_user": False,
        },
    }
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            if type(response.status) is not int or not 200 <= response.status < 300:
                _fail("watchdog-delivery-failed")
            response.read(1025)
    except LocalFailure:
        raise
    except Exception:
        _fail("watchdog-delivery-failed")


def _record_result(state, result, timestamp):
    updated = dict(state)
    updated["last_run"] = timestamp
    updated["total_runs"] += 1
    updated["last_reason_code"] = result.reason_code
    observed_age = result.generation_age_seconds
    if observed_age is not None:
        prior = updated["maximum_generation_age_seconds"]
        updated["maximum_generation_age_seconds"] = (
            observed_age if prior is None else max(prior, observed_age)
        )
    if result.healthy:
        updated["healthy_runs"] += 1
        updated["consecutive_successes"] += 1
        updated["consecutive_failures"] = 0
    else:
        updated["failed_runs"] += 1
        updated["consecutive_failures"] += 1
        updated["consecutive_successes"] = 0
    return updated


def run_once(config, *, now, urlopen, runner):
    """Perform one locked probe/evaluate/deliver/save transaction."""
    _prepare_runtime_directory(config)
    with _HeldLock(config.lock_file):
        state = _load_state(config.state_file, absent_allowed=True)
        current_time = _now_utc(now)
        timestamp = _public_timestamp(current_time)
        if state is None:
            state = _initial_state(timestamp)
        elif current_time < _parse_timestamp(state["last_run"]):
            _fail("watchdog-state-invalid")

        summary = probe_summary(config, current_time, urlopen=urlopen)
        docker = probe_docker(config, runner=runner)
        if not summary.healthy:
            combined = summary
        elif not docker.healthy:
            combined = ProbeResult(
                False, docker.reason_code, summary.generation_age_seconds
            )
        else:
            combined = summary
        updated = _record_result(state, combined, timestamp)

        transition = None
        if (
            updated["alert_state"] == "normal"
            and not combined.healthy
            and updated["consecutive_failures"] >= config.failure_threshold
        ):
            transition = "outage"
        elif (
            updated["alert_state"] == "outage"
            and combined.healthy
            and updated["consecutive_successes"] >= config.recovery_threshold
        ):
            transition = "recovery"

        if transition is not None:
            try:
                _deliver_transition(
                    config, transition, combined.reason_code, urlopen=urlopen
                )
            except LocalFailure:
                _atomic_save_state(config.state_file, updated)
                raise
            if transition == "outage":
                updated["alert_state"] = "outage"
                updated["outage_transitions"] += 1
            else:
                updated["alert_state"] = "normal"
                updated["recovery_transitions"] += 1

        _atomic_save_state(config.state_file, updated)


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        _fail("watchdog-arguments-invalid")


def _parser():
    parser = _SafeArgumentParser(prog="soak_watchdog.py")
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--config", required=True)
    report_parser = commands.add_parser("report")
    report_parser.add_argument("--state-file", required=True)
    return parser


def _write_report(path, stdout):
    supplied = _path_text(path, "watchdog-state-invalid")
    _validate_runtime_parent(supplied)
    state = _load_state(supplied, absent_allowed=False)
    report = {key: state[key] for key in _REPORT_KEYS}
    stdout.write(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
    stdout.flush()


def main(
    argv=None,
    *,
    now=None,
    urlopen=urllib.request.urlopen,
    runner=None,
    stdout=None,
    stderr=None,
):
    """Run one probe transaction or print one privacy-safe state report."""
    if argv is None:
        argv = sys.argv[1:]
    if now is None:
        now = lambda: datetime.now(timezone.utc)
    if stdout is None:
        stdout = sys.stdout
    if stderr is None:
        stderr = sys.stderr
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "run":
            config = load_config(arguments.config)
            run_once(
                config,
                now=now,
                urlopen=urlopen,
                runner=runner,
            )
        else:
            _write_report(arguments.state_file, stdout)
        return 0
    except LocalFailure as exc:
        stderr.write(exc.code + "\n")
        stderr.flush()
        return 2
    except Exception:
        stderr.write("watchdog-local-failure\n")
        stderr.flush()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
