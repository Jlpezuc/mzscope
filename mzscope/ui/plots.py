"""Pila de graficos de tiempo (uno o varios, eje X compartido)."""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from .. import filters
from ..settings import ChannelConfig, PlotConfig


MAX_POINTS = 4000     # puntos por curva tras la decimacion min/max


def decimate_minmax(t: np.ndarray, data: np.ndarray, max_points: int = MAX_POINTS) -> tuple[np.ndarray, np.ndarray]:
    """Reduce (n, C) a <= max_points filas conservando minimo y maximo de cada bloque.

    Con bloques de k muestras salen 2 filas por bloque (min y max), asi que el coste
    de dibujar no depende del tamano del buffer. NaN (huecos) se conservan si todo
    el bloque es NaN.
    """
    n = len(t)
    if n <= max_points:
        return t, data
    k = int(np.ceil(2.0 * n / max_points))
    m = n // k
    blk = data[:m * k].reshape(m, k, data.shape[1])
    lo = np.fmin.reduce(blk, axis=1)
    hi = np.fmax.reduce(blk, axis=1)
    out = np.empty((2 * m, data.shape[1]), dtype=data.dtype)
    out[0::2] = lo
    out[1::2] = hi
    tt = np.repeat(t[:m * k:k], 2)
    return tt, out


