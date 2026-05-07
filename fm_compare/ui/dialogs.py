"""
Modal dialogs: Settings, Business Dictionary Editor, About, KPI Resolution.
"""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import Any

from fm_compare.core.app_settings import AppSettings
from fm_compare.core.business_dictionary import (
    BusinessDictionary, save_dictionary, export_to_excel, import_from_excel
)
from fm_compare.core.models import KPIResolution


class SettingsDialog(tk.Toplevel):
    """Edit materiality thresholds, mode, top-X, and options."""

    def __init__(self, parent, settings: AppSettings):
        super().__init__(parent)
        self.title("Настройки сравнения")
        self.resizable(False, False)
        self.grab_set()
        self._settings = settings
        self._saved = False

        pad = {"padx": 8, "pady": 4}
        frame = tk.LabelFrame(self, text="Материальность", padx=8, pady=6)
        frame.pack(fill="x", padx=10, pady=(10, 4))

        tk.Label(frame, text="Абсолютный порог (руб.):").grid(row=0, column=0, sticky="w", **pad)
        self._abs_var = tk.StringVar(value=str(settings.materiality_abs or ""))
        tk.Entry(frame, textvariable=self._abs_var, width=20).grid(row=0, column=1, sticky="w", **pad)

        tk.Label(frame, text="Процентный порог (%):").grid(row=1, column=0, sticky="w", **pad)
        self._pct_var = tk.StringVar(value=str(settings.materiality_pct or ""))
        tk.Entry(frame, textvariable=self._pct_var, width=20).grid(row=1, column=1, sticky="w", **pad)

        tk.Label(frame, text="(Если оба не заданы — все изменения считаются существенными)",
                 fg="gray").grid(row=2, column=0, columnspan=2, sticky="w", padx=8)

        opt_frame = tk.LabelFrame(self, text="Параметры отчёта", padx=8, pady=6)
        opt_frame.pack(fill="x", padx=10, pady=4)

        tk.Label(opt_frame, text="Top-X изменений:").grid(row=0, column=0, sticky="w", **pad)
        self._topx_var = tk.StringVar(value=str(settings.top_x))
        tk.Spinbox(opt_frame, textvariable=self._topx_var, from_=5, to=50,
                   increment=5, width=8).grid(row=0, column=1, sticky="w", **pad)

        self._comments_var = tk.BooleanVar(value=settings.include_comments)
        tk.Checkbutton(opt_frame, text="Включить изменения комментариев",
                       variable=self._comments_var).grid(row=1, column=0, columnspan=2,
                                                          sticky="w", **pad)

        self._hidden_var = tk.BooleanVar(value=settings.include_hidden_rows)
        tk.Checkbutton(opt_frame, text="Включить изменения скрытых строк",
                       variable=self._hidden_var).grid(row=2, column=0, columnspan=2,
                                                        sticky="w", **pad)

        self._debug_var = tk.BooleanVar(value=settings.debug_mode)
        tk.Checkbutton(opt_frame, text="Debug-логирование (только координаты ячеек)",
                       variable=self._debug_var).grid(row=3, column=0, columnspan=2,
                                                       sticky="w", **pad)

        out_frame = tk.LabelFrame(self, text="Директория вывода", padx=8, pady=6)
        out_frame.pack(fill="x", padx=10, pady=4)
        self._out_var = tk.StringVar(value=str(settings.output_dir or ""))
        tk.Entry(out_frame, textvariable=self._out_var, width=45,
                 state="readonly").pack(side="left", padx=4)
        tk.Button(out_frame, text="Обзор…",
                  command=self._browse_dir).pack(side="left", padx=4)
        tk.Button(out_frame, text="Сбросить",
                  command=lambda: self._out_var.set("")).pack(side="left")

        btn_frame = tk.Frame(self)
        btn_frame.pack(fill="x", padx=10, pady=10)
        tk.Button(btn_frame, text="Сохранить", command=self._save,
                  width=12).pack(side="right")
        tk.Button(btn_frame, text="Отмена", command=self.destroy,
                  width=12).pack(side="right", padx=4)

        self.transient(parent)
        self.wait_window()

    def _browse_dir(self) -> None:
        d = filedialog.askdirectory(title="Выберите директорию для отчётов")
        if d:
            self._out_var.set(d)

    def _save(self) -> None:
        try:
            abs_val = float(self._abs_var.get()) if self._abs_var.get().strip() else None
            pct_val = float(self._pct_var.get()) if self._pct_var.get().strip() else None
            top_x = int(self._topx_var.get())
            if top_x < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Ошибка", "Проверьте числовые поля (абс. порог, %, Top-X).")
            return

        self._settings.materiality_abs = abs_val
        self._settings.materiality_pct = pct_val
        self._settings.top_x = top_x
        self._settings.include_comments = self._comments_var.get()
        self._settings.include_hidden_rows = self._hidden_var.get()
        self._settings.debug_mode = self._debug_var.get()
        out = self._out_var.get().strip()
        self._settings.output_dir = out if out else None
        self._settings.save()
        self._saved = True
        self.destroy()

    @property
    def saved(self) -> bool:
        return self._saved


