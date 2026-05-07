"""
Rule-based Executive Summary generator. Russian language output.
No overall verdict — only per-KPI-group assessment.
"""
from __future__ import annotations
from typing import Any

from fm_compare.core.models import (
    KPIValue, DiffRow, FormulaChange, TimingShift, Warning,
    CompareMode, Severity, ChangeType, MatchType
)
from fm_compare.core.business_dictionary import BusinessDictionary, is_key_sheet
from fm_compare.security import safe_logger as log


def _fmt_num(v: Any, unit: str = "") -> str:
    if v is None:
        return "н/д"
    if isinstance(v, float):
        if unit:
            return f"{v:,.2f} {unit}".strip()
        if abs(v) >= 1_000_000_000:
            return f"{v / 1_000_000_000:.2f} млрд"
        if abs(v) >= 1_000_000:
            return f"{v / 1_000_000:.1f} млн"
        if abs(v) >= 1_000:
            return f"{v / 1_000:.1f} тыс"
        return f"{v:.2f}"
    if isinstance(v, int):
        if unit:
            return f"{v:,} {unit}".strip()
        if abs(v) >= 1_000_000_000:
            return f"{v / 1_000_000_000:.2f} млрд"
        if abs(v) >= 1_000_000:
            return f"{v / 1_000_000:.1f} млн"
        return f"{v:,}"
    return str(v)


def _direction_word(impact: str, delta: Any) -> str:
    if impact == "positive":
        return "улучшились" if delta and delta > 0 else "улучшились"
    if impact == "negative":
        return "ухудшились"
    return "изменились"


