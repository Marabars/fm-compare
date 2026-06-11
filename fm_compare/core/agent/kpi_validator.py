"""
LLM-based validation of auto-detected KPI cell addresses.

For each KPIResolution from kpi_extractor.resolve_kpis_preview this module
builds a compact column-context (letter, header text, value), sends all KPIs
in a single LLM call, and returns corrected resolutions plus a change-list
for UI highlighting.

Graceful fallback: if the gateway is not configured, unreachable, or returns
unparseable output, the original resolutions are returned unchanged and
corrections is empty.
"""
from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass
from typing import Any

from openpyxl.utils import column_index_from_string, get_column_letter

from fm_compare.core.excel_reader import WorkbookData, SheetData
from fm_compare.core.llm.client import ChatMessage, GatewayClient
from fm_compare.core.llm.config import GatewayConfig
from fm_compare.core.llm.errors import GatewayError
from fm_compare.core.models import KPIResolution
from fm_compare.core.utils import is_numeric
from fm_compare.security import safe_logger as log

# Candidate columns sent per KPI per version to keep the prompt small.
_MAX_CANDIDATES = 15
# Row range to scan for column header text above the KPI row.
_HEADER_SCAN_ROWS = 5

_SYSTEM_PROMPT = (
    "Ты — финансовый аналитик, специализирующийся на девелоперских проектах. "
    "Тебе передан список KPI финансовой модели с кандидатными колонками Excel. "
    "Для каждого KPI выбери колонку с актуальным headline-значением показателя, "
    "а не историческим, промежуточным или сравнительным. "
    "Правила: предпочитай заголовки «Итого», «Факт», «Текущ», «ФМ», «Бюджет», "
    "дату последнего квартала; избегай «Было»/«Стало»/«+−» (сравнительные) "
    "и исторические кварталы старше двух лет. "
    "Если auto-detected колонка (помечена [auto]) выглядит правильной — оставь её. "
    "Ответь ТОЛЬКО JSON-массивом без текста вне JSON: "
    '[{"kpi":"...","v1_col":"H","v1_reason":"...","v2_col":"H","v2_reason":"..."},...]'
)


@dataclass
class KPICorrection:
    """Records an LLM-proposed address change for one KPI."""
    kpi_name: str
    old_addr_v1: str
    new_addr_v1: str
    old_addr_v2: str
    new_addr_v2: str
    reason: str

    @property
    def changed(self) -> bool:
        return (
            self.old_addr_v1 != self.new_addr_v1
            or self.old_addr_v2 != self.new_addr_v2
        )


# ------------------------------------------------------------------ #
# Context builders
# ------------------------------------------------------------------ #

def _col_candidates(
    sd: SheetData,
    row: int,
    current_col: int,
    label_col: int,
) -> list[dict[str, Any]]:
    """Enumerate numeric-valued columns in the KPI row with their header text."""
    cap = min(sd.max_col, 200)  # guard against broken max_col (~1M on broken sheets)
    out: list[dict[str, Any]] = []
    for col in range(label_col + 1, cap + 1):
        cd = sd.cells.get((row, col))
        if cd is None or not is_numeric(cd.value):
            continue
        headers: list[str] = []
        for hrow in range(1, _HEADER_SCAN_ROWS + 1):
            hcd = sd.cells.get((hrow, col))
            if hcd and hcd.value is not None:
                h = str(hcd.value).strip()
                if h and h not in headers:
                    headers.append(h)
        out.append({
            "col": get_column_letter(col),
            "is_current": col == current_col,
            "headers": headers[:3],
            "value": cd.value,
        })
        if len(out) >= _MAX_CANDIDATES:
            break
    return out


def _fmt_candidates(candidates: list[dict[str, Any]]) -> str:
    lines = []
    for c in candidates:
        mark = " [auto]" if c["is_current"] else ""
        hdrs = " / ".join(c["headers"]) if c["headers"] else "—"
        lines.append(f"    {c['col']}{mark}: «{hdrs}» = {c['value']}")
    return "\n".join(lines) if lines else "    (нет кандидатов)"


def _build_user_text(
    resolutions: list[KPIResolution],
    wb_v1: WorkbookData,
    wb_v2: WorkbookData,
    label_col: int,
) -> str:
    blocks: list[str] = []
    for res in resolutions:
        if not res.addr_v1 and not res.addr_v2:
            continue
        lines = [f"KPI: {res.kpi_name}"]
        if res.sheet_v1 and res.row_v1 and res.col_v1:
            sd = wb_v1.sheets.get(res.sheet_v1)
            if sd:
                cands = _col_candidates(sd, res.row_v1, res.col_v1, label_col)
                lines.append(f"  V1 (найдено: {res.addr_v1}):")
                lines.append(_fmt_candidates(cands))
        if res.sheet_v2 and res.row_v2 and res.col_v2:
            sd = wb_v2.sheets.get(res.sheet_v2)
            if sd:
                cands = _col_candidates(sd, res.row_v2, res.col_v2, label_col)
                lines.append(f"  V2 (найдено: {res.addr_v2}):")
                lines.append(_fmt_candidates(cands))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# ------------------------------------------------------------------ #
