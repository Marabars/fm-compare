"""
Compare engine: orchestrates the full pipeline from two workbook paths to CompareResult.
Runs in a background thread; reports progress via callback.
"""
from __future__ import annotations
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

from fm_compare.core.models import (
    CompareResult, CompareMode, Warning, Severity, CellAddress
)
from fm_compare.core.app_settings import AppSettings
from fm_compare.core.excel_reader import load_workbook_data, get_workbook_info
from fm_compare.core.business_dictionary import BusinessDictionary
from fm_compare.core.kpi_extractor import extract_kpis, build_kpi_comparison
from fm_compare.core.value_differ import build_diff
from fm_compare.core.formula_differ import compare_formulas
from fm_compare.core.timing_detector import detect_timing_shifts
from fm_compare.core.summary_generator import generate_summary
from fm_compare import __version__
from fm_compare.security import safe_logger as log


ProgressCallback = Callable[[int, str], None]  # (pct, message)


def _noop(pct: int, msg: str) -> None:
    pass


def run_compare(
    path_v1: Path,
    path_v2: Path,
    bd: BusinessDictionary,
    settings: AppSettings,
    selected_sheets_v1: list[str],
    selected_sheets_v2: list[str],
    progress: ProgressCallback = _noop,
    kpi_overrides_v1: dict[str, CellAddress] | None = None,
    kpi_overrides_v2: dict[str, CellAddress] | None = None,
    kpi_unit_overrides: dict[str, str] | None = None,
) -> CompareResult:
    """
    Full compare pipeline.  Call from a background thread.
    Raises on unrecoverable error; returns CompareResult on success.
    """
    mode = CompareMode(settings.mode)
    quick = (mode == CompareMode.QUICK)
    warnings: list[Warning] = []

    # ── 1. Load workbooks ──────────────────────────────────────────────────
    progress(5, "Загрузка V1…")
    wb_v1 = load_workbook_data(path_v1, selected_sheets_v1)

    progress(15, "Загрузка V2…")
    wb_v2 = load_workbook_data(path_v2, selected_sheets_v2)

    info_v1 = get_workbook_info(path_v1)
    info_v2 = get_workbook_info(path_v2)

    # Union of sheets actually loaded (may differ from requested if sheet was missing)
    sheets_v1_set = set(wb_v1.sheets.keys())
    sheets_v2_set = set(wb_v2.sheets.keys())
    selected_sheets = sorted(sheets_v1_set | sheets_v2_set)

    if not (sheets_v1_set & sheets_v2_set):
        raise ValueError(
            "Нет общих листов между V1 и V2. "
            "Проверьте выбранные листы — в обоих файлах должен быть хотя бы один одинаковый лист."
        )

    # Warn for sheets present in one but not both
    for s in sheets_v1_set - sheets_v2_set:
        warnings.append(Warning(
            severity=Severity.MEDIUM, category="sheet_missing",
            message=f"Лист «{s}» есть в V1, но отсутствует в V2",
            related_sheet=s, manual_check_required=True,
        ))
    for s in sheets_v2_set - sheets_v1_set:
        warnings.append(Warning(
            severity=Severity.MEDIUM, category="sheet_missing",
            message=f"Лист «{s}» есть в V2, но отсутствует в V1",
            related_sheet=s, manual_check_required=True,
        ))

    # ── 2. KPI extraction ─────────────────────────────────────────────────
    progress(25, "Извлечение KPI…")
    kpi_v1 = extract_kpis(wb_v1, bd, addr_overrides=kpi_overrides_v1)
    kpi_v2 = extract_kpis(wb_v2, bd, addr_overrides=kpi_overrides_v2)
    kpi_values = build_kpi_comparison(kpi_v1, kpi_v2, bd)

    # Apply user-confirmed units (override dictionary defaults)
    if kpi_unit_overrides:
        for kv in kpi_values:
            u = kpi_unit_overrides.get(kv.kpi_name)
            if u:
                kv.unit = u

    # ── 3. Value diff ─────────────────────────────────────────────────────
    progress(40, "Сравнение значений…")
    diff_rows, diff_warns = build_diff(
        wb_v1, wb_v2, bd,
        selected_sheets=selected_sheets,
        global_abs=settings.materiality_abs,
        global_pct=settings.materiality_pct,
    )
    warnings.extend(diff_warns)

    # Raw diff rows for Full mode audit trail
    raw_diff_rows: list[dict] = []
    if not quick:
        for d in diff_rows:
            raw_diff_rows.append({
                "sheet": d.addr_v1.sheet if d.addr_v1 else (d.addr_v2.sheet if d.addr_v2 else ""),
                "business_key_v1": d.business_key_v1,
                "business_key_v2": d.business_key_v2,
                "match_type": d.match_type.value,
                "confidence": f"{d.match_confidence:.0%}",
                "addr_v1": str(d.addr_v1) if d.addr_v1 else "",
                "addr_v2": str(d.addr_v2) if d.addr_v2 else "",
                "value_v1": d.value_v1,
                "value_v2": d.value_v2,
                "delta": d.delta,
                "delta_pct": f"{d.delta_pct:+.1f}%" if d.delta_pct else "",
                "change_type": d.change_type.value,
                "is_material": d.is_material,
                "sign_changed": d.sign_changed,
                "kpi_group": d.kpi_group,
            })

    # ── 4. Formula diff ───────────────────────────────────────────────────
    formula_changes = []
    dep_graph: dict = {}
    if not quick:
        progress(55, "Сравнение формул…")
        formula_changes, formula_warns, dep_graph = compare_formulas(
            wb_v1, wb_v2, bd, selected_sheets=selected_sheets
        )
        warnings.extend(formula_warns)
    else:
        progress(55, "Пропуск формул (Quick режим)…")

    # ── 5. Timing shifts ──────────────────────────────────────────────────
    progress(70, "Анализ сдвигов по периодам…")
    timing_shifts, timing_warns = detect_timing_shifts(
        wb_v1, wb_v2, bd,
        selected_sheets=selected_sheets,
        quick_mode=quick,
    )
    warnings.extend(timing_warns)

    # ── 6. Comment / hidden row diffs ─────────────────────────────────────
    comment_changes: list[dict] = []
    hidden_row_changes: list[dict] = []
    if settings.include_comments:
        for d in diff_rows:
            if d.change_type.value == "comment":
                comment_changes.append({
                    "sheet": d.addr_v1.sheet if d.addr_v1 else "",
                    "addr_v1": str(d.addr_v1) if d.addr_v1 else "",
                    "addr_v2": str(d.addr_v2) if d.addr_v2 else "",
                    "comment_v1": d.value_v1,
                    "comment_v2": d.value_v2,
                })
    if settings.include_hidden_rows:
        for d in diff_rows:
            if d.change_type.value == "hidden_row":
                hidden_row_changes.append({
                    "sheet": d.addr_v1.sheet if d.addr_v1 else "",
                    "row_v1": d.addr_v1.row if d.addr_v1 else "",
                    "row_v2": d.addr_v2.row if d.addr_v2 else "",
                    "hidden_v1": d.value_v1,
                    "hidden_v2": d.value_v2,
                })

    # ── 7. Summary ────────────────────────────────────────────────────────
    progress(85, "Генерация резюме…")
    run_meta = {
        "run_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "app_version": __version__,
    }
    summary_blocks = generate_summary(
        mode=mode,
        kpi_values=kpi_values,
        diff_rows=diff_rows,
        formula_changes=formula_changes,
        timing_shifts=timing_shifts,
        warnings=warnings,
        bd=bd,
        top_x=settings.top_x,
        run_meta=run_meta,
    )

    # ── 8. Assemble result ────────────────────────────────────────────────
    run_settings = {
        **run_meta,
        "mode": mode.value,
        "materiality_abs": settings.materiality_abs,
        "materiality_pct": settings.materiality_pct,
        "top_x": settings.top_x,
        "sheets_v1_count": len(sheets_v1_set),
        "sheets_v2_count": len(sheets_v2_set),
        "include_comments": settings.include_comments,
        "include_hidden_rows": settings.include_hidden_rows,
    }

    result = CompareResult(
        mode=mode,
        workbook_v1=info_v1,
        workbook_v2=info_v2,
        kpi_values=kpi_values,
        diff_rows=diff_rows,
        formula_changes=formula_changes,
        timing_shifts=timing_shifts,
        warnings=warnings,
        summary_blocks=summary_blocks,
        raw_diff_rows=raw_diff_rows,
        comment_changes=comment_changes,
        hidden_row_changes=hidden_row_changes,
        run_settings=run_settings,
        dependency_graph=dep_graph,
    )

    log.info(
        f"Compare done: {len(diff_rows)} diffs, {len(kpi_values)} KPIs, "
        f"{len(formula_changes)} formula changes, {len(timing_shifts)} shifts, "
        f"{len(warnings)} warnings"
    )
    progress(100, "Готово")
    return result
