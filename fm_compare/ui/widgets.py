"""
Reusable UI widgets: file picker row, status bar, progress overlay.
"""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path
from typing import Callable


class FilePickerRow(tk.Frame):
    """Label + Entry (read-only) + Browse button row for picking an xlsx file."""

    def __init__(
        self,
        parent,
        label: str,
        on_change: Callable[[Path | None], None] | None = None,
        **kw,
    ):
        super().__init__(parent, **kw)
        self._on_change = on_change
        self._path: Path | None = None

        tk.Label(self, text=label, width=6, anchor="w").pack(side="left")
        self._var = tk.StringVar()
        self._entry = tk.Entry(self, textvariable=self._var, state="readonly",
                               width=55, relief="sunken")
        self._entry.pack(side="left", padx=(4, 4), fill="x", expand=True)
        tk.Button(self, text="Обзор…", command=self._browse).pack(side="left")
        tk.Button(self, text="×", command=self._clear, width=2).pack(side="left", padx=(2, 0))

    def _browse(self) -> None:
        p = filedialog.askopenfilename(
            title="Выберите файл Excel",
            filetypes=[("Excel files", "*.xlsx *.xlsm"), ("All files", "*.*")],
        )
        if p:
            self.set_path(Path(p))

    def _clear(self) -> None:
        self.set_path(None)

    def set_path(self, path: Path | None) -> None:
        self._path = path
        self._var.set(str(path) if path else "")
        if self._on_change:
            self._on_change(path)

    @property
    def path(self) -> Path | None:
        return self._path


class StatusBar(tk.Frame):
    """Bottom status bar with a message label and a progress bar."""

    def __init__(self, parent, **kw):
        super().__init__(parent, bd=1, relief="sunken", **kw)
        self._label_var = tk.StringVar(value="Готово")
        tk.Label(self, textvariable=self._label_var, anchor="w").pack(
            side="left", fill="x", expand=True, padx=4
        )
        self._progress = ttk.Progressbar(self, length=180, mode="determinate")
        self._progress.pack(side="right", padx=4, pady=2)
        self._progress["value"] = 0

    def update(self, pct: int, message: str) -> None:
        self._label_var.set(message)
        self._progress["value"] = pct


class SheetSelectorDialog(tk.Toplevel):
    """Modal dialog for selecting sheets from a workbook."""

    def __init__(self, parent, title: str, sheet_names: list[str],
                 preselected: list[str] | None = None):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.grab_set()
        self._result: list[str] | None = None

        tk.Label(self, text="Выберите листы для сравнения:", anchor="w").pack(
            fill="x", padx=10, pady=(10, 4)
        )

        frame = tk.Frame(self)
        frame.pack(fill="both", expand=True, padx=10, pady=4)

        scrollbar = tk.Scrollbar(frame, orient="vertical")
        self._listbox = tk.Listbox(
            frame, selectmode="multiple", yscrollcommand=scrollbar.set,
            width=50, height=min(20, max(5, len(sheet_names)))
        )
        scrollbar.config(command=self._listbox.yview)
        self._listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for name in sheet_names:
            self._listbox.insert("end", name)

        pre = set(preselected or sheet_names)
        for i, name in enumerate(sheet_names):
            if name in pre:
                self._listbox.selection_set(i)

        btn_frame = tk.Frame(self)
        btn_frame.pack(fill="x", padx=10, pady=8)
        tk.Button(btn_frame, text="Выбрать все", command=self._select_all).pack(side="left")
        tk.Button(btn_frame, text="Снять все", command=self._deselect_all).pack(side="left", padx=4)
        tk.Button(btn_frame, text="OK", command=self._ok, width=10).pack(side="right")
        tk.Button(btn_frame, text="Отмена", command=self.destroy, width=10).pack(side="right", padx=4)

        self.transient(parent)
        self.wait_window()

    def _select_all(self) -> None:
        self._listbox.selection_set(0, "end")

    def _deselect_all(self) -> None:
        self._listbox.selection_clear(0, "end")

    def _ok(self) -> None:
        idxs = self._listbox.curselection()
        self._result = [self._listbox.get(i) for i in idxs]
        self.destroy()

    @property
    def result(self) -> list[str] | None:
        return self._result