class DictionaryEditorDialog(tk.Toplevel):
    """Simple viewer/editor for the business dictionary (KPI + sheet patterns)."""

    def __init__(self, parent, bd: BusinessDictionary):
        super().__init__(parent)
        self.title("Бизнес-словарь")
        self.geometry("900x560")
        self.grab_set()
        self._bd = bd

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self._build_kpi_tab(notebook)
        self._build_sheet_tab(notebook)

        btn_frame = tk.Frame(self)
        btn_frame.pack(fill="x", padx=8, pady=6)
        tk.Button(btn_frame, text="Экспорт в Excel…",
                  command=self._export).pack(side="left")
        tk.Button(btn_frame, text="Импорт из Excel…",
                  command=self._import).pack(side="left", padx=4)
        tk.Button(btn_frame, text="Сбросить к умолчаниям",
                  command=self._reset).pack(side="left", padx=(20, 0))
        tk.Button(btn_frame, text="Закрыть", command=self.destroy,
                  width=12).pack(side="right")

        self.transient(parent)
        self.wait_window()

    def _build_kpi_tab(self, nb: ttk.Notebook) -> None:
        frame = tk.Frame(nb)
        nb.add(frame, text="KPI Dictionary")

        cols = ("name", "group", "level", "unit", "direction")
        headers = ("Название", "Группа", "Уровень", "Единица", "Улучшение")
        widths = (260, 180, 60, 80, 90)

        tree = ttk.Treeview(frame, columns=cols, show="headings")
        for col, h, w in zip(cols, headers, widths):
            tree.heading(col, text=h)
            tree.column(col, width=w, anchor="w")

        sb = tk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        for k in self._bd.kpi_dict:
            tree.insert("", "end", values=(
                k.name, k.group, k.level, k.unit, k.better_direction
            ))

    def _build_sheet_tab(self, nb: ttk.Notebook) -> None:
        frame = tk.Frame(nb)
        nb.add(frame, text="Sheet Dictionary")

        cols = ("pattern", "group", "key", "analyze")
        headers = ("Паттерн имени листа", "Группа", "Ключевой", "Анализ по умолч.")
        widths = (240, 180, 80, 110)

        tree = ttk.Treeview(frame, columns=cols, show="headings")
        for col, h, w in zip(cols, headers, widths):
            tree.heading(col, text=h)
            tree.column(col, width=w, anchor="w")

        sb = tk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        for s in self._bd.sheet_dict:
            tree.insert("", "end", values=(
                s.name_pattern, s.group,
                "Да" if s.key_sheet else "Нет",
                "Да" if s.analyze_by_default else "Нет",
            ))

    def _export(self) -> None:
        p = filedialog.asksaveasfilename(
            title="Экспорт словаря",
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")],
            initialfile="business_dictionary_export.xlsx",
        )
        if not p:
            return
        try:
            export_to_excel(self._bd, Path(p))
            messagebox.showinfo("Готово", f"Словарь экспортирован:\n{p}")
        except Exception as e:
            messagebox.showerror("Ошибка экспорта", str(e))

    def _import(self) -> None:
        p = filedialog.askopenfilename(
            title="Импорт словаря из Excel",
            filetypes=[("Excel", "*.xlsx")],
        )
        if not p:
            return
        bd_new, errors = import_from_excel(Path(p))
        if errors:
            messagebox.showwarning("Предупреждения импорта", "\n".join(errors[:10]))
        if bd_new is None:
            messagebox.showerror("Ошибка", "Не удалось импортировать словарь.")
            return
        self._bd.__dict__.update(bd_new.__dict__)
        save_dictionary(self._bd)
        messagebox.showinfo("Готово", "Словарь импортирован и сохранён.")
        self.destroy()

    def _reset(self) -> None:
        if not messagebox.askyesno(
            "Сбросить словарь",
            "Восстановить словарь по умолчанию? Ваши изменения будут потеряны."
        ):
            return
        from fm_compare.core.business_dictionary import load_dictionary
        default = load_dictionary(force_default=True)
        self._bd.__dict__.update(default.__dict__)
        save_dictionary(self._bd)
        messagebox.showinfo("Готово", "Словарь восстановлен по умолчанию.")
        self.destroy()


