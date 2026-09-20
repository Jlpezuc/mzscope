"""Pestana de disparo: fuente, flanco, nivel, histeresis, posicion y modo de barrido."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QPushButton, QRadioButton, QSlider, QVBoxLayout, QWidget)

from ..trigger import EDGES, SWEEPS, TriggerConfig
from .channels import CompactDoubleSpinBox


class TriggerPanel(QWidget):
    changed = Signal()          # cualquier parametro cambiado (nivel, fuente, flanco, ...)
    arm_requested = Signal()    # boton Armar (modo unico) / rearmar
    autolevel_requested = Signal()
    mode_requested = Signal(bool)   # True = activar el modo disparo

    def __init__(self, cfg: TriggerConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._block = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)

        intro = QLabel("En el modo <b>Disparo</b> la pantalla no avanza: se captura una ventana alrededor "
                       "del instante en que la señal fuente cruza el nivel, como en un osciloscopio. "
                       "El modo se elige en la barra superior (<i>Modo</i>).")
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        lay.addWidget(intro)

        hb = QHBoxLayout()
        self.btn_mode = QPushButton("Activar modo disparo")
        self.btn_mode.setCheckable(True)
        self.btn_mode.setObjectName("primary")
        hb.addWidget(self.btn_mode)
        lay.addLayout(hb)

        g = QGroupBox("Condición de disparo")
        f = QFormLayout(g)
        self.cmb_source = QComboBox()
        self.cmb_source.setMaxVisibleItems(22)
        self.cmb_edge = QComboBox()
        for key, label in EDGES:
            self.cmb_edge.addItem(label, key)
        self.sp_level = CompactDoubleSpinBox()
        self.sp_level.setRange(-1e9, 1e9)
        self.sp_level.setSingleStep(0.1)
        self.sp_level.setToolTip("En unidades de la señal tal como la manda la placa (sin escala ni offset), "
                                 "después del acoplamiento del gráfico: con AC, 0 = cruce por la media")
        self.sp_hyst = CompactDoubleSpinBox()
        self.sp_hyst.setRange(0.0, 1e9)
        self.sp_hyst.setSingleStep(0.01)
        self.sp_hyst.setToolTip("Para disparar de nuevo la señal debe alejarse del nivel al menos esta cantidad "
                                "(evita disparos múltiples por ruido)")
        self.btn_autolevel = QPushButton("Nivel al 50 %")
        self.btn_autolevel.setToolTip("Pone el nivel en el punto medio entre el mínimo y el máximo recientes de la fuente")
        lvl = QHBoxLayout()
        lvl.addWidget(self.sp_level, 1)
        lvl.addWidget(self.btn_autolevel)
        f.addRow("Fuente", self.cmb_source)
        f.addRow("Flanco", self.cmb_edge)
        f.addRow("Nivel", lvl)
        f.addRow("Histéresis", self.sp_hyst)
        hint = QLabel("El nivel también se puede arrastrar en el gráfico (línea discontinua).")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        f.addRow(hint)
        lay.addWidget(g)

        g2 = QGroupBox("Posición del disparo en la ventana")
        v2 = QVBoxLayout(g2)
        self.sl_pre = QSlider(Qt.Horizontal)
        self.sl_pre.setRange(0, 95)
        self.sl_pre.setTickInterval(10)
        self.sl_pre.setTickPosition(QSlider.TicksBelow)
        self.lbl_pre = QLabel("")
        self.lbl_pre.setObjectName("value")
        h2 = QHBoxLayout()
        h2.addWidget(self.sl_pre, 1)
        h2.addWidget(self.lbl_pre)
        v2.addLayout(h2)
        pre_hint = QLabel("Porcentaje de la ventana anterior al disparo (t < 0).")
        pre_hint.setObjectName("muted")
        v2.addWidget(pre_hint)
        lay.addWidget(g2)

        g3 = QGroupBox("Barrido")
        v3 = QVBoxLayout(g3)
        self.grp_sweep = QButtonGroup(self)
        self.rb_sweep: dict[str, QRadioButton] = {}
        descr = {"auto": "dispara si puede; si no hay disparo muestra la señal libre",
                 "normal": "solo actualiza cuando hay disparo",
                 "single": "una captura y se detiene; Armar para la siguiente"}
        for key, label in SWEEPS:
            rb = QRadioButton(f"{label} — {descr[key]}")
            self.rb_sweep[key] = rb
            self.grp_sweep.addButton(rb)
            v3.addWidget(rb)
        h3 = QHBoxLayout()
        self.btn_arm = QPushButton("Armar")
        self.btn_arm.setObjectName("primary")
        self.lbl_state = QLabel("")
        self.lbl_state.setObjectName("value")
        h3.addWidget(self.btn_arm)
        h3.addWidget(self.lbl_state, 1)
        v3.addLayout(h3)
        lay.addWidget(g3)
        lay.addStretch(1)

        self.cmb_source.currentIndexChanged.connect(self._edited)
        self.cmb_edge.currentIndexChanged.connect(self._edited)
        self.sp_level.valueChanged.connect(self._edited)
        self.sp_hyst.valueChanged.connect(self._edited)
        self.sl_pre.valueChanged.connect(self._edited)
        for rb in self.rb_sweep.values():
            rb.toggled.connect(self._edited)
        self.btn_arm.clicked.connect(self.arm_requested)
        self.btn_autolevel.clicked.connect(self.autolevel_requested)
        self.btn_mode.toggled.connect(self.mode_requested)
        self.load()

    # ---- modelo -----------------------------------------------------------
    def set_channels(self, labels: list[str]):
        self._block = True
        cur = self.cfg.source
        self.cmb_source.clear()
        for i, l in enumerate(labels):
            self.cmb_source.addItem(f"{i + 1}: {l}", i)
        self.cmb_source.setCurrentIndex(min(max(cur, 0), len(labels) - 1))
        self._block = False

    def load(self):
        c = self.cfg
        self._block = True
        self.cmb_edge.setCurrentIndex(max(0, [k for k, _ in EDGES].index(c.edge) if c.edge in dict(EDGES) else 0))
        self.sp_level.setValue(c.level)
        self.sp_hyst.setValue(c.hysteresis)
        self.sl_pre.setValue(int(round(c.pretrigger * 100)))
        self.lbl_pre.setText(f"{self.sl_pre.value()} %")
        self.rb_sweep.get(c.sweep, self.rb_sweep["auto"]).setChecked(True)
        self.btn_arm.setEnabled(c.sweep == "single")
        self._block = False

    def set_level(self, level: float):
        """Nivel cambiado desde fuera (arrastre en el grafico o nivel automatico)."""
        self._block = True
        self.sp_level.setValue(level)
        self.cfg.level = level
        self._block = False
        self.changed.emit()

    def set_mode(self, on: bool):
        self.btn_mode.blockSignals(True)
        self.btn_mode.setChecked(on)
        self.btn_mode.setText("Desactivar modo disparo" if on else "Activar modo disparo")
        self.btn_mode.blockSignals(False)

    def set_state(self, text: str):
        self.lbl_state.setText(text)

    def _edited(self, *_):
        if self._block:
            return
        c = self.cfg
        c.source = int(self.cmb_source.currentData() or 0)
        c.edge = self.cmb_edge.currentData() or "rising"
        c.level = float(self.sp_level.value())
        c.hysteresis = float(self.sp_hyst.value())
        c.pretrigger = self.sl_pre.value() / 100.0
        self.lbl_pre.setText(f"{self.sl_pre.value()} %")
        for key, rb in self.rb_sweep.items():
            if rb.isChecked():
                c.sweep = key
        self.btn_arm.setEnabled(c.sweep == "single")
        self.changed.emit()
