"""
HTTP routes for the FM Compare web service.

Reproduces the desktop two-phase workflow:
  upload -> resolve-preview (KPI) -> run -> status -> summary -> report.xlsx
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse
from starlette.background import BackgroundTask

from fm_compare.core.app_settings import AppSettings, load
from fm_compare.core.business_dictionary import load_dictionary
from fm_compare.core.excel_reader import get_sheet_names, load_workbook_quick, rename_sheets
from fm_compare.core.kpi_extractor import resolve_kpis_preview, extract_kpis, build_kpi_comparison
from fm_compare.core.kpi_resolver import resolutions_to_overrides, parse_cell_address
from fm_compare.core.models import KPIResolution, CompareMode, CellAddress
from fm_compare.core.report_exporter import export_report, suggest_filename
from fm_compare.security import safe_logger as log

from fm_compare.web import auth, jobs, storage
from fm_compare.web.dashboard import build_dashboard, auto_resolutions
from fm_compare.web.serialization import resolution_to_json, summary_payload, correction_to_json
from fm_compare.core.agent.kpi_validator import validate_resolutions
from fm_compare.core.llm.summary_llm import enhance_summary
from fm_compare.core.llm.chat import stream_chat_turn

router = APIRouter()

_MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB per file


# ── Auth pages ──────────────────────────────────────────────────────────────

@router.post("/login")
async def login(request: Request, password: str = Form("")):
    if not auth.check_password(password):
        return RedirectResponse(url="/login?error=1", status_code=302)
    resp = RedirectResponse(url="/", status_code=302)
    resp.set_cookie(
        auth.COOKIE_NAME, auth.session_token(),
        httponly=True, samesite="lax", max_age=12 * 3600,
    )
    return resp


@router.post("/api/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE_NAME)
    return resp


# ── Workflow API ──────────────────────────────────────────────────────────────

@router.post("/api/upload")
async def upload(
    v1: UploadFile = File(...),
    v2: UploadFile = File(...),
    v3: UploadFile | None = File(None),
):
    """Upload V1 and V2 workbooks (required) plus optional V3; return job_id and sheet lists."""
    storage.cleanup_expired()
    job = jobs.create_job()
    try:
        for slot, f in [("v1", v1), ("v2", v2)] + ([("v3", v3)] if v3 and v3.filename else []):
            data = await f.read()
            if len(data) > _MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"Файл {slot} слишком большой (макс. 100 МБ)",
                )
            path = storage.save_upload(job.job_id, slot, f.filename, data)
            if slot == "v1":
                job.path_v1 = path
            elif slot == "v2":
                job.path_v2 = path
            else:
                job.path_v3 = path
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    sheets_v1 = get_sheet_names(job.path_v1)
    sheets_v2 = get_sheet_names(job.path_v2)
    sheets_v3 = get_sheet_names(job.path_v3) if job.path_v3 else []
    job.status = "uploaded"
    return {
        "job_id": job.job_id,
        "sheets_v1": sheets_v1,
        "sheets_v2": sheets_v2,
        "sheets_v3": sheets_v3,
        "has_v3": bool(job.path_v3),
    }


@router.post("/api/{job_id}/resolve-preview")
async def resolve_preview(job_id: str, request: Request):
    """Phase 1: auto-detect KPI addresses/units for the selected sheets."""
    job = jobs.get_job(job_id)
    if job is None or not job.path_v1 or not job.path_v2:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    body = await request.json()
    job.sheets_v1 = list(body.get("sheets_v1") or [])
    job.sheets_v2 = list(body.get("sheets_v2") or [])
    job.sheets_v3 = list(body.get("sheets_v3") or [])
    if not job.sheets_v1 or not job.sheets_v2:
        raise HTTPException(status_code=422, detail="Выберите листы в обоих файлах")

    bd = load_dictionary()
    wb_v1 = load_workbook_quick(job.path_v1, job.sheets_v1)
    wb_v2 = load_workbook_quick(job.path_v2, job.sheets_v2)
    wb_v3 = load_workbook_quick(job.path_v3, job.sheets_v3) if (job.path_v3 and job.sheets_v3) else None
    resolutions = resolve_kpis_preview(wb_v1, wb_v2, bd, wb_v3=wb_v3)
    # LLM validation with graceful fallback (no-op if gateway unavailable)
    resolutions, corrections = validate_resolutions(resolutions, wb_v1, wb_v2)
    job.resolutions = resolutions
    return {
        "resolutions": [resolution_to_json(r) for r in resolutions],
        "corrections": [correction_to_json(c) for c in corrections],
    }


# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/api/{job_id}/dashboard")
async def get_dashboard(job_id: str):
    """
    Return the KPI comparison dashboard (left=v1, center=Δ, right=v2).

    Builds from auto-detected or previously confirmed resolutions.
    Cached per job after first build; call POST to invalidate with overrides.
    """
    job = jobs.get_job(job_id)
    if job is None or not job.path_v1 or not job.path_v2:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if not job.sheets_v1 or not job.sheets_v2:
        raise HTTPException(status_code=422, detail="Сначала выберите листы (resolve-preview)")

    if job.dashboard is not None:
        return job.dashboard

    bd = load_dictionary()
    ov1, ov2, ov3 = _overrides_from_resolutions_for_dashboard(job.resolutions or [])
    payload = build_dashboard(
        job.path_v1, job.path_v2, bd, job.sheets_v1, job.sheets_v2, ov1, ov2,
        path_v3=job.path_v3 if job.sheets_v3 else None,
        sheets_v3=job.sheets_v3 or None,
        overrides_v3=ov3 or None,
    )
    # Also include the resolutions so the UI can show editable cell addresses.
    payload["resolutions"] = [resolution_to_json(r) for r in (job.resolutions or [])]
    job.dashboard = payload
    return payload


@router.post("/api/{job_id}/dashboard")
async def post_dashboard(job_id: str, request: Request):
    """
    Recalculate dashboard with inline address overrides from the UI.

    Body: {"resolutions": [...same schema as resolve-preview...]}
    Returns the same shape as GET /dashboard.
    """
    job = jobs.get_job(job_id)
    if job is None or not job.path_v1 or not job.path_v2:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if not job.sheets_v1 or not job.sheets_v2:
        raise HTTPException(status_code=422, detail="Сначала выберите листы (resolve-preview)")

    body = await request.json()
    ov1, ov2, ov3, errors = _parse_dashboard_overrides(body.get("resolutions") or [])
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors[:10]))

    bd = load_dictionary()
    payload = build_dashboard(
        job.path_v1, job.path_v2, bd, job.sheets_v1, job.sheets_v2, ov1, ov2,
        path_v3=job.path_v3 if job.sheets_v3 else None,
        sheets_v3=job.sheets_v3 or None,
        overrides_v3=ov3 or None,
    )
    payload["resolutions"] = body.get("resolutions") or []
    job.dashboard = payload
    return payload


def _overrides_from_resolutions_for_dashboard(
    resolutions: list,
) -> tuple[dict[str, CellAddress], dict[str, CellAddress], dict[str, CellAddress]]:
    """Convert stored KPIResolution list → (ov1, ov2, ov3) dicts."""
    from fm_compare.core.models import KPIResolution as _KPIRes
    ov1: dict[str, CellAddress] = {}
    ov2: dict[str, CellAddress] = {}
    ov3: dict[str, CellAddress] = {}
    for r in resolutions:
        if not isinstance(r, _KPIRes):
            continue
        for attr, ov in [("addr_v1", ov1), ("addr_v2", ov2), ("addr_v3", ov3)]:
            raw = getattr(r, attr, "")
            if raw:
                addr = parse_cell_address(raw) if isinstance(raw, str) else raw
                if addr:
                    ov[r.kpi_name] = addr
    return ov1, ov2, ov3


def _parse_dashboard_overrides(
    rows: list[dict],
) -> tuple[dict[str, CellAddress], dict[str, CellAddress], dict[str, CellAddress], list[str]]:
    """Parse the browser-supplied resolution rows into override dicts (V1, V2, V3)."""
    ov1: dict[str, CellAddress] = {}
    ov2: dict[str, CellAddress] = {}
    ov3: dict[str, CellAddress] = {}
    errors: list[str] = []
    for r in rows:
        name = r.get("kpi_name", "")
        for ver, raw, ov in [("V1", r.get("addr_v1") or "", ov1),
                              ("V2", r.get("addr_v2") or "", ov2),
                              ("V3", r.get("addr_v3") or "", ov3)]:
            if raw:
                addr = parse_cell_address(raw)
                if addr is None:
                    errors.append(f"{name}: неверный адрес {ver} «{raw}»")
                else:
                    ov[name] = addr
    return ov1, ov2, ov3, errors


def _float_or_none(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _settings_from_body(body: dict) -> AppSettings:
    """Build AppSettings from request body without persisting to disk."""
    data = load()  # defaults merged with any stored values
    data["mode"] = body.get("mode", data.get("mode", "full"))
    data["materiality_abs"] = _float_or_none(body.get("materiality_abs"))
    data["materiality_pct"] = _float_or_none(body.get("materiality_pct"))
    if body.get("top_x"):
        try:
            data["top_x"] = int(body["top_x"])
        except (TypeError, ValueError):
            pass
    data["include_comments"] = bool(body.get("include_comments", True))
    data["include_hidden_rows"] = bool(body.get("include_hidden_rows", True))
    return AppSettings(data)


def _overrides_from_resolutions(rows: list[dict]) -> tuple[
    dict, dict, dict, dict, list[str]
]:
    """Rebuild KPIResolution objects from the edited browser table."""
    resolutions: list[KPIResolution] = []
    errors: list[str] = []
    for r in rows:
        addr_v1 = parse_cell_address(r.get("addr_v1", "")) if r.get("addr_v1") else None
        addr_v2 = parse_cell_address(r.get("addr_v2", "")) if r.get("addr_v2") else None
        addr_v3 = parse_cell_address(r.get("addr_v3", "")) if r.get("addr_v3") else None
        if r.get("addr_v1") and addr_v1 is None:
            errors.append(f"{r.get('kpi_name')}: неверный адрес V1 «{r.get('addr_v1')}»")
        if r.get("addr_v2") and addr_v2 is None:
            errors.append(f"{r.get('kpi_name')}: неверный адрес V2 «{r.get('addr_v2')}»")
        if r.get("addr_v3") and addr_v3 is None:
            errors.append(f"{r.get('kpi_name')}: неверный адрес V3 «{r.get('addr_v3')}»")
        resolutions.append(KPIResolution(
            kpi_name=r.get("kpi_name", ""),
            kpi_group=r.get("kpi_group", ""),
            kpi_level=int(r.get("kpi_level") or 1),
            search_pattern="",
            sheet_v1=addr_v1.sheet if addr_v1 else "",
            row_v1=addr_v1.row if addr_v1 else 0,
            col_v1=addr_v1.col if addr_v1 else 0,
            label_v1=r.get("label_v1", ""),
            addr_v1=str(addr_v1) if addr_v1 else "",
            sheet_v2=addr_v2.sheet if addr_v2 else "",
            row_v2=addr_v2.row if addr_v2 else 0,
            col_v2=addr_v2.col if addr_v2 else 0,
            label_v2=r.get("label_v2", ""),
            addr_v2=str(addr_v2) if addr_v2 else "",
            unit_v1=r.get("unit_v1") or "",
            unit_v2=r.get("unit_v2") or "",
            sheet_v3=addr_v3.sheet if addr_v3 else "",
            row_v3=addr_v3.row if addr_v3 else None,
            col_v3=addr_v3.col if addr_v3 else None,
            label_v3=r.get("label_v3", ""),
            addr_v3=str(addr_v3) if addr_v3 else "",
            unit_v3=r.get("unit_v3") or "",
            source=r.get("source", "auto"),
        ))
    ov1 = resolutions_to_overrides(resolutions, "v1")
    ov2 = resolutions_to_overrides(resolutions, "v2")
    ov3 = resolutions_to_overrides(resolutions, "v3")
    units = {
        r.kpi_name: (r.unit_v1 or r.unit_v2 or r.unit_v3)
        for r in resolutions
        if r.unit_v1 or r.unit_v2 or r.unit_v3
    }
    return ov1, ov2, ov3, units, errors


@router.post("/api/{job_id}/run")
async def run(job_id: str, request: Request):
    """Phase 2: launch the full comparison in the background."""
    job = jobs.get_job(job_id)
    if job is None or not job.path_v1 or not job.path_v2:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    body = await request.json()
    settings = _settings_from_body(body)
    ov1, ov2, ov3, units, errors = _overrides_from_resolutions(body.get("resolutions") or [])
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors[:10]))

    jobs.start_compare(job, settings, ov1, ov2, units)
    if job.path_v3 and job.sheets_v3:
        jobs.start_compare_v2_v3(job, settings, ov2, ov3, units)
    return {"job_id": job.job_id, "status": job.status}


@router.get("/api/{job_id}/status")
async def status(job_id: str):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return {
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "error": job.error,
    }


@router.get("/api/{job_id}/summary")
async def summary(job_id: str):
    job = jobs.get_job(job_id)
    if job is None or job.result is None:
        raise HTTPException(status_code=404, detail="Результат ещё не готов")

    payload = summary_payload(job.result, job.top_x)
    # Phase 3: enrich the rule-based summary with an LLM overview (best-effort).
    payload["summary_blocks"] = enhance_summary(payload["summary_blocks"])
    return payload


@router.get("/api/{job_id}/status2")
async def status2(job_id: str):
    """Status of the V2 vs V3 comparison (Stage 4 — optional second run)."""
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return {
        "status": job.status_v2_v3,
        "progress": job.progress_v2_v3,
        "message": job.message_v2_v3,
        "error": job.error_v2_v3,
    }


@router.get("/api/{job_id}/report2.xlsx")
async def report2(job_id: str):
    """Download the V2 vs V3 comparison Excel report."""
    job = jobs.get_job(job_id)
    if job is None or job.result_v2_v3 is None:
        raise HTTPException(status_code=404, detail="Результат V2 vs V3 ещё не готов")

    bd = load_dictionary()
    mode = job.result_v2_v3.mode
    out_dir = Path(tempfile.mkdtemp(prefix="fm_report_v2v3_"))
    fname = suggest_filename(mode).replace(".xlsx", "_v2v3.xlsx")
    out_path = out_dir / fname
    export_report(job.result_v2_v3, bd, out_path, mode)
    log.info(f"Report V2 vs V3 exported: job={job_id}")
    return FileResponse(
        out_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=out_path.name,
        background=BackgroundTask(shutil.rmtree, out_dir, ignore_errors=True),
    )


@router.post("/api/{job_id}/sensitivity")
async def run_sensitivity(job_id: str, request: Request):
    """
    Start one-at-a-time sensitivity analysis on the v1 workbook.

    Request body:
    {
        "inputs": [
            {"name": "Цена", "addr": "PRICE!B5", "unit": "руб./кв.м",
             "base_value": 150000, "values": [120000, 135000, 150000, 165000, 180000]}
        ],
        "kpi_addrs": [
            {"name": "NPV", "addr": "DB!C10"},
            {"name": "IRR", "addr": "DB!C11"}
        ],
        "timeout": 90
    }

    Returns 202 immediately; poll GET /sensitivity for status and results.
    """
    from fm_compare.core.models import SensitivityInput
    from fm_compare.core.kpi_resolver import parse_cell_address

    job = jobs.get_job(job_id)
    if job is None or not job.path_v1:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    body = await request.json()
    raw_inputs = body.get("inputs") or []
    raw_kpis = body.get("kpi_addrs") or []
    try:
        timeout = int(body.get("timeout") or 90)
    except (TypeError, ValueError):
        timeout = 90

    if not raw_inputs:
        raise HTTPException(status_code=422, detail="Укажите хотя бы один входной параметр")
    if not raw_kpis:
        raise HTTPException(status_code=422, detail="Укажите хотя бы один KPI для отслеживания")

    parsed_inputs: list[SensitivityInput] = []
    errors: list[str] = []
    for row in raw_inputs:
        addr = parse_cell_address(row.get("addr", ""))
        if addr is None:
            errors.append(f"Неверный адрес входного параметра: {row.get('addr')!r}")
            continue
        try:
            vals = [float(v) for v in (row.get("values") or [])]
        except (TypeError, ValueError):
            errors.append(f"Нечисловое значение в диапазоне для {row.get('name')!r}")
            continue
        if not vals:
            errors.append(f"Не задан диапазон значений для {row.get('name')!r}")
            continue
        parsed_inputs.append(SensitivityInput(
            name=row.get("name", addr.sheet),
            addr=addr,
            unit=row.get("unit", ""),
            base_value=float(row.get("base_value") or 0),
            values=vals,
        ))

    kpi_addrs: dict[str, CellAddress] = {}
    for row in raw_kpis:
        addr = parse_cell_address(row.get("addr", ""))
        if addr is None:
            errors.append(f"Неверный адрес KPI: {row.get('addr')!r}")
            continue
        kpi_addrs[row.get("name", row.get("addr"))] = addr

    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors[:10]))

    jobs.start_sensitivity(job, parsed_inputs, kpi_addrs, timeout=timeout)
    return {"job_id": job_id, "status": "running"}


@router.get("/api/{job_id}/sensitivity")
async def get_sensitivity(job_id: str):
    """Poll sensitivity status; returns results when status == 'done'."""
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    if job.sensitivity_status == "idle":
        return {"status": "idle"}
    if job.sensitivity_status == "running":
        return {"status": "running"}
    if job.sensitivity_status == "error":
        return {"status": "error", "error": job.sensitivity_error}

    # done — serialize result
    res = job.sensitivity_result
    if res is None:
        return {"status": "idle"}

    def _ser_scenario(s):
        return {
            "label": s.label,
            "inputs": s.inputs,
            "kpi_values": s.kpi_values,
        }

    return {
        "status": "done",
        "input_names": res.input_names,
        "kpi_names": res.kpi_names,
        "base": _ser_scenario(res.base_scenario),
        "scenarios": [_ser_scenario(s) for s in res.scenarios],
    }


@router.get("/api/{job_id}/report.xlsx")
async def report(job_id: str):
    job = jobs.get_job(job_id)
    if job is None or job.result is None:
        raise HTTPException(status_code=404, detail="Результат ещё не готов")

    bd = load_dictionary()
    mode = job.result.mode
    out_dir = Path(tempfile.mkdtemp(prefix="fm_report_"))
    out_path = out_dir / suggest_filename(mode)
    export_report(job.result, bd, out_path, mode)
    log.info(f"Report exported: job={job_id}")
    return FileResponse(
        out_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=out_path.name,
        background=BackgroundTask(shutil.rmtree, out_dir, ignore_errors=True),
    )


@router.post("/api/{job_id}/chat")
async def chat_stream(job_id: str, request: Request):
    """
    Stream one chat turn for the given job's dashboard context.

    Request body:
        {"history": [{"role": "user"|"assistant", "content": "..."}, ...]}

    The history must be non-empty and end with a role="user" message (the new
    user turn). Content of each message is capped at 2 000 characters.

    Returns a text/event-stream SSE response:
        data: <fragment>\\n\\n
        data: [DONE]\\n\\n  (always last)
    """
    from fastapi.responses import StreamingResponse

    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    body = await request.json()
    history = body.get("history")

    if not isinstance(history, list) or not history:
        raise HTTPException(status_code=422, detail="history должен быть непустым списком")

    last = history[-1]
    if not isinstance(last, dict) or last.get("role") != "user":
        raise HTTPException(
            status_code=422,
            detail="Последний элемент history должен иметь role='user'",
        )

    _MAX_CONTENT = 2_000
    for i, turn in enumerate(history):
        if not isinstance(turn, dict):
            raise HTTPException(status_code=422, detail=f"history[{i}]: ожидается объект")
        if turn.get("role") not in ("user", "assistant"):
            raise HTTPException(
                status_code=422,
                detail=f"history[{i}]: role должен быть 'user' или 'assistant'",
            )
        content = turn.get("content", "")
        if not isinstance(content, str) or len(content) > _MAX_CONTENT:
            raise HTTPException(
                status_code=422,
                detail=f"history[{i}]: content должен быть строкой до {_MAX_CONTENT} символов",
            )

    dashboard = job.dashboard or {}

    async def _sse():
        try:
            for fragment in stream_chat_turn(history, dashboard):
                safe = fragment.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
                yield f"data: {safe}\n\n"
        except Exception as e:
            log.warning(f"chat_stream error: job={job_id} ({type(e).__name__})")
            yield f"data: [ERROR] {type(e).__name__}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(_sse(), media_type="text/event-stream")
