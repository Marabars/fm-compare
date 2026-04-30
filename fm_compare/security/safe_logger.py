"""
Safe logger: logs only technical events, never business data.
Forbidden: cell values, formulas, business keys, counterparty names,
           file paths, model row content, comments content.
Allowed: operation names, row counts, timing, errors without data.
"""
import logging
import os
import sys
from datetime import datetime
from pathlib import Path


_LOG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "FM_Compare" / "logs"
_logger: logging.Logger | None = None
_debug_mode = False


def _get_log_dir() -> Path:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR


def setup_logging(debug: bool = False) -> None:
    global _logger, _debug_mode
    _debug_mode = debug
    log_dir = _get_log_dir()
    log_file = log_dir / f"fm_compare_{datetime.now().strftime('%Y%m%d')}.log"

    _logger = logging.getLogger("fm_compare")
    _logger.setLevel(logging.DEBUG if debug else logging.INFO)
    _logger.handlers.clear()

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG if debug else logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    _logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    _logger.addHandler(ch)


def _ensure() -> logging.Logger:
    if _logger is None:
        setup_logging()
    return _logger  # type: ignore[return-value]


def info(msg: str) -> None:
    _ensure().info(msg)


def warning(msg: str) -> None:
    _ensure().warning(msg)


def error(msg: str) -> None:
    _ensure().error(msg)


def debug(msg: str) -> None:
    if _debug_mode:
        _ensure().debug(msg)


def debug_coords(sheet: str, row: int, col: int) -> None:
    """In debug mode logs cell coordinates only — never values."""
    if _debug_mode:
        _ensure().debug(f"cell coords: sheet_id={hash(sheet) & 0xFFFF} row={row} col={col}")


def cleanup_old_logs(keep_days: int = 7) -> None:
    try:
        cutoff = datetime.now().timestamp() - keep_days * 86400
        for f in _get_log_dir().glob("fm_compare_*.log"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except Exception:
        pass
