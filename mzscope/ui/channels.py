"""Tabla de los 20 canales: senal de la placa, grafico, color y boton de ajustes (escala/offset
y filtro del grafico, ver channel_dialog.py)."""
from __future__ import annotations

import ast
import math
import operator

from PySide6.QtCore import QLocale, Qt, Signal
from PySide6.QtGui import QColor, QValidator
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QHBoxLayout, QHeaderView,
                               QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import protocol as P
from ..header import BoardNames
from ..settings import ChannelConfig, PlotConfig


class ColorButton(QPushButton):
    changed = Signal(str)

    def __init__(self, color: str):
        super().__init__()
        self.setFixedSize(28, 20)
        self.setCursor(Qt.PointingHandCursor)
        self.setAutoDefault(False)          # en un QDialog, Intro no debe "pulsar" este boton
        self.set_color(color)
        self.clicked.connect(self._pick)

    def set_color(self, color: str):
        self.color = color
        self.setStyleSheet(f"QPushButton {{ background: {color}; border: 1px solid #555; border-radius: 3px; }}")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.color), self, "Color de la traza")
        if c.isValid():
            self.set_color(c.name())
            self.changed.emit(self.color)


_EXPR_CHARS = set("0123456789.,+-*/()eE ")


def parse_number_expr(text: str) -> float | None:
    """Evalua un numero o una expresion aritmetica simple ("1/65536", "3.3*2", "-1e-3").

    Acepta ``,`` o ``.`` como separador decimal (nunca separador de miles). Devuelve
    None si el texto no es una expresion valida.
    """
    txt = text.strip().replace(",", ".")
    if not txt or any(ch not in _EXPR_CHARS for ch in txt):
        return None
    try:
        tree = ast.parse(txt, mode="eval")
    except SyntaxError:
        return None
    ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.Pow: operator.pow}

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            return ops[type(node.op)](ev(node.left), ev(node.right))
        raise ValueError("expresion no permitida")

    try:
        v = ev(tree)
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    return v if math.isfinite(v) else None


