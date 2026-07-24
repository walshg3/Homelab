#!/usr/bin/env python3
"""Deterministic independent watchdog for the public status authority."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import ipaddress
import json
import math
import os
import re
import selectors
import signal
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from publisher import schema as public_schema


PUBLIC_HOST = "status.walshit.com"
EXPECTED_HEALTH_URL = f"https://{PUBLIC_HOST}/healthz.txt"
EXPECTED_SUMMARY_URL = f"https://{PUBLIC_HOST}/status/v2/summary.json"
EXPECTED_HTTP_ROOT_URL = f"http://{PUBLIC_HOST}/"
EXPECTED_HTTPS_ROOT_URL = f"https://{PUBLIC_HOST}/"
EXPECTED_HEALTH_BODY = b"status-walshit-web-ok"
EXPECTED_COMPONENT_IDS = frozenset(("watch", "home", "requests", "audiobooks", "site"))
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) StatusPublicWatchdog/1.0"
)

FAILURE_THRESHOLD = 3
RECOVERY_THRESHOLD = 2
MAX_CONFIG_BYTES = 16 * 1024
MAX_SECRET_BYTES = 8 * 1024
MAX_STATE_BYTES = 64 * 1024
MAX_WEBHOOK_RESPONSE_BYTES = 1024
MAX_RESOLVERS = 16

REASON_CODES = (
    "ok",
    "health_transport_error",
    "health_http_status",
    "health_redirect",
    "health_body_oversized",
    "health_body_invalid",
    "summary_transport_error",
    "summary_http_status",
    "summary_redirect",
    "summary_body_oversized",
    "summary_content_type_invalid",
    "summary_cache_control_invalid",
    "summary_edge_invalid",
    "summary_json_invalid",
    "summary_generated_at_invalid",
    "summary_generation_future",
    "summary_generation_stale",
    "summary_components_invalid",
    "summary_schema_invalid",
    "http_transport_error",
    "http_redirect_status",
    "http_redirect_location",
    "dns_timeout",
    "dns_command_error",
    "dns_output_oversized",
    "dns_address_invalid",
    "dns_no_address",
    "cycle_deadline_exceeded",
)
_REASON_CODE_SET = frozenset(REASON_CODES)
_CONFIG_KEYS = frozenset(
    (
        "schema_version",
        "health_url",
        "summary_url",
        "http_root_url",
        "https_root_url",
        "webhook_env_file",
        "state_file",
        "lock_file",
        "resolvers",
        "operation_timeout_seconds",
        "cycle_timeout_seconds",
        "max_health_body_bytes",
        "max_summary_body_bytes",
        "max_dns_output_bytes",
        "max_age_seconds",
        "future_tolerance_seconds",
    )
)
_STATE_KEYS = frozenset(
    (
        "schema_version",
        "total_runs",
        "healthy_runs",
        "failed_runs",
        "reason_counts",
        "consecutive_failures",
        "consecutive_successes",
        "last_reason_code",
        "last_run",
        "last_success",
        "last_failure",
        "last_transition",
        "status",
        "alerted",
        "maximum_generation_age_seconds",
    )
)
_RFC3339_UTC_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$"
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


class LocalFailure(Exception):
    """A fatal local failure represented only by a fixed safe code."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


class LockBusy(Exception):
    """The nonblocking singleton lock is already held."""


class HardCycleTimeout(BaseException):
    """An OS-enforced whole-transaction deadline expired."""


class _HardCycleDeadline:
    def __init__(self, seconds):
        self.seconds = seconds
        self.previous_handler = None
        self.previous_timer = None

    @staticmethod
    def _expired(_signum, _frame):
        raise HardCycleTimeout from None

    def __enter__(self):
        try:
            self.previous_handler = signal.getsignal(signal.SIGALRM)
            self.previous_timer = signal.getitimer(signal.ITIMER_REAL)
            signal.signal(signal.SIGALRM, self._expired)
            signal.setitimer(signal.ITIMER_REAL, self.seconds, 0.0)
            return self
        except Exception:
            self._restore()
            _fail("watchdog-local-failure")
        except BaseException:
            self._restore()
            raise

    def _restore(self):
        try:
            signal.setitimer(signal.ITIMER_REAL, 0.0, 0.0)
        except Exception:
            pass
        if self.previous_handler is not None:
            try:
                signal.signal(signal.SIGALRM, self.previous_handler)
            except Exception:
                pass
        if self.previous_timer is not None:
            delay, interval = self.previous_timer
            try:
                signal.setitimer(signal.ITIMER_REAL, delay, interval)
            except Exception:
                pass
        self.previous_handler = None
        self.previous_timer = None

    def __exit__(self, *_args):
        self._restore()
        return False


