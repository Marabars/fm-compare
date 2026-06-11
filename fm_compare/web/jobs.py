"""
In-memory job registry for comparison runs.

A comparison can take minutes for large workbooks, so each run executes on a
background thread (the engine is already designed for this — it accepts a
progress(pct, msg) callback). The browser polls /status for progress.

State is intentionally in-memory: a single-instance service, restart clears
jobs. No business data is logged here.
"""
from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fm_compare.core.engine import run_compare
from fm_compare.core.models import CompareResult, CellAddress, SensitivityInput, SensitivityResult
from fm_compare.core.app_settings import AppSettings
from fm_compare.core.business_dictionary import load_dictionary
from fm_compare.security import safe_logger as log

_EXECUTOR = ThreadPoolExecutor(max_workers=4)
_LOCK = threading.Lock()


@dataclass
class JobState:
    job_id: str
    status: str = "created"            # created | uploaded | running | done | error
    progress: int = 0
    message: str = ""
    error: str | None = None
    result: CompareResult | None = None
    # paths + selections captured during the workflow
    path_v1: Path | None = None
    path_v2: Path | None = None
    sheets_v1: list[str] = field(default_factory=list)
    sheets_v2: list[str] = field(default_factory=list)
    top_x: int = 10
    # dashboard state: cached resolutions and built payload
    resolutions: list | None = None   # list[KPIResolution] after resolve-preview
    dashboard: dict | None = None     # last built_dashboard payload
    # sensitivity analysis state
    sensitivity_status: str = "idle"  # idle | running | done | error
    sensitivity_result: SensitivityResult | None = None
    sensitivity_error: str | None = None
    # Stage 4 — V3 (optional third version)
    path_v3: Path | None = None
    sheets_v3: list[str] = field(default_factory=list)
    result_v2_v3: CompareResult | None = None
    status_v2_v3: str = "idle"    # idle | running | done | error
    error_v2_v3: str | None = None
    progress_v2_v3: int = 0
    message_v2_v3: str = ""


_JOBS: dict[str, JobState] = {}


def create_job() -> JobState:
    job_id = uuid.uuid4().hex
    state = JobState(job_id=job_id)
    with _LOCK:
        _JOBS[job_id] = state
    return state


def get_job(job_id: str) -> JobState | None:
    with _LOCK:
        return _JOBS.get(job_id)


def start_compare(
    job: JobState,
    settings: AppSettings,
    kpi_overrides_v1: dict[str, CellAddress],
    kpi_overrides_v2: dict[str, CellAddress],
    kpi_unit_overrides: dict[str, str],
) -> None:
    """Submit the comparison to the thread pool and return immediately."""
    job.status = "running"
    job.progress = 0
    job.message = "Запуск сравнения…"
    job.top_x = settings.top_x

    def _progress(pct: int, msg: str) -> None:
        job.progress = pct
        job.message = msg

    def _run() -> None:
        try:
            bd = load_dictionary()
            result = run_compare(
                job.path_v1,
                job.path_v2,
                bd,
                settings,
                job.sheets_v1,
                job.sheets_v2,
                progress=_progress,
                kpi_overrides_v1=kpi_overrides_v1 or None,
                kpi_overrides_v2=kpi_overrides_v2 or None,
                kpi_unit_overrides=kpi_unit_overrides or None,
            )
            job.result = result
            job.progress = 100
            job.message = "Готово."
            job.status = "done"
            log.info(f"Compare done: job={job.job_id}")
        except Exception as e:
            job.status = "error"
            # Message may surface to the user; engine raises plain Russian text
            # for known cases (e.g. no common sheets). Type name otherwise.
            job.error = str(e) or type(e).__name__
            log.error(f"Compare failed: job={job.job_id} ({type(e).__name__})")

    _EXECUTOR.submit(_run)


def start_compare_v2_v3(
    job: JobState,
    settings: AppSettings,
    kpi_overrides_v2: dict[str, CellAddress],
    kpi_overrides_v3: dict[str, CellAddress],
    kpi_unit_overrides: dict[str, str],
) -> None:
    """Submit V2 vs V3 comparison (V2 plays the role of 'v1', V3 plays 'v2')."""
    job.status_v2_v3 = "running"
    job.progress_v2_v3 = 0
    job.message_v2_v3 = "Запуск сравнения V2 vs V3…"

    def _progress(pct: int, msg: str) -> None:
        job.progress_v2_v3 = pct
        job.message_v2_v3 = msg

    def _run() -> None:
        try:
            bd = load_dictionary()
            result = run_compare(
                job.path_v2,
                job.path_v3,
                bd,
                settings,
                job.sheets_v2,
                job.sheets_v3,
                progress=_progress,
                kpi_overrides_v1=kpi_overrides_v2 or None,
                kpi_overrides_v2=kpi_overrides_v3 or None,
                kpi_unit_overrides=kpi_unit_overrides or None,
            )
            job.result_v2_v3 = result
            job.progress_v2_v3 = 100
            job.message_v2_v3 = "Готово."
            job.status_v2_v3 = "done"
            log.info(f"Compare V2 vs V3 done: job={job.job_id}")
        except Exception as e:
            job.status_v2_v3 = "error"
            job.error_v2_v3 = str(e) or type(e).__name__
            log.error(f"Compare V2 vs V3 failed: job={job.job_id} ({type(e).__name__})")

    _EXECUTOR.submit(_run)


def start_sensitivity(
    job: JobState,
    inputs: list[SensitivityInput],
    kpi_addrs: dict[str, CellAddress],
    timeout: int = 90,
) -> None:
    """Submit a sensitivity analysis run to the thread pool and return immediately."""
    job.sensitivity_status = "running"
    job.sensitivity_result = None
    job.sensitivity_error = None

    def _run() -> None:
        from fm_compare.core.sensitivity import run_scenarios
        from fm_compare.core.recalc import RecalcError
        try:
            result = run_scenarios(job.path_v1, inputs, kpi_addrs, timeout=timeout)
            job.sensitivity_result = result
            job.sensitivity_status = "done"
            log.info(f"Sensitivity done: job={job.job_id}")
        except RecalcError as e:
            job.sensitivity_status = "error"
            job.sensitivity_error = str(e)
            log.error(f"Sensitivity recalc error: job={job.job_id} ({e})")
        except Exception as e:
            job.sensitivity_status = "error"
            job.sensitivity_error = str(e) or type(e).__name__
            log.error(f"Sensitivity failed: job={job.job_id} ({type(e).__name__})")

    _EXECUTOR.submit(_run)
