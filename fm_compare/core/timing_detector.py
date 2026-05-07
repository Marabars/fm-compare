"""
Timing shift detector: identifies when totals stay similar but distributions across
periods shift. Reads period headers from row headers.
"""
from __future__ import annotations
from typing import Any

from fm_compare.core.excel_reader import WorkbookData, SheetData, find_period_headers
from fm_compare.core.business_dictionary import BusinessDictionary, get_sheet_group
from fm_compare.core.key_matcher import extract_keys
from fm_compare.core.models import TimingShift, CellAddress, Warning, Severity
from fm_compare.core.utils import is_numeric
from fm_compare.security import safe_logger as log


CF_GROUPS = {"Денежный поток", "Продажи и коммерция", "Финансирование и инвесторы"}
QUICK_MODE_GROUPS = CF_GROUPS

SHIFT_THRESHOLD_PCT = 5.0  # minimum % diff per period to flag as shift


def detect_timing_shifts(
    wb_v1: WorkbookData,
    wb_v2: WorkbookData,
    bd: BusinessDictionary,
    selected_sheets: list[str],
    quick_mode: bool = False,
) -> tuple[list[TimingShift], list[Warning]]:
    shifts: list[TimingShift] = []
    warnings: list[Warning] = []

    for sheet_name in selected_sheets:
        group = get_sheet_group(bd, sheet_name)
        if quick_mode and group not in QUICK_MODE_GROUPS:
            continue

        sd_v1 = wb_v1.sheets.get(sheet_name)
        sd_v2 = wb_v2.sheets.get(sheet_name)
        if sd_v1 is None or sd_v2 is None:
            continue

        # Find period columns by detecting headers
        periods_v1 = find_period_headers(sd_v1)
        periods_v2 = find_period_headers(sd_v2)

        if not periods_v1 or not periods_v2:
            continue

        # Map period label → column index
        period_cols_v1 = {str(p[2]): p[1] for p in periods_v1}
        period_cols_v2 = {str(p[2]): p[1] for p in periods_v2}
        common_periods = sorted(set(period_cols_v1) & set(period_cols_v2))

        if len(common_periods) < 2:
            continue

        keys_v1 = extract_keys(sd_v1)
        keys_v2 = extract_keys(sd_v2)
        keys_v1_map = {k.key: k for k in keys_v1}
        keys_v2_map = {k.key: k for k in keys_v2}

        common_keys = set(keys_v1_map) & set(keys_v2_map)

        for key in common_keys:
            kr1 = keys_v1_map[key]
            kr2 = keys_v2_map[key]

            # Get per-period values
            vals_v1: list[tuple[str, float]] = []
            vals_v2: list[tuple[str, float]] = []

            for period in common_periods:
                col1 = period_cols_v1[period]
                col2 = period_cols_v2[period]
                cd1 = sd_v1.cells.get((kr1.row, col1))
                cd2 = sd_v2.cells.get((kr2.row, col2))
                v1 = cd1.value if cd1 and is_numeric(cd1.value) else 0.0
                v2 = cd2.value if cd2 and is_numeric(cd2.value) else 0.0
                vals_v1.append((period, v1))
                vals_v2.append((period, v2))

            total_v1 = sum(v for _, v in vals_v1)
            total_v2 = sum(v for _, v in vals_v2)

            # Check if total is roughly same but distribution shifted
            if abs(total_v1) < 1 and abs(total_v2) < 1:
                continue

            total_similar = (
                total_v1 == 0 or
                abs((total_v2 - total_v1) / abs(total_v1) * 100) < 10.0
            )
            if not total_similar:
                continue

            # Find periods with significant shifts
            max_shift_pct = 0.0
            shift_periods = 0
            shifted_right = 0
            shifted_amount = 0.0

            for (p, v1), (_, v2) in zip(vals_v1, vals_v2):
                if abs(total_v1) > 0:
                    diff_pct = abs((v2 - v1) / abs(total_v1) * 100)
                    if diff_pct > SHIFT_THRESHOLD_PCT:
                        shift_periods += 1
                        max_shift_pct = max(max_shift_pct, diff_pct)
                        if v2 > v1:
                            shifted_right += 1
                        shifted_amount += abs(v2 - v1)

            if shift_periods < 2:
                continue

            # Determine direction: positive = shifted right (later)
            periods_shift = shifted_right - (shift_periods - shifted_right)

            addr_v1 = CellAddress(sheet=sheet_name, row=kr1.row, col=2)
            addr_v2 = CellAddress(sheet=sheet_name, row=kr2.row, col=2)

            shifts.append(TimingShift(
                business_key=key,
                sheet=sheet_name,
                kpi_group=group or "",
                periods_shifted=periods_shift,
                amount_shifted=shifted_amount,
                addr_v1=addr_v1,
                addr_v2=addr_v2,
            ))

    if shifts:
        for s in shifts:
            if s.kpi_group in CF_GROUPS:
                warnings.append(Warning(
                    severity=Severity.HIGH,
                    category="timing_shift",
                    message=f"Существенный timing shift по группе {s.kpi_group}",
                    related_sheet=s.sheet,
                    manual_check_required=True,
                ))
                break  # one warning per run is enough for summary

    log.info(f"Timing shifts: {len(shifts)} detected")
    return shifts, warnings
