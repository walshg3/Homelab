#!/usr/bin/env python3
"""Authenticated ntfy JSON stream to Discord webhook bridge."""

from __future__ import annotations

import argparse
import email.utils
import json
import os
import random
import re
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ORIGIN_MARKER = "[ntfy-discord-bridge-origin]"
ALLOWED_TOPICS = frozenset(("homelab-critical", "homelab-ops"))
EXCLUDED_TAGS = frozenset(("test", "smoke"))
MAX_EVENT_BYTES = 65_536
MAX_TITLE_CHARS = 256
MAX_MESSAGE_CHARS = 1_400
MAX_DISCORD_CONTENT_CHARS = 1_900
MAX_ERROR_CHARS = 500
MAX_SECRET_FILE_BYTES = 16_384

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_DISCORD_WEBHOOK_RE = re.compile(
    r"https?://(?:canary\.|ptb\.)?(?:discord(?:app)?\.com)/api(?:/v\d+)?/webhooks/[^\s\"'<>]+",
    re.IGNORECASE,
)
_AUTH_RE = re.compile(
    r"(?i)(\bauthorization\b\s*['\"]?\s*[:=]\s*['\"]?\s*)"
    r"(?:bearer\s+)?[^\s,;\"'}]+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_TOKEN_RE = re.compile(
    r"(?i)(\b(?:access[_-]?token|api[_-]?key|token|secret|password)\b"
    r"\s*['\"]?\s*[:=]\s*['\"]?\s*)"
    r"[^\s,;\"'}]+"
)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_NTFY_TOKEN_RE = re.compile(r"\btk_[A-Za-z0-9_-]{8,}\b")


class BridgeError(Exception):
    """Base bridge failure."""


class DeliveryError(BridgeError):
    """Discord delivery failed after bounded retries."""


class PermanentDeliveryError(DeliveryError):
    """Discord rejected the request with a non-retryable status."""


@dataclass(frozen=True)
class Config:
    ntfy_url: str
    subscriber_token: str
    webhook_url: str
    state_file: Path
    state_max_age: int
    subscribe_timeout: float
    reconnect_max: float
    discord_timeout: float
    discord_attempts: int
    retry_max: float


def _number(name: str, default: str, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name, default)
    try:
        value = float(raw)
    except ValueError as exc:
        raise BridgeError(f"{name} must be numeric") from exc
    if not minimum <= value <= maximum:
        raise BridgeError(f"{name} must be between {minimum:g} and {maximum:g}")
    return value


def read_secret_file(path: Path) -> dict[str, str]:
    try:
        stat = path.stat()
        if stat.st_size > MAX_SECRET_FILE_BYTES:
            raise BridgeError("bridge secret file exceeds size limit")
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise BridgeError(f"cannot read bridge secret file: {type(exc).__name__}") from exc
    if stat.st_mode & 0o077:
        raise BridgeError("bridge secret file must not be accessible by group or other")
    values: dict[str, str] = {}
    for number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise BridgeError(f"invalid secret file line {number}")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key not in {"NTFY_SUBSCRIBER_TOKEN", "DISCORD_WEBHOOK_URL"}:
            raise BridgeError(f"unsupported key in secret file line {number}")
        if not value or len(value) > 4_096 or key in values:
            raise BridgeError(f"missing or duplicate value in secret file line {number}")
        values[key] = value
    missing = {"NTFY_SUBSCRIBER_TOKEN", "DISCORD_WEBHOOK_URL"} - values.keys()
    if missing:
        raise BridgeError("bridge secret file is missing required keys")
    if any(character.isspace() for character in values["NTFY_SUBSCRIBER_TOKEN"]) or _CONTROL_RE.search(
        values["NTFY_SUBSCRIBER_TOKEN"]
    ):
        raise BridgeError("NTFY_SUBSCRIBER_TOKEN contains invalid characters")
    if any(character.isspace() for character in values["DISCORD_WEBHOOK_URL"]) or _CONTROL_RE.search(
        values["DISCORD_WEBHOOK_URL"]
    ):
        raise BridgeError("DISCORD_WEBHOOK_URL contains invalid characters")
    try:
        webhook = urllib.parse.urlsplit(values["DISCORD_WEBHOOK_URL"])
        webhook_port = webhook.port
    except ValueError as exc:
        raise BridgeError("DISCORD_WEBHOOK_URL must be a Discord channel webhook URL") from exc
    path_parts = webhook.path.split("/")
    if (
        webhook.scheme != "https"
        or webhook.hostname not in {"discord.com", "discordapp.com"}
        or webhook.username is not None
        or webhook.password is not None
        or webhook_port is not None
        or webhook.fragment
        or len(path_parts) != 5
        or path_parts[:3] != ["", "api", "webhooks"]
        or not path_parts[3]
        or not path_parts[4]
    ):
        raise BridgeError("DISCORD_WEBHOOK_URL must be a Discord channel webhook URL")
    return values


