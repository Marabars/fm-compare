"""
Persistent application settings stored locally.
Never stores file paths or business data — only UI preferences and thresholds.
"""
import json
import os
from pathlib import Path
from typing import Any


_SETTINGS_DIR = Path(os.environ.get("APPDATA", Path.home())) / "FM_Compare"
_SETTINGS_FILE = _SETTINGS_DIR / "settings.json"

_DEFAULTS: dict[str, Any] = {
    "mode": "full",                  # "full" | "quick"
    "materiality_abs": None,         # float | None
    "materiality_pct": None,         # float | None
    "top_x": 10,                     # int
    "include_comments": True,
    "include_hidden_rows": True,
    "output_dir": None,              # str | None — last used, not stored for safety
    "debug_mode": False,
    "last_sheets_v1": [],            # list[str] — sheet names selected last time
    "last_sheets_v2": [],
    "window_width": 960,
    "window_height": 700,
}


def load() -> dict[str, Any]:
    settings = dict(_DEFAULTS)
    try:
        if _SETTINGS_FILE.exists():
            with open(_SETTINGS_FILE, encoding="utf-8") as f:
                stored = json.load(f)
            for k, v in stored.items():
                if k in _DEFAULTS:
                    settings[k] = v
    except Exception:
        pass
    return settings


def save(settings: dict[str, Any]) -> None:
    try:
        _SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        safe = {k: v for k, v in settings.items() if k in _DEFAULTS}
        with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(safe, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get(key: str) -> Any:
    return load().get(key, _DEFAULTS.get(key))


def set_value(key: str, value: Any) -> None:
    s = load()
    s[key] = value
    save(s)


class AppSettings:
    """Thin object wrapper around the settings dict for convenient attribute access."""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    # --- attribute access ---
    @property
    def mode(self) -> str:
        return self._data["mode"]

    @mode.setter
    def mode(self, v: str) -> None:
        self._data["mode"] = v

    @property
    def materiality_abs(self) -> float | None:
        return self._data.get("materiality_abs")

    @materiality_abs.setter
    def materiality_abs(self, v: float | None) -> None:
        self._data["materiality_abs"] = v

    @property
    def materiality_pct(self) -> float | None:
        return self._data.get("materiality_pct")

    @materiality_pct.setter
    def materiality_pct(self, v: float | None) -> None:
        self._data["materiality_pct"] = v

    @property
    def top_x(self) -> int:
        return int(self._data.get("top_x", 10))

    @top_x.setter
    def top_x(self, v: int) -> None:
        self._data["top_x"] = v

    @property
    def include_comments(self) -> bool:
        return bool(self._data.get("include_comments", True))

    @include_comments.setter
    def include_comments(self, v: bool) -> None:
        self._data["include_comments"] = v

    @property
    def include_hidden_rows(self) -> bool:
        return bool(self._data.get("include_hidden_rows", True))

    @include_hidden_rows.setter
    def include_hidden_rows(self, v: bool) -> None:
        self._data["include_hidden_rows"] = v

    @property
    def output_dir(self) -> str | None:
        return self._data.get("output_dir")

    @output_dir.setter
    def output_dir(self, v: str | None) -> None:
        self._data["output_dir"] = v

    @property
    def debug_mode(self) -> bool:
        return bool(self._data.get("debug_mode", False))

    @debug_mode.setter
    def debug_mode(self, v: bool) -> None:
        self._data["debug_mode"] = v

    @property
    def last_sheets_v1(self) -> list[str]:
        return self._data.get("last_sheets_v1", [])

    @last_sheets_v1.setter
    def last_sheets_v1(self, v: list[str]) -> None:
        self._data["last_sheets_v1"] = v

    @property
    def last_sheets_v2(self) -> list[str]:
        return self._data.get("last_sheets_v2", [])

    @last_sheets_v2.setter
    def last_sheets_v2(self, v: list[str]) -> None:
        self._data["last_sheets_v2"] = v

    @property
    def window_width(self) -> int:
        return int(self._data.get("window_width", 960))

    @window_width.setter
    def window_width(self, v: int) -> None:
        self._data["window_width"] = v

    @property
    def window_height(self) -> int:
        return int(self._data.get("window_height", 700))

    @window_height.setter
    def window_height(self, v: int) -> None:
        self._data["window_height"] = v

    def save(self) -> None:
        save(self._data)


def load_settings() -> AppSettings:
    return AppSettings(load())
