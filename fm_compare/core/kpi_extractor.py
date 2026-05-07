"""
KPI extractor: finds KPI values in workbook sheets using dictionary rules.
Searches by row label patterns, not just coordinates.
"""
from __future__ import annotations
import re
from typing import Any

from fm_compare.core.excel_reader import WorkbookData, SheetData
from fm_compare.core.business_dictionary import BusinessDictionary, KPIEntry
from fm_compare.core.models import CellAddress, KPIValue
from fm_compare.core.utils import is_numeric
from fm_compare.security import safe_logger as log


def _find_summary_col(sd: SheetData) -> int | None:
    """Find the column that likely contains summary/total values."""
    for col in range(3, min(sd.max_col + 1, 20)):
        non_empty = sum(1 for row in range(1, min(sd.max_row + 1, 50))
                        if sd.cells.get((row, col)) and
                        is_numeric(sd.cells[(row, col)].value))
        if non_empty > 3:
            return col
    return 3


def _find_kpi_row(
    sd: SheetData,
    kpi: KPIEntry,
    label_col: int = 2,
) -> tuple[int, Any] | None:
    """
    Find row matching KPI search pattern.
    Returns (row_index, label_cell_value) or None.
    """
    try:
        pattern = re.compile(kpi.search_pattern, re.IGNORECASE | re.UNICODE)
    except re.error:
        return None

    for row in range(1, sd.max_row + 1):
        cd = sd.cells.get((row, label_col))
        if cd is None or cd.value is None:
            continue
        label = str(cd.value).strip()
        if pattern.search(label):
            return row, label
    return None


def _get_aggregate_value(sd: SheetData, row: int, label_col: int = 2) -> tuple[Any, int | None]:
    """
    Get the most representative value for a KPI row.
    Prefers summary column or last non-empty numeric in row.
    """
    best_val = None
    best_col = None
    summary_col = _find_summary_col(sd)

    if summary_col:
        cd = sd.cells.get((row, summary_col))
        if cd and is_numeric(cd.value):
            return cd.value, summary_col

    # Fallback: rightmost non-empty numeric
    for col in range(label_col + 1, sd.max_col + 1):
        cd = sd.cells.get((row, col))
        if cd and is_numeric(cd.value):
            best_val = cd.value
            best_col = col

    return best_val, best_col


def extract_kpis(
    wb: WorkbookData,
    bd: BusinessDictionary,
    label_col: int = 2,
) -> dict[str, tuple[Any, CellAddress | None]]:
    """
    Extract all KPI values from workbook.
    Returns dict: kpi_name → (value, CellAddress).
    Searches all sheets, uses first match found.
    """
    results: dict[str, tuple[Any, CellAddress | None]] = {}
    found = 0

    for kpi in bd.kpi_dict:
        for sheet_name, sd in wb.sheets.items():
            match = _find_kpi_row(sd, kpi, label_col)
            if match is None:
                continue
            row_idx, _ = match
            value, col = _get_aggregate_value(sd, row_idx, label_col)
            if value is not None:
                addr = CellAddress(sheet=sheet_name, row=row_idx, col=col or 3)
                results[kpi.name] = (value, addr)
                found += 1
                break
        else:
            results[kpi.name] = (None, None)

    log.info(f"KPI extraction: found={found} total={len(bd.kpi_dict)}")
    return results


def build_kpi_comparison(
    kpi_v1: dict[str, tuple[Any, CellAddress | None]],
    kpi_v2: dict[str, tuple[Any, CellAddress | None]],
    bd: BusinessDictionary,
) -> list[KPIValue]:
    """Compare KPI values between V1 and V2."""
    comparison: list[KPIValue] = []
    kpi_by_name = {k.name: k for k in bd.kpi_dict}

    all_names = list(dict.fromkeys(list(kpi_v1.keys()) + list(kpi_v2.keys())))

    for name in all_names:
        v1_val, addr_v1 = kpi_v1.get(name, (None, None))
        v2_val, addr_v2 = kpi_v2.get(name, (None, None))
        kpi_entry = kpi_by_name.get(name)

        delta: Any = None
        delta_pct: float | None = None
        impact = "neutral"
        note = ""

        if is_numeric(v1_val) and is_numeric(v2_val):
            delta = v2_val - v1_val
            if v1_val != 0:
                delta_pct = (delta / abs(v1_val)) * 100
            else:
                delta_pct = None  # division by zero — no meaningful percentage

            if kpi_entry:
                direction = kpi_entry.better_direction
                if direction == "up":
                    impact = "positive" if delta > 0 else ("negative" if delta < 0 else "neutral")
                elif direction == "down":
                    impact = "positive" if delta < 0 else ("negative" if delta > 0 else "neutral")
        elif v1_val is None and v2_val is None:
            note = "KPI не найден"
            impact = "neutral"
        elif v1_val is None:
            note = "Только в V2"
        elif v2_val is None:
            note = "Только в V1"

        comparison.append(KPIValue(
            kpi_name=name,
            kpi_group=kpi_entry.group if kpi_entry else "Прочее",
            kpi_level=kpi_entry.level if kpi_entry else 2,
            unit=kpi_entry.unit if kpi_entry else "",
            value_v1=v1_val,
            value_v2=v2_val,
            delta=delta,
            delta_pct=delta_pct,
            impact=impact,
            note=note,
            addr_v1=addr_v1,
            addr_v2=addr_v2,
        ))

    return comparison