class KPIResolutionDialog(tk.Toplevel):
    """
    Phase-1 dialog: shows auto-detected KPI cell addresses and lets the user
    correct them before the main comparison runs.

    Usage:
        dlg = KPIResolutionDialog(parent, resolutions)
        # dialog is modal — blocks until user clicks Confirm or Cancel
        if dlg.confirmed:
            run_compare(dlg.resolutions)

    The user can:
    - Edit addr_v1 / addr_v2 directly in the table
    - Export the table to Excel, edit offline, then load it back
    - Confirm (proceed with compare) or Cancel (abort)
    """

    _COLS = [
        ("kpi_name",  "KPI",             220),
        ("kpi_group", "Группа",          110),
        ("level",     "Ур.",              36),
        ("label_v1",  "Строка V1",       175),
        ("addr_v1",   "Ячейка V1",       105),
        ("unit_v1",   "Ед. изм. V1",      80),
        ("label_v2",  "Строка V2",       175),
        ("addr_v2",   "Ячейка V2",       105),
        ("unit_v2",   "Ед. изм. V2",      80),
        ("source",    "Источник",          70),
    ]
    # Column numbers (1-based) that the user may edit inline
    _EDITABLE_COLS = {
        5: "addr_v1",
        6: "unit_v1",
        8: "addr_v2",
        9: "unit_v2",
    }

    def __init__(self, parent, resolutions: list[KPIResolution]):
        super().__init__(parent)
        self.title("Проверка адресов и единиц измерения KPI — шаг 1 из 2")
        self.geometry("1200x560")
        self.resizable(True, True)
        self.grab_set()

        self._resolutions: list[KPIResolution] = [
            KPIResolution(**r.__dict__) for r in resolutions
        ]
        self.confirmed: bool = False

        self._build_ui()
        self._populate()

        self.transient(parent)
        self.wait_window()

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        info = tk.Label(
            self,
            text=(
                "Ниже показаны строки, ячейки и единицы измерения, которые алгоритм нашёл "
                "для каждого KPI в обоих файлах.\n"
                "Двойной щелчок по колонкам «Ячейка» или «Ед. изм.» — редактирование "
                "прямо в таблице. Или экспортируйте в Excel, поправьте и загрузите обратно.\n"
                "Нажмите «Подтвердить и сравнить» чтобы запустить сравнение."
            ),
            justify="left", anchor="w", wraplength=1120,
            font=("TkDefaultFont", 9), fg="#444444",
        )
        info.pack(fill="x", padx=10, pady=(8, 2))

        # Treeview
        tree_frame = tk.Frame(self)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=4)

        col_ids = [c[0] for c in self._COLS]
        self._tree = ttk.Treeview(
            tree_frame, columns=col_ids, show="headings", selectmode="browse"
        )
        for cid, header, width in self._COLS:
            self._tree.heading(cid, text=header)
            self._tree.column(cid, width=width, anchor="w", stretch=False)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        # Color hints
        self._tree.tag_configure("found", background="#C6EFCE")
        self._tree.tag_configure("missing", background="#FFEB9C")
        self._tree.tag_configure("manual", background="#DCE6F1")

        self._tree.bind("<Double-1>", self._on_double_click)

        # Button bar
        btn_frame = tk.Frame(self)
        btn_frame.pack(fill="x", padx=10, pady=8)

        tk.Button(btn_frame, text="Экспорт в Excel…",
                  command=self._export, width=18).pack(side="left")
        tk.Button(btn_frame, text="Загрузить из Excel…",
                  command=self._import, width=20).pack(side="left", padx=6)

        tk.Button(btn_frame, text="Отмена", command=self.destroy,
                  width=12).pack(side="right")
        tk.Button(btn_frame, text="Подтвердить и сравнить",
                  command=self._confirm, width=24,
                  bg="#4472C4", fg="white",
                  activebackground="#2F5496").pack(side="right", padx=6)

    def _row_values(self, res: KPIResolution) -> list:
        return [
            res.kpi_name, res.kpi_group, res.kpi_level,
            res.label_v1, res.addr_v1, res.unit_v1,
            res.label_v2, res.addr_v2, res.unit_v2,
            res.source,
        ]

    def _row_tag(self, res: KPIResolution) -> str:
        if res.source == "manual":
            return "manual"
        if res.addr_v1 or res.addr_v2:
            return "found"
        return "missing"

    # ── Populate ───────────────────────────────────────────────────────────

    def _populate(self) -> None:
        self._tree.delete(*self._tree.get_children())
        for res in self._resolutions:
            self._tree.insert(
                "", "end",
                values=self._row_values(res),
                tags=(self._row_tag(res),),
            )

    # ── Inline edit ────────────────────────────────────────────────────────

    def _on_double_click(self, event: tk.Event) -> None:
        region = self._tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col_id = self._tree.identify_column(event.x)
        row_id = self._tree.identify_row(event.y)
        if not row_id:
            return

        col_num = int(col_id.lstrip("#"))
        field_key = self._EDITABLE_COLS.get(col_num)
        if field_key is None:
            return  # non-editable column — silently ignore

        bbox = self._tree.bbox(row_id, col_id)
        if not bbox:
            return

        x, y, w, h = bbox
        current_val = self._tree.item(row_id, "values")[col_num - 1]

        entry_var = tk.StringVar(value=current_val)
        entry = tk.Entry(self._tree, textvariable=entry_var, width=20)
        entry.place(x=x, y=y, width=w, height=h)
        entry.focus_set()
        entry.select_range(0, "end")

        def _commit(event=None):
            new_val = entry_var.get().strip()
            entry.destroy()
            idx = self._tree.index(row_id)
            res = self._resolutions[idx]
            if field_key in ("addr_v1", "addr_v2"):
                self._apply_addr(res, field_key, new_val)
            else:
                # unit_v1 or unit_v2 — plain string, no validation needed
                setattr(res, field_key, new_val)
                res.source = "manual"
            self._tree.item(
                row_id,
                values=self._row_values(res),
                tags=(self._row_tag(res),),
            )

        def _cancel(event=None):
            entry.destroy()

        entry.bind("<Return>", _commit)
        entry.bind("<Tab>", _commit)
        entry.bind("<Escape>", _cancel)
        entry.bind("<FocusOut>", _commit)

    def _apply_addr(self, res: KPIResolution, field: str, addr_str: str) -> None:
        from fm_compare.core.kpi_resolver import parse_cell_address
        if addr_str:
            parsed = parse_cell_address(addr_str)
            if parsed is None:
                messagebox.showwarning(
                    "Неверный формат",
                    f"Адрес «{addr_str}» не распознан.\n"
                    "Используйте формат: ИмяЛиста!E42",
                )
                return
            if field == "addr_v1":
                res.addr_v1 = addr_str
                res.sheet_v1 = parsed.sheet
                res.row_v1 = parsed.row
                res.col_v1 = parsed.col
            else:
                res.addr_v2 = addr_str
                res.sheet_v2 = parsed.sheet
                res.row_v2 = parsed.row
                res.col_v2 = parsed.col
        else:
            if field == "addr_v1":
                res.addr_v1 = ""
                res.sheet_v1 = ""
                res.row_v1 = None
                res.col_v1 = None
            else:
                res.addr_v2 = ""
                res.sheet_v2 = ""
                res.row_v2 = None
                res.col_v2 = None
        res.source = "manual"

    # ── Export / Import ────────────────────────────────────────────────────

    def _export(self) -> None:
        from fm_compare.core.kpi_resolver import export_resolutions_to_excel
        p = filedialog.asksaveasfilename(
            title="Экспорт таблицы KPI для редактирования",
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")],
            initialfile="kpi_resolution.xlsx",
        )
        if not p:
            return
        try:
            export_resolutions_to_excel(self._resolutions, Path(p))
            messagebox.showinfo("Готово", f"Таблица сохранена:\n{p}")
        except Exception as e:
            messagebox.showerror("Ошибка экспорта", str(e))

    def _import(self) -> None:
        from fm_compare.core.kpi_resolver import import_resolutions_from_excel
        p = filedialog.askopenfilename(
            title="Загрузить исправленную таблицу KPI",
            filetypes=[("Excel", "*.xlsx")],
        )
        if not p:
            return
        imported, errors = import_resolutions_from_excel(Path(p))
        if errors:
            messagebox.showwarning(
                "Предупреждения импорта",
                "\n".join(errors[:15]),
            )
        if not imported:
            messagebox.showerror("Ошибка", "Не удалось загрузить таблицу.")
            return

        # Merge: match by kpi_name, apply imported addr + unit fields
        by_name = {r.kpi_name: r for r in imported}
        for res in self._resolutions:
            imp = by_name.get(res.kpi_name)
            if imp is None:
                continue
            for fld in ("addr_v1", "sheet_v1", "row_v1", "col_v1", "unit_v1",
                        "addr_v2", "sheet_v2", "row_v2", "col_v2", "unit_v2",
                        "source"):
                setattr(res, fld, getattr(imp, fld))

        self._populate()
        messagebox.showinfo("Готово", f"Загружено {len(imported)} строк.")

    # ── Confirm ────────────────────────────────────────────────────────────

    def _confirm(self) -> None:
        self.confirmed = True
        self.destroy()

    @property
    def resolutions(self) -> list[KPIResolution]:
        return self._resolutions


class AboutDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("О программе")
        self.resizable(False, False)
        self.grab_set()

        from fm_compare import __version__, APP_NAME
        lines = [
            APP_NAME,
            f"Версия {__version__}",
            "",
            "Инструмент сравнения версий финансовых моделей.",
            "Работает полностью офлайн.",
            "Финансовые данные не покидают доверенный контур.",
            "",
            "© 2025. Только для внутреннего использования.",
        ]
        for line in lines:
            font = ("TkDefaultFont", 12, "bold") if line == APP_NAME else ("TkDefaultFont", 10)
            tk.Label(self, text=line, font=font, anchor="center").pack(
                padx=30, pady=(6 if line == APP_NAME else 2)
            )
        tk.Button(self, text="OK", command=self.destroy, width=10).pack(pady=12)

        self.transient(parent)
        self.wait_window()
