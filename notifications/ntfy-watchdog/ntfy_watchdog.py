"""Independent ntfy public-health watchdog."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping
import datetime
import errno
import fcntl
import http.client
import json
import os
import stat
import urllib.error
import urllib.request


FAILURE_THRESHOLD = 3
RECOVERY_THRESHOLD = 2
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0 Safari/537.36 "
    "Hermes-Ntfy-Watchdog/1.0"
)


def probe_health(url: str, *, timeout: float = 10) -> tuple[bool, str]:
    try:
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(4096))
            if response.status == 200 and isinstance(payload, dict) and payload.get("healthy") is True:
                return True, ""
            return False, f"unexpected health response (HTTP {response.status})"
    except Exception as exc:
        return False, f"health probe failed: {type(exc).__name__}"


def initial_state() -> dict[str, Any]:
    return {
        "version": 1,
        "status": "unknown",
        "alerted": False,
        "consecutive_failures": 0,
        "consecutive_successes": 0,
        "last_probe_at": None,
        "last_error": None,
    }


def evaluate_result(
    state: dict[str, Any],
    *,
    healthy: bool,
    now: str,
    detail: str = "",
    failure_threshold: int = FAILURE_THRESHOLD,
    recovery_threshold: int = RECOVERY_THRESHOLD,
) -> tuple[dict[str, Any], str | None]:
    if (
        isinstance(failure_threshold, bool)
        or not isinstance(failure_threshold, int)
        or failure_threshold <= 0
        or isinstance(recovery_threshold, bool)
        or not isinstance(recovery_threshold, int)
        or recovery_threshold <= 0
    ):
        raise ValueError("watchdog thresholds must be positive integers")
    current = {**initial_state(), **state}
    current["last_probe_at"] = now
    event = None

    if healthy:
        current["consecutive_failures"] = 0
        current["consecutive_successes"] += 1
        current["last_error"] = None
        if current["alerted"] and current["consecutive_successes"] >= recovery_threshold:
            current["alerted"] = False
            event = "recovery"
        current["status"] = "healthy"
    else:
        current["consecutive_successes"] = 0
        current["consecutive_failures"] += 1
        current["last_error"] = detail
        if not current["alerted"] and current["consecutive_failures"] >= failure_threshold:
            current["alerted"] = True
            event = "outage"
        current["status"] = "down" if current["alerted"] else "degraded"

    return current, event


def load_webhook_url(path: Path) -> str:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise PermissionError("watchdog secret file must not be a symlink") from None
        raise
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise PermissionError("watchdog secret must be a regular file")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise PermissionError("watchdog secret file must not be group/world accessible")
        with os.fdopen(descriptor, "r") as handle:
            descriptor = -1
            content = handle.read(8193)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(content) > 8192:
        raise ValueError("watchdog secret file is too large")
    values = {}
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, value = stripped.split("=", 1)
            values[key.strip()] = value.strip()
    webhook_url = values.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url.startswith("https://"):
        raise ValueError("DISCORD_WEBHOOK_URL must use HTTPS")
    return webhook_url


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return initial_state()
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("watchdog state must be a JSON object")
    return {**initial_state(), **payload}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(state, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def _discord_content(event: str, state: dict[str, Any]) -> str:
    if event == "outage":
        return (
            "**ntfy outage detected**\n"
            f"Public health probe failed {state['consecutive_failures']} consecutive times.\n"
            f"Last result: {state.get('last_error') or 'unhealthy response'}\n"
            "Source: independent OpenClaw watchdog (not ntfy)."
        )
    if event == "recovery":
        return (
            "**ntfy service recovered**\n"
            f"Public health probe passed {state['consecutive_successes']} consecutive times.\n"
            "Source: independent OpenClaw watchdog (not ntfy)."
        )
    raise ValueError(f"unsupported watchdog event: {event}")


def post_discord(webhook_url: str, event: str, state: dict[str, Any], *, timeout: float = 10) -> None:
    payload = {
        "content": _discord_content(event, state),
        "allowed_mentions": {"parse": []},
    }
    try:
        request = urllib.request.Request(
            webhook_url,
            method="POST",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_status = response.status
    except (http.client.InvalidURL, ValueError):
        raise RuntimeError("Discord webhook delivery failed (invalid URL)") from None
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Discord webhook delivery failed (HTTP {exc.code})") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Discord webhook delivery failed ({type(exc).__name__})") from None
    except Exception as exc:
        raise RuntimeError(f"Discord webhook delivery failed ({type(exc).__name__})") from None
    if response_status not in (200, 204):
        raise RuntimeError(f"Discord webhook delivery failed (HTTP {response_status})")


def lock_path_for(state_path: Path) -> Path:
    return state_path.with_name(f"{state_path.name}.lock")


@contextmanager
def run_lock(state_path: Path) -> Iterator[bool]:
    path = lock_path_for(state_path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    os.fchmod(descriptor, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            pass
        yield acquired
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def run_once(
    *,
    probe_url: str,
    webhook_url: str,
    state_path: Path,
    now: str,
    failure_threshold: int = FAILURE_THRESHOLD,
    recovery_threshold: int = RECOVERY_THRESHOLD,
    timeout: float = 10,
) -> str | None:
    with run_lock(state_path) as acquired:
        if not acquired:
            return None
        previous = load_state(state_path)
        healthy, detail = probe_health(probe_url, timeout=timeout)
        current, event = evaluate_result(
            previous,
            healthy=healthy,
            now=now,
            detail=detail,
            failure_threshold=failure_threshold,
            recovery_threshold=recovery_threshold,
        )
        if event is not None:
            post_discord(webhook_url, event, current, timeout=timeout)
        save_state(state_path, current)
        return event


def main(*, environ: Mapping[str, str] | None = None, now: str | None = None) -> int:
    environment = os.environ if environ is None else environ
    hermes_home = Path(
        environment.get(
            "HERMES_HOME",
            str(Path.home() / ".hermes" / "profiles" / "homelab"),
        )
    )
    probe_url = environment.get(
        "NTFY_WATCHDOG_URL",
        "https://notify.walshit.com/v1/health",
    )
    secret_path = Path(
        environment.get(
            "NTFY_WATCHDOG_SECRET_FILE",
            str(hermes_home / "secrets" / "ntfy-watchdog.env"),
        )
    )
    state_path = Path(
        environment.get(
            "NTFY_WATCHDOG_STATE_FILE",
            str(hermes_home / "runtime" / "ntfy-watchdog" / "state.json"),
        )
    )
    timestamp = now or datetime.datetime.now(datetime.timezone.utc).isoformat()
    webhook_url = load_webhook_url(secret_path)
    run_once(
        probe_url=probe_url,
        webhook_url=webhook_url,
        state_path=state_path,
        now=timestamp,
        failure_threshold=int(environment.get("NTFY_WATCHDOG_FAILURE_THRESHOLD", FAILURE_THRESHOLD)),
        recovery_threshold=int(environment.get("NTFY_WATCHDOG_RECOVERY_THRESHOLD", RECOVERY_THRESHOLD)),
        timeout=float(environment.get("NTFY_WATCHDOG_TIMEOUT_SECONDS", "10")),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