@dataclass(frozen=True, slots=True)
class Config:
    health_url: str
    summary_url: str
    http_root_url: str
    https_root_url: str
    webhook_env_file: str
    state_file: str
    lock_file: str
    resolvers: tuple[str, ...]
    operation_timeout_seconds: int
    cycle_timeout_seconds: int
    max_health_body_bytes: int
    max_summary_body_bytes: int
    max_dns_output_bytes: int
    max_age_seconds: int
    future_tolerance_seconds: int


@dataclass(frozen=True, slots=True)
class ProbeResult:
    healthy: bool
    reason_code: str
    generation_age_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class _CommandResult:
    returncode: int | None
    stdout: bytes
    timed_out: bool = False
    output_oversized: bool = False


def _fail(code):
    raise LocalFailure(code) from None


def _path_text(path, code):
    try:
        value = os.fspath(path)
    except TypeError:
        _fail(code)
    if (
        type(value) is not str
        or not value
        or "\x00" in value
        or not os.path.isabs(value)
        or value == os.path.sep
        or os.path.normpath(value) != value
    ):
        _fail(code)
    return value


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


def _restricted_metadata(
    metadata, maximum, code, *, exact_mode, allow_open_unlinked=False
):
    mode = stat.S_IMODE(metadata.st_mode)
    permitted_mode = mode == 0o600 if exact_mode else mode in {0o400, 0o600}
    permitted_links = (
        metadata.st_nlink in {0, 1} if allow_open_unlinked else metadata.st_nlink == 1
    )
    if (
        not stat.S_ISREG(metadata.st_mode)
        or not permitted_mode
        or not permitted_links
        or metadata.st_uid != os.geteuid()
        or metadata.st_size > maximum
    ):
        _fail(code)