class TimePlot(pg.PlotWidget):
    """Un grafico de tiempo con sus curvas (una por canal asignado)."""

    def __init__(self, cfg: PlotConfig, theme: dict):
        super().__init__(axisItems={"bottom": pg.AxisItem("bottom")})
        self.cfg = cfg
        self.index = 0
        # trazas dibujadas, de atras hacia delante: clave "s<slot>" (senal) o "f<slot>" (filtro)
        self.traces: list[tuple[str, int, str]] = []
        self.curves: dict[str, pg.PlotDataItem] = {}
        self.labels: dict[int, str] = {}
        self.ts = 1e-4
        # marcadores de disparo: nivel (horizontal, arrastrable) y t = 0 (vertical)
        self.level_line = pg.InfiniteLine(angle=0, movable=True, label="nivel {value:.4g}",
                                          labelOpts={"position": 0.12, "movable": True})
        self.level_line.setVisible(False)
        self.level_line.setZValue(20)
        self.zero_line = pg.InfiniteLine(angle=90, movable=False, pos=0.0)
        self.zero_line.setVisible(False)
        self.zero_line.setZValue(10)
        self.addItem(self.level_line, ignoreBounds=True)
        self.addItem(self.zero_line, ignoreBounds=True)
        self.setDownsampling(auto=True, mode="peak")
        self.setClipToView(True)
        self.setMouseEnabled(x=True, y=True)
        self.getPlotItem().setContentsMargins(4, 4, 8, 2)
        self.legend = self.addLegend(offset=(-8, 4), labelTextSize="9pt")
        self.apply_theme(theme)
        self.apply_config()

    def apply_theme(self, theme: dict):
        self.theme = theme
        self.setBackground(theme["plot_bg"])
        fg = theme["plot_fg"]
        for ax in ("left", "bottom"):
            a = self.getAxis(ax)
            a.setPen(pg.mkPen(fg))
            a.setTextPen(pg.mkPen(fg))
        self.getPlotItem().titleLabel.setText(self.cfg.title, color=fg, size="10pt")
        self.legend.setLabelTextColor(fg)
        self.legend.setBrush(pg.mkBrush(theme["plot_bg"] + "B0"))
        self.legend.setPen(pg.mkPen(theme["grid"]))
        self.showGrid(x=self.cfg.grid, y=self.cfg.grid, alpha=0.25)
        self.zero_line.setPen(pg.mkPen(fg, width=1, style=Qt.DashLine))
        self.level_line.label.setColor(fg)

    def apply_config(self):
        pi = self.getPlotItem()
        pi.setTitle(self.cfg.title, color=self.theme["plot_fg"], size="10pt")
        pi.setLabel("left", self.cfg.y_label or None)
        pi.setLabel("bottom", "t", units="s")
        self.showGrid(x=self.cfg.grid, y=self.cfg.grid, alpha=0.25)
        if self.cfg.auto_y:
            self.enableAutoRange(axis="y", enable=True)
        else:
            self.enableAutoRange(axis="y", enable=False)
            self.setYRange(self.cfg.y_min, self.cfg.y_max, padding=0)

    def set_channels(self, index: int, configs: list[ChannelConfig], labels: list[str]):
        """Crea las curvas del grafico ``index``: una por traza de ``cfg.traces`` (senal y/o filtro
        de cada canal asignado y visible), en orden de dibujo (la ultima queda delante)."""
        self.index = index
        for c in self.curves.values():
            self.removeItem(c)
        self.legend.clear()
        self.curves.clear()
        self.labels = {slot: labels[slot] for slot in range(len(labels))}
        self.traces = self.cfg.traces(index, configs)
        both = self.cfg.show == "ambas"
        for z, (key, slot, kind) in enumerate(self.traces):
            color = QColor(configs[slot].color)
            if kind == "s":
                if both:
                    color.setAlpha(110)          # original atenuada cuando tambien se ve el filtro
                pen = pg.mkPen(color, width=1.0 if both else 1.4)
                name = f"{slot + 1}: {labels[slot]}"
            else:
                pen = pg.mkPen(color, width=2.0 if both else 1.6)
                name = f"{slot + 1}: {labels[slot]} · {filters.describe(self.cfg.filter)}"
            curve = pg.PlotDataItem(pen=pen, name=name, connect="finite", skipFiniteCheck=True)
            curve.setZValue(z)
            self.addItem(curve)
            self.curves[key] = curve

    def _legend_label(self, key: str) -> pg.LabelItem | None:
        for sample, label in self.legend.items:
            if sample.item is self.curves.get(key):
                return label
        return None

    def warmup_samples(self) -> int:
        """Muestras extra que hay que pedir antes (y despues si es centrado) de la ventana visible."""
        if self.cfg.show == "original" or not self.traces:
            return 0
        return filters.warmup_samples(self.cfg.filter, self.ts)

    def update_data(self, t: np.ndarray, data: np.ndarray, configs: list[ChannelConfig],
                    keep: tuple[float, float] | None = None):
        """Escala y acopla cada canal, aplica el filtro del grafico a las trazas "f", recorta a
        ``keep`` (rango de tiempo que se dibuja: fuera quedan las muestras de arranque de los
        filtros), diezma y dibuja."""
        if not self.traces:
            return
        cols = []
        scaled: dict[int, np.ndarray] = {}
        for key, slot, kind in self.traces:
            if slot not in scaled:
                c = configs[slot]
                y = data[:, slot]
                if c.scale != 1.0 or c.offset != 0.0:
                    y = y * c.scale + c.offset
                scaled[slot] = filters.couple(y, self.cfg.coupling)   # el acoplamiento va antes que todo
            y = scaled[slot]
            if kind == "f":
                y, info = filters.apply_filter(t, y, self.cfg.filter, self.ts)
                if "fund_hz" in info:
                    lbl = self._legend_label(key)
                    if lbl is not None:
                        lbl.setText(f"{slot + 1}: {self.labels.get(slot, '')} · {filters.describe(self.cfg.filter, info)}")
            cols.append(y)
        if len(t) == 0:
            return
        dd = np.column_stack(cols).astype(np.float32, copy=False)
        if keep is not None:
            m = (t >= keep[0]) & (t <= keep[1])
            if m.any() and not m.all():
                t, dd = t[m], dd[m]      # asi la decimacion reparte sus puntos solo entre lo visible
        tt, dd = decimate_minmax(t, dd)
        for i, (key, _, _) in enumerate(self.traces):
            self.curves[key].setData(tt, dd[:, i])


