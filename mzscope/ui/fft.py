"""Panel de FFT de un canal (espectro de amplitud, pico y THD)."""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget)

FFT_SIZES = [512, 1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072]


def amplitude_spectrum(x: np.ndarray, ts: float) -> tuple[np.ndarray, np.ndarray]:
    """Espectro de amplitud de pico (ventana Hann, compensada) y eje de frecuencias en Hz."""
    x = np.nan_to_num(np.asarray(x, dtype=np.float64))
    n = len(x)
    if n < 8:
        return np.zeros(1), np.zeros(1)
    x = x - x.mean()
    w = np.hanning(n)
    X = np.fft.rfft(x * w)
    amp = 2.0 * np.abs(X) / w.sum()
    amp[0] /= 2.0
    f = np.fft.rfftfreq(n, ts)
    return f, amp


def thd(f: np.ndarray, amp: np.ndarray, f0: float, n_harm: int = 40) -> float:
    """THD (%) respecto a la fundamental f0 usando los picos en k*f0."""
    if f0 <= 0 or len(f) < 4:
        return float("nan")
    df = f[1] - f[0]
    def peak_at(fx):
        i = int(round(fx / df))
        if i >= len(amp):
            return 0.0
        lo, hi = max(i - 2, 0), min(i + 3, len(amp))
        return float(amp[lo:hi].max())
    fund = peak_at(f0)
    if fund <= 0:
        return float("nan")
    s = 0.0
    for k in range(2, n_harm + 1):
        if k * f0 >= f[-1]:
            break
        s += peak_at(k * f0) ** 2
    return 100.0 * np.sqrt(s) / fund


class FftPanel(QWidget):
    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.ts = 100e-6
        top = QHBoxLayout()
        top.setContentsMargins(6, 4, 6, 0)
        self.channel = QComboBox()
        self.channel.setMinimumWidth(220)
        self.size = QComboBox()
        for n in FFT_SIZES:
            self.size.addItem(f"{n} puntos", n)
        self.size.setCurrentIndex(3)
        self.log = QCheckBox("Escala log")
        self.hold = QCheckBox("Congelar")
        self.info = QLabel("")
        self.info.setObjectName("value")
        top.addWidget(QLabel("Canal:"))
        top.addWidget(self.channel)
        top.addWidget(QLabel("Tamaño:"))
        top.addWidget(self.size)
        top.addWidget(self.log)
        top.addWidget(self.hold)
        top.addStretch(1)
        top.addWidget(self.info)
        self.plot = pg.PlotWidget()
        self.plot.setDownsampling(auto=True, mode="peak")
        self.plot.setClipToView(True)
        self.curve = pg.PlotDataItem(pen=pg.mkPen(theme["accent"], width=1.3))
        self.plot.addItem(self.curve)
        self.marker = pg.ScatterPlotItem(size=9, pen=pg.mkPen("#F2994A"), brush=pg.mkBrush("#F2994A"))
        self.plot.addItem(self.marker)
        self.plot.setLabel("bottom", "f", units="Hz")
        self.plot.setLabel("left", "amplitud")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(top)
        lay.addWidget(self.plot)
        self.log.toggled.connect(self._toggle_log)
        self.apply_theme(theme)

    def apply_theme(self, theme: dict):
        self.theme = theme
        self.plot.setBackground(theme["plot_bg"])
        for ax in ("left", "bottom"):
            a = self.plot.getAxis(ax)
            a.setPen(pg.mkPen(theme["plot_fg"]))
            a.setTextPen(pg.mkPen(theme["plot_fg"]))
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.curve.setPen(pg.mkPen(theme["accent"], width=1.3))

    def set_channels(self, labels: list[str]):
        cur = self.channel.currentIndex()
        self.channel.blockSignals(True)
        self.channel.clear()
        for i, l in enumerate(labels):
            self.channel.addItem(f"{i + 1}: {l}", i)
        self.channel.setCurrentIndex(max(cur, 0))
        self.channel.blockSignals(False)

    @property
    def n_points(self) -> int:
        return int(self.size.currentData())

    @property
    def slot(self) -> int:
        return int(self.channel.currentData() or 0)

    def _toggle_log(self, on: bool):
        self.plot.setLogMode(x=False, y=on)

    def update_spectrum(self, x: np.ndarray, ts: float, label: str):
        if self.hold.isChecked():
            return
        f, amp = amplitude_spectrum(x, ts)
        if self.log.isChecked():
            amp = np.maximum(amp, 1e-12)
        self.curve.setData(f, amp)
        if len(f) > 2:
            i = int(np.argmax(amp[1:]) + 1)
            f0, a0 = float(f[i]), float(amp[i])
            self.marker.setData([f0], [a0])
            t = thd(f, amp, f0)
            self.info.setText(f"{label}   fs = {1 / ts:,.0f} Hz   Δf = {f[1]:.3g} Hz   "
                              f"pico: {f0:,.2f} Hz / {a0:.4g}   THD: {t:.2f} %")
