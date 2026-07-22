"""Deterministic foreground scheduler and local publisher healthcheck."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
import json
import math
import os
import re
import signal
import stat
import sys
import time
from types import MappingProxyType

from publisher.history import validate_history_window
from publisher.privacy import assert_public_safe
from publisher.publisher import main as publisher_main
from publisher.schema import validate_history, validate_incidents, validate_summary


MAX_SECRET_BYTES = 4096
MAX_SECRET_VALUE_BYTES = 1024
MAX_PUBLIC_JSON_BYTES = 2 * 1024 * 1024
MAX_PUBLIC_JSON_DEPTH = 64
MAX_PUBLIC_JSON_NODES = 20_000
MAX_PUBLIC_JSON_TEXT_BYTES = 1024 * 1024
MAX_HEALTH_AGE_SECONDS = 180
MAX_FUTURE_SKEW_SECONDS = 30
PUBLIC_DOCUMENTS = ("summary.json", "history.json", "incidents.json")
_SECRET_KEYS = frozenset(("PLEX_TOKEN", "PLEX_MACHINE_IDENTIFIER"))
_SECRET_LIMITS = {"PLEX_TOKEN": 1024, "PLEX_MACHINE_IDENTIFIER": 256}
_TOKEN = re.compile(r"^[A-Za-z0-9._-]+$")


class SecretFileError(ValueError):
    """Fixed, non-projecting secret rejection."""

    def __init__(self):
        super().__init__("secret file rejected")


class _ServeTermination(BaseException):
    """Private control flow for a quiet SIGTERM-driven serve shutdown."""


def _reject_secret():
    raise SecretFileError() from None


class _SecretMapping(Mapping):
    """Minimal immutable mapping whose representation never projects values."""

    __slots__ = ("_values",)

    def __init__(self, values):
        object.__setattr__(self, "_values", MappingProxyType(dict(values)))

    def __getitem__(self, key):
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __repr__(self):
        return "<secret mapping>"

    def __setattr__(self, _name, _value):
        raise TypeError("secret mapping is immutable")


def _open_directory_no_follow(absolute_directory):
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    current_fd = os.open(os.path.sep, flags)
    try:
        for component in absolute_directory.split(os.path.sep):
            if not component:
                continue
            next_fd = os.open(component, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _read_secret_bytes(file_fd):
    chunks = []
    size = 0
    while size <= MAX_SECRET_BYTES:
        chunk = os.read(file_fd, min(1024, MAX_SECRET_BYTES + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    if size > MAX_SECRET_BYTES:
        _reject_secret()
    return b"".join(chunks)


def _secret_identity(metadata):
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


def _validate_secret_metadata(metadata):
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or mode not in {0o400, 0o600}
        or metadata.st_uid not in {0, os.geteuid()}
        or (metadata.st_uid == 0 and os.geteuid() != 0 and mode != 0o400)
        or metadata.st_size > MAX_SECRET_BYTES
    ):
        _reject_secret()


def load_secret_file(path):
    """Read the exact two-key secret without following its final path entry."""
    directory_fd = -1
    file_fd = -1
    try:
        supplied = os.fspath(path)
        if type(supplied) is not str or "\x00" in supplied:
            _reject_secret()
        absolute = os.path.abspath(supplied)
        parent, filename = os.path.split(absolute)
        if filename in {"", ".", ".."}:
            _reject_secret()
        directory_fd = _open_directory_no_follow(parent)
        entry = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
        _validate_secret_metadata(entry)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        file_fd = os.open(filename, flags, dir_fd=directory_fd)
        opened = os.fstat(file_fd)
        _validate_secret_metadata(opened)
        if (entry.st_dev, entry.st_ino) != (opened.st_dev, opened.st_ino):
            _reject_secret()
        raw = _read_secret_bytes(file_fd)
        final = os.fstat(file_fd)
        _validate_secret_metadata(final)
        if _secret_identity(opened) != _secret_identity(final):
            _reject_secret()
    except (KeyboardInterrupt, SystemExit, _ServeTermination):
        raise
    except SecretFileError:
        raise
    except BaseException:
        _reject_secret()
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if directory_fd >= 0:
            os.close(directory_fd)

    try:
        text = raw.decode("ascii")
    except (UnicodeDecodeError, UnboundLocalError):
        _reject_secret()
    if any(ord(character) < 32 and character != "\n" for character in text):
        _reject_secret()
    if not text.endswith("\n") or "\r" in text:
        _reject_secret()
    values = {}
    for line in text[:-1].split("\n"):
        if line.count("=") != 1:
            _reject_secret()
        key, value = line.split("=", 1)
        if key not in _SECRET_KEYS or key in values:
            _reject_secret()
        if (not value or len(value.encode("ascii")) > MAX_SECRET_VALUE_BYTES
                or len(value.encode("ascii")) > _SECRET_LIMITS[key]):
            _reject_secret()
        if _TOKEN.fullmatch(value) is None:
            _reject_secret()
        values[key] = value
    if set(values) != _SECRET_KEYS:
        _reject_secret()
    return _SecretMapping(values)


@dataclass(frozen=True, slots=True)
class ServeOptions:
    config: str
    content_root: str
    output_dir: str
    state_file: str
    secret_file: str
    interval_seconds: float = 60.0


def _once_arguments(options):
    return [
        "--once",
        "--config", options.config,
        "--content-root", options.content_root,
        "--output-dir", options.output_dir,
        "--state-file", options.state_file,
    ]


def run_serve(options, *, publish=publisher_main, monotonic=time.monotonic,
              sleep=time.sleep, stdout=None):
    """Run immediately and then on monotonic, skipped-tick interval boundaries."""
    if type(options) is not ServeOptions:
        raise TypeError("serve options required")
    interval = options.interval_seconds
    if (isinstance(interval, bool) or not isinstance(interval, (int, float))
            or not math.isfinite(interval) or not 0 < interval <= 86400):
        raise ValueError("interval must be bounded and positive")
    if stdout is None:
        stdout = sys.stdout
    environment = load_secret_file(options.secret_file)
    arguments = _once_arguments(options)
    deadline = monotonic()
    while True:
        cycle_stdout = StringIO()
        cycle_stderr = StringIO()
        try:
            try:
                result = publish(arguments, environ=environment,
                                 stdout=cycle_stdout, stderr=cycle_stderr)
            except KeyboardInterrupt:
                return 0
            except Exception:
                result = 1
            if result != 0:
                stdout.write("publisher-cycle-failed\n")
                stdout.flush()
            deadline += interval
            current = monotonic()
            while deadline < current:
                deadline += interval
            delay = deadline - current
            if delay > 0:
                sleep(delay)
        except KeyboardInterrupt:
            return 0


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("non-finite JSON number")


def _decode_public_json(raw):
    """Strictly decode one document and enforce the publisher's local bounds."""
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except RecursionError:
        raise ValueError("public document rejected") from None

    nodes = 0
    text_size = 0
    stack = [(document, 0)]
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if depth > MAX_PUBLIC_JSON_DEPTH or nodes > MAX_PUBLIC_JSON_NODES:
            raise ValueError("public document rejected")
        if type(value) is dict:
            for key, child in value.items():
                text_size += len(key.encode("utf-8"))
                stack.append((child, depth + 1))
        elif type(value) is list:
            stack.extend((child, depth + 1) for child in value)
        elif type(value) is str:
            text_size += len(value.encode("utf-8"))
        elif type(value) is float and not math.isfinite(value):
            raise ValueError("public document rejected")
        elif value is not None and type(value) not in (bool, int, float):
            raise ValueError("public document rejected")
        if text_size > MAX_PUBLIC_JSON_TEXT_BYTES:
            raise ValueError("public document rejected")
    return document


