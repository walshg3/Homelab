"""Bounded public HTTPS probes and their deliberately minimal outcomes."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import ipaddress
import json
import math
import os
import re
import signal
import ssl
import stat
import threading
import time
import tomllib
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import xml.etree.ElementTree as ElementTree

from publisher.privacy import assert_public_safe
from publisher.schema import COMPONENTS
from publisher.statemachine import PROBE_OUTCOMES


CONFIG_SCHEMA_VERSION = "1"
MAX_CONFIG_BYTES = 32 * 1024
MAX_CONFIG_TEXT_LENGTH = 2_048
MAX_RESPONSE_BYTES = 64 * 1024
MAX_JSON_NODES = 512
MAX_JSON_DEPTH = 8
MAX_JSON_TEXT = 32 * 1024
MAX_XML_ELEMENTS = 128
MAX_XML_DEPTH = 8
MAX_PLEX_CONNECTIONS = 8
_USER_AGENT = "Walshit-Status-Probe/1"
_PLEX_PRODUCT = "Walshit Status Probe"
_PLEX_CLIENT_IDENTIFIER = "walshit-status-public-probe-v1"

_KINDS = {
    "watch": "plex-composite",
    "home": "json-equals",
    "requests": "json-requires-string",
    "audiobooks": "text-exact",
    "site": "text-exact",
}
_URLS = {
    "watch": {
        "web_url": "https://app.plex.tv/desktop/",
        "resources_url": (
            "https://plex.tv/api/v2/resources?"
            "includeHttps=1&includeRelay=1&includeIPv6=1"
        ),
    },
    "home": {"url": "https://home.walshit.com/api/health"},
    "requests": {"url": "https://requests.walshit.com/api/v1/status"},
    "audiobooks": {"url": "https://books.walshit.com/healthcheck"},
    "site": {"url": "https://walshit.com/healthz.txt"},
}
_COMMON_KEYS = {
    "component_id",
    "kind",
    "slow_seconds",
    "timeout_seconds",
    "follow_redirects",
}
_TIMINGS = {
    "watch": (3.0, 10.0),
    "home": (2.0, 10.0),
    "requests": (2.0, 10.0),
    "audiobooks": (2.0, 10.0),
    "site": (2.0, 10.0),
}


@dataclass(frozen=True, slots=True)
class CheckConfig:
    """One immutable check with only its public, non-secret settings."""

    component_id: str
    kind: str
    slow_seconds: float
    timeout_seconds: float
    follow_redirects: bool
    url: str | None = None
    web_url: str | None = None
    resources_url: str | None = None

    def to_document(self):
        document = {
            "component_id": self.component_id,
            "kind": self.kind,
            "slow_seconds": self.slow_seconds,
            "timeout_seconds": self.timeout_seconds,
            "follow_redirects": self.follow_redirects,
        }
        for key in ("url", "web_url", "resources_url"):
            value = getattr(self, key)
            if value is not None:
                document[key] = value
        return document


@dataclass(frozen=True, slots=True)
class ProbeConfig:
    """Recursively immutable versioned collection of canonical checks."""

    schema_version: str
    checks: tuple[CheckConfig, ...]

    def to_document(self):
        return {
            "schema_version": self.schema_version,
            "checks": [check.to_document() for check in self.checks],
        }


def _safe_public_url(value, expected, field):
    if type(value) is not str or not value or len(value) > MAX_CONFIG_TEXT_LENGTH:
        raise TypeError(f"{field} must be bounded text")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{field} is not a safe public URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
        or parsed.fragment
        or port not in (None, 443)
    ):
        raise ValueError(f"{field} is not a safe public HTTPS URL")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost"} or hostname.endswith((".local", ".internal")):
        raise ValueError(f"{field} cannot target a private hostname")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError(f"{field} cannot use a literal IP address")
    if value != expected:
        raise ValueError(f"{field} is not the canonical public target")
    return value


def _positive_number(value, field):
    if type(value) not in (int, float):
        raise TypeError(f"{field} must be a number")
    converted = float(value)
    if not math.isfinite(converted) or converted <= 0:
        raise ValueError(f"{field} must be finite and positive")
    return converted


def _parse_check(document, index):
    if type(document) is not dict:
        raise TypeError(f"checks[{index}] must be a table")
    component_id = document.get("component_id")
    if type(component_id) is not str or component_id not in COMPONENTS:
        raise ValueError(f"checks[{index}] has an unknown component")
    url_keys = set(_URLS[component_id])
    if set(document) != _COMMON_KEYS | url_keys:
        raise ValueError(f"checks[{index}] has an invalid shape")
    if document["kind"] != _KINDS[component_id]:
        raise ValueError(f"checks[{index}] has the wrong kind")
    if type(document["follow_redirects"]) is not bool:
        raise TypeError(f"checks[{index}].follow_redirects must be a boolean")
    if document["follow_redirects"]:
        raise ValueError("redirects are prohibited")
    slow = _positive_number(document["slow_seconds"], "slow_seconds")
    timeout = _positive_number(document["timeout_seconds"], "timeout_seconds")
    if (slow, timeout) != _TIMINGS[component_id]:
        raise ValueError("check timing does not match the authoritative contract")
    urls = {
        key: _safe_public_url(
            document[key], expected, f"checks[{index}].{key}"
        )
        for key, expected in _URLS[component_id].items()
    }
    return CheckConfig(
        component_id=component_id,
        kind=document["kind"],
        slow_seconds=slow,
        timeout_seconds=timeout,
        follow_redirects=False,
        **urls,
    )


def parse_check_config(text) -> ProbeConfig:
    """Parse and validate one bounded canonical TOML configuration."""
    if type(text) is not str:
        raise TypeError("config must be text")
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_CONFIG_BYTES:
        raise ValueError("config is too large")
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError("config is not valid TOML") from exc
    if type(document) is not dict or set(document) != {"schema_version", "checks"}:
        raise ValueError("config has an invalid top-level shape")
    if document["schema_version"] != CONFIG_SCHEMA_VERSION:
        raise ValueError("config has an unsupported schema version")
    checks = document["checks"]
    if type(checks) is not list or len(checks) != len(COMPONENTS):
        raise ValueError("config must contain exactly five checks")
    parsed_checks = tuple(_parse_check(check, index) for index, check in enumerate(checks))
    component_ids = [check.component_id for check in parsed_checks]
    if len(component_ids) != len(set(component_ids)) or set(component_ids) != set(COMPONENTS):
        raise ValueError("config must contain the exact component registry")
    by_component = {check.component_id: check for check in parsed_checks}
    config = ProbeConfig(
        CONFIG_SCHEMA_VERSION,
        tuple(by_component[component_id] for component_id in COMPONENTS),
    )
    assert_public_safe(text, source="probe configuration")
    return config


def _read_config_file(path):
    try:
        supplied = os.fspath(path)
    except TypeError as exc:
        raise TypeError("config path must be path-like") from exc
    if type(supplied) is not str:
        raise TypeError("config path must resolve to text")
    absolute = os.path.abspath(supplied)
    parent, filename = os.path.split(absolute)
    if not filename:
        raise ValueError("config path must name a file")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if no_follow is None or directory_flag is None:
        raise ValueError("safe file operations are unavailable")
    directory_fd = os.open(os.path.sep, os.O_RDONLY | directory_flag | no_follow)
    try:
        for part in (item for item in parent.split(os.path.sep) if item):
            child_fd = os.open(
                part,
                os.O_RDONLY | directory_flag | no_follow | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = child_fd
        flags = os.O_RDONLY | no_follow | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        file_fd = os.open(filename, flags, dir_fd=directory_fd)
        try:
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_CONFIG_BYTES:
                raise ValueError("config must be a bounded regular file")
            data = bytearray()
            while len(data) <= MAX_CONFIG_BYTES:
                chunk = os.read(file_fd, min(8192, MAX_CONFIG_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            after = os.fstat(file_fd)
            if (
                len(data) > MAX_CONFIG_BYTES
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
            ):
                raise ValueError("config changed or exceeded its bound")
            return bytes(data).decode("utf-8")
        finally:
            os.close(file_fd)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError("config path is unsafe") from exc
        raise
    finally:
        os.close(directory_fd)


def load_check_config(path) -> ProbeConfig:
    """Load a bounded regular TOML file without following path symlinks."""
    return parse_check_config(_read_config_file(path))


@dataclass(frozen=True, slots=True, repr=False)
class HttpResponse:
    """Bounded response projection returned by an injected transport."""

    status: int
    content_type: str
    body: bytes

    def __repr__(self):
        return "HttpResponse(<redacted>)"

    def __post_init__(self):
        if type(self.status) is not int or not 100 <= self.status <= 599:
            raise ValueError("response status is invalid")
        if type(self.content_type) is not str or len(self.content_type) > 256:
            raise TypeError("response content type is invalid")
        if type(self.body) is not bytes:
            raise TypeError("response body must be bytes")


class ProbeLayerError(RuntimeError):
    """Fixed probe trust/configuration failure normalized to public no-data."""


class _ResponseTooLarge(OSError):
    pass


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _bounded_read(handle):
    length = handle.headers.get("Content-Length")
    if length is not None:
        if type(length) is not str or re.fullmatch(r"[0-9]+", length) is None:
            raise OSError("invalid response metadata")
        if int(length) > MAX_RESPONSE_BYTES:
            raise _ResponseTooLarge()
    body = handle.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise _ResponseTooLarge()
    return body


class StdlibHttpTransport:
    """Small verified-TLS, GET-only, no-redirect stdlib transport.

    On Unix in the main thread, a scoped SIGALRM bounds open plus body read.
    Other platforms and worker threads retain urllib's remaining timeout and
    the probe layer's post-call monotonic deadline validation.
    """

    def __init__(self, opener=None):
        if opener is None:
            context = ssl.create_default_context()
            opener = urllib.request.build_opener(
                _NoRedirectHandler(), urllib.request.HTTPSHandler(context=context)
            )
        if not callable(getattr(opener, "open", None)):
            raise TypeError("opener must provide open")
        self._opener = opener

    def __call__(self, url, *, headers, timeout):
        if type(timeout) not in (int, float):
            raise TypeError("timeout must be numeric")
        timeout = float(timeout)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        request = urllib.request.Request(url, headers=headers, method="GET")
        with _HardWallTimeout(timeout):
            try:
                handle = self._opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as error:
                handle = error
            with handle:
                status = getattr(handle, "status", None)
                if status is None:
                    status = handle.getcode()
                content_type = handle.headers.get("Content-Type", "")
                body = _bounded_read(handle)
            try:
                return HttpResponse(
                    status=status, content_type=content_type, body=body
                )
            except (TypeError, ValueError) as exc:
                raise OSError("invalid response metadata") from exc


def _raise_hard_timeout(_signal_number, _frame):
    raise TimeoutError()


class _HardWallTimeout:
    """Best-effort process-local wall timer that restores prior signal state."""

    def __init__(self, seconds):
        self._seconds = seconds
        self._active = False
        self._prior_handler = None
        self._prior_timer = None
        self._started = None

    def __enter__(self):
        if threading.current_thread() is not threading.main_thread():
            return self
        if not all(
            hasattr(signal, name)
            for name in ("SIGALRM", "ITIMER_REAL", "getitimer", "setitimer")
        ):
            return self
        try:
            prior_handler = signal.getsignal(signal.SIGALRM)
            prior_timer = signal.getitimer(signal.ITIMER_REAL)
            self._prior_handler = prior_handler
            self._prior_timer = prior_timer
            signal.signal(signal.SIGALRM, _raise_hard_timeout)
            self._started = time.monotonic()
            signal.setitimer(signal.ITIMER_REAL, self._seconds)
        except (OSError, ValueError):
            try:
                if self._prior_handler is not None:
                    signal.setitimer(signal.ITIMER_REAL, 0.0)
                    signal.signal(signal.SIGALRM, self._prior_handler)
                    prior_delay, prior_interval = self._prior_timer
                    signal.setitimer(
                        signal.ITIMER_REAL, prior_delay, prior_interval
                    )
            except (OSError, ValueError):
                pass
            return self
        self._active = True
        return self

    def __exit__(self, exception_type, exception, traceback):
        if not self._active:
            return False
        elapsed = max(0.0, time.monotonic() - self._started)
        try:
            signal.setitimer(signal.ITIMER_REAL, 0.0)
            signal.signal(signal.SIGALRM, self._prior_handler)
            prior_delay, prior_interval = self._prior_timer
            if prior_delay > 0.0:
                prior_delay = max(1e-6, prior_delay - elapsed)
            signal.setitimer(signal.ITIMER_REAL, prior_delay, prior_interval)
        finally:
            self._active = False
        return False


def _base_media_type(value):
    return value.split(";", 1)[0].strip().lower()


def _clock_value(clock):
    value = clock()
    if type(value) not in (int, float):
        raise TypeError("clock must return numbers")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("clock must be monotonic")
    return value


def _timed_request(transport, clock, url, headers, deadline):
    started = _clock_value(clock)
    remaining = deadline - started
    if not math.isfinite(remaining) or remaining <= 0:
        return None
    try:
        response = transport(url, headers=headers, timeout=remaining)
    except (OSError, TimeoutError, urllib.error.URLError):
        return None
    finished = _clock_value(clock)
    if type(response) is not HttpResponse:
        raise TypeError("transport must return HttpResponse")
    if len(response.body) > MAX_RESPONSE_BYTES:
        return None
    elapsed = finished - started
    if not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError("clock must be monotonic")
    if finished > deadline:
        return None
    return response, elapsed


def _walk_json(value, depth=0):
    if depth > MAX_JSON_DEPTH:
        raise ValueError("JSON is too deeply nested")
    nodes = 1
    text_size = 0
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise ValueError("JSON object key is invalid")
            text_size += len(key)
            child_nodes, child_text = _walk_json(child, depth + 1)
            nodes += child_nodes
            text_size += child_text
    elif type(value) is list:
        for child in value:
            child_nodes, child_text = _walk_json(child, depth + 1)
            nodes += child_nodes
            text_size += child_text
    elif type(value) is str:
        text_size += len(value)
    elif type(value) is float and not math.isfinite(value):
        raise ValueError("JSON number is not finite")
    elif value is not None and type(value) not in (bool, int, float):
        raise ValueError("JSON value is invalid")
    if nodes > MAX_JSON_NODES or text_size > MAX_JSON_TEXT:
        raise ValueError("JSON exceeds complexity bounds")
    return nodes, text_size


def _strict_json_object(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("JSON object contains a duplicate key")
        document[key] = value
    return document


def _reject_json_constant(value):
    raise ValueError("JSON contains a non-standard numeric constant")


def _json_body(response):
    if _base_media_type(response.content_type) != "application/json":
        return None
    try:
        value = json.loads(
            response.body.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
        _walk_json(value)
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
        return None
    return value


def _request_headers(accept):
    return {"Accept": accept, "User-Agent": _USER_AGENT}


def _public_http_outcome(check, transport, clock, deadline):
    requested = _timed_request(
        transport,
        clock,
        check.url,
        _request_headers(
            "application/json"
            if check.kind.startswith("json-")
            else "text/plain"
        ),
        deadline,
    )
    if requested is None:
        return "failure"
    response, elapsed = requested
    if response.status != 200:
        return "failure"
    if check.kind.startswith("json-"):
        body = _json_body(response)
        if type(body) is not dict:
            return "failure"
        if check.component_id == "home":
            valid = body.get("status") == "healthy"
        else:
            version = body.get("version")
            valid = type(version) is str and bool(version.strip())
    else:
        if _base_media_type(response.content_type) != "text/plain":
            return "failure"
        try:
            text = response.body.decode("utf-8").strip()
        except UnicodeError:
            return "failure"
        expected = "OK" if check.component_id == "audiobooks" else "walshit-landing-hugo-ok"
        valid = text == expected
    if not valid:
        return "failure"
    return "slow" if elapsed > check.slow_seconds else "success"


def _runtime_value(environ, key, maximum):
    try:
        value = environ.get(key)
    except AttributeError as exc:
        raise ProbeLayerError() from exc
    if (
        type(value) is not str
        or not 1 <= len(value) <= maximum
        or any(ord(character) < 33 or ord(character) > 126 for character in value)
    ):
        raise ProbeLayerError()
    return value


def _parse_resources(response, machine_identifier):
    value = _json_body(response)
    if type(value) is not list:
        raise ProbeLayerError()
    matching = []
    for device in value:
        if type(device) is not dict:
            raise ProbeLayerError()
        identifier = device.get("clientIdentifier")
        provides = device.get("provides")
        if type(identifier) is not str:
            raise ProbeLayerError()
        if type(provides) is str:
            provided = {item.strip() for item in provides.split(",") if item.strip()}
        elif type(provides) is list and all(type(item) is str for item in provides):
            provided = set(provides)
        else:
            raise ProbeLayerError()
        if identifier == machine_identifier and "server" in provided:
            matching.append(device)
    if len(matching) != 1:
        raise ProbeLayerError()
    connections = matching[0].get("connections")
    if type(connections) is not list:
        raise ProbeLayerError()
    remote = []
    for connection in connections:
        if type(connection) is not dict:
            raise ProbeLayerError()
        uri = connection.get("uri")
        local = connection.get("local")
        relay = connection.get("relay")
        if type(uri) is not str or type(local) is not bool or type(relay) is not bool:
            raise ProbeLayerError()
        if not local:
            remote.append((uri, relay))
    return remote[:MAX_PLEX_CONNECTIONS]


def _identity_url(uri):
    if (
        type(uri) is not str
        or not uri
        or len(uri) > MAX_CONFIG_TEXT_LENGTH
        or any(ord(character) < 33 or ord(character) > 126 for character in uri)
    ):
        raise ProbeLayerError()
    try:
        parsed = urlsplit(uri)
        port = parsed.port
    except ValueError as exc:
        raise ProbeLayerError() from exc
    hostname = parsed.hostname
    authority = parsed.netloc
    if hostname is not None:
        hostname = hostname.lower()
    labels = () if hostname is None else tuple(hostname.split("."))
    canonical_hostname = (
        hostname is not None
        and len(hostname) <= 253
        and all(
            1 <= len(label) <= 63
            and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
            is not None
            for label in labels
        )
    )
    if (
        uri.partition(":")[0] != "https"
        or parsed.scheme != "https"
        or hostname is None
        or not canonical_hostname
        or parsed.username is not None
        or parsed.password is not None
        or "%" in authority
        or authority.endswith(":")
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or port not in (None, 443, 32400)
        or not (hostname == "plex.direct" or hostname.endswith(".plex.direct"))
    ):
        raise ProbeLayerError()
    if ":" in authority:
        _host_text, separator, port_text = authority.rpartition(":")
        if not separator or port_text not in {"443", "32400"}:
            raise ProbeLayerError()
    return uri.rstrip("/") + "/identity"


def _valid_identity(response, machine_identifier):
    if response.status != 200 or _base_media_type(response.content_type) not in {
        "text/xml",
        "application/xml",
    }:
        return False
    lowered = response.body.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        return False
    try:
        root = ElementTree.fromstring(response.body)
    except ElementTree.ParseError:
        return False
    if root.tag != "MediaContainer":
        return False
    count = 0
    stack = [(root, 0)]
    while stack:
        element, depth = stack.pop()
        count += 1
        if count > MAX_XML_ELEMENTS or depth > MAX_XML_DEPTH:
            return False
        if len(element.attrib) > 32:
            return False
        stack.extend((child, depth + 1) for child in list(element))
    return root.attrib.get("machineIdentifier") == machine_identifier


def _plex_outcome(check, transport, clock, environ, started, deadline):
    token = _runtime_value(environ, "PLEX_TOKEN", 1_024)
    machine_identifier = _runtime_value(
        environ, "PLEX_MACHINE_IDENTIFIER", 256
    )
    if re.fullmatch(r"[A-Za-z0-9._-]+", machine_identifier) is None:
        raise ProbeLayerError()

    web_request = _timed_request(
        transport,
        clock,
        check.web_url,
        _request_headers("text/html"),
        deadline,
    )
    if web_request is None:
        return "failure"
    web, _web_elapsed = web_request
    if (
        web.status != 200
        or _base_media_type(web.content_type) != "text/html"
        or re.search(
            br"<title>\s*Plex\s*</title>", web.body, flags=re.IGNORECASE
        )
        is None
    ):
        return "failure"

    plex_headers = _request_headers("application/json")
    plex_headers.update(
        {
            "X-Plex-Token": token,
            "X-Plex-Product": _PLEX_PRODUCT,
            "X-Plex-Client-Identifier": _PLEX_CLIENT_IDENTIFIER,
        }
    )
    resources_request = _timed_request(
        transport,
        clock,
        check.resources_url,
        plex_headers,
        deadline,
    )
    if resources_request is None:
        raise ProbeLayerError()
    resources, _resources_elapsed = resources_request
    if resources.status != 200:
        raise ProbeLayerError()
    remote = _parse_resources(resources, machine_identifier)
    resources_validated = _clock_value(clock)
    if resources_validated < started:
        raise ValueError("clock must be monotonic")
    if resources_validated > deadline:
        raise ProbeLayerError()
    if not remote:
        return "failure"

    relay_working = False
    identity_headers = dict(plex_headers)
    identity_headers["Accept"] = "text/xml"
    for uri, relay in remote:
        identity_url = _identity_url(uri)
        identity_request = _timed_request(
            transport,
            clock,
            identity_url,
            identity_headers,
            deadline,
        )
        if identity_request is None:
            continue
        identity, _identity_elapsed = identity_request
        identity_valid = _valid_identity(identity, machine_identifier)
        classified_at = _clock_value(clock)
        total_elapsed = classified_at - started
        if not math.isfinite(total_elapsed) or total_elapsed < 0:
            raise ValueError("clock must be monotonic")
        if classified_at > deadline:
            return "failure"
        if not identity_valid:
            continue
        if relay:
            relay_working = True
        else:
            return "slow" if total_elapsed > check.slow_seconds else "success"
    if relay_working:
        classified_at = _clock_value(clock)
        if classified_at < started:
            raise ValueError("clock must be monotonic")
        if classified_at > deadline:
            return "failure"
        return "slow"
    return "failure"


def probe_check(check, *, transport, clock=time.monotonic, environ=None) -> ProbeResult:
    """Run one validated check and expose no metadata beyond its outcome."""
    if type(check) is not CheckConfig:
        raise TypeError("check must be a CheckConfig")
    if not callable(transport) or not callable(clock):
        raise TypeError("transport and clock must be callable")
    if environ is None:
        environ = os.environ
    started = _clock_value(clock)
    deadline = started + check.timeout_seconds
    try:
        if check.kind == "plex-composite":
            outcome = _plex_outcome(
                check, transport, clock, environ, started, deadline
            )
        else:
            outcome = _public_http_outcome(check, transport, clock, deadline)
    except ProbeLayerError:
        outcome = "error"
    return ProbeResult(check.component_id, outcome)


def run_probe_cycle(
    config,
    *,
    transport=None,
    clock=time.monotonic,
    environ=None,
) -> tuple[ProbeResult, ...]:
    """Run exactly one isolated check per component in canonical order."""
    if type(config) is not ProbeConfig:
        config = load_check_config(config)
    if transport is None:
        transport = StdlibHttpTransport()
    if environ is None:
        environ = os.environ
    results = []
    for check in config.checks:
        result = probe_check(
            check, transport=transport, clock=clock, environ=environ
        )
        results.append(result)
    return validate_complete_cycle(results)


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """One deliberately minimal probe outcome supplied by a caller."""

    component_id: str
    outcome: str

    def __post_init__(self):
        if type(self.component_id) is not str:
            raise TypeError("component_id must be a string")
        if self.component_id not in COMPONENTS:
            raise ValueError("component_id must be a public component")
        if type(self.outcome) is not str:
            raise TypeError("outcome must be a string")
        if self.outcome not in PROBE_OUTCOMES:
            raise ValueError("outcome must be a supported probe outcome")


def validate_complete_cycle(results) -> tuple[ProbeResult, ...]:
    """Validate one result per component and return canonical registry order."""
    if type(results) not in (list, tuple):
        raise TypeError("probe results must be a concrete sequence")
    if any(type(result) is not ProbeResult for result in results):
        raise TypeError("every probe result must be a ProbeResult")
    component_ids = [result.component_id for result in results]
    if len(component_ids) != len(set(component_ids)):
        raise ValueError("probe results contain duplicate components")
    if set(component_ids) != set(COMPONENTS):
        raise ValueError("probe results must contain every component exactly once")
    by_component = {result.component_id: result for result in results}
    return tuple(by_component[component_id] for component_id in COMPONENTS)
