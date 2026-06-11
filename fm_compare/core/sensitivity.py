"""
One-at-a-time sensitivity analysis engine.

run_scenarios() fixes all inputs at their base_value, then varies each input
through its `values` list one at a time (OAT — one-at-a-time method).
For each scenario it calls recalc_with_overrides() to let LibreOffice
recalculate the workbook, then reads the requested KPI cells.

A SensitivityResult is returned containing the base scenario and all
variation scenarios, keyed by input name and KPI name.
"""
from __future__ import annotations

from pathlib import Path

from fm_compare.core.excel_reader import WorkbookData
from fm_compare.core.models import (
    CellAddress,
    SensitivityInput,
    SensitivityResult,
    SensitivityScenario,
)
from fm_compare.core.recalc import RecalcError, addr_to_str, recalc_with_overrides
from fm_compare.security import safe_logger as log


def _read_kpi(wb: WorkbookData, addr: CellAddress) -> float | None:
    """Extract a numeric value from a recalculated WorkbookData."""
    sheet = wb.sheets.get(addr.sheet)
    if sheet is None:
        return None
    cell = sheet.cells.get((addr.row, addr.col))
    if cell is None:
        return None
    v = cell.value
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def run_scenarios(
    path: Path,
    inputs: list[SensitivityInput],
    kpi_addrs: dict[str, CellAddress],
    timeout: int = 90,
) -> SensitivityResult:
    """
    Run one-at-a-time sensitivity analysis on `path`.

    Parameters
    ----------
    path        : Excel workbook to analyse (v1 file)
    inputs      : list of SensitivityInput (name, addr, base_value, values)
    kpi_addrs   : mapping KPI name → CellAddress to read after each recalc
    timeout     : per-scenario LibreOffice timeout in seconds

    Returns
    -------
    SensitivityResult with base + all variation scenarios
    """
    if not inputs:
        raise ValueError("At least one SensitivityInput is required")
    if not kpi_addrs:
        raise ValueError("At least one KPI address is required")

    base_overrides = {addr_to_str(inp.addr): inp.base_value for inp in inputs}

    log.info(f"Sensitivity: base recalc for {path.name}")
    base_wb = recalc_with_overrides(path, base_overrides, timeout=timeout)
    base_kpis = {name: _read_kpi(base_wb, addr) for name, addr in kpi_addrs.items()}
    base_inputs = {inp.name: inp.base_value for inp in inputs}
    base_scenario = SensitivityScenario(
        inputs=base_inputs,
        kpi_values=base_kpis,
        label="base",
    )
    log.info(f"Sensitivity: base done, KPIs={list(base_kpis.keys())}")

    scenarios: list[SensitivityScenario] = []
    total = sum(len(inp.values) for inp in inputs)
    done = 0

    for inp in inputs:
        for val in inp.values:
            done += 1
            if val == inp.base_value:
                scenarios.append(SensitivityScenario(
                    inputs={**base_inputs, inp.name: val},
                    kpi_values=base_kpis,
                    label=f"{inp.name}={val}",
                ))
                continue

            overrides = {**base_overrides, addr_to_str(inp.addr): val}
            log.info(f"Sensitivity: scenario {done}/{total} {inp.name}={val}")
            try:
                wb = recalc_with_overrides(path, overrides, timeout=timeout)
                kpis = {name: _read_kpi(wb, addr) for name, addr in kpi_addrs.items()}
            except RecalcError as e:
                log.error(f"Sensitivity: recalc failed for {inp.name}={val}: {e}")
                kpis = {name: None for name in kpi_addrs}

            scenarios.append(SensitivityScenario(
                inputs={**base_inputs, inp.name: val},
                kpi_values=kpis,
                label=f"{inp.name}={val}",
            ))

    return SensitivityResult(
        base_scenario=base_scenario,
        scenarios=scenarios,
        input_names=[inp.name for inp in inputs],
        kpi_names=list(kpi_addrs.keys()),
    )
