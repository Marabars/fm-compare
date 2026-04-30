"""
FM Compare — entry point.
Run:  python main.py
"""
import sys

# Ensure Python 3.8+
if sys.version_info < (3, 8):
    sys.exit("Python 3.8+ required.")

import tkinter as tk
from fm_compare.ui.main_window import MainWindow
from fm_compare.security import safe_logger as log


def main() -> None:
    log.info("Application starting")
    app = MainWindow()
    app.mainloop()
    log.info("Application closed")


if __name__ == "__main__":
    main()
