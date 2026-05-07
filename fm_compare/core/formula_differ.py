"""
Formula differ: compares formulas between workbooks, classifies changes,
builds dependency graph best-effort.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any

from fm_compare.core.excel_reader import WorkbookData, SheetData, CellData
from fm_compare.core.business_dictionary import BusinessDictionary, is_key_sheet
from fm_compare.core.models import FormulaChange, CellAddress, Warning, Severity
from fm_compare.security import safe_logger as log


_CELL_REF = re.compile(r"'?([^'!]+)'?!?\$?([A-Z]+)\$?(\d+)")
_COMPLEX_FUNCS = re.compile(r"\b(INDIRECT|OFFSET|SUMIFS|SUMPRODUCT|INDEX|MATCH)\b", re.IGNORECASE)


def _is_abs_rel_only(f1: str, f2: str) -> bool:
    """True if only $ signs differ (absolute/relative ref change)."""
    return f1.replace("$", "") == f2.replace("$", "")


def _extract_refs(formula: str) -> list[tuple[str, int, int]]:
    """Extract cell references from formula: list of (sheet_or_empty, col, row)."""
    refs = []
    for m in _CELL_REF.finditer(formula):
        try:
            from openpyxl.utils import column_index_from_string
            sheet_part = m.group(1) if "!" in m.group(0) else ""
            col = column_index_from_string(m.group(2))
            row = int(m.group(3))
            refs.append((sheet_part, col, row))
        except Exception:
            pass
    return refs


def compare_formulas(
    wb_v1: WorkbookData,
    wb_v2: WorkbookData,
    bd: BusinessDictionary,
    selected_sheets: list[str],
) -> tuple[list[FormulaChange], list[Warning], dict]:
    """
    Compare formulas across selected sheets.
    Returns (formula_changes, warnings, dependency_graph).
    """
    changes: list[FormulaChange] = []
    warnings: list[Warning] = []
    dep_graph: dict[str, list[str]] = {}  # cell_id → list of source cell_ids

    for sheet_name in selected_sheets:
        sd_v1 = wb_v1.sheets.get(sheet_name)
        sd_v2 = wb_v2.sheets.get(sheet_name)
        if sd_v1 is None or sd_v2 is None:
            continue

        key = is_key_sheet(bd, sheet_name)
        sheet_changes, sheet_warns, sheet_deps = _compare_sheet_formulas(
            sd_v1, sd_v2, sheet_name, is_key=key
        )
        changes.extend(sheet_changes)
        warnings.extend(sheet_warns)
        dep_graph.update(sheet_deps)

    logic_changes = sum(1 for c in changes if c.logic_changed)
    structural_changes = len(changes) - logic_changes
    log.info(f"Formula diff: {len(changes)} changes ({logic_changes} logic-only, {structural_changes} structural)")
    return changes, warnings, dep_graph


def _compare_sheet_formulas(
    sd_v1: SheetData,
    sd_v2: SheetData,
    sheet_name: str,
    is_key: bool,
) -> tuple[list[FormulaChange], list[Warning], dict]:
    changes: list[FormulaChange] = []
    warnings: list[Warning] = []
    dep_graph: dict[str, list[str]] = {}

    # Get all cells with formulas in either version
    all_coords = set(sd_v1.cells.keys()) | set(sd_v2.cells.keys())

    for (row, col) in all_coords:
        cd1 = sd_v1.cells.get((row, col))
        cd2 = sd_v2.cells.get((row, col))

        f1 = cd1.formula if cd1 else None
        f2 = cd2.formula if cd2 else None
        v1 = cd1.value if cd1 else None
        v2 = cd2.value if cd2 else None

        if f1 == f2:
            # Build dependency graph even if unchanged
            if f2:
                cell_id = f"{sheet_name}!R{row}C{col}"
                refs = _extract_refs(f2)
                if refs:
                    dep_graph[cell_id] = [
                        f"{r[0] or sheet_name}!R{r[2]}C{r[1]}" for r in refs
                    ]
            continue

        if f1 is None and f2 is None:
            continue

        addr_v1 = CellAddress(sheet=sheet_name, row=row, col=col)
        addr_v2 = CellAddress(sheet=sheet_name, row=row, col=col)

        # Classify change
        if f1 and f2 and _is_abs_rel_only(f1, f2):
            # Low priority: only absolute/relative ref changed
            fc = FormulaChange(
                addr_v1=addr_v1, addr_v2=addr_v2,
                formula_v1=f1 or "", formula_v2=f2 or "",
                value_v1=v1, value_v2=v2,
                logic_changed=False,
                dependency_partial=False,
            )
            changes.append(fc)
            continue

        # Check for complex formulas (partial dependency)
        partial = False
        if f2 and _COMPLEX_FUNCS.search(f2):
            partial = True
            if is_key:
                warnings.append(Warning(
                    severity=Severity.MEDIUM,
                    category="partial_dependency",
                    message="Сложная формула — частичная трассировка зависимостей",
                    related_sheet=sheet_name,
                    related_cell=str(addr_v2),
                    manual_check_required=True,
                ))

        logic_changed = (f1 != f2) and (v1 == v2)
        if logic_changed and is_key:
            warnings.append(Warning(
                severity=Severity.MEDIUM,
                category="formula_changed_value_same",
                message="Изменена логика формулы, значение не изменилось",
                related_sheet=sheet_name,
                related_cell=str(addr_v2),
                manual_check_required=True,
            ))

        fc = FormulaChange(
            addr_v1=addr_v1, addr_v2=addr_v2,
            formula_v1=f1 or "", formula_v2=f2 or "",
            value_v1=v1, value_v2=v2,
            logic_changed=logic_changed,
            dependency_partial=partial,
        )
        changes.append(fc)

        # Build dep graph for V2 formula
        if f2:
            cell_id = f"{sheet_name}!R{row}C{col}"
            refs = _extract_refs(f2)
            if refs:
                dep_graph[cell_id] = [
                    f"{r[0] or sheet_name}!R{r[2]}C{r[1]}" for r in refs
                ]

    return changes, warnings, dep_graph


def build_dependency_path(
    target_cell: str,
    dep_graph: dict[str, list[str]],
    max_depth: int = 5,
) -> list[list[str]]:
    """
    Trace dependency paths from target cell backwards.
    Returns list of paths (each path = list of cell IDs from target to source).
    """
    paths: list[list[str]] = []
    visited: set[str] = set()

    def dfs(cell: str, path: list[str], depth: int) -> None:
        if depth > max_depth or cell in visited:
            return
        visited.add(cell)
        sources = dep_graph.get(cell, [])
        if not sources:
            paths.append(list(path))
            return
        for src in sources[:10]:  # limit fan-out
            dfs(src, path + [src], depth + 1)

    dfs(target_cell, [target_cell], 0)
    return paths[:20]  # limit total paths