def load_config() -> Config:
    secrets = read_secret_file(Path(os.environ.get("BRIDGE_SECRET_FILE", "/run/secrets/discord_bridge_env")))
    ntfy_url = os.environ.get("BRIDGE_NTFY_URL", "http://ntfy").rstrip("/")
    parsed = urllib.parse.urlsplit(ntfy_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise BridgeError("BRIDGE_NTFY_URL must be an HTTP(S) base URL")
    attempts = int(_number("BRIDGE_DISCORD_MAX_ATTEMPTS", "5", 1, 10))
    return Config(
        ntfy_url=ntfy_url,
        subscriber_token=secrets["NTFY_SUBSCRIBER_TOKEN"],
        webhook_url=secrets["DISCORD_WEBHOOK_URL"],
        state_file=Path(os.environ.get("BRIDGE_STATE_FILE", "/data/state.json")),
        state_max_age=int(_number("BRIDGE_STATE_MAX_AGE_SECONDS", "180", 30, 3600)),
        subscribe_timeout=_number("BRIDGE_SUBSCRIBE_TIMEOUT_SECONDS", "75", 10, 300),
        reconnect_max=_number("BRIDGE_RECONNECT_MAX_SECONDS", "30", 1, 300),
        discord_timeout=_number("BRIDGE_DISCORD_TIMEOUT_SECONDS", "15", 1, 60),
        discord_attempts=attempts,
        retry_max=_number("BRIDGE_RETRY_MAX_SECONDS", "30", 1, 300),
    )


def sanitize(value: Any, limit: int) -> str:
    text = str(value) if value is not None else ""
    text = _CONTROL_RE.sub(" ", text)
    text = _DISCORD_WEBHOOK_RE.sub("[REDACTED_DISCORD_WEBHOOK]", text)
    text = _AUTH_RE.sub(r"\1[REDACTED]", text)
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _TOKEN_RE.sub(r"\1[REDACTED]", text)
    text = _JWT_RE.sub("[REDACTED_TOKEN]", text)
    text = _NTFY_TOKEN_RE.sub("[REDACTED_TOKEN]", text)
    text = re.sub(r" {2,}", " ", text).strip()
    if len(text) > limit:
        text = text[: max(0, limit - 1)].rstrip() + "…"
    return text


def should_forward(event: dict[str, Any]) -> bool:
    if event.get("event") != "message" or event.get("topic") not in ALLOWED_TOPICS:
        return False
    try:
        if int(event.get("priority", 3)) < 4:
            return False
    except (TypeError, ValueError):
        return False
    tags = event.get("tags", [])
    if not isinstance(tags, list) or any(str(tag).lower() in EXCLUDED_TAGS for tag in tags):
        return False
    if event.get("attachment") or event.get("actions"):
        return False
    title = str(event.get("title", ""))
    message = str(event.get("message", ""))
    return ORIGIN_MARKER not in title and ORIGIN_MARKER not in message


def discord_payload(event: dict[str, Any]) -> bytes:
    topic = sanitize(event.get("topic", "unknown"), 64)
    title = sanitize(event.get("title", ""), MAX_TITLE_CHARS)
    message = sanitize(event.get("message", ""), MAX_MESSAGE_CHARS)
    try:
        priority = int(event.get("priority", 3))
    except (TypeError, ValueError):
        priority = 3
    heading = f"**{title}**\n" if title else ""
    content = f"{ORIGIN_MARKER}\n`{topic}` priority {priority}\n{heading}{message or '(no message)'}"
    content = content[:MAX_DISCORD_CONTENT_CHARS]
    return json.dumps(
        {"content": content, "allowed_mentions": {"parse": []}},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _retry_after(headers: Any, now: Callable[[], float]) -> float | None:
    value = headers.get("Retry-After") if headers is not None else None
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            parsed = email.utils.parsedate_to_datetime(value).timestamp()
            return max(0.0, parsed - now())
        except (TypeError, ValueError, OverflowError):
            return None


def deliver(
    config: Config,
    payload: bytes,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleeper: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
    now: Callable[[], float] = time.time,
) -> None:
    last_reason = "unknown failure"
    for attempt in range(1, config.discord_attempts + 1):
        request = urllib.request.Request(
            config.webhook_url,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "homelab-ntfy-discord-bridge/1"},
            method="POST",
        )
        retry_delay: float | None = None
        try:
            with opener(request, timeout=config.discord_timeout) as response:
                status = getattr(response, "status", None)
                if status is None:
                    status = response.getcode()
                if 200 <= status < 300:
                    return
                if status == 429 or 500 <= status < 600:
                    last_reason = f"Discord HTTP {status}"
                    retry_delay = _retry_after(response.headers, now) if status == 429 else None
                else:
                    raise PermanentDeliveryError(f"Discord returned non-retryable HTTP {status}")
        except urllib.error.HTTPError as exc:
            try:
                if exc.code == 429 or 500 <= exc.code < 600:
                    last_reason = f"Discord HTTP {exc.code}"
                    retry_delay = _retry_after(exc.headers, now) if exc.code == 429 else None
                else:
                    raise PermanentDeliveryError(f"Discord returned non-retryable HTTP {exc.code}") from exc
            finally:
                exc.close()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_reason = f"Discord transport {type(exc).__name__}"
        if attempt == config.discord_attempts:
            break
        base = retry_delay if retry_delay is not None else min(2 ** (attempt - 1), config.retry_max)
        delay = max(0.0, base) + min(1.0, max(0.0, jitter()))
        sleeper(min(config.retry_max, delay))
    raise DeliveryError(f"delivery exhausted {config.discord_attempts} attempts: {last_reason}")


class StateStore:
    def __init__(self, path: Path):
        self.path = path

    def read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def cursor(self) -> str | None:
        cursor = self.read().get("cursor")
        return cursor if isinstance(cursor, str) and 0 < len(cursor) <= 128 else None

    def write(self, cursor: str | None, status: str, error: str = "") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "cursor": cursor,
            "status": status,
            "updated_at": time.time(),
            "error": sanitize(error, MAX_ERROR_CHARS),
        }
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, separators=(",", ":"), sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)
        directory_fd = os.open(self.path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def subscription_url(config: Config, cursor: str | None) -> str:
    topics = ",".join(sorted(ALLOWED_TOPICS))
    url = f"{config.ntfy_url}/{topics}/json"
    if cursor:
        url += "?" + urllib.parse.urlencode({"since": cursor})
    return url


def process_event(
    event: dict[str, Any],
    config: Config,
    state: StateStore,
    deliverer: Callable[[Config, bytes], None] = deliver,
) -> bool:
    # ntfy also emits open/keepalive control records with IDs. Those IDs are
    # not message replay cursors and must never replace the last message ID.
    if event.get("event") != "message":
        return False
    message_id = event.get("id")
    if not isinstance(message_id, str) or not message_id or len(message_id) > 128:
        return False
    if should_forward(event):
        deliverer(config, discord_payload(event))
    state.write(message_id, "running")
    return True


def healthcheck(path: Path, max_age: int) -> int:
    state = StateStore(path).read()
    updated = state.get("updated_at")
    if state.get("status") != "running" or not isinstance(updated, (int, float)):
        return 1
    return 0 if 0 <= time.time() - updated <= max_age else 1


def run(config: Config) -> int:
    state = StateStore(config.state_file)
    stop_event = threading.Event()

    def request_stop(_signum: int, _frame: Any) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    reconnect_delay = 1.0
    cursor = state.cursor()
    state.write(cursor, "running")
    while not stop_event.is_set():
        request = urllib.request.Request(
            subscription_url(config, cursor),
            headers={
                "Authorization": f"Bearer {config.subscriber_token}",
                "Accept": "application/x-ndjson",
                "User-Agent": "homelab-ntfy-discord-bridge/1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=config.subscribe_timeout) as response:
                reconnect_delay = 1.0
                state.write(cursor, "running")
                while not stop_event.is_set():
                    line = response.readline(MAX_EVENT_BYTES + 1)
                    if not line:
                        break
                    if len(line) > MAX_EVENT_BYTES:
                        raise BridgeError("ntfy event exceeds size limit")
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise BridgeError("ntfy returned invalid JSON") from exc
                    if not isinstance(event, dict):
                        continue
                    if event.get("event") == "keepalive":
                        state.write(cursor, "running")
                        continue
                    if process_event(event, config, state):
                        cursor = event["id"]
        except PermanentDeliveryError as exc:
            error = sanitize(f"{type(exc).__name__}: {exc}", MAX_ERROR_CHARS)
            print(f"bridge halted at cursor after permanent failure: {error}", file=sys.stderr, flush=True)
            state.write(cursor, "blocked", error)
            stop_event.wait()
            break
        except (urllib.error.URLError, TimeoutError, OSError, BridgeError) as exc:
            error = sanitize(f"{type(exc).__name__}: {exc}", MAX_ERROR_CHARS)
            print(f"bridge reconnecting: {error}", file=sys.stderr, flush=True)
            state.write(cursor, "degraded", error)
        if not stop_event.is_set():
            stop_event.wait(reconnect_delay + random.random())
            reconnect_delay = min(config.reconnect_max, reconnect_delay * 2)
    state.write(cursor, "stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args(argv)
    if args.healthcheck:
        path = Path(os.environ.get("BRIDGE_STATE_FILE", "/data/state.json"))
        max_age = int(_number("BRIDGE_STATE_MAX_AGE_SECONDS", "180", 30, 3600))
        return healthcheck(path, max_age)
    try:
        return run(load_config())
    except BridgeError as exc:
        print(f"bridge fatal: {sanitize(exc, MAX_ERROR_CHARS)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
