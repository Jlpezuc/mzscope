"""Slider con dos tiradores (rango minimo..maximo) para elegir los armonicos a dibujar."""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


class RangeSlider(QWidget):
    """Dos tiradores sobre una misma pista. ``changed(lo, hi)`` al soltar o mover."""

    changed = Signal(int, int)
    HANDLE = 12
    MARGIN = 10

    def __init__(self, minimum: int = 0, maximum: int = 100, parent=None):
        super().__init__(parent)
        self.minimum, self.maximum = minimum, maximum
        self.lo, self.hi = minimum, maximum
        self._drag: str | None = None
        self.labels: dict[int, str] = {}       # etiquetas bajo la pista (p. ej. {0: "DC"})
        self.setMinimumHeight(50)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)

    # ---- valores ------------------------------------------------------------
    def set_values(self, lo: int, hi: int, emit: bool = False):
        lo = max(self.minimum, min(int(lo), self.maximum))
        hi = max(self.minimum, min(int(hi), self.maximum))
        if lo > hi:
            lo, hi = hi, lo
        if (lo, hi) != (self.lo, self.hi):
            self.lo, self.hi = lo, hi
            self.update()
            if emit:
                self.changed.emit(self.lo, self.hi)

    # ---- geometria ----------------------------------------------------------
    def _track(self) -> tuple[int, int]:
        return self.MARGIN, self.width() - self.MARGIN

    def _x(self, v: int) -> int:
        x0, x1 = self._track()
        span = max(self.maximum - self.minimum, 1)
        return int(x0 + (v - self.minimum) / span * (x1 - x0))

    def _v(self, x: float) -> int:
        x0, x1 = self._track()
        span = max(self.maximum - self.minimum, 1)
        return int(round(self.minimum + (x - x0) / max(x1 - x0, 1) * span))

    # ---- raton / teclado ------------------------------------------------------
    def mousePressEvent(self, ev):
        x = ev.position().x()
        dlo, dhi = abs(x - self._x(self.lo)), abs(x - self._x(self.hi))
        if dlo == dhi:
            self._drag = "lo" if x < self._x(self.lo) else "hi"
        else:
            self._drag = "lo" if dlo < dhi else "hi"
        self._move_to(x)

    def mouseMoveEvent(self, ev):
        if self._drag:
            self._move_to(ev.position().x())

    def mouseReleaseEvent(self, ev):
        self._drag = None

    def _move_to(self, x: float):
        v = self._v(x)
        if self._drag == "lo":
            self.set_values(min(v, self.hi), self.hi, emit=True)
        else:
            self.set_values(self.lo, max(v, self.lo), emit=True)

    def keyPressEvent(self, ev):
        k = ev.key()
        step = 10 if ev.modifiers() & Qt.ShiftModifier else 1
        if k == Qt.Key_Left:
            self.set_values(self.lo - step, self.hi - step, emit=True)
        elif k == Qt.Key_Right:
            self.set_values(self.lo + step, self.hi + step, emit=True)
        else:
            super().keyPressEvent(ev)

    def wheelEvent(self, ev):
        d = 1 if ev.angleDelta().y() > 0 else -1
        self.set_values(self.lo + d, self.hi + d, emit=True)

    # ---- dibujo ---------------------------------------------------------------
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        fg = pal.color(pal.ColorRole.WindowText)
        accent = pal.color(pal.ColorRole.Highlight)
        x0, x1 = self._track()
        y = 16
        p.setPen(QPen(QColor(fg.red(), fg.green(), fg.blue(), 70), 4, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(x0, y, x1, y)
        p.setPen(QPen(accent, 4, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(self._x(self.lo), y, self._x(self.hi), y)
        # marcas cada 10
        p.setPen(QPen(QColor(fg.red(), fg.green(), fg.blue(), 110), 1))
        for v in range(self.minimum, self.maximum + 1, 10):
            x = self._x(v)
            p.drawLine(x, y + 6, x, y + 9)
        p.setPen(fg)
        f = p.font()
        f.setPointSizeF(max(f.pointSizeF() - 1.5, 6.0))
        p.setFont(f)
        for v in range(self.minimum, self.maximum + 1, 10):
            txt = self.labels.get(v, str(v))
            p.drawText(QRect(self._x(v) - 20, y + 10, 40, 14), Qt.AlignHCenter | Qt.AlignTop, txt)
        # tiradores
        h = self.HANDLE
        for v in (self.lo, self.hi):
            p.setPen(QPen(fg, 1))
            p.setBrush(accent if self.isEnabled() else QColor(128, 128, 128))
            p.drawEllipse(self._x(v) - h // 2, y - h // 2, h, h)
        p.end()
