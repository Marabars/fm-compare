"""
Main application window.
"""
from __future__ import annotations
import threading
import traceback
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
from typing import Any

from fm_compare.core.app_settings import AppSettings, load_settings
from fm_compare.core.business_dictionary import load_dictionary
from fm_compare.core.excel_reader import get_workbook_info, load_workbook_data, load_workbook_quick
from fm_compare.core.engine import run_compare
from fm_compare.core.kpi_extractor import resolve_kpis_preview
from fm_compare.core.kpi_resolver import resolutions_to_overrides
from fm_compare.core.report_exporter import export_report, suggest_filename
from fm_compare.core.models import CompareResult, CompareMode, KPIResolution
from fm_compare.ui.widgets import FilePickerRow, StatusBar, SheetSelectorDialog
from fm_compare.ui.dialogs import SettingsDialog, DictionaryEditorDialog, AboutDialog, KPIResolutionDialog
from fm_compare.ui.results_view import ResultsView
from fm_compare.security import safe_logger as log
from fm_compare import APP_NAME, __version__


class MainWindow(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{__version__}")
        self.minsize(960, 700)

        self._settings: AppSettings = load_settings()
        self._bd = load_dictionary()
        self._result: CompareResult | None = None
        self._sheets_v1: list[str] = []
        self._sheets_v2: list[str] = []
        self._selected_v1: list[str] = []
        self._selected_v2: list[str] = []

        self._restore_geometry()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── geometry ──────────────────────────────────────────────────────────

    def _restore_geometry(self) -> None:
        w = self._settings.window_width
        h = self._settings.window_height
        self.geometry(f"{w}x{h}")

    def _save_geometry(self) -> None:
        self._settings.window_width = self.winfo_width()
        self._settings.window_height = self.winfo_height()
        self._settings.save()

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._build_menu()

        top_pane = tk.Frame(self, bd=0)
        top_pane.pack(fill="x", padx=8, pady=(8, 0))

        self._build_file_panel(top_pane)
        self._build_mode_panel(top_pane)
        self._build_action_panel(top_pane)

        sep = ttk.Separator(self, orient="horizontal")
        sep.pack(fill="x", padx=4, pady=4)

        self._results_view = ResultsView(self)
        self._results_view.pack(fill="both", expand=True, padx=4, pady=(0, 0))

        self._status = StatusBar(self)
        self._status.pack(fill="x", side="bottom")

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Настройки…", command=self._open_settings)
        file_menu.add_separator()
        file_menu.add_command(label="Выход", command=self._on_close)
        menubar.add_cascade(label="Файл", menu=file_menu)

        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label="Бизнес-словарь…", command=self._open_dict_editor)
        tools_menu.add_command(label="Открыть папку отчётов", command=self._open_output_dir)
        menubar.add_cascade(label="Инструменты", menu=tools_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="О программе", command=lambda: AboutDialog(self))
        menubar.add_cascade(label="Справка", menu=help_menu)

        self.config(menu=menubar)

    def _build_file_panel(self, parent: tk.Frame) -> None:
        fp_frame = tk.LabelFrame(parent, text="Файлы", padx=6, pady=4)
        fp_frame.pack(fill="x", pady=(0, 4))

        self._picker_v1 = FilePickerRow(
            fp_frame, label="V1:", on_change=self._on_v1_changed
        )
        self._picker_v1.pack(fill="x", pady=2)

        self._picker_v2 = FilePickerRow(
            fp_frame, label="V2:", on_change=self._on_v2_changed
        )
        self._picker_v2.pack(fill="x", pady=2)

        sheet_row = tk.Frame(fp_frame)
        sheet_row.pack(fill="x", pady=2)
        tk.Label(sheet_row, text="Листы:").pack(side="left")
        self._sheets_label = tk.Label(sheet_row, text="—", fg="gray", anchor="w")
        self._sheets_label.pack(side="left", padx=8, fill="x", expand=True)
        self._btn_sheets_v1 = tk.Button(
            sheet_row, text="Листы V1…", command=self._select_sheets_v1,
            state="disabled"
        )
        self._btn_sheets_v1.pack(side="left", padx=2)
        self._btn_sheets_v2 = tk.Button(
            sheet_row, text="Листы V2…", command=self._select_sheets_v2,
            state="disabled"
        )
        self._btn_sheets_v2.pack(side="left", padx=2)

    def _build_mode_panel(self, parent: tk.Frame) -> None:
        mode_frame = tk.LabelFrame(parent, text="Режим", padx=6, pady=4)
        mode_frame.pack(fill="x", pady=(0, 4))

        self._mode_var = tk.StringVar(value=self._settings.mode)
        modes = [
            ("Полный аудит (Full Audit Trail)", "full"),
            ("Быстрая проверка KPI (Quick KPI Check)", "quick"),
        ]
        for label, val in modes:
            tk.Radiobutton(
                mode_frame, text=label, variable=self._mode_var, value=val,
                command=self._on_mode_change
            ).pack(anchor="w")

        info_frame = tk.Frame(mode_frame)
        info_frame.pack(fill="x", pady=(4, 0))
        tk.Label(info_frame, text="Порог материальности:", fg="gray").pack(side="left")
        self._thresh_label = tk.Label(info_frame, text=self._thresh_text(), fg="gray")
        self._thresh_label.pack(side="left", padx=4)
        tk.Button(info_frame, text="Изменить…", command=self._open_settings,
                  relief="flat", fg="blue", cursor="hand2").pack(side="left")

    def _thresh_text(self) -> str:
        a = self._settings.materiality_abs
        p = self._settings.materiality_pct
        if a is None and p is None:
            return "все изменения существенны"
        parts = []
        if a is not None:
            parts.append(f"абс > {a:,.0f}")
        if p is not None:
            parts.append(f"% > {p:.1f}%")
        return "  |  ".join(parts)

    def _build_action_panel(self, parent: tk.Frame) -> None:
        act_frame = tk.Frame(parent)
        act_frame.pack(fill="x", pady=4)

        self._btn_run = tk.Button(
            act_frame, text="▶  Запустить сравнение",
            command=self._run_compare,
            bg="#4472C4", fg="white",
            font=("TkDefaultFont", 11, "bold"),
            relief="flat", padx=16, pady=6,
            state="disabled",
        )
        self._btn_run.pack(side="left")

        self._btn_export = tk.Button(
            act_frame, text="Сохранить отчёт…",
            command=self._export_report,
            state="disabled", padx=10,
        )
        self._btn_export.pack(side="left", padx=8)

        self._btn_settings = tk.Button(
            act_frame, text="⚙ Настройки",
            command=self._open_settings, padx=8
        )
        self._btn_settings.pack(side="right")

        self._btn_dict = tk.Button(
            act_frame, text="📖 Словарь",
            command=self._open_dict_editor, padx=8
        )
        self._btn_dict.pack(side="right", padx=4)

    # ── event handlers ────────────────────────────────────────────────────

    def _on_v1_changed(self, path: Path | None) -> None:
        self._sheets_v1 = []
        self._selected_v1 = []
        self._btn_sheets_v1.config(state="disabled")
        if path and path.exists():
            try:
                info = get_workbook_info(path)
                self._sheets_v1 = info.sheet_names
                self._selected_v1 = self._settings.last_sheets_v1 or self._sheets_v1[:]
                # keep only sheets that exist
                self._selected_v1 = [s for s in self._selected_v1 if s in self._sheets_v1]
                if not self._selected_v1:
                    self._selected_v1 = self._sheets_v1[:]
                self._btn_sheets_v1.config(state="normal")
            except Exception as e:
                messagebox.showerror("Ошибка загрузки V1", str(e))
        self._update_sheets_label()
        self._update_run_button()

    def _on_v2_changed(self, path: Path | None) -> None:
        self._sheets_v2 = []
        self._selected_v2 = []
        self._btn_sheets_v2.config(state="disabled")
        if path and path.exists():
            try:
                info = get_workbook_info(path)
                self._sheets_v2 = info.sheet_names
                self._selected_v2 = self._settings.last_sheets_v2 or self._sheets_v2[:]
                self._selected_v2 = [s for s in self._selected_v2 if s in self._sheets_v2]
                if not self._selected_v2:
                    self._selected_v2 = self._sheets_v2[:]
                self._btn_sheets_v2.config(state="normal")
            except Exception as e:
                messagebox.showerror("Ошибка загрузки V2", str(e))
        self._update_sheets_label()
        self._update_run_button()

    def _on_mode_change(self) -> None:
        self._settings.mode = self._mode_var.get()

    def _update_sheets_label(self) -> None:
        v1_cnt = len(self._selected_v1)
        v2_cnt = len(self._selected_v2)
        if v1_cnt or v2_cnt:
            self._sheets_label.config(
                text=f"V1: {v1_cnt} лист(ов)   V2: {v2_cnt} лист(ов)", fg="#333333"
            )
        else:
            self._sheets_label.config(text="—", fg="gray")

    def _update_run_button(self) -> None:
        ready = (
            self._picker_v1.path is not None
            and self._picker_v2.path is not None
            and bool(self._selected_v1)
            and bool(self._selected_v2)
        )
        self._btn_run.config(state="normal" if ready else "disabled")

    def _select_sheets_v1(self) -> None:
        dlg = SheetSelectorDialog(
            self, "Листы V1", self._sheets_v1, self._selected_v1
        )
        if dlg.result is not None:
            self._selected_v1 = dlg.result
            self._settings.last_sheets_v1 = self._selected_v1
            self._settings.save()
            self._update_sheets_label()
            self._update_run_button()

    def _select_sheets_v2(self) -> None:
        dlg = SheetSelectorDialog(
            self, "Листы V2", self._sheets_v2, self._selected_v2
        )
        if dlg.result is not None:
            self._selected_v2 = dlg.result
            self._settings.last_sheets_v2 = self._selected_v2
            self._settings.save()
            self._update_sheets_label()
            self._update_run_button()

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self, self._settings)
        if dlg.saved:
            self._thresh_label.config(text=self._thresh_text())

    def _open_dict_editor(self) -> None:
        DictionaryEditorDialog(self, self._bd)
        self._bd = load_dictionary()

    def _open_output_dir(self) -> None:
        out = self._settings.output_dir
        if not out:
            out = str(Path.home() / "Documents")
        import subprocess, os
        try:
            os.startfile(out)
        except Exception:
            subprocess.Popen(["explorer", out])

    # ── compare pipeline ──────────────────────────────────────────────────

    def _run_compare(self) -> None:
        if not self._picker_v1.path or not self._picker_v2.path:
            messagebox.showwarning("Нет файлов", "Выберите оба файла V1 и V2.")
            return

        self._btn_run.config(state="disabled")
        self._btn_export.config(state="disabled")
        self._result = None
        self._results_view.clear()

        self._settings.mode = self._mode_var.get()
        self._settings.save()

        # Phase 1: quick load + KPI resolution preview
        self._status.update(5, "Фаза 1: определение адресов KPI…")

        def phase1_worker() -> None:
            try:
                wb_v1 = load_workbook_quick(self._picker_v1.path, self._selected_v1)
                wb_v2 = load_workbook_quick(self._picker_v2.path, self._selected_v2)
                resolutions = resolve_kpis_preview(wb_v1, wb_v2, self._bd)
                self.after(0, self._open_resolution_dialog, resolutions)
            except Exception as exc:
                log.error(f"Phase-1 error: type={type(exc).__name__}")
                self.after(0, self._on_compare_error, traceback.format_exc())

        threading.Thread(target=phase1_worker, daemon=True).start()

    def _open_resolution_dialog(
        self, resolutions: list[KPIResolution]
    ) -> None:
        """Called on UI thread after Phase 1 completes."""
        self._status.update(10, "Ожидание подтверждения адресов KPI…")
        dlg = KPIResolutionDialog(self, resolutions)

        if not dlg.confirmed:
            # User cancelled — re-enable run button, clear status
            self._btn_run.config(state="normal")
            self._status.update(0, "Сравнение отменено")
            return

        confirmed = dlg.resolutions
        overrides_v1 = resolutions_to_overrides(confirmed, "v1")
        overrides_v2 = resolutions_to_overrides(confirmed, "v2")
        # Use unit_v1 as the authoritative unit (V1 is the baseline)
        unit_overrides = {
            r.kpi_name: r.unit_v1
            for r in confirmed
            if r.unit_v1
        }

        # Phase 2: full compare with confirmed addresses and units
        self._status.update(15, "Фаза 2: полное сравнение…")

        def phase2_worker() -> None:
            try:
                result = run_compare(
                    path_v1=self._picker_v1.path,
                    path_v2=self._picker_v2.path,
                    bd=self._bd,
                    settings=self._settings,
                    selected_sheets_v1=self._selected_v1,
                    selected_sheets_v2=self._selected_v2,
                    progress=self._on_progress,
                    kpi_overrides_v1=overrides_v1,
                    kpi_overrides_v2=overrides_v2,
                    kpi_unit_overrides=unit_overrides,
                )
                self.after(0, self._on_compare_done, result)
            except Exception as exc:
                log.error(f"Compare error: type={type(exc).__name__}")
                self.after(0, self._on_compare_error, traceback.format_exc())

        threading.Thread(target=phase2_worker, daemon=True).start()

    def _on_progress(self, pct: int, message: str) -> None:
        self.after(0, self._status.update, pct, message)

    def _on_compare_done(self, result: CompareResult) -> None:
        self._result = result
        self._results_view.load_result(result, top_x=self._settings.top_x)
        self._btn_run.config(state="normal")
        self._btn_export.config(state="normal")
        n_warn = sum(1 for w in result.warnings if w.severity.value in ("critical", "high"))
        self._status.update(
            100,
            f"Готово. Diff: {len(result.diff_rows)},  KPI: {len(result.kpi_values)},  "
            f"Формул: {len(result.formula_changes)},  Шифтов: {len(result.timing_shifts)},  "
            f"Предупреждений: {len(result.warnings)} ({n_warn} critical/high)"
        )

    def _on_compare_error(self, tb: str) -> None:
        self._btn_run.config(state="normal")
        self._status.update(0, "Ошибка сравнения")
        messagebox.showerror(
            "Ошибка сравнения",
            "При сравнении возникла ошибка. Подробности в логе.\n\n"
            + tb[:1200]
        )

    # ── export ────────────────────────────────────────────────────────────

    def _export_report(self) -> None:
        if not self._result:
            return

        mode = CompareMode(self._settings.mode)
        default_name = suggest_filename(mode)
        out_dir = self._settings.output_dir or str(Path.home() / "Documents")

        path = filedialog.asksaveasfilename(
            title="Сохранить отчёт",
            initialdir=out_dir,
            initialfile=default_name,
            defaultextension=".xlsx",
            filetypes=[("Excel files", "*.xlsx")],
        )
        if not path:
            return

        self._btn_export.config(state="disabled")
        self._status.update(50, "Экспорт отчёта…")

        def worker() -> None:
            try:
                out = export_report(self._result, self._bd, Path(path), mode)
                self.after(0, self._on_export_done, str(out))
            except Exception as exc:
                tb = traceback.format_exc()
                log.error(f"Export error: {type(exc).__name__}: {exc}\n{tb}")
                self.after(0, self._on_export_error, f"{type(exc).__name__}: {exc}\n\n{tb[:1000]}")

        threading.Thread(target=worker, daemon=True).start()

    def _on_export_done(self, path: str) -> None:
        self._btn_export.config(state="normal")
        self._status.update(100, f"Отчёт сохранён: {Path(path).name}")
        if messagebox.askyesno("Отчёт сохранён",
                               f"Файл сохранён:\n{path}\n\nОткрыть файл?"):
            import os
            try:
                os.startfile(path)
            except Exception:
                pass

    def _on_export_error(self, msg: str) -> None:
        self._btn_export.config(state="normal")
        self._status.update(0, "Ошибка экспорта")
        messagebox.showerror("Ошибка экспорта", msg)

    # ── close ─────────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        self._save_geometry()
        self.destroy()
