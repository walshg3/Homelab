#!/usr/bin/env python3
"""No-argument Hermes wrapper for the recurring status soak watchdog."""

from __future__ import annotations

import os
from pathlib import Path
import sys


if __package__:
    from .soak_watchdog import main as watchdog_main
else:
    script_directory = Path(__file__).resolve().parent
    repository_root = script_directory.parent
    if (repository_root / "publisher" / "schema.py").is_file():
        sys.path.insert(0, os.fspath(repository_root))
        from operations.soak_watchdog import main as watchdog_main
    else:
        from soak_watchdog import main as watchdog_main


def _profile_home():
    value = os.environ.get("HERMES_HOME")
    if (
        not value
        or "\x00" in value
        or not os.path.isabs(value)
        or value == os.path.sep
        or os.path.normpath(value) != value
    ):
        raise ValueError("profile rejected")
    return Path(value)


def main():
    try:
        config = _profile_home() / "config" / "status-soak" / "watchdog.json"
        return watchdog_main(["run", "--config", os.fspath(config)])
    except Exception:
        sys.stderr.write("status-soak-wrapper-invalid\n")
        sys.stderr.flush()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
