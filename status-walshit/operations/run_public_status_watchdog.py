#!/usr/bin/env python3
"""No-argument Hermes wrapper for the permanent public status watchdog."""

from __future__ import annotations

import os
from pathlib import Path
import sys


if __package__:
    from .public_status_watchdog import main as watchdog_main
else:
    script_directory = Path(__file__).resolve().parent
    if os.fspath(script_directory) not in sys.path:
        sys.path.insert(0, os.fspath(script_directory))
    repository_root = script_directory.parent
    if (repository_root / "publisher" / "schema.py").is_file():
        sys.path.insert(0, os.fspath(repository_root))
        from operations.public_status_watchdog import main as watchdog_main
    else:
        from public_status_watchdog import main as watchdog_main


def _hermes_home():
    value = os.environ.get("HERMES_HOME")
    if (
        not value
        or "\x00" in value
        or not os.path.isabs(value)
        or value == os.path.sep
        or os.path.normpath(value) != value
    ):
        raise ValueError("invalid profile")
    return Path(value)


def main():
    try:
        config = (
            _hermes_home()
            / "config"
            / "status-public-watchdog"
            / "watchdog.json"
        )
        return watchdog_main(["--config", os.fspath(config)])
    except Exception:
        sys.stderr.write("status-public-watchdog-wrapper-invalid\n")
        sys.stderr.flush()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
