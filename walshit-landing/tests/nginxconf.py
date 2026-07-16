"""Minimal nginx config block parser used to check add_header inheritance.

nginx only inherits ``add_header`` directives from an outer block into an
inner block (http -> server -> location) when the inner block defines none
of its own. A location that adds so much as one ``add_header`` (e.g. just
``Cache-Control``) silently drops every header set at the server level. This
module parses nginx.conf-style text into a block tree (respecting quoted
strings, since our CSP value itself contains literal semicolons) so tests
can compute the *effective* headers for a given location the way nginx
actually resolves them, instead of guessing from raw text.
"""
from __future__ import annotations

from pathlib import Path


class Directive:
    __slots__ = ("words",)

    def __init__(self, words):
        self.words = words

    @property
    def name(self):
        return self.words[0] if self.words else ""


class Block:
    def __init__(self, header_words, parent=None):
        self.header_words = header_words
        self.parent = parent
        self.directives = []  # Directive, in this block's own body (post include-expansion)
        self.children = []  # Block

    @property
    def header(self):
        return " ".join(self.header_words)

    def own_directives(self, name):
        return [d for d in self.directives if d.name == name]

    def effective_directives(self, name):
        """nginx: inherit from the parent only if this block defines none itself."""
        own = self.own_directives(name)
        if own:
            return own
        if self.parent is not None:
            return self.parent.effective_directives(name)
        return []


def _tokenize(text: str):
    tokens = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if ch == "#":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch in "{};":
            tokens.append(ch)
            i += 1
            continue
        if ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                if text[j] == "\\":
                    j += 1
                j += 1
            tokens.append(text[i:j + 1])
            i = j + 1
            continue
        j = i
        while j < n and text[j] not in " \t\r\n{};#":
            j += 1
        tokens.append(text[i:j])
        i = j
    return tokens


def _resolve_include_path(root: Path, raw_path: str) -> Path:
    unquoted = raw_path.strip('"')
    return root / Path(unquoted).name


def parse(text: str, root: Path) -> Block:
    """Parse nginx config text into a Block tree, inlining `include` directives.

    `root` is the directory used to resolve include file basenames (this is a
    static, pre-build check — it does not follow real filesystem-absolute
    paths like /etc/nginx/, it just matches by filename in `root`).
    """
    tokens = _tokenize(text)
    pos = 0

    def parse_block(header_words, parent):
        block = Block(header_words, parent)
        nonlocal pos
        words = []
        while pos < len(tokens):
            tok = tokens[pos]
            if tok == "{":
                pos += 1
                child = parse_block(words, block)
                block.children.append(child)
                words = []
                continue
            if tok == "}":
                pos += 1
                break
            if tok == ";":
                pos += 1
                if words and words[0] == "include":
                    inc_path = _resolve_include_path(root, words[1])
                    if inc_path.is_file():
                        inc_text = inc_path.read_text()
                        inc_directives, _ = _parse_directives_only(inc_text)
                        block.directives.extend(inc_directives)
                    else:
                        # Not one of our reviewed include files (e.g. the
                        # stock /etc/nginx/mime.types) -- irrelevant to
                        # add_header inheritance, keep as an opaque directive.
                        block.directives.append(Directive(words))
                elif words:
                    block.directives.append(Directive(words))
                words = []
                continue
            words.append(tok)
            pos += 1
        return block

    def _parse_directives_only(inc_text):
        inc_tokens = _tokenize(inc_text)
        directives = []
        words = []
        k = 0
        while k < len(inc_tokens):
            tok = inc_tokens[k]
            if tok == ";":
                if words:
                    directives.append(Directive(words))
                words = []
            else:
                words.append(tok)
            k += 1
        return directives, None

    return parse_block([], None)


def find_blocks(block: Block, predicate):
    matches = []
    if predicate(block):
        matches.append(block)
    for child in block.children:
        matches.extend(find_blocks(child, predicate))
    return matches
