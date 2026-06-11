"""
Comparison dashboard data builder (Stage 1c).

Produces the left/center/right structure for the compare dashboard:
  left   = KPI values of version 1
  center = change drivers (Δ, Δ%, direction)
  right  = KPI values of version 2

KPIs come from the existing two-phase extractor + business dictionary
(`resolve_kpis_preview` / `extract_kpis`). The auto-detected address can be
overridden per KPI from the UI (the value column differs per sheet), so this
module also recomputes a single KPI when the user edits its address.

Numbers are computed in code; the LLM (Stage 3) only narrates the result.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fm_compare.core.excel_reader import load_workbook_data, WorkbookData
from fm_compare.core.business_dictionary import BusinessDictionary
from fm_compare.core.cross_check import cross_sheet_check
from fm_compare.core.kpi_extractor import resolve_kpis_preview, extract_kpis, build_kpi_comparison
from fm_compare.core.kpi_resolver import resolutions_to_overrides, parse_cell_address
from fm_compare.core.models import KPIResolution, CellAddress, Discrepancy
from fm_compare.core.utils import is_numeric
from fm_compare.web.serialization import discrepancy_to_json
from fm_compare.security import safe_logger as log


def _pct(a: Any, b: Any) -> float | None:
    if not (is_numeric(a) and is_numeric(b)):
        return None
    denom = abs(float(b)) if abs(float(b)) > 1e-9 else None
    if denom is None:
        return None
    return (float(a) - float(b)) / denom * 100.0


def _kpi_row(kv) -> dict:
    """One KPI row with left (v2=base/old) / center (Δ) / right (v1=new)."""
    # Convention: v1 = newer version (left in upload), v2 = older.
    delta = kv.delta
    direction = "flat"
    if is_numeric(delta) and delta:
        direction = "up" if delta > 0 else "down"
    return {
        "kpi_name": kv.kpi_name,
        "kpi_group": kv.kpi_group,
        "unit": kv.unit,
        "value_v1": kv.value_v1,
        "value_v2": kv.value_v2,
        "delta": delta,
        "delta_pct": kv.delta_pct,
        "direction": direction,
        "impact": kv.impact,
        "addr_v1": str(kv.addr_v1) if kv.addr_v1 else "",
        "addr_v2": str(kv.addr_v2) if kv.addr_v2 else "",
        "note": kv.note,
    }


def build_dashboard(
    path_v1: Path,
    path_v2: Path,
    bd: BusinessDictionary,
    sheets_v1: list[str],
    sheets_v2: list[str],
    overrides_v1: dict[str, CellAddress] | None = None,
    overrides_v2: dict[str, CellAddress] | None = None,
    # Stage 4 — three-version comparison (all optional)
    path_v3: Path | None = None,
    sheets_v3: list[str] | None = None,
    overrides_v3: dict[str, CellAddress] | None = None,
) -> dict:
    """
    Build the comparison dashboard payload.

    When path_v3/sheets_v3 are provided, each KPI row is enriched with
    value_v3, addr_v3, delta_v2_v3 and delta_v2_v3_pct, and has_v3=True is
    returned so the frontend can render the extra columns.
    """
    wb1 = load_workbook_data(path_v1, sheets_v1)
    wb2 = load_workbook_data(path_v2, sheets_v2)

    kpi_v1 = extract_kpis(wb1, bd, addr_overrides=overrides_v1)
    kpi_v2 = extract_kpis(wb2, bd, addr_overrides=overrides_v2)
    comparison = build_kpi_comparison(kpi_v1, kpi_v2, bd)

    level1 = [k for k in comparison if k.kpi_level == 1]
    rows = [_kpi_row(k) for k in (level1 or comparison)]

    discrepancies = cross_sheet_check(wb1, sheets_v1)

    has_v3 = bool(path_v3 and sheets_v3)
    if has_v3:
        wb3 = load_workbook_data(path_v3, sheets_v3)
        kpi_v3 = extract_kpis(wb3, bd, addr_overrides=overrides_v3)
        _enrich_rows_with_v3(rows, kpi_v2, kpi_v3)

    return {
        "kpis": rows,
        "has_v3": has_v3,
        "date_v1": _file_date(path_v1),
        "date_v2": _file_date(path_v2),
        "date_v3": _file_date(path_v3) if has_v3 else None,
        "discrepancies": [discrepancy_to_json(d) for d in discrepancies],
    }


def _enrich_rows_with_v3(
    rows: list[dict],
    kpi_v2: dict[str, tuple],
    kpi_v3: dict[str, tuple],
) -> None:
    """Add V3 fields to each KPI row in-place (delta convention: V3 - V2 = old - new)."""
    for row in rows:
        name = row["kpi_name"]
        v3_val, v3_addr = kpi_v3.get(name, (None, None))
        v2_val, _ = kpi_v2.get(name, (None, None))
        row["value_v3"] = v3_val
        row["addr_v3"] = str(v3_addr) if v3_addr else ""
        if is_numeric(v2_val) and is_numeric(v3_val):
            delta = float(v3_val) - float(v2_val)
            row["delta_v2_v3"] = delta
            row["delta_v2_v3_pct"] = (delta / abs(float(v2_val))) * 100 if float(v2_val) != 0 else None
        else:
            row["delta_v2_v3"] = None
            row["delta_v2_v3_pct"] = None


def auto_resolutions(
    path_v1: Path,
    path_v2: Path,
    bd: BusinessDictionary,
    sheets_v1: list[str],
    sheets_v2: list[str],
) -> list[KPIResolution]:
    """Auto-detect KPI addresses for the dashboard (editable in the UI)."""
    wb1 = load_workbook_data(path_v1, sheets_v1)
    wb2 = load_workbook_data(path_v2, sheets_v2)
    return resolve_kpis_preview(wb1, wb2, bd)


def _file_date(path: Path) -> str | None:
    """Model date = file mtime (per product decision), ISO date string."""
    try:
        from datetime import datetime
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return None