def _read_restricted_file(
    path, maximum, code, *, exact_mode, require_path_stable=False
):
    supplied = _path_text(path, code)
    descriptor = -1
    try:
        entry = os.lstat(supplied)
        _restricted_metadata(entry, maximum, code, exact_mode=exact_mode)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(supplied, flags)
        opened = os.fstat(descriptor)
        _restricted_metadata(
            opened,
            maximum,
            code,
            exact_mode=exact_mode,
            allow_open_unlinked=True,
        )
        if (entry.st_dev, entry.st_ino) != (opened.st_dev, opened.st_ino):
            _fail(code)
        chunks = []
        size = 0
        while size <= maximum:
            chunk = os.read(descriptor, min(4096, maximum + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        if size > maximum:
            _fail(code)
        final = os.fstat(descriptor)
        _restricted_metadata(
            final,
            maximum,
            code,
            exact_mode=exact_mode,
            allow_open_unlinked=True,
        )
        if _metadata_identity(opened) != _metadata_identity(final):
            _fail(code)
        if require_path_stable:
            final_entry = os.lstat(supplied)
            _restricted_metadata(
                final_entry, maximum, code, exact_mode=exact_mode
            )
            if _metadata_identity(final) != _metadata_identity(final_entry):
                _fail(code)
        return b"".join(chunks)
    except LocalFailure:
        raise
    except Exception:
        _fail(code)
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _json_no_duplicates(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate key")
        document[key] = value
    return document


def _reject_json_constant(_value):
    raise ValueError("non-finite value")


def _decode_json(raw):
    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_json_no_duplicates,
        parse_constant=_reject_json_constant,
    )


def _positive_int(value, maximum, code):
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _nonnegative_int(value):
    return type(value) is int and value >= 0


def _absolute_configured_path(value):
    return _path_text(value, "watchdog-config-invalid")


def load_config(path):
    """Load and strictly validate a descriptor-safe mode-0600 JSON config."""
    try:
        document = _decode_json(
            _read_restricted_file(
                path,
                MAX_CONFIG_BYTES,
                "watchdog-config-invalid",
                exact_mode=True,
            )
        )
    except LocalFailure:
        raise
    except Exception:
        _fail("watchdog-config-invalid")
    if (
        type(document) is not dict
        or set(document) != _CONFIG_KEYS
        or document.get("schema_version") != "1"
        or document.get("health_url") != EXPECTED_HEALTH_URL
        or document.get("summary_url") != EXPECTED_SUMMARY_URL
        or document.get("http_root_url") != EXPECTED_HTTP_ROOT_URL
        or document.get("https_root_url") != EXPECTED_HTTPS_ROOT_URL
    ):
        _fail("watchdog-config-invalid")

    webhook_file = _absolute_configured_path(document["webhook_env_file"])
    state_file = _absolute_configured_path(document["state_file"])
    lock_file = _absolute_configured_path(document["lock_file"])
    if (
        len({webhook_file, state_file, lock_file}) != 3
        or os.path.dirname(state_file) != os.path.dirname(lock_file)
    ):
        _fail("watchdog-config-invalid")

    resolvers = document["resolvers"]
    if (
        type(resolvers) is not list
        or not resolvers
        or len(resolvers) > MAX_RESOLVERS
        or len(resolvers) != len(set(resolvers))
    ):
        _fail("watchdog-config-invalid")
    for resolver in resolvers:
        try:
            address = ipaddress.ip_address(resolver)
        except (TypeError, ValueError):
            _fail("watchdog-config-invalid")
        if type(resolver) is not str or not _is_public_address(address):
            _fail("watchdog-config-invalid")

    operation_timeout = _positive_int(
        document["operation_timeout_seconds"], 60, "watchdog-config-invalid"
    )
    cycle_timeout = _positive_int(
        document["cycle_timeout_seconds"], 300, "watchdog-config-invalid"
    )
    if cycle_timeout < operation_timeout:
        _fail("watchdog-config-invalid")

    return Config(
        health_url=document["health_url"],
        summary_url=document["summary_url"],
        http_root_url=document["http_root_url"],
        https_root_url=document["https_root_url"],
        webhook_env_file=webhook_file,
        state_file=state_file,
        lock_file=lock_file,
        resolvers=tuple(resolvers),
        operation_timeout_seconds=operation_timeout,
        cycle_timeout_seconds=cycle_timeout,
        max_health_body_bytes=_positive_int(
            document["max_health_body_bytes"], 4096, "watchdog-config-invalid"
        ),
        max_summary_body_bytes=_positive_int(
            document["max_summary_body_bytes"], 1024 * 1024, "watchdog-config-invalid"
        ),
        max_dns_output_bytes=_positive_int(
            document["max_dns_output_bytes"], 64 * 1024, "watchdog-config-invalid"
        ),
        max_age_seconds=_positive_int(
            document["max_age_seconds"], 3600, "watchdog-config-invalid"
        ),
        future_tolerance_seconds=_positive_int(
            document["future_tolerance_seconds"], 60, "watchdog-config-invalid"
        ),
    )


def _parse_discord_url(value):
    if type(value) is not str or not value or len(value) > 2048:
        _fail("watchdog-secret-invalid")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        _fail("watchdog-secret-invalid")
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _DISCORD_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or _DISCORD_PATH_RE.fullmatch(parsed.path) is None
        or any(ord(character) < 33 or ord(character) > 126 for character in value)
    ):
        _fail("watchdog-secret-invalid")
    return value


def load_webhook(path):
    """Load one restricted Discord webhook env assignment without pathname rereads."""
    try:
        raw = _read_restricted_file(
            path,
            MAX_SECRET_BYTES,
            "watchdog-secret-invalid",
            exact_mode=False,
        )
        text = raw.decode("ascii")
    except LocalFailure:
        raise
    except Exception:
        _fail("watchdog-secret-invalid")
    lines = text.splitlines()
    prefix = "DISCORD_WEBHOOK_URL="
    if len(lines) != 1 or not lines[0].startswith(prefix):
        _fail("watchdog-secret-invalid")
    return _parse_discord_url(lines[0][len(prefix) :])


def _public_timestamp(value):
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def _parse_public_timestamp(value):
    if type(value) is not str or _RFC3339_UTC_RE.fullmatch(value) is None:
        raise ValueError("invalid timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("invalid timestamp")
    return parsed.astimezone(timezone.utc)


def _now_utc(now):
    try:
        value = now()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError("invalid clock")
        return value.astimezone(timezone.utc)
    except Exception:
        _fail("watchdog-local-failure")


def _validate_runtime_directory(path):
    supplied = _path_text(path, "watchdog-state-invalid")
    parent = os.path.dirname(supplied)
    descriptor = -1
    try:
        entry = os.lstat(parent)
        if (
            not stat.S_ISDIR(entry.st_mode)
            or stat.S_ISLNK(entry.st_mode)
            or stat.S_IMODE(entry.st_mode) != 0o700
            or entry.st_uid != os.geteuid()
        ):
            _fail("watchdog-state-invalid")
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(parent, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or stat.S_IMODE(opened.st_mode) != 0o700
            or opened.st_uid != os.geteuid()
            or (entry.st_dev, entry.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            _fail("watchdog-state-invalid")
    except LocalFailure:
        raise
    except Exception:
        _fail("watchdog-state-invalid")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


class _HeldLock:
    def __init__(self, path):
        self.path = path
        self.descriptor = -1

    def __enter__(self):
        try:
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            self.descriptor = os.open(self.path, flags, 0o600)
            metadata = os.fstat(self.descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
                or metadata.st_uid != os.geteuid()
            ):
                _fail("watchdog-lock-invalid")
            try:
                fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise LockBusy from None
            return self
        except LockBusy:
            self._close()
            raise
        except LocalFailure:
            self._close()
            raise
        except Exception:
            self._close()
            _fail("watchdog-lock-invalid")

    def _close(self):
        if self.descriptor >= 0:
            try:
                os.close(self.descriptor)
            except OSError:
                pass
            self.descriptor = -1

    def __exit__(self, *_args):
        self._close()
        return False


def _initial_state():
    return {
        "schema_version": "1",
        "total_runs": 0,
        "healthy_runs": 0,
        "failed_runs": 0,
        "reason_counts": {code: 0 for code in REASON_CODES},
        "consecutive_failures": 0,
        "consecutive_successes": 0,
        "last_reason_code": None,
        "last_run": None,
        "last_success": None,
        "last_failure": None,
        "last_transition": None,
        "status": "healthy",
        "alerted": False,
        "maximum_generation_age_seconds": None,
    }


def _validate_optional_timestamp(value):
    if value is None:
        return None
    parsed = _parse_public_timestamp(value)
    if _public_timestamp(parsed) != value:
        raise ValueError("noncanonical timestamp")
    return parsed


def _validate_state(document):
    if (
        type(document) is not dict
        or set(document) != _STATE_KEYS
        or document.get("schema_version") != "1"
        or document.get("status") not in {"healthy", "outage"}
        or type(document.get("alerted")) is not bool
        or document["alerted"] != (document["status"] == "outage")
        or document.get("last_reason_code") not in _REASON_CODE_SET
    ):
        _fail("watchdog-state-invalid")
    integer_keys = (
        "total_runs",
        "healthy_runs",
        "failed_runs",
        "consecutive_failures",
        "consecutive_successes",
    )
    if any(not _nonnegative_int(document[key]) for key in integer_keys):
        _fail("watchdog-state-invalid")
    counts = document["reason_counts"]
    if (
        type(counts) is not dict
        or tuple(counts) != REASON_CODES
        or any(not _nonnegative_int(counts[code]) for code in REASON_CODES)
    ):
        _fail("watchdog-state-invalid")
    maximum_age = document["maximum_generation_age_seconds"]
    if maximum_age is not None and not _nonnegative_int(maximum_age):
        _fail("watchdog-state-invalid")
    try:
        timestamps = {
            key: _validate_optional_timestamp(document[key])
            for key in (
                "last_run",
                "last_success",
                "last_failure",
                "last_transition",
            )
        }
    except Exception:
        _fail("watchdog-state-invalid")
    total = document["total_runs"]
    if (
        total < 1
        or document["healthy_runs"] + document["failed_runs"] != total
        or sum(counts.values()) != total
        or counts["ok"] != document["healthy_runs"]
        or sum(counts[code] for code in REASON_CODES if code != "ok")
        != document["failed_runs"]
        or counts[document["last_reason_code"]] == 0
        or document["consecutive_failures"] > document["failed_runs"]
        or document["consecutive_successes"] > document["healthy_runs"]
        or bool(document["consecutive_failures"])
        and bool(document["consecutive_successes"])
        or (document["last_reason_code"] == "ok")
        != bool(document["consecutive_successes"])
        or timestamps["last_run"] is None
        or (timestamps["last_success"] is None) != (document["healthy_runs"] == 0)
        or (timestamps["last_failure"] is None) != (document["failed_runs"] == 0)
        or any(
            counts[code] > 0
            for code in (
                "ok",
                "summary_generation_future",
                "summary_generation_stale",
            )
        )
        and maximum_age is None
        or document["failed_runs"] < FAILURE_THRESHOLD
        and timestamps["last_transition"] is not None
        or document["status"] == "healthy"
        and timestamps["last_transition"] is not None
        and document["healthy_runs"] < RECOVERY_THRESHOLD
        or document["status"] == "outage"
        and timestamps["last_transition"] is None
    ):
        _fail("watchdog-state-invalid")
    last_run = timestamps["last_run"]
    if any(
        value is not None and value > last_run
        for key, value in timestamps.items()
        if key != "last_run"
    ):
        _fail("watchdog-state-invalid")
    failures = document["consecutive_failures"]
    successes = document["consecutive_successes"]
    if document["status"] == "healthy":
        streak_valid = (
            (1 <= failures < FAILURE_THRESHOLD and successes == 0)
            or (failures == 0 and successes >= 1)
        )
    else:
        streak_valid = (
            (failures >= FAILURE_THRESHOLD and successes == 0)
            or (failures == 0 and successes == 1)
        )
    if (
        not streak_valid
        or document["last_reason_code"] == "ok"
        and timestamps["last_success"] != last_run
        or document["last_reason_code"] != "ok"
        and timestamps["last_failure"] != last_run
    ):
        _fail("watchdog-state-invalid")
    return document


def _load_state(path):
    try:
        os.lstat(path)
    except FileNotFoundError:
        return _initial_state()
    except Exception:
        _fail("watchdog-state-invalid")
    try:
        document = _decode_json(
            _read_restricted_file(
                path,
                MAX_STATE_BYTES,
                "watchdog-state-invalid",
                exact_mode=True,
            )
        )
    except LocalFailure:
        raise
    except Exception:
        _fail("watchdog-state-invalid")
    return _validate_state(document)


def _atomic_save_state(path, document):
    _validate_state(document)
    raw = (
        json.dumps(document, ensure_ascii=True, separators=(",", ":")) + "\n"
    ).encode("ascii")
    if len(raw) > MAX_STATE_BYTES:
        _fail("watchdog-state-invalid")
    parent = os.path.dirname(path)
    temporary_path = None
    descriptor = -1
    directory_descriptor = -1
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=".public-watchdog-state-", dir=parent
        )
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.replace(temporary_path, path)
            temporary_path = None
            directory_descriptor = os.open(
                parent,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            os.fsync(directory_descriptor)
        except BaseException as interruption:
            try:
                committed = (
                    _read_restricted_file(
                        path,
                        MAX_STATE_BYTES,
                        "watchdog-state-invalid",
                        exact_mode=True,
                        require_path_stable=True,
                    )
                    == raw
                )
            except HardCycleTimeout:
                raise
            except Exception:
                committed = False
            except BaseException:
                raise
            if not committed:
                if isinstance(interruption, HardCycleTimeout):
                    raise interruption
                if isinstance(interruption, Exception):
                    _fail("watchdog-state-invalid")
                raise interruption
            temporary_path = None
    except Exception:
        _fail("watchdog-state-invalid")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if directory_descriptor >= 0:
            try:
                os.close(directory_descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _default_open_url(request, *, timeout):
    tls_context = ssl.create_default_context()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirectHandler(),
        urllib.request.HTTPSHandler(context=tls_context),
    )
    return opener.open(request, timeout=timeout)


def _remaining_timeout(config, deadline, monotonic):
    try:
        remaining = deadline - monotonic()
    except Exception:
        _fail("watchdog-local-failure")
    if remaining <= 0:
        return None
    return min(config.operation_timeout_seconds, remaining)


def _open_response(request, timeout, open_url):
    try:
        return open_url(request, timeout=timeout), None
    except urllib.error.HTTPError as error:
        return error, None
    except Exception:
        return None, "transport"


def _response_url(response):
    try:
        return response.geturl()
    except Exception:
        return None


def _response_status(response):
    try:
        status_code = response.status
    except Exception:
        return None
    return status_code if type(status_code) is int else None


def _response_header(response, name):
    try:
        value = response.headers.get(name)
    except Exception:
        return None
    return value if type(value) is str else None


def _read_response(response, maximum):
    try:
        raw = response.read(maximum + 1)
    except Exception:
        return None
    return raw if type(raw) is bytes else None


def _close_response(response):
    try:
        response.close()
    except Exception:
        pass


def probe_health(config, deadline, *, monotonic, open_url):
    timeout = _remaining_timeout(config, deadline, monotonic)
    if timeout is None:
        return ProbeResult(False, "cycle_deadline_exceeded")
    request = urllib.request.Request(
        config.health_url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/plain"},
        method="GET",
    )
    response, error = _open_response(request, timeout, open_url)
    if error:
        return ProbeResult(False, "health_transport_error")
    try:
        status_code = _response_status(response)
        if status_code is not None and 300 <= status_code <= 399:
            return ProbeResult(False, "health_redirect")
        if _response_url(response) != config.health_url:
            return ProbeResult(False, "health_redirect")
        if status_code != 200:
            return ProbeResult(False, "health_http_status")
        raw = _read_response(response, config.max_health_body_bytes)
        if raw is None:
            return ProbeResult(False, "health_transport_error")
        if len(raw) > config.max_health_body_bytes:
            return ProbeResult(False, "health_body_oversized")
        if raw.strip() != EXPECTED_HEALTH_BODY:
            return ProbeResult(False, "health_body_invalid")
        return ProbeResult(True, "ok")
    finally:
        _close_response(response)


def _summary_generated_at(document):
    try:
        value = document["generated_at"]
        if type(value) is not str or _RFC3339_UTC_RE.fullmatch(value) is None:
            raise ValueError("not UTC")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError("not UTC")
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def probe_summary(
    config,
    deadline,
    *,
    now,
    monotonic,
    open_url,
    earliest_time,
):
    timeout = _remaining_timeout(config, deadline, monotonic)
    if timeout is None:
        return ProbeResult(False, "cycle_deadline_exceeded")
    request = urllib.request.Request(
        config.summary_url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        method="GET",
    )
    response, error = _open_response(request, timeout, open_url)
    if error:
        return ProbeResult(False, "summary_transport_error")
    try:
        status_code = _response_status(response)
        if status_code is not None and 300 <= status_code <= 399:
            return ProbeResult(False, "summary_redirect")
        if _response_url(response) != config.summary_url:
            return ProbeResult(False, "summary_redirect")
        if status_code != 200:
            return ProbeResult(False, "summary_http_status")
        content_type = _response_header(response, "Content-Type")
        if (
            content_type is None
            or content_type.split(";", 1)[0].strip().lower() != "application/json"
        ):
            return ProbeResult(False, "summary_content_type_invalid")
        cache_control = _response_header(response, "Cache-Control")
        cache_tokens = (
            {
                token.strip().lower()
                for token in cache_control.split(",")
                if token.strip()
            }
            if cache_control is not None
            else set()
        )
        if "no-store" not in cache_tokens:
            return ProbeResult(False, "summary_cache_control_invalid")
        server = _response_header(response, "Server")
        cf_ray = _response_header(response, "CF-Ray")
        if (
            server is None
            or server.strip().lower() != "cloudflare"
            or cf_ray is None
            or not cf_ray.strip()
            or len(cf_ray) > 256
        ):
            return ProbeResult(False, "summary_edge_invalid")
        raw = _read_response(response, config.max_summary_body_bytes)
        if raw is None:
            return ProbeResult(False, "summary_transport_error")
        if len(raw) > config.max_summary_body_bytes:
            return ProbeResult(False, "summary_body_oversized")
        current_time = _now_utc(now)
        if earliest_time is not None and current_time < earliest_time:
            _fail("watchdog-clock-backward")
    finally:
        _close_response(response)

    try:
        document = _decode_json(raw)
    except Exception:
        return ProbeResult(False, "summary_json_invalid")
    generated_at = _summary_generated_at(document)
    if generated_at is None:
        return ProbeResult(False, "summary_generated_at_invalid")
    try:
        components = document["components"]
        component_ids = [component["id"] for component in components]
        if (
            type(components) is not list
            or len(component_ids) != len(set(component_ids))
            or set(component_ids) != EXPECTED_COMPONENT_IDS
        ):
            return ProbeResult(False, "summary_components_invalid")
    except Exception:
        return ProbeResult(False, "summary_components_invalid")
    try:
        public_schema.validate_summary(document)
    except Exception:
        return ProbeResult(False, "summary_schema_invalid")
    age = (current_time - generated_at).total_seconds()
    observed_age = max(0, math.ceil(age))
    if age < -config.future_tolerance_seconds:
        return ProbeResult(False, "summary_generation_future", observed_age)
    if age > config.max_age_seconds:
        return ProbeResult(False, "summary_generation_stale", observed_age)
    return ProbeResult(True, "ok", observed_age)


def probe_http_redirect(config, deadline, *, monotonic, open_url):
    timeout = _remaining_timeout(config, deadline, monotonic)
    if timeout is None:
        return ProbeResult(False, "cycle_deadline_exceeded")
    request = urllib.request.Request(
        config.http_root_url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.8"},
        method="GET",
    )
    response, error = _open_response(request, timeout, open_url)
    if error:
        return ProbeResult(False, "http_transport_error")
    try:
        if _response_status(response) not in {301, 308}:
            return ProbeResult(False, "http_redirect_status")
        if _response_header(response, "Location") != config.https_root_url:
            return ProbeResult(False, "http_redirect_location")
        return ProbeResult(True, "ok")
    finally:
        _close_response(response)


def _is_public_address(address):
    return bool(
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
    )


def _terminate_process(process, deadline):
    try:
        if process.poll() is None:
            process.kill()
    except Exception:
        pass
    try:
        remaining = max(0.0, deadline - time.monotonic())
        process.wait(timeout=remaining)
    except Exception:
        pass


def _run_dns_bounded(argv, timeout, maximum):
    process = None
    stream = None
    selector = None
    output = bytearray()
    deadline = time.monotonic() + timeout
    reap_allowance = min(0.1, max(0.01, timeout / 5))
    io_deadline = deadline - reap_allowance
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        stream = process.stdout
        if stream is None:
            raise OSError("missing output")
        descriptor = stream.fileno()
        os.set_blocking(descriptor, False)
        selector = selectors.DefaultSelector()
        selector.register(descriptor, selectors.EVENT_READ)
        eof = False
        while not eof:
            remaining = io_deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process(process, deadline)
                return _CommandResult(None, bytes(output), timed_out=True)
            events = selector.select(remaining)
            if not events:
                if process.poll() is None:
                    _terminate_process(process, deadline)
                    return _CommandResult(None, bytes(output), timed_out=True)
                events = ((None, None),)
            for _key, _mask in events:
                chunk = os.read(
                    descriptor, min(4096, maximum + 1 - len(output))
                )
                if not chunk:
                    eof = True
                    break
                output.extend(chunk)
                if len(output) > maximum:
                    _terminate_process(process, deadline)
                    return _CommandResult(
                        process.returncode, bytes(output), output_oversized=True
                    )
        remaining = max(0, deadline - time.monotonic())
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _terminate_process(process, deadline)
            return _CommandResult(None, bytes(output), timed_out=True)
        return _CommandResult(returncode, bytes(output))
    finally:
        if selector is not None:
            try:
                selector.close()
            except Exception:
                pass
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        if process is not None and process.poll() is None:
            _terminate_process(process, deadline)


_DNS_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def _is_bounded_cname(value):
    if not value.endswith(".") or not 2 <= len(value) <= 254:
        return False
    labels = value[:-1].split(".")
    return all(_DNS_LABEL_RE.fullmatch(label) is not None for label in labels)


def _dns_argv(resolver, query_type, timeout):
    return [
        "/usr/bin/dig",
        "+short",
        "+tries=1",
        f"+time={max(1, math.ceil(timeout))}",
        f"@{resolver}",
        PUBLIC_HOST,
        query_type,
    ]


def probe_dns(config, deadline, *, monotonic, dns_runner):
    for resolver in config.resolvers:
        found_address = False
        for query_type in ("A", "AAAA"):
            timeout = _remaining_timeout(config, deadline, monotonic)
            if timeout is None:
                return ProbeResult(False, "cycle_deadline_exceeded")
            argv = _dns_argv(resolver, query_type, timeout)
            try:
                if dns_runner is None:
                    completed = _run_dns_bounded(
                        argv, timeout, config.max_dns_output_bytes
                    )
                else:
                    completed = dns_runner(
                        argv,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        timeout=timeout,
                        check=False,
                    )
            except subprocess.TimeoutExpired:
                return ProbeResult(False, "dns_timeout")
            except Exception:
                return ProbeResult(False, "dns_command_error")
            if getattr(completed, "timed_out", False):
                return ProbeResult(False, "dns_timeout")
            output = getattr(completed, "stdout", None)
            if (
                getattr(completed, "returncode", None) != 0
                or type(output) is not bytes
            ):
                return ProbeResult(False, "dns_command_error")
            if (
                getattr(completed, "output_oversized", False)
                or len(output) > config.max_dns_output_bytes
            ):
                return ProbeResult(False, "dns_output_oversized")
            for raw_line in output.splitlines():
                try:
                    line = raw_line.decode("ascii")
                except UnicodeDecodeError:
                    return ProbeResult(False, "dns_address_invalid")
                try:
                    address = ipaddress.ip_address(line)
                except ValueError:
                    if _is_bounded_cname(line):
                        continue
                    return ProbeResult(False, "dns_address_invalid")
                if (
                    (query_type == "A" and address.version != 4)
                    or (query_type == "AAAA" and address.version != 6)
                    or not _is_public_address(address)
                ):
                    return ProbeResult(False, "dns_address_invalid")
                found_address = True
        if not found_address:
            return ProbeResult(False, "dns_no_address")
    return ProbeResult(True, "ok")


def probe_cycle(
    config,
    deadline,
    *,
    now,
    monotonic,
    open_url,
    dns_runner,
    earliest_time,
):
    summary_age = None
    for probe in (
        lambda: probe_health(
            config, deadline, monotonic=monotonic, open_url=open_url
        ),
        lambda: probe_summary(
            config,
            deadline,
            now=now,
            monotonic=monotonic,
            open_url=open_url,
            earliest_time=earliest_time,
        ),
        lambda: probe_http_redirect(
            config, deadline, monotonic=monotonic, open_url=open_url
        ),
        lambda: probe_dns(
            config, deadline, monotonic=monotonic, dns_runner=dns_runner
        ),
    ):
        try:
            result = probe()
        except LocalFailure:
            raise
        except Exception:
            if _remaining_timeout(config, deadline, monotonic) is None:
                result = ProbeResult(False, "cycle_deadline_exceeded")
            else:
                _fail("watchdog-local-failure")
        if result.generation_age_seconds is not None:
            summary_age = result.generation_age_seconds
        if _remaining_timeout(config, deadline, monotonic) is None:
            return ProbeResult(
                False, "cycle_deadline_exceeded", summary_age
            )
        if not result.healthy:
            if summary_age is not None and result.generation_age_seconds is None:
                return ProbeResult(False, result.reason_code, summary_age)
            return result
    return ProbeResult(True, "ok", summary_age)


def _apply_result(state, result, current_time):
    updated = {
        **state,
        "reason_counts": dict(state["reason_counts"]),
    }
    timestamp = _public_timestamp(current_time)
    updated["total_runs"] += 1
    updated["last_run"] = timestamp
    updated["last_reason_code"] = result.reason_code
    updated["reason_counts"][result.reason_code] += 1
    if result.generation_age_seconds is not None:
        previous = updated["maximum_generation_age_seconds"]
        updated["maximum_generation_age_seconds"] = max(
            result.generation_age_seconds,
            previous if previous is not None else 0,
        )

    transition = None
    if result.healthy:
        updated["healthy_runs"] += 1
        updated["last_success"] = timestamp
        updated["consecutive_successes"] += 1
        updated["consecutive_failures"] = 0
        if (
            updated["status"] == "outage"
            and updated["consecutive_successes"] == RECOVERY_THRESHOLD
        ):
            updated["status"] = "healthy"
            updated["alerted"] = False
            updated["last_transition"] = timestamp
            transition = "recovery"
    else:
        updated["failed_runs"] += 1
        updated["last_failure"] = timestamp
        updated["consecutive_failures"] = max(
            updated["consecutive_failures"] + 1,
            FAILURE_THRESHOLD if updated["status"] == "outage" else 1,
        )
        updated["consecutive_successes"] = 0
        if (
            updated["status"] == "healthy"
            and updated["consecutive_failures"] == FAILURE_THRESHOLD
        ):
            updated["status"] = "outage"
            updated["alerted"] = True
            updated["last_transition"] = timestamp
            transition = "outage"
    return updated, transition


def alert_payload(transition, reason_code, consecutive_count):
    if (
        transition not in {"outage", "recovery"}
        or reason_code not in _REASON_CODE_SET
        or type(consecutive_count) is not int
        or consecutive_count < 1
    ):
        _fail("watchdog-local-failure")
    label = transition.upper()
    return {
        "content": (
            f"{PUBLIC_HOST} watchdog {label}: "
            f"reason={reason_code} consecutive={consecutive_count}"
        ),
        "allowed_mentions": {"parse": [], "users": [], "roles": []},
    }


def _post_alert(
    webhook,
    payload,
    config,
    deadline,
    *,
    monotonic,
    open_url,
):
    timeout = _remaining_timeout(config, deadline, monotonic)
    if timeout is None:
        _fail("watchdog-alert-failed")
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    request = urllib.request.Request(
        webhook,
        data=raw,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    response, error = _open_response(request, timeout, open_url)
    if error:
        _fail("watchdog-alert-failed")
    try:
        if _response_status(response) not in {200, 204}:
            _fail("watchdog-alert-failed")
        body = _read_response(response, MAX_WEBHOOK_RESPONSE_BYTES)
        if body is None or len(body) > MAX_WEBHOOK_RESPONSE_BYTES:
            _fail("watchdog-alert-failed")
        if _remaining_timeout(config, deadline, monotonic) is None:
            _fail("watchdog-alert-failed")
    finally:
        _close_response(response)


def run_once(
    config,
    *,
    now,
    monotonic,
    open_url,
    dns_runner,
):
    with _HardCycleDeadline(config.cycle_timeout_seconds):
        try:
            deadline = monotonic() + config.cycle_timeout_seconds
        except Exception:
            _fail("watchdog-local-failure")
        _validate_runtime_directory(config.state_file)
        try:
            with _HeldLock(config.lock_file):
                state = _load_state(config.state_file)
                current_time = _now_utc(now)
                previous_run = (
                    _parse_public_timestamp(state["last_run"])
                    if state["last_run"] is not None
                    else None
                )
                if previous_run is not None and current_time < previous_run:
                    _fail("watchdog-clock-backward")
                result = probe_cycle(
                    config,
                    deadline,
                    now=now,
                    monotonic=monotonic,
                    open_url=open_url,
                    dns_runner=dns_runner,
                    earliest_time=previous_run,
                )
                updated, transition = _apply_result(state, result, current_time)
                _validate_state(updated)
                if transition is not None:
                    webhook = load_webhook(config.webhook_env_file)
                    count = (
                        updated["consecutive_failures"]
                        if transition == "outage"
                        else updated["consecutive_successes"]
                    )
                    payload = alert_payload(
                        transition,
                        result.reason_code if transition == "outage" else "ok",
                        count,
                    )
                    _post_alert(
                        webhook,
                        payload,
                        config,
                        deadline,
                        monotonic=monotonic,
                        open_url=open_url,
                    )
                _atomic_save_state(config.state_file, updated)
        except LockBusy:
            return 0
    return 0


class _SafeParser(argparse.ArgumentParser):
    def error(self, _message):
        _fail("watchdog-arguments-invalid")


def _parser():
    parser = _SafeParser(add_help=False)
    parser.add_argument("--config", required=True)
    return parser


def main(
    argv=None,
    *,
    now=lambda: datetime.now(timezone.utc),
    monotonic=time.monotonic,
    open_url=_default_open_url,
    dns_runner=None,
    stdout=sys.stdout,
    stderr=sys.stderr,
):
    del stdout
    try:
        arguments = _parser().parse_args(argv)
        config = load_config(arguments.config)
        return run_once(
            config,
            now=now,
            monotonic=monotonic,
            open_url=open_url,
            dns_runner=dns_runner,
        )
    except HardCycleTimeout:
        stderr.write("watchdog-cycle-hard-timeout\n")
        stderr.flush()
        return 2
    except LocalFailure as failure:
        stderr.write(f"{failure.code}\n")
        stderr.flush()
        return 2
    except Exception:
        stderr.write("watchdog-local-failure\n")
        stderr.flush()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