class PlotStack(QWidget):
    """Varios TimePlot apilados con eje X enlazado."""

    layout_changed = Signal()
    level_dragged = Signal(float)     # nivel de disparo arrastrado en el grafico (unidades de la senal)

    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.plots: list[TimePlot] = []
        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.setHandleWidth(4)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.splitter)
        self.follow = True
        self.ts = 1e-4
        self.trigger_mode = False
        self._trig_slot = -1
        self._trig_cfg: ChannelConfig | None = None

    # ---- estructura -------------------------------------------------------
    def rebuild(self, plot_cfgs: list[PlotConfig], chan_cfgs: list[ChannelConfig], labels: list[str]):
        for p in self.plots:
            p.setParent(None)
            p.deleteLater()
        self.plots = []
        for i, pc in enumerate(plot_cfgs):
            tp = TimePlot(pc, self.theme)
            tp.ts = self.ts
            tp.set_channels(i, chan_cfgs, labels)
            tp.level_line.sigPositionChanged.connect(self._on_level_dragged)
            if self.plots:
                tp.setXLink(self.plots[0])
            self.splitter.addWidget(tp)
            self.plots.append(tp)
        if self.plots:
            self.splitter.setSizes([1000] * len(self.plots))
        self.layout_changed.emit()

    def apply_theme(self, theme: dict):
        self.theme = theme
        for p in self.plots:
            p.apply_theme(theme)

    def apply_plot_configs(self):
        for p in self.plots:
            p.apply_config()

    # ---- disparo ------------------------------------------------------------
    def set_trigger(self, enabled: bool, slot: int, level: float, cfg: ChannelConfig | None):
        """Muestra el nivel de disparo en el grafico del canal fuente y la linea t = 0."""
        self.trigger_mode = enabled
        self._trig_slot = slot
        self._trig_cfg = cfg
        for i, p in enumerate(self.plots):
            p.zero_line.setVisible(enabled)
            on = enabled and cfg is not None and cfg.plot == i
            p.level_line.setVisible(on)
            if on:
                p.level_line.setPen(pg.mkPen(QColor(cfg.color), width=1.2, style=Qt.DashLine))
                p.level_line.setHoverPen(pg.mkPen(QColor(cfg.color), width=2.5))
                p.level_line.blockSignals(True)
                p.level_line.setValue(self._level_to_plot(level, cfg, p.cfg))
                p.level_line.blockSignals(False)
                p.level_line.label.valueChanged()

    @staticmethod
    def _level_to_plot(level: float, cfg: ChannelConfig, plot: PlotConfig) -> float:
        """Nivel (unidades de la senal acoplada, sin escala) -> coordenada Y del grafico.
        Con acoplamiento AC la traza dibujada es (x - media)·escala: el offset desaparece."""
        return level * cfg.scale + (0.0 if plot.coupling == "AC" else cfg.offset)

    def _on_level_dragged(self):
        cfg = self._trig_cfg
        if cfg is None or not (0 <= cfg.plot < len(self.plots)):
            return
        p = self.plots[cfg.plot]
        y = float(p.level_line.value())
        off = 0.0 if p.cfg.coupling == "AC" else cfg.offset
        self.level_dragged.emit((y - off) / (cfg.scale or 1.0))

    def trigger_coupling(self, cfg: ChannelConfig | None) -> str:
        """Acoplamiento del grafico en el que esta el canal fuente del disparo (DC si no esta dibujado)."""
        if cfg is not None and 0 <= cfg.plot < len(self.plots):
            return self.plots[cfg.plot].cfg.coupling
        return "DC"

    def set_trigger_range(self, window_s: float, pretrigger: float):
        if self.plots:
            self.plots[0].setXRange(-pretrigger * window_s, (1.0 - pretrigger) * window_s, padding=0)

    # ---- datos ------------------------------------------------------------
    def warmup_samples(self) -> tuple[int, int]:
        """(antes, despues): muestras extra a pedir alrededor de la ventana visible para que los
        filtros lleguen a regimen fuera de la pantalla. 'despues' solo si algun filtro es centrado."""
        before = after = 0
        for p in self.plots:
            n = p.warmup_samples()
            before = max(before, n)
            if p.cfg.filter.centered:
                after = max(after, n)
        return before, after

    def set_ts(self, ts: float):
        """Periodo de muestreo (base de tiempo del buffer), para la fundamental."""
        self.ts = ts
        for p in self.plots:
            p.ts = ts

    def update_data(self, t: np.ndarray, data: np.ndarray, configs: list[ChannelConfig], window_s: float,
                    keep: tuple[float, float] | None = None):
        """``keep``: rango de tiempo que se dibuja; lo que hay fuera (muestras de arranque de los
        filtros) se filtra pero no se diezma ni se dibuja."""
        # el filtro se aplica sobre las muestras reales; cada grafico diezma sus propias trazas
        for p in self.plots:
            p.update_data(t, data, configs, keep)
        if self.follow and not self.trigger_mode and self.plots and len(t):
            self.plots[0].setXRange(t[-1] - window_s, t[-1], padding=0)

    def clear_data(self):
        for p in self.plots:
            for c in p.curves.values():
                c.clear()
