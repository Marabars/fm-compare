"""
Value differ: compares cell values across two workbooks using matched business keys.
Applies materiality rules, detects sign changes, builds DiffRow list.
"""
from __future__ import annotations
from typing import Any

from fm_compare.core.excel_reader import WorkbookData, SheetData
from fm_compare.core.key_matcher import MatchResult, KeyedRow, extract_keys
from fm_compare.core.business_dictionary import BusinessDictionary, get_materiality, get_sheet_group
from fm_compare.core.models import (
    DiffRow, ChangeType, MatchType, CellAddress, Warning, Severity
)
from fm_compare.security import safe_logger as log


def _is_material(
    delta: float | None,
    v1: float | None,
    abs_thresh: float | None,
    pct_thresh: float | None,
) -> bool:
    """OR logic: material if exceeds either threshold. If both None — all changes material."""
    if delta is None:
        return False
    if abs_thresh is None and pct_thresh is None:
        return True
    abs_ok = abs_thresh is not None and abs(delta) >= abs_thresh
    if v1 and v1 != 0 and pct_thresh is not None:
        pct_ok = abs(delta / v1 * 100) >= pct_thresh
    else:
        pct_ok = False
    return abs_ok or pct_ok


def build_diff(
    wb_v1: WorkbookData,
    wb_v2: WorkbookData,
    bd: BusinessDictionary,
    selected_sheets: list[str],
    global_abs: float | None = None,
    global_pct: float | None = None,
    value_col_start: int = 3,
    label_col: int = 2,
) -> tuple[list[DiffRow], list[Warning]]:
    """
    Build business diff rows for all selected sheets.
    Returns (diff_rows, warnings).
    """
    all_diffs: list[DiffRow] = []
    all_warnings: list[Warning] = []

    for sheet_name in selected_sheets:
        sd_v1 = wb_v1.sheets.get(sheet_name)
        sd_v2 = wb_v2.sheets.get(sheet_name)

        if sd_v1 is None and sd_v2 is None:
            continue

        if sd_v1 is None:
            all_warnings.append(Warning(
                severity=Severity.HIGH, category="sheet_missing",
                message=f"Лист отсутствует в V1",
                related_sheet=sheet_name, manual_check_required=True
            ))
            continue

        if sd_v2 is None:
            all_warnings.append(Warning(
                severity=Severity.HIGH, category="sheet_missing",
                message=f"Лист отсутствует в V2",
                related_sheet=sheet_name, manual_check_required=True
            ))
            continue

        group = get_sheet_group(bd, sheet_name)
        abs_thresh, pct_thresh = get_materiality(bd, group or "default") if group else (None, None)
        if global_abs is not None:
            abs_thresh = global_abs
        if global_pct is not None:
            pct_thresh = global_pct

        from fm_compare.core.key_matcher import match_sheets
        keys_v1 = extract_keys(sd_v1, label_col=label_col)
        keys_v2 = extract_keys(sd_v2, label_col=label_col)
        matches = match_sheets(keys_v1, keys_v2, bd, global_abs, global_pct)

        sheet_diffs = _diff_sheet(
            sd_v1, sd_v2, matches, sheet_name, group or "",
            abs_thresh, pct_thresh, value_col_start, label_col, bd
        )
        all_diffs.extend(sheet_diffs["diffs"])
        all_warnings.extend(sheet_diffs["warnings"])

    log.info(f"Value diff: {len(all_diffs)} diff rows, {len(all_warnings)} warnings")
    return all_diffs, all_warnings


