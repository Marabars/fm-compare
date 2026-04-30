"""
Modal dialogs: Settings, Business Dictionary Editor, About.
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
