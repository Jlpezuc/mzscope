"""Punto de entrada: python -m mzscope"""
import sys

from PySide6.QtWidgets import QApplication

from .settings import Settings
from .ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("MicroZohm Scope")
    app.setOrganizationName("MicroZohm")
    if sys.platform.startswith("win"):
        from PySide6.QtGui import QFont
        app.setFont(QFont("Segoe UI", 10))
    settings = Settings.load()
    win = MainWindow(settings)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
