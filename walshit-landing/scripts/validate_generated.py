#!/usr/bin/env python3
"""Validate a real Hugo output tree using only Python's standard library."""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path
import re
import sys
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

REQUIRED_FILES = {
    "index.html",
    "404.html",
    "sitemap.xml",
    "styles.css",
    "app.js",
    "healthz.txt",
    "walsh-ticket-crest.png",
    "updates/index.html",
    "updates/index.xml",
    "updates/welcome-to-updates/index.html",
    "guides/index.html",
    "guides/getting-started/index.html",
    "guides/requesting-media/index.html",
}
ALLOWED_EXTERNAL_ANCHOR_HOSTS = {
    "app.plex.tv",
    "home.walshit.com",
    "requests.walshit.com",
    "books.walshit.com",
    "bookrequests.walshit.com",
    "status.walshit.com",
    "buymeacoffee.com",
}
EXPECTED_PUBLIC_SCHEME = "https"
EXPECTED_PUBLIC_HOST = "walshit.com"
BROWSER_API_RE = re.compile(
    r"\b(fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon)\b"
)


class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.classes: set[str] = set()
        self.links: list[str] = []
        self.runtime_assets: list[tuple[str, str]] = []
        self.forms = 0
        self.iframes = 0
        self.inline_scripts = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        identifier = values.get("id")
        if identifier:
            self.ids.add(identifier)
        classes = values.get("class")
        if classes:
            self.classes.update(classes.split())
        href = values.get("href")
        if tag == "a" and href:
            self.links.append(href)
        if tag == "script":
            src = values.get("src")
            if src:
                self.runtime_assets.append(("script", src))
            else:
                self.inline_scripts += 1
        if tag == "link" and values.get("rel") == "stylesheet" and href:
            self.runtime_assets.append(("style", href))
        src = values.get("src")
        if tag in {"img", "source"} and src:
            self.runtime_assets.append((tag, src))
        if tag == "form":
            self.forms += 1
        if tag == "iframe":
            self.iframes += 1


def page_url(relative: str) -> str:
    if relative == "index.html":
        return "/"
    if relative.endswith("/index.html"):
        return "/" + relative[: -len("index.html")]
    return "/" + relative


def target_file(public: Path, path: str) -> Path:
    clean = path.lstrip("/")
    if not clean:
        return public / "index.html"
    candidate = public / clean
    if path.endswith("/") or candidate.is_dir():
        return candidate / "index.html"
    return candidate


def validate_public_url(value: str | None, context: str, errors: list[str]) -> None:
    if not value:
        errors.append(f"{context}: missing public URL")
        return
    parsed = urlparse(value.strip())
    if (
        parsed.scheme != EXPECTED_PUBLIC_SCHEME
        or parsed.hostname != EXPECTED_PUBLIC_HOST
        or parsed.port
    ):
        errors.append(f"{context}: expected https://walshit.com URL, got {value!r}")


def validate(public: Path) -> list[str]:
    errors: list[str] = []
    missing = sorted(name for name in REQUIRED_FILES if not (public / name).is_file())
    if missing:
        errors.append(f"missing generated files: {missing}")

    if (public / "healthz.txt").is_file():
        body = (public / "healthz.txt").read_text().strip()
        if body != "walshit-landing-hugo-ok":
            errors.append(f"unexpected health body: {body!r}")

    documents: dict[str, Document] = {}
    for path in sorted(public.rglob("*.html")):
        relative = path.relative_to(public).as_posix()
        parser = Document()
        try:
            parser.feed(path.read_text())
        except Exception as exc:
            errors.append(f"{relative}: HTML parse failed: {exc}")
            continue
        documents[relative] = parser
        if parser.forms:
            errors.append(f"{relative}: contains {parser.forms} form element(s)")
        if parser.iframes:
            errors.append(f"{relative}: contains {parser.iframes} iframe(s)")
        if parser.inline_scripts:
            errors.append(f"{relative}: contains {parser.inline_scripts} inline script(s)")

        base = page_url(relative)
        for kind, ref in parser.runtime_assets:
            parsed = urlparse(urljoin(base, ref))
            if parsed.scheme or parsed.netloc:
                errors.append(f"{relative}: external runtime {kind} asset {ref!r}")
                continue
            dest = target_file(public, parsed.path)
            if not dest.is_file():
                errors.append(f"{relative}: missing runtime {kind} asset {ref!r}")

        for href in parser.links:
            parsed_direct = urlparse(href)
            if parsed_direct.scheme in {"http", "https"}:
                if (
                    parsed_direct.scheme != "https"
                    or parsed_direct.hostname not in ALLOWED_EXTERNAL_ANCHOR_HOSTS
                    or parsed_direct.port is not None
                ):
                    errors.append(f"{relative}: unexpected external anchor {href!r}")
                continue
            if parsed_direct.scheme or parsed_direct.netloc or href.startswith(("mailto:", "tel:")):
                errors.append(f"{relative}: unsupported anchor {href!r}")
                continue
            parsed = urlparse(urljoin(base, href))
            dest = target_file(public, parsed.path)
            if not dest.is_file():
                errors.append(f"{relative}: broken internal link {href!r}")

    for relative in (
        "updates/welcome-to-updates/index.html",
        "guides/getting-started/index.html",
        "guides/requesting-media/index.html",
    ):
        parser = documents.get(relative)
        if parser and not {"article", "article-title", "article-body"}.issubset(parser.classes):
            errors.append(f"{relative}: missing themed article classes")

    for path in sorted(public.rglob("*.xml")):
        try:
            ET.parse(path)
        except ET.ParseError as exc:
            errors.append(f"{path.relative_to(public)}: XML parse failed: {exc}")
    feed = public / "updates" / "index.xml"
    if feed.is_file():
        tree = ET.parse(feed)
        channel = tree.find("./channel")
        if channel is None:
            errors.append("updates/index.xml: missing RSS channel")
        else:
            validate_public_url(channel.findtext("link"), "updates RSS channel link", errors)
        items = tree.findall("./channel/item")
        if not items:
            errors.append("updates/index.xml: RSS feed contains no item")
        for index, item in enumerate(items, start=1):
            validate_public_url(item.findtext("link"), f"updates RSS item {index} link", errors)
            validate_public_url(item.findtext("guid"), f"updates RSS item {index} GUID", errors)

    sitemap = public / "sitemap.xml"
    if sitemap.is_file():
        tree = ET.parse(sitemap)
        locations = [node.text for node in tree.iter() if node.tag.endswith("loc")]
        if not locations:
            errors.append("sitemap.xml: contains no locations")
        for index, value in enumerate(locations, start=1):
            validate_public_url(value, f"sitemap location {index}", errors)

    for script in sorted(public.rglob("*.js")):
        match = BROWSER_API_RE.search(script.read_text())
        if match:
            errors.append(f"{script.relative_to(public)}: forbidden browser API {match.group(1)}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-dir", required=True, type=Path)
    args = parser.parse_args()
    public = args.public_dir.resolve()
    if not public.is_dir():
        print(f"public directory does not exist: {public}", file=sys.stderr)
        return 2
    errors = validate(public)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("generated output validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
