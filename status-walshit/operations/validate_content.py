"""Validate operator-authored status content without printing narrative."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from publisher.content import load_content


DEFAULT_CONTENT_ROOT = Path(__file__).resolve().parents[1] / "content"


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        raise ValueError("arguments rejected") from None


def _parser():
    parser = _SafeArgumentParser(prog="python3 -m operations.validate_content")
    parser.add_argument("content_root", nargs="?", default=DEFAULT_CONTENT_ROOT)
    return parser


def main(argv=None, *, stdout=None, stderr=None):
    """Print only event/issue counts and loader-sanitized issue identifiers."""
    if argv is None:
        argv = sys.argv[1:]
    if stdout is None:
        stdout = sys.stdout
    if stderr is None:
        stderr = sys.stderr
    try:
        arguments = _parser().parse_args(argv)
        result = load_content(Path(arguments.content_root))
        stdout.write(f"active_events={len(result.active_events)}\n")
        stdout.write(f"closed_events={len(result.closed_events)}\n")
        stdout.write(f"issues={len(result.issues)}\n")
        for issue in result.issues:
            stdout.write(f"issue {issue.relative_filename} {issue.code}\n")
        stdout.flush()
        return 1 if result.issues else 0
    except Exception:
        stderr.write("content-validation-failed\n")
        stderr.flush()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
