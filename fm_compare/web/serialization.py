"""
JSON serialization for engine dataclasses.

Converts CompareResult / KPIResolution and friends into plain JSON-friendly
structures for the browser. Enums become their `.value`; CellAddress becomes
its "Sheet!E42" string; nested dataclasses are recursed.
"""
from __future__ import annotations

import dataclasses
from enum import Enum
from typing import Any

from fm_compare.core.models import (
    CellAddress, KPIResolution, KPIValue, DiffRow, CompareResult, Discrepancy,
)


def to_jsonable(obj: Any) -> Any:
    """Recursively convert dataclasses/enums/addresses into JSON-safe values."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, CellAddress):
        return str(obj)
    if dataclasses.is_dataclass(obj):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    return str(obj)


def resolution_to_json(r: KPIResolution) -> dict:
    """A single KPI resolution row for the browser table."""
    return {
        "kpi_name": r.kpi_name,
        "kpi_group": r.kpi_group,
        "kpi_level": r.kpi_level,
        "label_v1": r.label_v1,
        "addr_v1": str(r.addr_v1) if r.addr_v1 else "",
        "unit_v1": r.unit_v1 or "",
        "label_v2": r.label_v2,
        "addr_v2": str(r.addr_v2) if r.addr_v2 else "",
        "unit_v2": r.unit_v2 or "",
        "label_v3": r.label_v3,
        "addr_v3": str(r.addr_v3) if r.addr_v3 else "",
        "unit_v3": r.unit_v3 or "",
        "source": r.source,
    }


def kpi_value_to_json(k: KPIValue) -> dict:
    return {
        "kpi_name": k.kpi_name,
        "kpi_group": k.kpi_group,
        "kpi_level": k.kpi_level,
        "unit": k.unit,
        "value_v1": k.value_v1,
        "value_v2": k.value_v2,
        "delta": k.delta,
        "delta_pct": k.delta_pct,
        "impact": k.impact,
        "note": k.note,
    }


def top_changes_to_json(diff_rows: list[DiffRow], top_x: int) -> list[dict]:
    """Top-X material changes by absolute delta, split negative/positive."""
    material = [
        d for d in diff_rows
        if getattr(d, "is_material", False)
        and isinstance(d.delta, (int, float)) and d.delta != 0
    ]
    material.sort(key=lambda d: abs(d.delta) if d.delta else 0, reverse=True)

    half = top_x // 2
    extra = top_x % 2
    negative = [d for d in material if d.delta < 0][: half + extra]
    positive = [d for d in material if d.delta > 0][: half]

    def _row(d: DiffRow) -> dict:
        return {
            "key": d.business_key_v1 or d.business_key_v2 or "",
            "delta": d.delta,
            "delta_pct": d.delta_pct,
            "value_v1": d.value_v1,
            "value_v2": d.value_v2,
            "direction": "down" if (d.delta or 0) < 0 else "up",
        }

    return [_row(d) for d in negative] + [_row(d) for d in positive]


def discrepancy_to_json(d: Discrepancy) -> dict:
    return {
        "article": d.article,
        "sheet_a": d.sheet_a,
        "value_a": d.value_a,
        "sheet_b": d.sheet_b,
        "value_b": d.value_b,
        "delta": d.delta,
        "delta_pct": d.delta_pct,
        "severity": d.severity.value if hasattr(d.severity, "value") else str(d.severity),
        "message": d.message,
    }


def correction_to_json(c) -> dict:
    return {
        "kpi_name": c.kpi_name,
        "old_addr_v1": c.old_addr_v1,
        "new_addr_v1": c.new_addr_v1,
        "old_addr_v2": c.old_addr_v2,
        "new_addr_v2": c.new_addr_v2,
        "reason": c.reason,
    }


def summary_payload(result: CompareResult, top_x: int) -> dict:
    """Full JSON payload for the results screen (summary + KPI + top changes)."""
    return {
        "mode": result.mode.value if hasattr(result.mode, "value") else str(result.mode),
        "summary_blocks": result.summary_blocks,
        "kpi_values": [kpi_value_to_json(k) for k in result.kpi_values],
        "top_changes": top_changes_to_json(result.diff_rows, top_x),
        "counts": {
            "diff_rows": len(result.diff_rows),
            "kpi_values": len(result.kpi_values),
            "formula_changes": len(result.formula_changes),
            "timing_shifts": len(result.timing_shifts),
            "warnings": len(result.warnings),
        },
    }