def _diff_sheet(
    sd_v1: SheetData,
    sd_v2: SheetData,
    matches: list[MatchResult],
    sheet_name: str,
    group: str,
    abs_thresh: float | None,
    pct_thresh: float | None,
    value_col_start: int,
    label_col: int,
    bd: BusinessDictionary,
) -> dict:
    diffs: list[DiffRow] = []
    warnings: list[Warning] = []

    for m in matches:
        if m.match_type == MatchType.NOT_MATCHED:
            warnings.append(Warning(
                severity=Severity.MEDIUM, category="not_matched",
                message="Строка не сопоставлена с V2",
                related_sheet=sheet_name, manual_check_required=False
            ))

        if m.match_type == MatchType.FUZZY and m.confidence < 0.9:
            warnings.append(Warning(
                severity=Severity.LOW, category="fuzzy_match",
                message=f"Нечёткое сопоставление (уверенность {m.confidence:.0%})",
                related_sheet=sheet_name
            ))

        # Compare values across value columns
        if m.row_v1 is None and m.row_v2 is None:
            continue

        if m.row_v1 is None:
            # New item in V2
            for col in range(value_col_start, sd_v2.max_col + 1):
                cd2 = sd_v2.cells.get((m.row_v2, col))
                if cd2 is None or not isinstance(cd2.value, (int, float)):
                    continue
                addr_v2 = CellAddress(sheet=sheet_name, row=m.row_v2, col=col)
                diffs.append(_make_diff_row(
                    m, addr_v1=None, addr_v2=addr_v2,
                    v1=0.0, v2=cd2.value,
                    formula_v1=None, formula_v2=cd2.formula,
                    sheet_name=sheet_name, group=group,
                    abs_thresh=abs_thresh, pct_thresh=pct_thresh,
                    change_type=ChangeType.NEW_ITEM
                ))
            continue

        if m.row_v2 is None:
            # Deleted item
            for col in range(value_col_start, sd_v1.max_col + 1):
                cd1 = sd_v1.cells.get((m.row_v1, col))
                if cd1 is None or not isinstance(cd1.value, (int, float)):
                    continue
                addr_v1 = CellAddress(sheet=sheet_name, row=m.row_v1, col=col)
                diffs.append(_make_diff_row(
                    m, addr_v1=addr_v1, addr_v2=None,
                    v1=cd1.value, v2=0.0,
                    formula_v1=cd1.formula, formula_v2=None,
                    sheet_name=sheet_name, group=group,
                    abs_thresh=abs_thresh, pct_thresh=pct_thresh,
                    change_type=ChangeType.DELETED_ITEM
                ))
            continue

        # Compare matching rows column by column
        max_col = max(sd_v1.max_col, sd_v2.max_col)
        for col in range(value_col_start, max_col + 1):
            cd1 = sd_v1.cells.get((m.row_v1, col))
            cd2 = sd_v2.cells.get((m.row_v2, col))
            v1 = cd1.value if cd1 else None
            v2 = cd2.value if cd2 else None
            f1 = cd1.formula if cd1 else None
            f2 = cd2.formula if cd2 else None

            if v1 == v2 and f1 == f2:
                continue

            addr_v1 = CellAddress(sheet=sheet_name, row=m.row_v1, col=col) if cd1 else None
            addr_v2 = CellAddress(sheet=sheet_name, row=m.row_v2, col=col) if cd2 else None

            change_type = ChangeType.VALUE
            if f1 != f2:
                change_type = ChangeType.FORMULA

            diff = _make_diff_row(
                m, addr_v1=addr_v1, addr_v2=addr_v2,
                v1=v1, v2=v2, formula_v1=f1, formula_v2=f2,
                sheet_name=sheet_name, group=group,
                abs_thresh=abs_thresh, pct_thresh=pct_thresh,
                change_type=change_type
            )
            diffs.append(diff)

        # Compare hidden row status
        hidden_v1 = sd_v1.row_hidden.get(m.row_v1, False)
        hidden_v2 = sd_v2.row_hidden.get(m.row_v2, False)
        if hidden_v1 != hidden_v2:
            addr_v1 = CellAddress(sheet=sheet_name, row=m.row_v1, col=label_col)
            addr_v2 = CellAddress(sheet=sheet_name, row=m.row_v2, col=label_col)
            diffs.append(_make_diff_row(
                m, addr_v1=addr_v1, addr_v2=addr_v2,
                v1=hidden_v1, v2=hidden_v2,
                formula_v1=None, formula_v2=None,
                sheet_name=sheet_name, group=group,
                abs_thresh=None, pct_thresh=None,
                change_type=ChangeType.HIDDEN_ROW
            ))

    return {"diffs": diffs, "warnings": warnings}


def _make_diff_row(
    m: MatchResult,
    addr_v1: CellAddress | None, addr_v2: CellAddress | None,
    v1: Any, v2: Any,
    formula_v1: str | None, formula_v2: str | None,
    sheet_name: str, group: str,
    abs_thresh: float | None, pct_thresh: float | None,
    change_type: ChangeType,
) -> DiffRow:
    delta: Any = None
    delta_pct: float | None = None
    sign_changed = False

    if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
        delta = v2 - v1
        if v1 != 0:
            delta_pct = delta / abs(v1) * 100
        sign_changed = (v1 > 0 > v2) or (v1 < 0 < v2)

    material = _is_material(delta, v1 if isinstance(v1, (int, float)) else None,
                            abs_thresh, pct_thresh)
    if change_type in (ChangeType.HIDDEN_ROW, ChangeType.COMMENT):
        material = False

    return DiffRow(
        business_key_v1=m.key_v1,
        business_key_v2=m.key_v2,
        match_type=m.match_type,
        match_confidence=m.confidence,
        addr_v1=addr_v1,
        addr_v2=addr_v2,
        value_v1=v1,
        value_v2=v2,
        delta=delta,
        delta_pct=delta_pct,
        change_type=change_type,
        formula_v1=formula_v1,
        formula_v2=formula_v2,
        is_material=material,
        sign_changed=sign_changed,
        kpi_group=group,
        kpi_level=None,
    )
