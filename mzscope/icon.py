"""Icono de la aplicacion (assets/mzscope.svg): pantalla de osciloscopio con una traza.

``app_icon()`` devuelve un QIcon con el SVG renderizado a varios tamanos (16..256 px), que se
pone como icono de la aplicacion: la ventana principal y los dialogos lo heredan, y en Windows
tambien la barra de tareas (hace falta el AppUserModelID, ver ``setup_windows_taskbar``).
``export_icon()`` escribe assets/mzscope.png y assets/mzscope.ico para accesos directos.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap

ASSETS = Path(__file__).resolve().parent / "assets"
SVG = ASSETS / "mzscope.svg"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def _render(size: int) -> QImage:
    from PySide6.QtSvg import QSvgRenderer
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    r = QSvgRenderer(str(SVG))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p)
    p.end()
    return img


def app_icon() -> QIcon:
    icon = QIcon()
    if not SVG.is_file():
        return icon
    try:
        for s in SIZES:
            icon.addPixmap(QPixmap.fromImage(_render(s)))
    except ImportError:              # sin modulo QtSvg: Qt carga el SVG por su cuenta si tiene el plugin
        icon = QIcon(str(SVG))
    return icon


def setup_windows_taskbar(app_id: str = "MicroZohm.mzscope") -> None:
    """Sin esto Windows agrupa la ventana bajo python.exe y muestra el icono de Python en la barra."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except (AttributeError, OSError):
            pass


def export_icon() -> list[Path]:
    """Genera assets/mzscope.png (256 px) y assets/mzscope.ico (16..256 px)."""
    out = []
    png = ASSETS / "mzscope.png"
    _render(256).save(str(png))
    out.append(png)
    ico = ASSETS / "mzscope.ico"
    if _write_ico(ico, [_render(s) for s in (16, 24, 32, 48, 64, 128, 256)]):
        out.append(ico)
    return out


def _write_ico(path: Path, images: list[QImage]) -> bool:
    """ICO con entradas PNG (formato admitido por Windows Vista+), sin dependencias externas."""
    import struct
    from PySide6.QtCore import QBuffer, QIODevice
    entries = []
    for img in images:
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "PNG")
        entries.append((img.width(), img.height(), bytes(buf.data())))
    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    directory = b""
    data = b""
    for w, h, png in entries:
        directory += struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    path.write_bytes(header + directory + data)
    return True


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    QApplication([])
    for p in export_icon():
        print(p)