def generate_summary(
    mode: CompareMode,
    kpi_values: list[KPIValue],
    diff_rows: list[DiffRow],
    formula_changes: list[FormulaChange],
    timing_shifts: list[TimingShift],
    warnings: list[Warning],
    bd: BusinessDictionary,
    top_x: int = 10,
    run_meta: dict | None = None,
) -> list[dict]:
    """
    Generate summary blocks.
    Each block: {"type": str, "title": str, "text": str, "items": list}
    """
    blocks: list[dict] = []
    run_meta = run_meta or {}

    # Block 1: Mode notice for Quick
    if mode == CompareMode.QUICK:
        blocks.append({
            "type": "warning_banner",
            "title": "⚠ Неполный аудиторский след",
            "text": (
                "Выбран режим Quick KPI Check. Raw Diff не создан. "
                "Часть изменений формул и скрытых строк не анализировалась. "
                "Для полного audit trail повторите сравнение в режиме Full audit trail."
            ),
            "items": []
        })

    # Block 2: KPI by group
    kpi_by_group: dict[str, list[KPIValue]] = {}
    for k in kpi_values:
        kpi_by_group.setdefault(k.kpi_group, []).append(k)

    for group, kpis in kpi_by_group.items():
        level1 = [k for k in kpis if k.kpi_level == 1]
        if not level1:
            continue

        changed = [k for k in level1 if k.delta is not None and k.delta != 0]
        not_found = [k for k in level1 if k.value_v1 is None and k.value_v2 is None]

        lines = []
        for k in level1:
            if k.value_v1 is None and k.value_v2 is None:
                lines.append(f"• {k.kpi_name}: KPI не найден")
                continue
            if k.delta is None:
                lines.append(f"• {k.kpi_name}: V1={_fmt_num(k.value_v1, k.unit)}, V2={_fmt_num(k.value_v2, k.unit)}")
                continue
            pct_str = f" ({k.delta_pct:+.1f}%)" if k.delta_pct and abs(k.delta_pct) < 10000 else ""
            arrow = "▲" if k.delta > 0 else ("▼" if k.delta < 0 else "—")
            lines.append(
                f"• {k.kpi_name}: {_fmt_num(k.value_v1, k.unit)} → "
                f"{_fmt_num(k.value_v2, k.unit)} "
                f"{arrow}{pct_str} [{k.impact}]"
            )

        if not_found:
            for k in not_found:
                lines.append(f"• {k.kpi_name}: ⚠ не найден в модели")

        # Group-level assessment text
        positive = [k for k in changed if k.impact == "positive"]
        negative = [k for k in changed if k.impact == "negative"]
        if positive and not negative:
            assessment = f"Показатели группы «{group}» улучшились."
        elif negative and not positive:
            assessment = f"Показатели группы «{group}» ухудшились."
        elif positive and negative:
            assessment = f"В группе «{group}» есть и улучшения, и ухудшения."
        elif not_found:
            assessment = f"Показатели группы «{group}» не найдены — проверьте словарь KPI."
        else:
            assessment = f"Показатели группы «{group}» не изменились."

        blocks.append({
            "type": "kpi_group",
            "title": f"Группа KPI: {group}",
            "text": assessment,
            "items": lines,
        })

    # Block 3: Top-X changes
    material_diffs = [
        d for d in diff_rows
        if d.is_material and isinstance(d.delta, (int, float)) and d.delta != 0
    ]
    material_diffs.sort(key=lambda d: abs(d.delta) if d.delta else 0, reverse=True)

    half = top_x // 2
    extra = top_x % 2  # odd: extra goes to negative

    negative_changes = [d for d in material_diffs if isinstance(d.delta, (int, float)) and d.delta < 0]
    positive_changes = [d for d in material_diffs if isinstance(d.delta, (int, float)) and d.delta > 0]

    top_pos = positive_changes[:half]
    top_neg = negative_changes[:half + extra]

    top_items = []
    for d in top_neg:
        pct = f" ({d.delta_pct:+.1f}%)" if d.delta_pct else ""
        top_items.append(f"▼ {d.business_key_v1 or d.business_key_v2}: {_fmt_num(d.delta)}{pct}")
    for d in top_pos:
        pct = f" ({d.delta_pct:+.1f}%)" if d.delta_pct else ""
        top_items.append(f"▲ {d.business_key_v1 or d.business_key_v2}: +{_fmt_num(d.delta)}{pct}")

    if top_items:
        blocks.append({
            "type": "top_changes",
            "title": f"Топ-{top_x} изменений",
            "text": f"Крупнейшие существенные изменения ({len(material_diffs)} всего):",
            "items": top_items,
        })

    # Block 4: Formula changes on key sheets
    key_formula_changes = [
        fc for fc in formula_changes
        if is_key_sheet(bd, fc.addr_v1.sheet if fc.addr_v1 else "")
    ]
    if key_formula_changes:
        logic_changes = [fc for fc in key_formula_changes if fc.logic_changed]
        structural = [fc for fc in key_formula_changes if not fc.logic_changed]
        items = []
        if structural:
            items.append(f"Изменены формулы: {len(structural)} ячеек на ключевых листах")
        if logic_changes:
            items.append(f"Изменена логика (значение прежнее): {len(logic_changes)} ячеек — требует ручной проверки")
        blocks.append({
            "type": "formula_warning",
            "title": "Изменения формул на ключевых листах",
            "text": "Обнаружены изменения формул, влияющие на расчёты:",
            "items": items,
        })

    # Block 5: Sign changes
    sign_changes = [d for d in diff_rows if d.sign_changed]
    if sign_changes:
        blocks.append({
            "type": "sign_change_warning",
            "title": "⚠ Изменение знака показателей",
            "text": f"Обнаружено {len(sign_changes)} показателей со сменой знака — high-priority проверка:",
            "items": [d.business_key_v1 or d.business_key_v2 for d in sign_changes[:10]],
        })

    # Block 6: Timing shifts
    critical_shifts = [
        s for s in timing_shifts
        if s.kpi_group in {"Денежный поток", "Продажи и коммерция", "Финансирование и инвесторы"}
    ]
    if critical_shifts:
        blocks.append({
            "type": "timing_shifts",
            "title": "Сдвиги по периодам (Timing Shifts)",
            "text": f"Обнаружено {len(critical_shifts)} значимых сдвигов по CF/Продажам/Финансированию:",
            "items": [
                f"• {s.business_key}: сдвиг ~{s.periods_shifted:+d} периодов, "
                f"объём {_fmt_num(s.amount_shifted, 'руб.')}"
                for s in critical_shifts[:10]
            ],
        })

    # Block 7: Not matched warnings
    not_matched = [w for w in warnings if w.category in ("not_matched", "sheet_missing")]
    if not_matched:
        blocks.append({
            "type": "not_matched",
            "title": "Несопоставленные строки и листы",
            "text": f"{len(not_matched)} предупреждений о несопоставленных элементах:",
            "items": [w.message for w in not_matched[:10]],
        })

    # Block 8: Manual check list
    manual_check = [w for w in warnings if w.manual_check_required]
    if manual_check:
        blocks.append({
            "type": "manual_check",
            "title": "Что проверить аналитику вручную",
            "text": "Следующие пункты требуют ручной проверки:",
            "items": list(dict.fromkeys([w.message for w in manual_check]))[:15],
        })

    log.info(f"Summary generated: {len(blocks)} blocks")
    return blocks