# Resolution patching
# ------------------------------------------------------------------ #

def _parse_col(raw: Any) -> str | None:
    """Extract only column letters from an LLM-provided column spec (e.g. 'H' or 'H382')."""
    if not raw:
        return None
    letters = re.sub(r"[^A-Za-z]", "", str(raw)).upper()
    return letters or None


def _corrected_resolution(
    res: KPIResolution,
    v1_col: str | None,
    v2_col: str | None,
) -> KPIResolution:
    updates: dict[str, Any] = {}
    if v1_col and res.sheet_v1 and res.row_v1:
        try:
            updates["col_v1"] = column_index_from_string(v1_col)
            updates["addr_v1"] = f"{res.sheet_v1}!{v1_col}{res.row_v1}"
            updates["source"] = "llm"
        except ValueError:
            pass
    if v2_col and res.sheet_v2 and res.row_v2:
        try:
            updates["col_v2"] = column_index_from_string(v2_col)
            updates["addr_v2"] = f"{res.sheet_v2}!{v2_col}{res.row_v2}"
            updates["source"] = "llm"
        except ValueError:
            pass
    return dataclasses.replace(res, **updates) if updates else res


# ------------------------------------------------------------------ #
# Public API
# ------------------------------------------------------------------ #

def validate_resolutions(
    resolutions: list[KPIResolution],
    wb_v1: WorkbookData,
    wb_v2: WorkbookData,
    cfg: GatewayConfig | None = None,
    label_col: int = 2,
) -> tuple[list[KPIResolution], list[KPICorrection]]:
    """
    Validate auto-detected KPI addresses with the LLM.

    Returns (corrected_resolutions, corrections) where corrections contains
    only changed entries. If the gateway is unavailable, returns the original
    resolutions unchanged with an empty corrections list.
    """
    cfg = cfg or GatewayConfig.from_env()
    if not cfg.is_configured:
        log.info("KPI validation skipped: gateway not configured")
        return resolutions, []

    client = GatewayClient(cfg)
    if not client.health_check():
        log.warning("KPI validation skipped: gateway health check failed")
        return resolutions, []

    user_text = _build_user_text(resolutions, wb_v1, wb_v2, label_col)
    if not user_text:
        return resolutions, []

    try:
        result = client.chat(
            [
                ChatMessage("system", _SYSTEM_PROMPT),
                ChatMessage("user", user_text),
            ],
            temperature=0.0,
            max_tokens=1200,
        )
    except GatewayError as e:
        log.warning(
            f"KPI validation failed: {type(e).__name__} "
            f"code={getattr(e, 'code', None)} cid={getattr(e, 'correlation_id', None)}"
        )
        return resolutions, []

    raw = (result.content or "").strip()

    # Be tolerant: extract the JSON array even if the model adds surrounding text.
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end <= start:
        log.warning("KPI validation: LLM response has no JSON array; using auto-detected")
        return resolutions, []

    try:
        items = json.loads(raw[start: end + 1])
        if not isinstance(items, list):
            raise ValueError("expected list")
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning(f"KPI validation: JSON parse error ({exc}); using auto-detected")
        return resolutions, []

    log.info(
        f"KPI validation: model={result.model} cid={result.correlation_id} "
        f"items={len(items)} "
        f"tokens={result.usage.get('total_tokens') if result.usage else None}"
    )

    res_by_name = {r.kpi_name: r for r in resolutions}
    corrected = list(resolutions)
    corrections: list[KPICorrection] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        kpi_name = str(item.get("kpi", ""))
        if kpi_name not in res_by_name:
            continue

        v1_col = _parse_col(item.get("v1_col"))
        v2_col = _parse_col(item.get("v2_col"))
        reason = (
            f"V1: {item.get('v1_reason', '')}; V2: {item.get('v2_reason', '')}".strip("; ")
        )

        original = res_by_name[kpi_name]
        new_res = _corrected_resolution(original, v1_col, v2_col)
        correction = KPICorrection(
            kpi_name=kpi_name,
            old_addr_v1=original.addr_v1,
            new_addr_v1=new_res.addr_v1,
            old_addr_v2=original.addr_v2,
            new_addr_v2=new_res.addr_v2,
            reason=reason,
        )

        idx = next(
            (i for i, r in enumerate(corrected) if r.kpi_name == kpi_name), None
        )
        if idx is not None:
            corrected[idx] = new_res
        if correction.changed:
            corrections.append(correction)

    return corrected, corrections
