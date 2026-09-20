"""Punto de entrada: python -m mzscope"""
import sys

from PySide6.QtWidgets import QApplication

from .icon import app_icon, setup_windows_taskbar
from .settings import Settings
from .ui.main_window import MainWindow


def _log_without_console():
    """Con pythonw.exe (sin consola) stdout/stderr no existen: los errores van a outputs/mzscope.log."""
    if sys.stderr is not None and sys.stdout is not None:
        return
    from .logger import OUTPUT_DIR
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        f = open(OUTPUT_DIR / "mzscope.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = f
    except OSError:
        pass


def main():
    _log_without_console()
    setup_windows_taskbar()            # antes de crear ventanas
    app = QApplication(sys.argv)
    app.setApplicationName("MicroZohm Scope")
    app.setOrganizationName("MicroZohm")
    app.setWindowIcon(app_icon())      # ventana principal, dialogos y barra de tareas
    if sys.platform.startswith("win"):
        from PySide6.QtGui import QFont
        app.setFont(QFont("Segoe UI", 10))
    settings = Settings.load()
    win = MainWindow(settings)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
