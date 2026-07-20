"""Reusable public-output leak checks for status source and fixtures."""

import re


class LeakError(AssertionError):
    """Raised when content contains a private or secret-looking value."""


LEAK_PATTERNS = (
    ("API key", re.compile(r"(?i)\bapi[_-]?key\s*[:=]")),
    ("secret", re.compile(r"(?i)\bsecret\s*[:=]")),
    ("password", re.compile(r"(?i)\bpassword\s*[:=]")),
    ("bearer token", re.compile(r"(?i)\bbearer\s+[a-z0-9._-]{10,}")),
    ("token", re.compile(r"(?i)\b(?:access[_-]?)?token\s*[:=]\s*[a-z0-9._-]{8,}")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("provider token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b")),
    ("credential URL", re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:]+:[^\s/@]+@")),
    ("10/8 address", re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")),
    ("172.16/12 address", re.compile(r"\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b")),
    ("192.168/16 address", re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b")),
    ("loopback address", re.compile(r"\b127\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")),
    ("link-local address", re.compile(r"\b169\.254\.\d{1,3}\.\d{1,3}\b")),
    ("private IPv6 address", re.compile(r"(?i)(?:\b(?:fc|fd)[0-9a-f]{2}:|\bfe80:)")),
    ("private hostname", re.compile(r"(?i)(?:\blocalhost\b|\.local\b)")),
    ("internal POSIX path", re.compile(r"(?i)(?:^|[\s\"'=:(])/(?:home|root|srv|mnt|opt|etc|var/lib)/")),
    ("internal Windows path", re.compile(r"(?i)\b[a-z]:\\(?:users|windows|programdata)\\")),
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
    ("email identity", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)),
    (
        "recovery detail",
        re.compile(
            r"(?i)\b(?:recovery|restore|failover|backup)[_-]?"
            r"(?:key|code|path|command|host|target|procedure|steps?|instructions?|details?)\s*[:=]"
        ),
    ),
)


def find_public_leaks(text):
    """Return labels for every private-data pattern found in text."""
    if not isinstance(text, str):
        raise TypeError("public leak checks require text")
    return [label for label, pattern in LEAK_PATTERNS if pattern.search(text)]


def assert_public_safe(text, *, source="public output"):
    """Raise ``LeakError`` if text contains a private-data pattern."""
    matches = find_public_leaks(text)
    if matches:
        raise LeakError(f"{source} contains prohibited patterns: {', '.join(matches)}")