def _read_public_document(directory_fd, filename):
    metadata = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_PUBLIC_JSON_BYTES:
        raise ValueError("unsafe public document")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    file_fd = os.open(filename, flags, dir_fd=directory_fd)
    try:
        opened = os.fstat(file_fd)
        if (not stat.S_ISREG(opened.st_mode)
                or (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino)):
            raise ValueError("unsafe public document")
        chunks = []
        size = 0
        while size <= MAX_PUBLIC_JSON_BYTES:
            chunk = os.read(file_fd, min(8192, MAX_PUBLIC_JSON_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        final = os.fstat(file_fd)
        if (size > MAX_PUBLIC_JSON_BYTES
                or _secret_identity(opened) != _secret_identity(final)):
            raise ValueError("public document changed")
    finally:
        os.close(file_fd)
    return _decode_public_json(b"".join(chunks))


def _parse_generation(value):
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError("invalid generation")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("invalid generation")
    return parsed.astimezone(timezone.utc)


def check_public_health(public_dir, *, now=None):
    """Strictly validate the bounded v2 public projection and its freshness."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(os.fspath(public_dir), flags)
    try:
        documents = tuple(
            _read_public_document(directory_fd, filename)
            for filename in PUBLIC_DOCUMENTS
        )
    finally:
        os.close(directory_fd)
    summary, history, incidents = documents
    validate_summary(summary)
    validate_history(history)
    validate_history_window(history)
    validate_incidents(incidents)
    for document in documents:
        assert_public_safe(json.dumps(document, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")),
                           source="public status document")
    generations = {document["generated_at"] for document in documents}
    if len(generations) != 1:
        raise ValueError("generation mismatch")
    generated = _parse_generation(next(iter(generations)))
    current = datetime.now(timezone.utc) if now is None else now()
    if current.tzinfo is None:
        raise ValueError("clock must be timezone aware")
    age = (current.astimezone(timezone.utc) - generated).total_seconds()
    if age > MAX_HEALTH_AGE_SECONDS or age < -MAX_FUTURE_SKEW_SECONDS:
        raise ValueError("unhealthy generation age")


def health_main(public_dir="/var/lib/status/public", *, now=None, stdout=None):
    if stdout is None:
        stdout = sys.stdout
    try:
        check_public_health(public_dir, now=now)
    except BaseException:
        stdout.write("publisher-health-failed\n")
        return 1
    stdout.write("publisher-health-ok\n")
    return 0


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise ValueError("invalid arguments")


def _parser():
    parser = _Parser(allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    serve = subparsers.add_parser("serve", allow_abbrev=False)
    serve.add_argument("--config", required=True)
    serve.add_argument("--content-root", required=True)
    serve.add_argument("--output-dir", required=True)
    serve.add_argument("--state-file", required=True)
    serve.add_argument("--secret-file", required=True)
    serve.add_argument("--interval-seconds", type=float, default=60.0)
    health = subparsers.add_parser("health", allow_abbrev=False)
    health.add_argument("--public-dir", default="/var/lib/status/public")
    return parser


def _handle_sigterm(_signum, _frame):
    """Interrupt any serve phase through the PID-one clean stop path."""
    raise _ServeTermination


def main(argv=None):
    try:
        options = _parser().parse_args(argv)
        if options.mode == "health":
            return health_main(options.public_dir)
        serve_options = ServeOptions(
            options.config, options.content_root, options.output_dir,
            options.state_file, options.secret_file, options.interval_seconds,
        )
        sigterm_installed = False
        previous_sigterm = None
        try:
            try:
                previous_sigterm = signal.signal(signal.SIGTERM, _handle_sigterm)
                sigterm_installed = True
            except ValueError:
                # Signal handlers can only be installed by the main interpreter
                # thread. Container execution always uses that path; tests or
                # embedded callers may not.
                pass
            return run_serve(serve_options)
        except _ServeTermination:
            return 0
        finally:
            if sigterm_installed:
                signal.signal(signal.SIGTERM, previous_sigterm)
    except (SecretFileError, TypeError, ValueError):
        sys.stderr.write("publisher-runner-failed\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
