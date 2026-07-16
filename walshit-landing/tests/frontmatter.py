"""Minimal stdlib-only TOML front matter parser and content-safety helpers.

Hugo content files in this repo use TOML front matter delimited by
``+++`` fences. We avoid third-party YAML/TOML packages so validation
runs with a bare python3 interpreter.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import tomllib

FRONTMATTER_RE = re.compile(r"\A\+\+\+\r?\n(.*?)\r?\n\+\+\+\r?\n?", re.DOTALL)

ALLOWED_SERVICES = {"plex", "framerr", "seerr", "audiobookshelf", "site-wide"}
ALLOWED_STATUS = {"planned", "in-progress", "resolved", "completed", "info"}
ALLOWED_SEVERITY = {"low", "medium", "high"}

SECRET_PATTERNS = [
    re.compile(r"(?i)api[_-]?key\s*[:=]"),
    re.compile(r"(?i)secret\s*[:=]"),
    re.compile(r"(?i)password\s*[:=]"),
    re.compile(r"(?i)bearer\s+[a-z0-9._-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]

PRIVATE_ADDRESS_PATTERNS = [
    re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"(?i)\.local\b"),
]


class FrontMatterError(ValueError):
    pass


def parse_frontmatter(text: str) -> dict:
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise FrontMatterError("no +++ TOML front matter block found")
    try:
        data = tomllib.loads(match.group(1))
    except tomllib.TOMLDecodeError as exc:
        raise FrontMatterError(f"invalid TOML front matter: {exc}") from exc
    if not isinstance(data, dict):
        raise FrontMatterError("front matter must decode to a TOML table")
    return data


def content_body(text: str) -> str:
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise FrontMatterError("no +++ TOML front matter block found")
    return text[match.end():]


def require_rfc3339_datetime(value, field: str) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError as exc:
            raise FrontMatterError(f"{field} is not a valid RFC3339 datetime: {value!r}") from exc
    else:
        raise FrontMatterError(f"{field} must be an RFC3339 datetime, got {value!r}")
    if dt.tzinfo is None:
        raise FrontMatterError(f"{field} must be timezone-aware: {value!r}")
    return dt


def load_markdown_files(directory: Path):
    return sorted(p for p in directory.rglob("*.md") if p.name != "_index.md")
