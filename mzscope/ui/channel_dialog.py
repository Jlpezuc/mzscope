"""Ventana de ajustes de un canal y del filtro de su grafico.

Se abre desde el boton "Ajustes" de la tabla de canales (o desde la pestana Graficos). Tiene
tres bloques:

* **Canal**: color, escala y offset (y = escala · x + offset). Es lo unico que es por canal.
* **Grafico**: que se muestra (original, filtro o ambas) y el acoplamiento DC/AC, que se aplica
  a todas las trazas del grafico (originales y filtradas) y al nivel de disparo.
* **Filtro del grafico**: uno solo por grafico (selector), aplicado a TODAS sus senales tras el
  acoplamiento; los parametros que se ven dependen del filtro elegido (FilterConfig).
* **Orden de dibujo**: lista de las trazas del grafico (senales y filtros) de atras hacia
  delante; se mueven con los botones o con Ctrl+flechas.

Los cambios se aplican al momento: ``values_changed`` cuando solo hay que redibujar y
``structure_changed`` cuando cambian las trazas (que se muestra, orden, color).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem, QPushButton, QRadioButton, QSpinBox,
                               QStackedWidget, QVBoxLayout, QWidget)

from ..settings import FILTER_KINDS, MAX_HARMONIC, ChannelConfig, PlotConfig
from .channels import ColorButton, CompactDoubleSpinBox
from .range_slider import RangeSlider


class ChannelDialog(QDialog):
    values_changed = Signal()       # escala/offset/parametros del filtro: basta redibujar
    structure_changed = Signal()    # color, modo de visualizacion u orden: recrear las curvas

    def __init__(self, slot: int | None, channels: list[ChannelConfig], plots: list[PlotConfig],
                 labels: list[str], plot_index: int | None = None, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.channels = channels
        self.plots = plots
        self.labels = labels
        self.plot_index = channels[slot].plot if slot is not None else (plot_index if plot_index is not None else -1)
        self._loading = False
        self.setModal(False)
        self.setMinimumWidth(420)

        lay = QVBoxLayout(self)
        if slot is not None:
            lay.addWidget(self._build_channel_group())
        self.g_plot = self._build_plot_group()
        self.g_filter = self._build_filter_group()
        self.g_order = self._build_order_group()
        lay.addWidget(self.g_plot)
        lay.addWidget(self.g_filter)
        lay.addWidget(self.g_order)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.close)
        lay.addWidget(bb)
        # Intro en una casilla solo debe confirmar el valor: ningun boton es "por defecto"
        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
            b.setDefault(False)

        self._load()
        self._update_titles()

    # ---- construccion -------------------------------------------------------
    def _build_channel_group(self) -> QGroupBox:
        cfg = self.channels[self.slot]
        g = QGroupBox()
        self.g_channel = g
        f = QFormLayout(g)
        self.btn_color = ColorButton(cfg.color)
        self.btn_color.changed.connect(self._on_color)
        self.sp_scale = CompactDoubleSpinBox()
        self.sp_offset = CompactDoubleSpinBox()
        for sp in (self.sp_scale, self.sp_offset):
            sp.setRange(-1e9, 1e9)
            sp.setSingleStep(0.1)
            sp.setToolTip("y = escala · x + offset. Hasta 12 decimales; admite , o . como decimal y expresiones (1/65536, 3.3/4096)")
        self.sp_scale.valueChanged.connect(lambda v: self._set_channel("scale", float(v)))
        self.sp_offset.valueChanged.connect(lambda v: self._set_channel("offset", float(v)))
        f.addRow("Color", self.btn_color)
        f.addRow("Escala", self.sp_scale)
        f.addRow("Offset", self.sp_offset)
        hint = QLabel("y = escala · x + offset (se aplica antes del filtro; el CSV se graba con esta calibración)")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        f.addRow(hint)
        return g

    def _build_plot_group(self) -> QGroupBox:
        g = QGroupBox()
        f = QFormLayout(g)
        hb = QHBoxLayout()
        self.rb_show = {}
        for key, text in (("original", "Solo la señal"), ("filtro", "Solo el filtro"), ("ambas", "Ambas")):
            rb = QRadioButton(text)
            rb.toggled.connect(lambda on, k=key: on and self._set_show(k))
            self.rb_show[key] = rb
            hb.addWidget(rb)
        hb.addStretch(1)
        f.addRow("Mostrar", hb)
        self.cmb_coupling = QComboBox()
        self.cmb_coupling.addItem("DC (tal cual)", "DC")
        self.cmb_coupling.addItem("AC (centrada en 0: se resta la media)", "AC")
        self.cmb_coupling.setToolTip("Se aplica a todas las trazas del gráfico, originales y filtradas, "
                                     "y al nivel de disparo (con AC, nivel 0 = cruce por la media)")
        self.cmb_coupling.currentIndexChanged.connect(lambda _i: self._set_coupling(self.cmb_coupling.currentData()))
        f.addRow("Acoplamiento", self.cmb_coupling)
        return g

    def _build_filter_group(self) -> QGroupBox:
        g = QGroupBox()
        v = QVBoxLayout(g)
        self.cmb_kind = QComboBox()
        for key, text in FILTER_KINDS:
            self.cmb_kind.addItem(text, key)
        self.cmb_kind.currentIndexChanged.connect(lambda _i: self._set_filter("kind", self.cmb_kind.currentData()))
        top = QFormLayout()
        top.addRow("Filtro", self.cmb_kind)
        v.addLayout(top)

        # una pagina de parametros por tipo de filtro
        self.pages = QStackedWidget()
        self.page_index: dict[str, int] = {}

        def page(keys, build):
            w = QWidget()
            build(QFormLayout(w))
            idx = self.pages.addWidget(w)
            for k in keys:
                self.page_index[k] = idx

        page(("none",), lambda f: f.addRow(QLabel("Sin filtro: se dibuja la señal tras el acoplamiento.")))
        page(("mean", "rms"), self._build_page_moving)
        page(("harmonics",), self._build_page_harmonics)
        page(("lpf", "hpf"), self._build_page_lp_hp)
        page(("bpf", "brf"), self._build_page_band)
        page(("notch",), self._build_page_notch)
        v.addWidget(self.pages)

        # extras comunes
        ex = QHBoxLayout()
        self.sp_shift = QSpinBox()
        self.sp_shift.setRange(-1_000_000, 1_000_000)
        self.sp_shift.setSuffix(" muestras")
        self.sp_shift.setToolTip("Desplaza la traza filtrada en el tiempo (+ = retrasa). Útil para alinear señales con retardo conocido")
        self.sp_shift.valueChanged.connect(lambda val: self._set_filter("shift", int(val)))
        self.chk_invert = QCheckBox("Invertir el signo")
        self.chk_invert.toggled.connect(lambda val: self._set_filter("invert", bool(val)))
        ex.addWidget(QLabel("Desplazar"))
        ex.addWidget(self.sp_shift)
        ex.addSpacing(12)
        ex.addWidget(self.chk_invert)
        ex.addStretch(1)
        v.addLayout(ex)
        return g

    def _build_page_moving(self, f: QFormLayout):
        self.sp_width = QSpinBox()
        self.sp_width.setRange(1, 1_000_000)
        self.sp_width.setSuffix(" muestras")
        self.sp_width.setToolTip("Ancho de la ventana en muestras (ISR). A 10 kHz, 100 muestras = 10 ms")
        self.sp_width.valueChanged.connect(lambda val: self._set_filter("width", int(val)))
        self.chk_centered = QCheckBox("Centrada (sin retardo; si no, causal como un filtro real)")
        self.chk_centered.toggled.connect(lambda val: self._set_filter("centered", bool(val)))
        f.addRow("Ancho", self.sp_width)
        f.addRow(self.chk_centered)

    def _build_page_harmonics(self, f: QFormLayout):
        self.sp_fund = CompactDoubleSpinBox()
        self.sp_fund.setRange(0.0, 1e9)
        self.sp_fund.setSuffix(" Hz")
        self.sp_fund.setSpecialValueText("automática (pico de la FFT)")
        self.sp_fund.setToolTip("0 = la fundamental se estima con la FFT del tramo visible; otro valor = frecuencia fija")
        self.sp_fund.valueChanged.connect(lambda val: self._set_filter("fund_freq_hz", float(val)))
        f.addRow("Fundamental", self.sp_fund)
        self.sl_harm = RangeSlider(0, MAX_HARMONIC)
        self.sl_harm.labels = {0: "DC"}
        self.sl_harm.setToolTip("Armónicos que se suman en la traza: de DC (0) a 100. Arrastra cada tirador; "
                                "flechas = mover el rango, Shift+flechas = de 10 en 10")
        self.sl_harm.changed.connect(self._on_harm_slider)
        self.sp_kmin = QSpinBox()
        self.sp_kmax = QSpinBox()
        for sp in (self.sp_kmin, self.sp_kmax):
            sp.setRange(0, MAX_HARMONIC)
            sp.setSpecialValueText("DC")
        self.sp_kmin.valueChanged.connect(lambda val: self._on_harm_spin())
        self.sp_kmax.valueChanged.connect(lambda val: self._on_harm_spin())
        hb = QHBoxLayout()
        hb.addWidget(QLabel("desde"))
        hb.addWidget(self.sp_kmin)
        hb.addWidget(QLabel("hasta"))
        hb.addWidget(self.sp_kmax)
        hb.addStretch(1)
        f.addRow("Armónicos", self.sl_harm)
        f.addRow("", hb)
        self.lbl_harm = QLabel("")
        self.lbl_harm.setObjectName("muted")
        self.lbl_harm.setWordWrap(True)
        f.addRow(self.lbl_harm)

    def _build_page_lp_hp(self, f: QFormLayout):
        self.sp_fc = self._freq_spin("fc")
        self.sp_order = self._order_spin()
        f.addRow("Frecuencia de corte", self.sp_fc)
        f.addRow("Orden", self.sp_order)
        self.chk_zero1 = self._zero_phase_check()
        f.addRow(self.chk_zero1)

    def _build_page_band(self, f: QFormLayout):
        self.sp_flo = self._freq_spin("f_lo")
        self.sp_fhi = self._freq_spin("f_hi")
        self.sp_order2 = self._order_spin()
        f.addRow("Frecuencia inferior", self.sp_flo)
        f.addRow("Frecuencia superior", self.sp_fhi)
        f.addRow("Orden", self.sp_order2)
        self.chk_zero2 = self._zero_phase_check()
        f.addRow(self.chk_zero2)

    def _build_page_notch(self, f: QFormLayout):
        self.sp_fnotch = self._freq_spin("fc")
        self.sp_q = CompactDoubleSpinBox()
        self.sp_q.setRange(0.1, 1e4)
        self.sp_q.setToolTip("Factor de calidad: ancho de la banda rechazada = f0 / Q")
        self.sp_q.valueChanged.connect(lambda val: self._set_filter("q", float(val)))
        f.addRow("Frecuencia rechazada", self.sp_fnotch)
        f.addRow("Q", self.sp_q)
        self.chk_zero3 = self._zero_phase_check()
        f.addRow(self.chk_zero3)

    def _freq_spin(self, attr: str) -> CompactDoubleSpinBox:
        sp = CompactDoubleSpinBox()
        sp.setRange(1e-3, 1e9)
        sp.setSuffix(" Hz")
        sp.valueChanged.connect(lambda val, a=attr: self._set_filter(a, float(val)))
        return sp

    def _order_spin(self) -> QSpinBox:
        sp = QSpinBox()
        sp.setRange(1, 12)
        sp.setToolTip("Butterworth: pendiente 6 dB/octava por orden (orden 2 = 12 dB/oct = 40 dB/década)")
        sp.valueChanged.connect(lambda val: self._set_filter("order", int(val)))
        return sp

    def _zero_phase_check(self) -> QCheckBox:
        c = QCheckBox("Fase cero (sin retardo, filtrado ida y vuelta; si no, causal como un filtro real)")
        c.toggled.connect(lambda val: self._set_filter("centered", bool(val)))
        return c

    def _on_harm_slider(self, lo: int, hi: int):
        p = self._plot()
        if self._loading or p is None:
            return
        p.filter.k_min, p.filter.k_max = lo, hi
        self._sync_harm_widgets()
        self.values_changed.emit()

    def _on_harm_spin(self):
        p = self._plot()
        if self._loading or p is None:
            return
        lo, hi = self.sp_kmin.value(), self.sp_kmax.value()
        p.filter.k_min, p.filter.k_max = min(lo, hi), max(lo, hi)
        self._sync_harm_widgets()
        self.values_changed.emit()

    def _sync_harm_widgets(self):
        p = self._plot()
        if p is None:
            return
        was = self._loading
        self._loading = True
        self.sl_harm.set_values(p.filter.k_min, p.filter.k_max)
        self.sp_kmin.setValue(p.filter.k_min)
        self.sp_kmax.setValue(p.filter.k_max)
        self._loading = was
        lo, hi = p.filter.k_min, p.filter.k_max
        if hi == 0:
            txt = "Solo la componente continua (media)."
        elif lo == hi == 1:
            txt = "Solo la fundamental (senoide ajustada)."
        else:
            txt = f"Suma de los armónicos {'DC' if lo == 0 else lo} a {hi} de la fundamental" + \
                  (": cuanto más armónicos, más se parece a la señal." if lo <= 1 else ".")
        self.lbl_harm.setText(txt)

    def _build_order_group(self) -> QGroupBox:
        g = QGroupBox("Orden de dibujo (arriba = al fondo, abajo = delante)")
        h = QHBoxLayout(g)
        self.lst_order = QListWidget()
        self.lst_order.setToolTip("Las trazas se dibujan en este orden: la última de la lista queda por encima de todas")
        vb = QVBoxLayout()
        self.btn_back = QPushButton("▲ Atrás")
        self.btn_front = QPushButton("▼ Delante")
        self.btn_back.clicked.connect(lambda: self._move(-1))
        self.btn_front.clicked.connect(lambda: self._move(+1))
        vb.addWidget(self.btn_back)
        vb.addWidget(self.btn_front)
        vb.addStretch(1)
        h.addWidget(self.lst_order, 1)
        h.addLayout(vb)
        QShortcut(QKeySequence("Ctrl+Up"), self.lst_order, lambda: self._move(-1))
        QShortcut(QKeySequence("Ctrl+Down"), self.lst_order, lambda: self._move(+1))
        return g

    # ---- modelo -> widgets ----------------------------------------------------
    def _plot(self) -> PlotConfig | None:
        return self.plots[self.plot_index] if 0 <= self.plot_index < len(self.plots) else None

    def _update_titles(self):
        p = self._plot()
        if self.slot is not None:
            self.setWindowTitle(f"Canal {self.slot + 1}: {self.labels[self.slot]}")
            self.g_channel.setTitle(f"Canal {self.slot + 1} — {self.labels[self.slot]}")
        else:
            self.setWindowTitle(f"Gráfico «{p.title}»" if p else "Gráfico")
        if p is None:
            self.g_plot.setTitle("Gráfico — el canal no está asignado a ninguno")
            self.g_filter.setTitle("Filtro del gráfico")
        else:
            self.g_plot.setTitle(f"Gráfico «{p.title}»")
        self.g_plot.setEnabled(p is not None)
        self.g_filter.setEnabled(p is not None and p.show != "original")
        self.g_order.setEnabled(p is not None)

    def _load(self):
        self._loading = True
        if self.slot is not None:
            c = self.channels[self.slot]
            self.btn_color.set_color(c.color)
            self.sp_scale.setValue(c.scale)
            self.sp_offset.setValue(c.offset)
        p = self._plot()
        if p is not None:
            f = p.filter
            self.rb_show.get(p.show, self.rb_show["original"]).setChecked(True)
            self.cmb_coupling.setCurrentIndex(max(self.cmb_coupling.findData(p.coupling), 0))
            self.cmb_kind.setCurrentIndex(max(self.cmb_kind.findData(f.kind), 0))
            self.sp_width.setValue(f.width)
            self.chk_centered.setChecked(f.centered)
            self.sp_fund.setValue(f.fund_freq_hz)
            self._sync_harm_widgets()
            self.sp_fc.setValue(f.fc)
            self.sp_fnotch.setValue(f.fc)
            self.sp_order.setValue(f.order)
            self.sp_order2.setValue(f.order)
            self.sp_flo.setValue(f.f_lo)
            self.sp_fhi.setValue(f.f_hi)
            self.sp_q.setValue(f.q)
            for c in (self.chk_zero1, self.chk_zero2, self.chk_zero3):
                c.setChecked(f.centered)
            self.sp_shift.setValue(f.shift)
            self.chk_invert.setChecked(f.invert)
        self._loading = False
        self._update_enabled()
        self._rebuild_order()

    def _update_enabled(self):
        p = self._plot()
        if p is None:
            return
        filt_on = p.show != "original"
        self.g_filter.setEnabled(filt_on)
        self.g_filter.setTitle("Filtro del gráfico (se aplica a todas sus señales, tras el acoplamiento)" if filt_on
                               else "Filtro del gráfico — activa «Solo el filtro» o «Ambas» en Mostrar")
        self.pages.setCurrentIndex(self.page_index.get(p.filter.kind, 0))

    def _rebuild_order(self):
        p = self._plot()
        cur = self.lst_order.currentItem().data(Qt.UserRole) if self.lst_order.currentItem() else None
        self.lst_order.clear()
        if p is None:
            return
        for key, slot, kind in p.traces(self.plot_index, self.channels):
            text = f"{slot + 1}: {self.labels[slot]}" + ("  (filtro)" if kind == "f" else "")
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, key)
            if slot == self.slot:
                fnt = it.font()
                fnt.setBold(True)
                it.setFont(fnt)
            self.lst_order.addItem(it)
            if key == cur:
                self.lst_order.setCurrentItem(it)
        if self.lst_order.currentRow() < 0 and self.lst_order.count():
            self.lst_order.setCurrentRow(self.lst_order.count() - 1)

    # ---- widgets -> modelo ----------------------------------------------------
    def _set_channel(self, attr: str, value):
        if self._loading:
            return
        setattr(self.channels[self.slot], attr, value)
        self.values_changed.emit()

    def _on_color(self, color: str):
        if self._loading:
            return
        self.channels[self.slot].color = color
        self.structure_changed.emit()

    def _set_show(self, mode: str):
        p = self._plot()
        if self._loading or p is None or p.show == mode:
            return
        p.show = mode
        self._update_enabled()
        self._rebuild_order()
        self.structure_changed.emit()

    def _set_coupling(self, coupling: str):
        p = self._plot()
        if self._loading or p is None or p.coupling == coupling:
            return
        p.coupling = coupling
        self.structure_changed.emit()         # tambien recoloca la linea del nivel de disparo

    def _set_filter(self, attr: str, value):
        p = self._plot()
        if self._loading or p is None or getattr(p.filter, attr) == value:
            return
        setattr(p.filter, attr, value)
        if attr == "centered":                # el mismo flag se ve en varias paginas
            self._loading = True
            self.chk_centered.setChecked(value)
            for c in (self.chk_zero1, self.chk_zero2, self.chk_zero3):
                c.setChecked(value)
            self._loading = False
        self._update_enabled()
        if attr == "fund_freq_hz":
            self.values_changed.emit()        # la leyenda se actualiza sola con la frecuencia
        else:
            self.structure_changed.emit()     # cambia el texto de la leyenda

    def _move(self, delta: int):
        p = self._plot()
        it = self.lst_order.currentItem()
        if p is None or it is None:
            return
        p.move_trace(it.data(Qt.UserRole), delta, self.plot_index, self.channels)
        self._rebuild_order()
        self.structure_changed.emit()

    # ---- desde fuera ----------------------------------------------------------
    def refresh(self, labels: list[str]):
        """Se llama cuando cambia la estructura por otro lado (p. ej. el canal cambia de grafico)."""
        self.labels = labels
        if self.slot is not None:
            self.plot_index = self.channels[self.slot].plot
        self._load()
        self._update_titles()
