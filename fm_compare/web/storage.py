"""
Temporary file storage for uploaded workbooks and generated reports.

Uploads live under <app_data_dir>/uploads/<job_id>/. Files are financial
data, so they are kept only as long as needed and cleaned up by TTL.
File paths are never logged (safe_logger policy).
"""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from fm_compare.core.paths import app_data_dir
from fm_compare.security import safe_logger as log

_UPLOAD_ROOT = app_data_dir() / "uploads"
_TTL_SECONDS = 6 * 3600  # keep job files for 6 hours

_ALLOWED_SUFFIXES = {".xlsx", ".xlsm"}
_JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def job_dir(job_id: str) -> Path:
    if not _JOB_ID_RE.fullmatch(job_id):
        raise ValueError("Invalid job_id format")
    d = _UPLOAD_ROOT / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_upload(job_id: str, slot: str, filename: str, data: bytes) -> Path:
    """
    Persist an uploaded workbook to disk and return its Path.

    `slot` is "v1" or "v2". The original extension is preserved (validated);
    the stored name is deterministic so the engine can re-open it by path.
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise ValueError(
            f"Поддерживаются только файлы Excel {sorted(_ALLOWED_SUFFIXES)}; получено '{suffix}'."
        )
    target = job_dir(job_id) / f"{slot}{suffix}"
    target.write_bytes(data)
    log.info(f"Upload stored: job={job_id} slot={slot} size_kb={len(data)//1024}")
    return target


def cleanup_expired() -> None:
    """Remove job directories older than the TTL. Best-effort; never raises."""
    try:
        if not _UPLOAD_ROOT.exists():
            return
        cutoff = time.time() - _TTL_SECONDS
        for d in _UPLOAD_ROOT.iterdir():
            try:
                if d.is_dir() and d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                continue
    except Exception:
        pass