class CompactDoubleSpinBox(QDoubleSpinBox):
    """Casilla numerica que:

    * acepta hasta 12 decimales y muestra el valor sin ceros de relleno (1.5, 0.00123, ...);
    * admite ``,`` o ``.`` como decimal, sin separador de miles (independiente del idioma
      de Windows: con la configuracion regional en espanol Qt interpretaba "1.9" como 19);
    * admite expresiones: ``1/65536``, ``3.3/4096*2``, ``-1e-3``. Se evaluan al pulsar
      Intro o al salir de la casilla.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLocale(QLocale.c())
        self.setDecimals(12)
        self.setKeyboardTracking(False)

    def textFromValue(self, value: float) -> str:
        txt = f"{value:.9g}"
        return txt if "e" not in txt else f"{value:.12f}".rstrip("0").rstrip(".")

    def _core(self, text: str) -> str | None:
        """Texto sin prefijo ni sufijo (Qt los incluye en lo que pasa a validate/valueFromText).
        None si es el specialValueText (valor minimo)."""
        txt = text.strip()
        special = self.specialValueText()
        if special and txt == special.strip():
            return None
        pre, suf = self.prefix(), self.suffix()
        if pre and txt.startswith(pre.strip()):
            txt = txt[len(pre.strip()):]
        if suf and txt.endswith(suf.strip()):
            txt = txt[:-len(suf.strip())]
        return txt.strip()

    def valueFromText(self, text: str) -> float:
        core = self._core(text)
        if core is None:
            return self.minimum()
        v = parse_number_expr(core)
        return self.value() if v is None else v

    def validate(self, text: str, pos: int):
        core = self._core(text)
        if core is None:
            return (QValidator.Acceptable, text, pos)
        if any(ch not in _EXPR_CHARS for ch in core):
            return (QValidator.Invalid, text, pos)
        if parse_number_expr(core) is not None:
            return (QValidator.Acceptable, text, pos)
        return (QValidator.Intermediate, text, pos)

    def fixup(self, text: str) -> str:
        return self.textFromValue(self.value())


def settings_text(cfg: ChannelConfig) -> str:
    """Texto del boton de ajustes: la calibracion del canal ("×1 +0")."""
    return f"×{cfg.scale:.4g} {cfg.offset:+.4g}"


class ChannelPanel(QWidget):
    config_changed = Signal()        # asignacion/color cambiados (recrear las curvas)
    apply_requested = Signal()       # enviar seleccion a la placa
    settings_requested = Signal(int) # abrir la ventana de ajustes del canal (slot)

    COLS = ("Ch", "Ver", "Señal de la placa", "Gráfico", "Color", "Ajustes")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.names = BoardNames()
        self.channels: list[ChannelConfig] = []
        self.plots: list[PlotConfig] = []
        self._settings_buttons: dict[int, QPushButton] = {}
        self._building = False

        self.table = QTableWidget(P.CHANNELS, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        for col, wdt in ((0, 30), (1, 34), (3, 112), (4, 44), (5, 96)):
            self.table.setColumnWidth(col, wdt)
        self.table.horizontalHeaderItem(5).setToolTip("Escala y offset del canal, filtro del gráfico y orden de las trazas")
        self.table.horizontalHeaderItem(1).setToolTip("Dibujar el canal en su gráfico (desmarcado = oculto, "
                                                       "sigue recibiéndose y exportándose)")
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)

        self.btn_apply = QPushButton("Enviar selección a la placa")
        self.btn_apply.setObjectName("primary")
        self.btn_apply.setToolTip("Envía los 20 comandos de selección de canal (ids 201..220) al R5")
        self.btn_apply.clicked.connect(self.apply_requested)
        self.lbl_source = QLabel("")
        self.lbl_source.setObjectName("muted")
        self.lbl_source.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.btn_apply)
        lay.addWidget(self.lbl_source)

    # ---- modelo -----------------------------------------------------------
    def set_model(self, names: BoardNames, channels: list[ChannelConfig], plots: list[PlotConfig]):
        self.names = names
        self.channels = channels
        self.plots = plots
        self.lbl_source.setText(f"Señales de {names.source}")
        self._rebuild()

    def set_plots(self, plots: list[PlotConfig]):
        self.plots = plots
        self._rebuild()

    def _rebuild(self):
        self._building = True
        labels = self.names.observable_labels()
        for row in range(P.CHANNELS):
            cfg = self.channels[row]
            item = QTableWidgetItem(str(row + 1))
            item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 0, item)

            chk = QCheckBox()
            chk.setChecked(cfg.visible)
            chk.toggled.connect(lambda v, r=row: self._set(r, "visible", bool(v)))
            wrap_chk = QWidget()
            hc = QHBoxLayout(wrap_chk)
            hc.setContentsMargins(0, 0, 0, 0)
            hc.setAlignment(Qt.AlignCenter)
            hc.addWidget(chk)
            self.table.setCellWidget(row, 1, wrap_chk)

            sig = QComboBox()
            sig.setMaxVisibleItems(25)
            sig.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            sig.setMinimumContentsLength(4)
            for i, l in enumerate(labels):
                sig.addItem(f"{i:>2}  {l}", self.names.observables[i])
            idx = self.names.observables.index(cfg.signal) if cfg.signal in self.names.observables else 0
            sig.setCurrentIndex(idx)
            sig.currentIndexChanged.connect(lambda _i, r=row, w=sig: self._set(r, "signal", w.currentData()))
            self.table.setCellWidget(row, 2, sig)

            plot = QComboBox()
            plot.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            plot.setMinimumContentsLength(4)
            plot.addItem("—", -1)
            for i, p in enumerate(self.plots):
                plot.addItem(p.title, i)
            plot.setCurrentIndex(cfg.plot + 1 if -1 <= cfg.plot < len(self.plots) else 0)
            plot.currentIndexChanged.connect(lambda _i, r=row, w=plot: self._set(r, "plot", w.currentData()))
            self.table.setCellWidget(row, 3, plot)

            col = ColorButton(cfg.color)
            col.changed.connect(lambda c, r=row: self._set(r, "color", c))
            wrap = QWidget()
            hl = QHBoxLayout(wrap)
            hl.setContentsMargins(4, 0, 4, 0)
            hl.addWidget(col)
            self.table.setCellWidget(row, 4, wrap)

            btn = QPushButton(settings_text(cfg))
            btn.setToolTip("Escala y offset del canal, filtro del gráfico y orden de las trazas (y = escala · x + offset)")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _c, r=row: self.settings_requested.emit(r))
            self.table.setCellWidget(row, 5, btn)
            self._settings_buttons[row] = btn

        self.table.resizeRowsToContents()
        self._building = False

    def refresh_settings_text(self):
        """Actualiza el texto de los botones de ajustes (tras cambiar escala/offset en la ventana)."""
        for row, btn in self._settings_buttons.items():
            btn.setText(settings_text(self.channels[row]))

    def _set(self, row: int, attr: str, value):
        setattr(self.channels[row], attr, value)
        if not self._building:
            self.config_changed.emit()

    def labels(self) -> list[str]:
        """Etiqueta corta de cada canal (nombre de la senal seleccionada)."""
        return [self.names.pretty(c.signal) for c in self.channels]

    def signal_indices(self) -> list[int]:
        return [self.names.observables.index(c.signal) if c.signal in self.names.observables else 0
                for c in self.channels]
