"""Temas claro/oscuro (paleta Qt + hoja de estilos) y colores de las trazas."""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# Paleta de trazas (distinguibles en claro y oscuro)
TRACE_COLORS = [
    "#4C9BE8", "#F2994A", "#5BC77E", "#E8657C", "#B08CF0",
    "#F2CF4A", "#4FC8D6", "#E88DD0", "#A3C94E", "#F0785A",
    "#7FA8FF", "#C9A46A", "#3DC9A6", "#FF9CBB", "#9E9EF5",
    "#D4B84C", "#66C4E8", "#E37DAA", "#8FCC6D", "#F5A26A",
]

DARK = {
    "window": "#1f232b", "base": "#161a20", "alt": "#1b1f26", "text": "#e6e8ec",
    "muted": "#9aa3b2", "accent": "#4C9BE8", "border": "#2e3440", "button": "#2a303a",
    "plot_bg": "#14171c", "plot_fg": "#c7ccd6", "grid": "#3a414d",
    "led_off": "#3a414d",
}
LIGHT = {
    "window": "#f3f4f6", "base": "#ffffff", "alt": "#f7f8fa", "text": "#1f2329",
    "muted": "#5c6470", "accent": "#2f7fd6", "border": "#d5d9e0", "button": "#e9ecf1",
    "plot_bg": "#ffffff", "plot_fg": "#333940", "grid": "#d0d4db",
    "led_off": "#cfd3da",
}


def palette_for(theme: dict) -> QPalette:
    p = QPalette()
    c = QColor
    p.setColor(QPalette.Window, c(theme["window"]))
    p.setColor(QPalette.WindowText, c(theme["text"]))
    p.setColor(QPalette.Base, c(theme["base"]))
    p.setColor(QPalette.AlternateBase, c(theme["alt"]))
    p.setColor(QPalette.Text, c(theme["text"]))
    p.setColor(QPalette.Button, c(theme["button"]))
    p.setColor(QPalette.ButtonText, c(theme["text"]))
    p.setColor(QPalette.Highlight, c(theme["accent"]))
    p.setColor(QPalette.HighlightedText, c("#ffffff"))
    p.setColor(QPalette.ToolTipBase, c(theme["base"]))
    p.setColor(QPalette.ToolTipText, c(theme["text"]))
    p.setColor(QPalette.PlaceholderText, c(theme["muted"]))
    p.setColor(QPalette.Disabled, QPalette.Text, c(theme["muted"]))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, c(theme["muted"]))
    return p


def stylesheet_for(t: dict) -> str:
    return f"""
    QWidget {{ font-size: 10pt; }}
    QMainWindow, QDialog {{ background: {t['window']}; }}
    QToolBar {{ background: {t['window']}; border-bottom: 1px solid {t['border']}; padding: 3px; spacing: 6px; }}
    QToolBar QToolButton {{ padding: 4px 8px; border-radius: 4px; }}
    QToolBar QToolButton:hover {{ background: {t['button']}; }}
    QToolBar QToolButton:checked {{ background: {t['accent']}; color: white; }}
    QStatusBar {{ background: {t['window']}; border-top: 1px solid {t['border']}; color: {t['muted']}; }}
    QTabWidget::pane {{ border: 1px solid {t['border']}; border-radius: 4px; top: -1px; }}
    QTabBar::tab {{ padding: 6px 12px; border: 1px solid {t['border']}; border-bottom: none;
                    border-top-left-radius: 4px; border-top-right-radius: 4px; background: {t['button']}; }}
    QTabBar::tab:selected {{ background: {t['base']}; }}
    QGroupBox {{ border: 1px solid {t['border']}; border-radius: 6px; margin-top: 12px; padding: 8px 6px 6px 6px; font-weight: 600; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {t['muted']}; }}
    QPushButton {{ background: {t['button']}; border: 1px solid {t['border']}; border-radius: 4px; padding: 5px 10px; }}
    QPushButton:hover {{ border-color: {t['accent']}; }}
    QPushButton:pressed {{ background: {t['accent']}; color: white; }}
    QPushButton:checked {{ background: {t['accent']}; color: white; border-color: {t['accent']}; }}
    QPushButton:disabled {{ color: {t['muted']}; }}
    QPushButton#primary {{ background: {t['accent']}; color: white; border-color: {t['accent']}; font-weight: 600; }}
    QPushButton#danger {{ background: #c94b4b; color: white; border-color: #c94b4b; font-weight: 600; }}
    QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {{ background: {t['base']}; border: 1px solid {t['border']}; border-radius: 4px; padding: 3px 6px; }}
    QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {t['accent']}; }}
    QComboBox QAbstractItemView {{ background: {t['base']}; selection-background-color: {t['accent']}; }}
    QTableWidget, QTableView {{ background: {t['base']}; alternate-background-color: {t['alt']}; gridline-color: {t['border']};
                                border: 1px solid {t['border']}; border-radius: 4px; }}
    QHeaderView::section {{ background: {t['button']}; border: none; border-bottom: 1px solid {t['border']}; padding: 4px; font-weight: 600; }}
    QScrollBar:vertical {{ width: 10px; background: transparent; }}
    QScrollBar::handle:vertical {{ background: {t['border']}; border-radius: 5px; min-height: 24px; }}
    QScrollBar:horizontal {{ height: 10px; background: transparent; }}
    QScrollBar::handle:horizontal {{ background: {t['border']}; border-radius: 5px; min-width: 24px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QSplitter::handle {{ background: {t['border']}; }}
    QLabel#muted {{ color: {t['muted']}; }}
    QLabel#title {{ font-size: 13pt; font-weight: 700; }}
    QLabel#value {{ font-family: Consolas, 'DejaVu Sans Mono', monospace; font-size: 11pt; }}
    QCheckBox::indicator {{ width: 14px; height: 14px; }}
    QToolTip {{ background: {t['base']}; color: {t['text']}; border: 1px solid {t['border']}; }}
    """


def apply_theme(app: QApplication, dark: bool) -> dict:
    t = DARK if dark else LIGHT
    app.setStyle("Fusion")
    app.setPalette(palette_for(t))
    app.setStyleSheet(stylesheet_for(t))
    return t
