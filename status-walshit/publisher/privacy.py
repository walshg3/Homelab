"""Production-owned privacy checks for public status content."""

from __future__ import annotations

import ipaddress
import re


class LeakError(AssertionError):
    """Raised when content contains a private or secret-looking value."""


LEAK_PATTERNS = (
    (
        "credential marker",
        re.compile(
            r"(?i)\b(?:api[_-]?key|client[_-]?secret|secret|password|passphrase|"
            r"credentials?|authorization|(?:access|refresh)[_-]?tokens?|tokens?)"
            r"\b\s*[:=]"
        ),
    ),
    (
        "authorization value",
        re.compile(r"(?i)\b(?:bearer|basic)\s+[a-z0-9+/._=-]{16,}"),
    ),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "provider token",
        re.compile(
            r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b"
        ),
    ),
    (
        "credential URL",
        re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:]+:[^\s/@]+@"),
    ),
    ("private hostname", re.compile(r"(?i)(?:\blocalhost\b|\.local\b)")),
    (
        "internal POSIX path",
        re.compile(
            r"(?i)(?:^|[\s\"'=:(])/(?:home|root|srv|mnt|opt|etc|var/lib|"
            r"usr/local|tmp)(?:/|(?=$))"
        ),
    ),
    (
        "internal Windows path",
        re.compile(
            r"(?i)(?<![a-z0-9])(?:[a-z]:\\+[^\s\"']+|"
            r"\\{2,}[a-z0-9._-]+\\+[^\s\"']+)"
        ),
    ),
    (
        "media title field",
        re.compile(
            r"(?i)\b(?:(?:media|movie|series|episode|book|library)[_-]?(?:title|name)"
            r"|now[_-]?playing|current[_-]?(?:media|movie|series|episode|book)"
            r"|library[_-]?item)\s*[:=]"
        ),
    ),
    (
        "identity field",
        re.compile(
            r"(?i)\b(?:(?:operator|owner|admin|person|full)[_-]?name"
            r"|contact[_-]?person|personal[_-]?identity)\s*[:=]"
        ),
    ),
    (
        "natural-language identity",
        re.compile(
            r"\b(?:operator|owner|admin|administrator)\s+"
            r"[A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+)+\b"
        ),
    ),
    (
        "email identity",
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    ),
    (
        "recovery detail",
        re.compile(
            r"(?i)\b(?:recovery|restore|failover|backup)(?:[_-]|\s+)"
            r"(?:key|code|path|command|host|target|procedure|steps?|instructions?|details?)"
            r"\b(?:\s*[:=]|\s+(?:is|are|uses?|runs?|contains?|at)\b)?"
        ),
    ),
)

_IPV4_TOKEN_RE = re.compile(r"(?<![0-9.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9.])")
_IPV6_TOKEN_RE = re.compile(
    r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}"
    r"(?![0-9A-Fa-f:])"
)
_PRIVATE_IPV4_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "169.254.0.0/16",
    )
)
_PRIVATE_IPV6_NETWORKS = tuple(
    ipaddress.ip_network(value) for value in ("fc00::/7", "fe80::/10", "::1/128")
)
_EXPLICIT_SCHEME_RE = re.compile(
    r"(?i)(?<![a-z0-9+.-])(?:[a-z][a-z0-9+.-]*://|"
    r"(?:http|https|ftp|file|ssh|mailto|data|javascript):)"
)
_HOSTNAME_RE = re.compile(
    r"(?i)(?<![a-z0-9@-])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+"
    r"[a-z]{2,}(?![a-z0-9-])"
)
_URL_CONTINUATION_RE = re.compile(r"(?i)(?:[/?:#\\]|%(?:2f|3f|23|3a|5c))")


def has_disallowed_narrative_location(text):
    """Return whether text contains a URL-like form or non-public hostname."""
    if _EXPLICIT_SCHEME_RE.search(text):
        return True
    for match in _HOSTNAME_RE.finditer(text):
        if match.group(0).lower() != "walshit.com":
            return True
        if match.start() > 0 and text[match.start() - 1] in ".\\":
            return True
        if match.start() >= 2 and text[match.start() - 2 : match.start()] == "//":
            return True
        if _URL_CONTINUATION_RE.match(text, match.end()):
            return True
    return False


def _contains_private_ip(text):
    for match in _IPV4_TOKEN_RE.finditer(text):
        try:
            address = ipaddress.ip_address(match.group(0))
        except ValueError:
            continue
        if any(address in network for network in _PRIVATE_IPV4_NETWORKS):
            return True
    for match in _IPV6_TOKEN_RE.finditer(text):
        try:
            address = ipaddress.ip_address(match.group(0))
        except ValueError:
            continue
        if any(address in network for network in _PRIVATE_IPV6_NETWORKS):
            return True
    return False


def find_public_leaks(text):
    """Return deterministic labels for every prohibited public-data pattern."""
    if not isinstance(text, str):
        raise TypeError("public leak checks require text")
    matches = [label for label, pattern in LEAK_PATTERNS if pattern.search(text)]
    if _contains_private_ip(text):
        matches.append("private IP address")
    return matches


def assert_public_safe(text, *, source="public output"):
    """Raise ``LeakError`` if text contains a private-data pattern."""
    matches = find_public_leaks(text)
    if matches:
        raise LeakError(f"{source} contains prohibited patterns: {', '.join(matches)}")
