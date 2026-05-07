"""
Results view: tabbed panel that displays CompareResult sections as tables.
"""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk
from typing import Any

from fm_compare.core.models import CompareResult, KPIValue, DiffRow, FormulaChange, TimingShift, Warning


def _fmt(v: Any, decimals: int = 2) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:,.{decimals}f}"
    if isinstance(v, bool):
        return "Да" if v else "Нет"
    return str(v)


class _TableFrame(tk.Frame):
    """Scrollable Treeview with given columns."""

    def __init__(self, parent, columns: list[tuple[str, str, int]], **kw):
        super().__init__(parent, **kw)
        col_ids = [c[0] for c in columns]
        self._tree = ttk.Treeview(self, columns=col_ids, show="headings",
                                   selectmode="browse")
        for cid, header, width in columns:
            self._tree.heading(cid, text=header)
            self._tree.column(cid, width=width, anchor="w", stretch=False)

        vsb = ttk.Scrollbar(self, orient="vertical", command=self._tree.yview)
        hsb = ttk.Scrollbar(self, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

    def clear(self) -> None:
        for item in self._tree.get_children():
            self._tree.delete(item)

    def add_row(self, values: list, tags: tuple = ()) -> None:
        self._tree.insert("", "end", values=values, tags=tags)

    def configure_tags(self, tag_map: dict[str, dict]) -> None:
        for tag, opts in tag_map.items():
            self._tree.tag_configure(tag, **opts)


class ResultsView(ttk.Notebook):
    """Tab notebook showing all result sections."""

    # Row bg colors
    _TAGS = {
        "positive": {"background": "#C6EFCE"},
        "negative": {"background": "#FFC7CE"},
        "warning": {"background": "#FFEB9C"},
        "neutral": {},
    }

    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self._tabs: dict[str, _TableFrame] = {}
        self._summary_text: tk.Text | None = None
        self._build_tabs()

    def _build_tabs(self) -> None:
        # Summary tab
        sum_frame = tk.Frame(self)
        self.add(sum_frame, text="Резюме")
        self._summary_text = tk.Text(
            sum_frame, wrap="word", state="disabled",
            font=("Consolas", 10), padx=8, pady=6
        )
        sb = tk.Scrollbar(sum_frame, command=self._summary_text.yview)
        self._summary_text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._summary_text.pack(fill="both", expand=True)

        # KPI tab
        kpi_cols = [
            ("group", "Группа KPI", 140),
            ("level", "Ур.", 40),
            ("name", "KPI", 220),
            ("unit", "Ед.", 60),
            ("v1", "V1", 120),
            ("v2", "V2", 120),
            ("delta", "Δ", 120),
            ("delta_pct", "Δ%", 70),
            ("impact", "Impact", 80),
            ("note", "Примечание", 160),
            ("addr_v1", "Ячейка V1", 110),
            ("addr_v2", "Ячейка V2", 110),
        ]
        kpi_frame = _TableFrame(self, kpi_cols)
        kpi_frame.configure_tags(self._TAGS)
        self.add(kpi_frame, text="KPI")
        self._tabs["kpi"] = kpi_frame

        # Top Changes tab
        top_cols = [
            ("sheet", "Лист", 120),
            ("key_v1", "Ключ V1", 220),
            ("key_v2", "Ключ V2", 220),
            ("v1", "V1", 120),
            ("v2", "V2", 120),
            ("delta", "Δ", 120),
            ("delta_pct", "Δ%", 70),
            ("dir", "Направление", 100),
        ]
        top_frame = _TableFrame(self, top_cols)
        top_frame.configure_tags(self._TAGS)
        self.add(top_frame, text="Топ изменений")
        self._tabs["top"] = top_frame

        # Business Diff tab
        bd_cols = [
            ("sheet", "Лист", 110),
            ("key_v1", "Ключ V1", 200),
            ("key_v2", "Ключ V2", 200),
            ("addr_v1", "Ячейка V1", 100),
            ("addr_v2", "Ячейка V2", 100),
            ("match", "Сопост.", 70),
            ("conf", "Увер.", 54),
            ("v1", "V1", 100),
            ("v2", "V2", 100),
            ("delta", "Δ", 100),
            ("delta_pct", "Δ%", 60),
            ("type", "Тип", 80),
            ("material", "Сущ.", 44),
            ("sign", "Знак", 44),
        ]
        bd_frame = _TableFrame(self, bd_cols)
        bd_frame.configure_tags(self._TAGS)
        self.add(bd_frame, text="Business Diff")
        self._tabs["bd"] = bd_frame

        # Formula Changes tab
        fc_cols = [
            ("sheet", "Лист", 110),
            ("addr_v1", "Адрес V1", 90),
            ("addr_v2", "Адрес V2", 90),
            ("f1", "Формула V1", 220),
            ("f2", "Формула V2", 220),
            ("v1", "Знач. V1", 90),
            ("v2", "Знач. V2", 90),
            ("logic", "Логика ⚠", 70),
        ]
        fc_frame = _TableFrame(self, fc_cols)
        fc_frame.configure_tags(self._TAGS)
        self.add(fc_frame, text="Формулы")
        self._tabs["fc"] = fc_frame

        # Timing Shifts tab
        ts_cols = [
            ("sheet", "Лист", 120),
            ("group", "Группа", 160),
            ("key", "Ключ", 220),
            ("shift", "Сдвиг (пер.)", 90),
            ("amount", "Объём", 120),
            ("addr_v1", "Ячейка V1", 110),
            ("addr_v2", "Ячейка V2", 110),
        ]
        ts_frame = _TableFrame(self, ts_cols)
        ts_frame.configure_tags(self._TAGS)
        self.add(ts_frame, text="Timing Shifts")
        self._tabs["ts"] = ts_frame

        # Warnings tab
        w_cols = [
            ("sev", "Severity", 80),
            ("cat", "Категория", 140),
            ("msg", "Сообщение", 340),
            ("sheet", "Лист", 110),
            ("cell", "Ячейка", 110),
            ("check", "Проверить", 70),
        ]
        w_frame = _TableFrame(self, w_cols)
        w_frame.configure_tags({
            **self._TAGS,
            "critical": {"background": "#FF4444", "foreground": "white"},
            "high": {"background": "#FFC7CE"},
            "medium": {"background": "#FFEB9C"},
            "low": {"background": "#D9D9D9"},
        })
        self.add(w_frame, text="Предупреждения")
        self._tabs["warnings"] = w_frame

    def load_result(self, result: CompareResult, top_x: int = 10) -> None:
        self._load_summary(result)
        self._load_kpi(result.kpi_values)
        self._load_top(result.diff_rows, top_x)
        self._load_bd(result.diff_rows)
        self._load_fc(result.formula_changes)
        self._load_ts(result.timing_shifts)
        self._load_warnings(result.warnings)

    def _load_summary(self, result: CompareResult) -> None:
        t = self._summary_text
        t.config(state="normal")
        t.delete("1.0", "end")

        # Configure text tags for colors
        t.tag_configure("title", font=("Consolas", 13, "bold"))
        t.tag_configure("block_title", font=("Consolas", 11, "bold"),
                        background="#DCE6F1")
        t.tag_configure("neg", background="#FFC7CE")
        t.tag_configure("pos", background="#C6EFCE")
        t.tag_configure("warn", background="#FFEB9C")
        t.tag_configure("normal", font=("Consolas", 10))

        t.insert("end", "Сравнение версий финансовой модели\n", "title")
        run_date = result.run_settings.get("run_date", "")
        mode_str = "Полный аудит" if result.mode.value == "full" else "Quick KPI Check"
        t.insert("end", f"Дата: {run_date}   Режим: {mode_str}\n\n", "normal")

        for block in result.summary_blocks:
            btype = block.get("type", "")
            title = block.get("title", "")
            text = block.get("text", "")
            items = block.get("items", [])

            tag = "warn" if ("warning" in btype or "sign" in btype) else "block_title"
            t.insert("end", f"{title}\n", tag)

            if text:
                t.insert("end", f"{text}\n", "normal")

            for item in items:
                item_str = str(item)
                item_tag = "normal"
                if item_str.startswith("▼"):
                    item_tag = "neg"
                elif item_str.startswith("▲"):
                    item_tag = "pos"
                elif "⚠" in item_str or "не найден" in item_str.lower():
                    item_tag = "warn"
                t.insert("end", f"  {item_str}\n", item_tag)

            t.insert("end", "\n")

        t.config(state="disabled")

    def _load_kpi(self, kpi_values: list[KPIValue]) -> None:
        tab = self._tabs["kpi"]
        tab.clear()
        for k in kpi_values:
            tag = k.impact if k.impact in ("positive", "negative") else "neutral"
            if k.value_v1 is None and k.value_v2 is None:
                tag = "warning"
            pct = f"{k.delta_pct:+.1f}%" if k.delta_pct is not None and abs(k.delta_pct) < 10000 else ""
            tab.add_row([
                k.kpi_group, k.kpi_level, k.kpi_name, k.unit,
                _fmt(k.value_v1), _fmt(k.value_v2), _fmt(k.delta), pct,
                k.impact, k.note or "",
                str(k.addr_v1) if k.addr_v1 else "",
                str(k.addr_v2) if k.addr_v2 else "",
            ], tags=(tag,))

    def _load_top(self, diff_rows: list[DiffRow], top_x: int) -> None:
        tab = self._tabs["top"]
        tab.clear()
        material = [
            d for d in diff_rows
            if d.is_material and isinstance(d.delta, (int, float)) and d.delta != 0
        ]
        material.sort(key=lambda d: abs(d.delta) if d.delta else 0, reverse=True)
        half = top_x // 2
        extra = top_x % 2
        neg = [d for d in material if d.delta < 0][: half + extra]
        pos = [d for d in material if d.delta > 0][:half]
        for d in neg + pos:
            tag = "negative" if d.delta < 0 else "positive"
            sheet = d.addr_v1.sheet if d.addr_v1 else (d.addr_v2.sheet if d.addr_v2 else "")
            pct = f"{d.delta_pct:+.1f}%" if d.delta_pct is not None else ""
            tab.add_row([
                sheet, d.business_key_v1 or "", d.business_key_v2 or "",
                _fmt(d.value_v1), _fmt(d.value_v2), _fmt(d.delta), pct,
                "▼ Ухудшение" if d.delta < 0 else "▲ Улучшение",
            ], tags=(tag,))

    def _load_bd(self, diff_rows: list[DiffRow]) -> None:
        tab = self._tabs["bd"]
        tab.clear()
        from fm_compare.core.models import ChangeType, MatchType
        for d in diff_rows:
            if d.sign_changed:
                tag = "negative"
            elif d.change_type == ChangeType.NEW_ITEM:
                tag = "positive"
            elif d.change_type == ChangeType.DELETED_ITEM:
                tag = "negative"
            elif d.match_type in (MatchType.FUZZY, MatchType.NOT_MATCHED):
                tag = "warning"
            else:
                tag = "neutral"

            sheet = d.addr_v1.sheet if d.addr_v1 else (d.addr_v2.sheet if d.addr_v2 else "")
            pct = f"{d.delta_pct:+.1f}%" if d.delta_pct else ""
            tab.add_row([
                sheet, d.business_key_v1 or "", d.business_key_v2 or "",
                str(d.addr_v1) if d.addr_v1 else "",
                str(d.addr_v2) if d.addr_v2 else "",
                d.match_type.value, f"{d.match_confidence:.0%}",
                _fmt(d.value_v1), _fmt(d.value_v2), _fmt(d.delta), pct,
                d.change_type.value,
                "Да" if d.is_material else "Нет",
                "⚠" if d.sign_changed else "",
            ], tags=(tag,))

    def _load_fc(self, formula_changes: list[FormulaChange]) -> None:
        tab = self._tabs["fc"]
        tab.clear()
        for fc in formula_changes:
            tag = "warning" if fc.logic_changed else "neutral"
            tab.add_row([
                fc.addr_v1.sheet if fc.addr_v1 else "",
                str(fc.addr_v1) if fc.addr_v1 else "",
                str(fc.addr_v2) if fc.addr_v2 else "",
                (fc.formula_v1 or "")[:80],
                (fc.formula_v2 or "")[:80],
                _fmt(fc.value_v1), _fmt(fc.value_v2),
                "⚠ Да" if fc.logic_changed else "Нет",
            ], tags=(tag,))

    def _load_ts(self, timing_shifts: list[TimingShift]) -> None:
        tab = self._tabs["ts"]
        tab.clear()
        for s in timing_shifts:
            tag = "negative" if abs(s.periods_shifted) >= 3 else "warning"
            tab.add_row([
                s.sheet, s.kpi_group, s.business_key,
                f"{s.periods_shifted:+d}", _fmt(s.amount_shifted),
                str(s.addr_v1) if s.addr_v1 else "",
                str(s.addr_v2) if s.addr_v2 else "",
            ], tags=(tag,))

    def _load_warnings(self, warnings: list[Warning]) -> None:
        tab = self._tabs["warnings"]
        tab.clear()
        sev_tag = {
            "critical": "critical",
            "high": "high",
            "medium": "medium",
            "low": "low",
            "info": "neutral",
        }
        for w in warnings:
            tag = sev_tag.get(w.severity.value, "neutral")
            tab.add_row([
                w.severity.value.upper(), w.category, w.message,
                w.related_sheet or "",
                w.related_cell or "",
                "Да" if w.manual_check_required else "",
            ], tags=(tag,))

    def clear(self) -> None:
        for tab in self._tabs.values():
            tab.clear()
        if self._summary_text:
            self._summary_text.config(state="normal")
            self._summary_text.delete("1.0", "end")
            self._summary_text.config(state="disabled")
