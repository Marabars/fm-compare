"""
Cross-platform application data directory.

The desktop app historically stored settings/logs/dictionary under
%APPDATA%/FM_Compare (Windows-only). On Linux/Docker there is no APPDATA,
so this helper resolves a sensible directory on every platform.

Resolution order:
  1. FM_COMPARE_DATA_DIR   — explicit override (set in Docker/compose)
  2. %APPDATA%/FM_Compare  — Windows desktop (unchanged behaviour)
  3. ~/.fm_compare         — Linux/macOS fallback

The directory is NOT created here; callers create it (with mkdir) when they
actually write, matching the existing modules' behaviour.
"""
from __future__ import annotations

import os
from pathlib import Path

_APP_DIR_NAME = "FM_Compare"


def app_data_dir() -> Path:
    """Return the base directory for app data (settings, logs, dictionary)."""
    override = os.environ.get("FM_COMPARE_DATA_DIR")
    if override:
        return Path(override)

    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / _APP_DIR_NAME

    return Path.home() / ".fm_compare"
