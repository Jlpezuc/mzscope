"""Ventana principal del scope."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox,
                               QPushButton, QSpinBox, QSplitter, QTabWidget, QToolBar, QVBoxLayout, QWidget)

from .. import protocol as P
from ..buffer import SampleBuffer
from ..client import BoardClient
from ..header import BoardNames, default_names, find_header, load_header
from ..logger import OUTPUT_DIR, CsvRecorder, write_csv
from ..settings import PlotConfig, Settings
from ..theme import apply_theme
from ..trigger import Trigger
from .channel_dialog import ChannelDialog
from .channels import ChannelPanel
from .control import ControlPanel
from .fft import FFT_SIZES, FftPanel
from .plots import PlotStack
from .trigger_panel import TriggerPanel

TIME_WINDOWS = [0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 60]


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.theme = apply_theme(QApplication.instance(), settings.dark_theme)
        self.setWindowTitle("MicroZohm Scope")
        self.resize(1500, 900)

        self.names = self._load_names()
        self.buffer = SampleBuffer(settings.capacity, n_slow=len(self.names.slow_data))
        self.buffer.ts_manual_us = settings.ts_manual_us or None
        self.isr_period_id = self.names.slow_index("JSSD_FLOAT_ISR_Period_us")
        self.client = BoardClient(self)
        self.recorder = CsvRecorder()
        self.trigger = Trigger(self.buffer, settings.trigger)
        self._capture: tuple[np.ndarray, np.ndarray] | None = None   # ventana mostrada en modo disparo
        self._capture_wall = 0.0
        self._capture_is_trig = False
        self.paused = False
        self._single = False
        self._last_status = None
        self._rec_written = 0
        self._lag_since = None
        self._header_mismatch = False
        self._pan_timer = QTimer(self)
        self._pan_timer.setSingleShot(True)
        self._pan_timer.setInterval(40)
        self._pan_timer.timeout.connect(self._draw_visible)

        self._build_ui()
        self._wire()
        self._refresh_structure()
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._refresh)
        self.timer.start()
        if settings.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(settings.window_geometry.encode()))
        # auto-conexion: esperar a la placa desde el arranque
        self._user_stopped = False
        self._closing = False
        if settings.auto_connect:
            QTimer.singleShot(300, self._start_connection)

    # ------------------------------------------------------------------ nombres
    def _load_names(self) -> BoardNames:
        p = Path(self.settings.header_path) if self.settings.header_path else None
        if not p or not p.is_file():
            p = find_header(Path(__file__).resolve().parent)
        if p and p.is_file():
            try:
                names = load_header(p)
                self.settings.header_path = str(p)
                return names
            except ValueError:
                pass
        return default_names()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        # ---- barra de herramientas
        tb = QToolBar("Principal")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(tb)
        self.act_run = QAction("Pausar", self)
        self.act_run.setCheckable(True)
        self.act_run.setShortcut(QKeySequence("Space"))
        self.act_single = QAction("Captura única", self)
        self.act_clear = QAction("Limpiar", self)
        self.act_fft = QAction("FFT", self)
        self.act_fft.setCheckable(True)
        self.act_follow = QAction("Seguir", self)
        self.act_follow.setCheckable(True)
        self.act_follow.setChecked(True)
        self.act_theme = QAction("Tema claro" if self.settings.dark_theme else "Tema oscuro", self)
        self.act_png = QAction("Guardar imagen", self)
        self.act_csv = QAction("Guardar CSV", self)
        self.act_rec = QAction("● Grabar", self)
        self.act_rec.setCheckable(True)
        self.act_rec.setShortcut(QKeySequence("R"))
        self.act_rec.setToolTip("Graba en outputs/mzscope_<fecha>.csv los canales marcados en 'Ver' (con escala y offset) "
                                "y el tiempo desde el inicio de la grabación, hasta que se pulsa otra vez (R)")
        tb.addAction(self.act_run)
        tb.addAction(self.act_single)
        tb.addAction(self.act_clear)
        tb.addSeparator()
        w = QWidget()
        hl = QHBoxLayout(w)
        hl.setContentsMargins(4, 0, 4, 0)
        hl.addWidget(QLabel("Ventana:"))
        self.cmb_window = QComboBox()
        for tw in TIME_WINDOWS:
            self.cmb_window.addItem(f"{tw * 1000:g} ms" if tw < 1 else f"{tw:g} s", tw)
        idx = min(range(len(TIME_WINDOWS)), key=lambda i: abs(TIME_WINDOWS[i] - self.settings.time_window_s))
        self.cmb_window.setCurrentIndex(idx)
        hl.addWidget(self.cmb_window)
        hl.addSpacing(10)
        hl.addWidget(QLabel("Modo:"))
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItem("Continuo", "roll")
        self.cmb_mode.addItem("Disparo", "trigger")
        self.cmb_mode.setToolTip("Continuo: la señal avanza como en un registrador.\n"
                                 "Disparo: la pantalla se congela en cada cruce del nivel (osciloscopio).")
        hl.addWidget(self.cmb_mode)
        tb.addWidget(w)
        tb.addAction(self.act_follow)
        tb.addSeparator()
        tb.addAction(self.act_fft)
        tb.addSeparator()
        tb.addAction(self.act_csv)
        tb.addAction(self.act_rec)
        tb.addAction(self.act_png)
        spacer = QWidget()
        spacer.setSizePolicy(spacer.sizePolicy().horizontalPolicy().Expanding, spacer.sizePolicy().verticalPolicy())
        tb.addWidget(spacer)
        tb.addAction(self.act_theme)

        # ---- panel lateral
        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(420)
        self.tabs.setMaximumWidth(680)
        self.tabs.addTab(self._build_connection_tab(), "Conexión")
        self.channel_panel = ChannelPanel()
        self.tabs.addTab(self.channel_panel, "Canales")
        self.tabs.addTab(self._build_plots_tab(), "Gráficos")
        self.trigger_panel = TriggerPanel(self.settings.trigger)
        self.tabs.addTab(self.trigger_panel, "Disparo")
        self.control_panel = ControlPanel(self.theme)
        self.tabs.addTab(self.control_panel, "Control")

        # ---- graficos + FFT
        self.plot_stack = PlotStack(self.theme)
        self.fft_panel = FftPanel(self.theme)
        self.fft_panel.setVisible(False)
        self.fft_panel.size.setCurrentIndex(FFT_SIZES.index(self.settings.fft_points) if self.settings.fft_points in FFT_SIZES else 3)
        self.fft_panel.log.setChecked(self.settings.fft_log)
        self.center_split = QSplitter(Qt.Vertical)
        self.center_split.addWidget(self.plot_stack)
        self.center_split.addWidget(self.fft_panel)
        self.center_split.setSizes([700, 300])
        self.center_split.setHandleWidth(4)

        main_split = QSplitter(Qt.Horizontal)
        main_split.addWidget(self.tabs)
        main_split.addWidget(self.center_split)
        main_split.setStretchFactor(1, 1)
        main_split.setSizes([500, 1000])
        main_split.setHandleWidth(4)
        self.setCentralWidget(main_split)

        # ---- barra de estado
        self.lbl_conn = QLabel("Desconectado")
        self.lbl_rate = QLabel("")
        self.lbl_ts = QLabel("")
        self.lbl_buf = QLabel("")
        self.lbl_rec = QLabel("")
        self.lbl_trig = QLabel("")
        for l in (self.lbl_conn, self.lbl_rate, self.lbl_ts, self.lbl_buf, self.lbl_trig, self.lbl_rec):
            self.statusBar().addWidget(l, 0)
            self.statusBar().addWidget(QLabel("  "), 0)

    def _build_connection_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        g = QGroupBox("Placa")
        f = QFormLayout(g)
        self.ed_host = QLineEdit(self.settings.host)
        self.sp_port = QSpinBox()
        self.sp_port.setRange(1, 65535)
        self.sp_port.setValue(self.settings.port)
        self.btn_connect = QPushButton("Conectar")
        self.btn_connect.setObjectName("primary")
        self.chk_apply = QCheckBox("Enviar la selección de canales al conectar")
        self.chk_apply.setChecked(self.settings.apply_channels_on_connect)
        self.chk_auto = QCheckBox("Conectar automáticamente cuando la placa esté disponible")
        self.chk_auto.setToolTip("Al arrancar y al perder la conexión, el scope se queda esperando a la placa y se conecta solo")
        self.chk_auto.setChecked(self.settings.auto_connect)
        f.addRow("Dirección IP", self.ed_host)
        f.addRow("Puerto", self.sp_port)
        f.addRow(self.btn_connect)
        f.addRow(self.chk_auto)
        f.addRow(self.chk_apply)
        lay.addWidget(g)

        g2 = QGroupBox("Base de tiempo")
        f2 = QFormLayout(g2)
        self.lbl_ts_auto = QLabel("—")
        self.lbl_ts_auto.setObjectName("value")
        self.sp_ts_manual = QDoubleSpinBox()
        self.sp_ts_manual.setRange(0.0, 1e6)
        self.sp_ts_manual.setDecimals(2)
        self.sp_ts_manual.setSuffix(" µs")
        self.sp_ts_manual.setSpecialValueText("automático (ISR_Period_us)")
        self.sp_ts_manual.setValue(self.settings.ts_manual_us)
        self.sp_ts_manual.setToolTip("0 = usar el periodo del ISR que envía la placa como dato lento")
        f2.addRow("Periodo medido por la placa", self.lbl_ts_auto)
        f2.addRow("Periodo manual", self.sp_ts_manual)
        self.lbl_gaps = QLabel("—")
        self.lbl_gaps.setObjectName("value")
        f2.addRow("Muestras perdidas / repetidas", self.lbl_gaps)
        lay.addWidget(g2)

        g3 = QGroupBox("Buffer")
        f3 = QFormLayout(g3)
        self.sp_capacity = QSpinBox()
        self.sp_capacity.setRange(10_000, 20_000_000)
        self.sp_capacity.setSingleStep(100_000)
        self.sp_capacity.setValue(self.settings.capacity)
        self.sp_capacity.setSuffix(" muestras")
        self.sp_capacity.setGroupSeparatorShown(True)
        f3.addRow("Capacidad", self.sp_capacity)
        self.lbl_capacity = QLabel("")
        self.lbl_capacity.setObjectName("muted")
        f3.addRow(self.lbl_capacity)
        lay.addWidget(g3)

        g4 = QGroupBox("Nombres de las señales (javascope.h)")
        f4 = QVBoxLayout(g4)
        self.ed_header = QLineEdit(self.settings.header_path)
        hb = QHBoxLayout()
        self.btn_header = QPushButton("…")
        self.btn_header.setMaximumWidth(36)
        self.btn_header_reload = QPushButton("Recargar")
        hb.addWidget(self.ed_header, 1)
        hb.addWidget(self.btn_header)
        hb.addWidget(self.btn_header_reload)
        f4.addLayout(hb)
        self.lbl_names = QLabel("")
        self.lbl_names.setObjectName("muted")
        self.lbl_names.setWordWrap(True)
        f4.addWidget(self.lbl_names)
        self.lbl_mismatch = QLabel("⚠ Los datos que llegan no encajan con este javascope.h (el número de datos lentos "
                                   "no coincide): casi todas las muestras se dan por perdidas. Carga el javascope.h "
                                   "de la versión del software que corre en la placa (nuevo: src/app; original: "
                                   "mzscope/headers/javascope_original.h).")
        self.lbl_mismatch.setWordWrap(True)
        self.lbl_mismatch.setStyleSheet("color: #E8657C;")
        self.lbl_mismatch.setVisible(False)
        f4.addWidget(self.lbl_mismatch)
        lay.addWidget(g4)
        lay.addStretch(1)
        return w

    def _build_plots_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        self.plot_list = QListWidget()
        hb = QHBoxLayout()
        self.btn_plot_add = QPushButton("＋ Añadir")
        self.btn_plot_del = QPushButton("− Quitar")
        hb.addWidget(self.btn_plot_add)
        hb.addWidget(self.btn_plot_del)
        lbl = QLabel("Gráficos: cada canal se asigna a un gráfico en la pestaña Canales (\"—\" = canal oculto).")
        lbl.setWordWrap(True)
        lay.addWidget(lbl)
        lay.addWidget(self.plot_list)
        lay.addLayout(hb)
        g = QGroupBox("Gráfico seleccionado")
        f = QFormLayout(g)
        self.ed_plot_title = QLineEdit()
        self.ed_plot_ylabel = QLineEdit()
        self.chk_plot_auto = QCheckBox("Escala Y automática")
        self.sp_plot_ymin = QDoubleSpinBox()
        self.sp_plot_ymax = QDoubleSpinBox()
        for sp in (self.sp_plot_ymin, self.sp_plot_ymax):
            sp.setRange(-1e9, 1e9)
            sp.setDecimals(4)
        self.chk_plot_grid = QCheckBox("Rejilla")
        f.addRow("Título", self.ed_plot_title)
        f.addRow("Etiqueta Y", self.ed_plot_ylabel)
        f.addRow(self.chk_plot_auto)
        f.addRow("Y mín", self.sp_plot_ymin)
        f.addRow("Y máx", self.sp_plot_ymax)
        f.addRow(self.chk_plot_grid)
        self.btn_plot_filter = QPushButton("Filtro y orden de las trazas…")
        self.btn_plot_filter.setToolTip("Qué se dibuja (señal, filtro o ambas), acoplamiento, fundamental, media móvil… "
                                        "y el orden de las trazas de este gráfico")
        f.addRow(self.btn_plot_filter)
        lay.addWidget(g)
        lay.addStretch(1)
        self._plot_edit_block = False
        self._dialog: ChannelDialog | None = None
        return w

    # ------------------------------------------------------------------ senales
    def _wire(self):
        self.btn_connect.clicked.connect(self._toggle_connection)
        self.client.connected.connect(self._on_connected)
        self.client.waiting.connect(self._on_waiting)
        self.client.disconnected.connect(self._on_disconnected)
        self.chk_auto.toggled.connect(self._on_auto_connect_toggled)
        self.client.packet_received.connect(self._on_packet)
        self.client.stats.connect(self._on_stats)
        self.chk_apply.toggled.connect(lambda v: setattr(self.settings, "apply_channels_on_connect", v))
        self.sp_ts_manual.valueChanged.connect(self._on_ts_manual)
        self.sp_capacity.valueChanged.connect(self._on_capacity)
        self.btn_header.clicked.connect(self._pick_header)
        self.btn_header_reload.clicked.connect(self._reload_header)
        self.ed_host.editingFinished.connect(lambda: setattr(self.settings, "host", self.ed_host.text().strip()))

        self.act_run.toggled.connect(self._on_pause)
        self.act_single.triggered.connect(self._on_single)
        self.act_clear.triggered.connect(self._on_clear)
        self.act_fft.toggled.connect(self.fft_panel.setVisible)
        self.act_follow.toggled.connect(lambda v: setattr(self.plot_stack, "follow", v))
        self.act_theme.triggered.connect(self._toggle_theme)
        self.act_png.triggered.connect(self._save_png)
        self.act_csv.triggered.connect(self._save_csv)
        self.act_rec.toggled.connect(self._toggle_record)
        self.cmb_window.currentIndexChanged.connect(self._on_window_changed)
        self.cmb_mode.currentIndexChanged.connect(lambda: self._set_mode(self.cmb_mode.currentData()))
        self.trigger_panel.mode_requested.connect(lambda on: self._set_mode("trigger" if on else "roll"))
        self.trigger_panel.changed.connect(self._on_trigger_changed)
        self.trigger_panel.arm_requested.connect(self._arm_trigger)
        self.trigger_panel.autolevel_requested.connect(self._trigger_autolevel)
        self.plot_stack.level_dragged.connect(self.trigger_panel.set_level)

        self.channel_panel.config_changed.connect(self._refresh_structure)
        self.channel_panel.apply_requested.connect(self._send_channel_selection)
        self.channel_panel.settings_requested.connect(self._open_channel_dialog)
        self.btn_plot_filter.clicked.connect(lambda: self._open_channel_dialog(None, self.plot_list.currentRow()))
        self.control_panel.command.connect(self.client.send_command)

        self.plot_list.currentRowChanged.connect(self._on_plot_selected)
        self.btn_plot_add.clicked.connect(self._on_plot_add)
        self.btn_plot_del.clicked.connect(self._on_plot_del)
        for w in (self.ed_plot_title, self.ed_plot_ylabel):
            w.editingFinished.connect(self._on_plot_edited)
        self.chk_plot_auto.toggled.connect(self._on_plot_edited)
        self.chk_plot_grid.toggled.connect(self._on_plot_edited)
        self.sp_plot_ymin.valueChanged.connect(self._on_plot_edited)
        self.sp_plot_ymax.valueChanged.connect(self._on_plot_edited)

        for p in self.plot_stack.plots:
            p.sigRangeChanged.connect(self._on_user_pan)

    # ------------------------------------------------------------------ estructura
    def _refresh_structure(self):
        s = self.settings
        self.channel_panel.set_model(self.names, s.channels, s.plots)
        self.plot_stack.rebuild(s.plots, s.channels, self.channel_panel.labels())
        for p in self.plot_stack.plots:
            p.getViewBox().sigRangeChangedManually.connect(self._on_user_pan)
        self.fft_panel.set_channels(self.channel_panel.labels())
        self.fft_panel.channel.setCurrentIndex(min(s.fft_channel, P.CHANNELS - 1))
        self.trigger_panel.set_channels(self.channel_panel.labels())
        self._apply_mode()
        self.control_panel.set_names(self.names)
        self.control_panel.set_connected(self.client.isRunning())
        self.lbl_names.setText(f"{len(self.names.observables) - 1} señales, {len(self.names.slow_data) - 1} datos lentos — {self.names.source} (formato {self.names.format})")
        self.ed_header.setText(self.settings.header_path)
        self._refresh_plot_list()
        self._update_capacity_label()
        if self._dialog is not None:
            self._dialog.refresh(self.channel_panel.labels())
        if self.paused and not self.trigger_mode:
            self._draw_visible()          # en pausa nadie mas redibuja

    # ------------------------------------------------------------------ ajustes de canal / filtro
    def _open_channel_dialog(self, slot: int | None, plot_index: int | None = None):
        """Ventana de escala/offset del canal, filtro del grafico y orden de las trazas (una sola a la vez)."""
        if self._dialog is not None:
            self._dialog.close()
        s = self.settings
        dlg = ChannelDialog(slot, s.channels, s.plots, self.channel_panel.labels(), plot_index, self)
        dlg.values_changed.connect(self._on_values_changed)
        dlg.structure_changed.connect(self._refresh_structure)
        dlg.finished.connect(lambda *_: setattr(self, "_dialog", None))
        self._dialog = dlg
        dlg.show()

    def _on_values_changed(self):
        """Escala/offset o parametro del filtro: no hace falta recrear las curvas, solo redibujar."""
        self.channel_panel.refresh_settings_text()
        if self.trigger_mode:
            self._apply_mode()          # la linea del nivel de disparo depende de escala/offset (y redibuja la captura)
        elif self.paused:
            self._draw_visible()

    def _refresh_plot_list(self):
        cur = self.plot_list.currentRow()
        self.plot_list.blockSignals(True)
        self.plot_list.clear()
        for p in self.settings.plots:
            self.plot_list.addItem(p.title)
        self.plot_list.blockSignals(False)
        self.plot_list.setCurrentRow(cur if 0 <= cur < len(self.settings.plots) else 0)

    def _on_plot_selected(self, row: int):
        if not (0 <= row < len(self.settings.plots)):
            return
        p = self.settings.plots[row]
        self._plot_edit_block = True
        self.ed_plot_title.setText(p.title)
        self.ed_plot_ylabel.setText(p.y_label)
        self.chk_plot_auto.setChecked(p.auto_y)
        self.sp_plot_ymin.setValue(p.y_min)
        self.sp_plot_ymax.setValue(p.y_max)
        self.chk_plot_grid.setChecked(p.grid)
        self._plot_edit_block = False

    def _on_plot_edited(self, *_):
        if self._plot_edit_block:
            return
        row = self.plot_list.currentRow()
        if not (0 <= row < len(self.settings.plots)):
            return
        p = self.settings.plots[row]
        title_changed = p.title != self.ed_plot_title.text()
        p.title = self.ed_plot_title.text() or f"Plot {row + 1}"
        p.y_label = self.ed_plot_ylabel.text()
        p.auto_y = self.chk_plot_auto.isChecked()
        p.y_min = self.sp_plot_ymin.value()
        p.y_max = self.sp_plot_ymax.value()
        p.grid = self.chk_plot_grid.isChecked()
        self.sp_plot_ymin.setEnabled(not p.auto_y)
        self.sp_plot_ymax.setEnabled(not p.auto_y)
        if title_changed:
            self._refresh_structure()
        else:
            self.plot_stack.apply_plot_configs()

    def _on_plot_add(self):
        n = len(self.settings.plots) + 1
        self.settings.plots.append(PlotConfig(f"Plot {n}"))
        self._refresh_structure()
        self.plot_list.setCurrentRow(n - 1)

    def _on_plot_del(self):
        row = self.plot_list.currentRow()
        if len(self.settings.plots) <= 1 or not (0 <= row < len(self.settings.plots)):
            return
        del self.settings.plots[row]
        for c in self.settings.channels:
            if c.plot == row:
                c.plot = -1
            elif c.plot > row:
                c.plot -= 1
        self._refresh_structure()

    def _on_user_pan(self, *_):
        # si el usuario mueve/zoomea con el raton dejamos de seguir
        if self.act_follow.isChecked() and not self.paused and not self.trigger_mode:
            self.act_follow.setChecked(False)
        if self.paused and not self.trigger_mode:
            self._pan_timer.start()      # en pausa: redibujar el rango visible con todo el detalle

    def _draw_visible(self):
        """Dibuja solo el rango de tiempo visible (con margen) del buffer."""
        b = self.buffer
        if b.count == 0 or not self.plot_stack.plots:
            return
        (x0, x1), _ = self.plot_stack.plots[0].viewRange()
        margin = 0.25 * (x1 - x0)
        pre, post = self._warmup_seconds()
        t, d = b.range_seconds(x0 - margin - pre, x1 + margin + post)
        follow = self.plot_stack.follow
        self.plot_stack.follow = False
        self.plot_stack.update_data(t, d, self.settings.channels, 0, keep=(x0 - margin, x1 + margin))
        self.plot_stack.follow = follow

    def _warmup_seconds(self) -> tuple[float, float]:
        """Segundos extra (antes, despues) que piden los filtros de los graficos para arrancar fuera de pantalla."""
        pre, post = self.plot_stack.warmup_samples()
        ts = self.buffer.ts_seconds
        return pre * ts, post * ts

    def _trigger_ac(self) -> bool:
        """True si el grafico del canal fuente del disparo esta en acoplamiento AC."""
        c = self.settings.trigger
        src_cfg = self.settings.channels[c.source] if 0 <= c.source < P.CHANNELS else None
        return self.plot_stack.trigger_coupling(src_cfg) == "AC"

    # ------------------------------------------------------------------ modo continuo / disparo
    @property
    def trigger_mode(self) -> bool:
        return self.settings.display_mode == "trigger"

    def _set_mode(self, mode: str):
        if mode == self.settings.display_mode:
            return
        self.settings.display_mode = mode
        self.trigger.arm()
        self._capture = None
        self._apply_mode()
        if mode == "trigger":
            self.plot_stack.clear_data()
        else:
            self.act_follow.setChecked(True)

    def _apply_mode(self):
        """Sincroniza los controles y los marcadores con el modo actual."""
        trig = self.trigger_mode
        c = self.settings.trigger
        self.cmb_mode.blockSignals(True)
        self.cmb_mode.setCurrentIndex(1 if trig else 0)
        self.cmb_mode.blockSignals(False)
        self.trigger_panel.set_mode(trig)
        self.act_follow.setEnabled(not trig)
        if trig and self.act_follow.isChecked():
            self.act_follow.setChecked(False)
        self.act_single.setText("Captura única" if not trig else "Armar (único)")
        src_cfg = self.settings.channels[c.source] if 0 <= c.source < P.CHANNELS else None
        self.plot_stack.set_trigger(trig, c.source, c.level, src_cfg if src_cfg and src_cfg.visible else None)
        if trig:
            self.plot_stack.set_trigger_range(float(self.cmb_window.currentData()), c.pretrigger)
            if self._capture is not None:
                self.plot_stack.update_data(self._capture[0], self._capture[1], self.settings.channels, 0,
                                            keep=self._trigger_keep())
        self._update_trigger_label()

    def _on_trigger_changed(self):
        # cualquier cambio de condicion reinicia la busqueda (y el holdoff)
        self.trigger.arm()
        self._apply_mode()

    def _on_window_changed(self):
        self.settings.time_window_s = self.cmb_window.currentData()
        if self.trigger_mode:
            self.trigger.arm()
            self._apply_mode()

    def _arm_trigger(self):
        self.trigger.arm()
        self._update_trigger_label()

    def _trigger_autolevel(self):
        c = self.settings.trigger
        d, _ = self.buffer.latest(self._window_samples())
        if len(d) == 0:
            return
        y = d[:, c.source] - self.trigger.source_dc(self._window_samples(), self._trigger_ac())
        if np.all(np.isnan(y)):
            return
        lo, hi = float(np.nanmin(y)), float(np.nanmax(y))
        if c.hysteresis == 0.0 and hi > lo:
            self.trigger_panel.sp_hyst.setValue(round(0.05 * (hi - lo), 4))
        self.trigger_panel.set_level(0.5 * (lo + hi))

    def _window_samples(self) -> int:
        return max(2, int(round(float(self.cmb_window.currentData()) / self.buffer.ts_seconds)))

    def _update_trigger_label(self):
        if not self.trigger_mode:
            self.lbl_trig.setText("")
            self.trigger_panel.set_state("modo continuo")
            return
        c = self.settings.trigger
        st = self.trigger.state
        if st == "parado":
            txt = "captura única terminada — pulsa Armar"
        elif st == "adquiriendo":
            txt = "disparado, adquiriendo…"
        elif self._capture is not None and not self._capture_is_trig:
            txt = "sin disparo (auto)"
        else:
            txt = "esperando disparo"
        edge = {"rising": "↗", "falling": "↘", "either": "↕"}[c.edge]
        self.lbl_trig.setText(f"Disparo ch{c.source + 1} {edge} {c.level:g} · {txt} · {self.trigger.captures} capturas")
        self.trigger_panel.set_state(f"{txt} ({self.trigger.captures} capturas)")

    def _trigger_keep(self) -> tuple[float, float]:
        """Rango de tiempo de la ventana de disparo (t = 0 en el disparo), sin las muestras de arranque."""
        window = float(self.cmb_window.currentData())
        pre = self.settings.trigger.pretrigger
        return (-pre * window, (1.0 - pre) * window)

    def _refresh_trigger(self):
        """Ciclo de refresco en modo disparo: busca disparos y muestra la captura."""
        window = float(self.cmb_window.currentData())
        c = self.settings.trigger
        n = self._window_samples()
        pre_n = int(n * c.pretrigger)
        pad_pre, pad_post = self.plot_stack.warmup_samples()
        if self.trigger.poll(n, pre_n, self._trigger_ac(), pad_pre, pad_post):
            self._capture = self.trigger.capture
            self._capture_is_trig = True
            self._capture_wall = time.monotonic()
            self.plot_stack.update_data(self._capture[0], self._capture[1], self.settings.channels, 0,
                                        keep=self._trigger_keep())
            self.plot_stack.set_trigger_range(window, c.pretrigger)
            if c.sweep == "single":
                self.act_run.setChecked(False)
        elif c.sweep == "auto" and self.trigger.enabled and self.trigger.pending is None \
                and time.monotonic() - self._capture_wall > max(2.0 * window, 0.5):
            fr = self.trigger.free_run(n, pre_n, pad_pre)
            if fr is not None:
                self._capture = fr
                self._capture_is_trig = False
                self._capture_wall = time.monotonic()
                self.plot_stack.update_data(fr[0], fr[1], self.settings.channels, 0, keep=self._trigger_keep())
        self._update_trigger_label()

    # ------------------------------------------------------------------ conexion
    def _toggle_connection(self):
        if self.client.isRunning():
            self._user_stopped = True          # parada manual: no reconectar
            self.client.stop_connection()
            self.btn_connect.setEnabled(False)
        else:
            self._start_connection()

    def _start_connection(self):
        """Conecta con la placa; con auto-conexion se queda esperando hasta que responda."""
        if self.client.isRunning():
            return
        self._user_stopped = False
        self.settings.host = self.ed_host.text().strip()
        self.settings.port = self.sp_port.value()
        self.buffer.clear()
        self.plot_stack.clear_data()
        retry = self.chk_auto.isChecked()
        self.btn_connect.setText("Esperando placa… (cancelar)" if retry else "Conectando…")
        self.btn_connect.setEnabled(retry)
        self.client.start_connection(self.settings.host, self.settings.port, retry=retry)

    def _on_auto_connect_toggled(self, on: bool):
        self.settings.auto_connect = on
        if on and not self.client.isRunning():
            self._start_connection()

    def _on_waiting(self, text: str):
        self.lbl_conn.setText(text)
        self.btn_connect.setText("Esperando placa… (cancelar)")
        self.btn_connect.setEnabled(True)

    def _on_connected(self, where: str):
        self.btn_connect.setText("Desconectar")
        self.btn_connect.setObjectName("danger")
        self.btn_connect.style().unpolish(self.btn_connect)
        self.btn_connect.style().polish(self.btn_connect)
        self.btn_connect.setEnabled(True)
        self.lbl_conn.setText(f"Conectado a {where}")
        self.control_panel.set_connected(True)
        if self.chk_apply.isChecked():
            self._send_channel_selection()

    def _on_disconnected(self, reason: str):
        self.client.wait(1000)
        self.btn_connect.setText("Conectar")
        self.btn_connect.setObjectName("primary")
        self.btn_connect.style().unpolish(self.btn_connect)
        self.btn_connect.style().polish(self.btn_connect)
        self.btn_connect.setEnabled(True)
        self.lbl_conn.setText(reason)
        self.lbl_rate.setText("")
        self.lbl_rate.setStyleSheet("")
        self._lag_since = None
        self.control_panel.set_connected(False)
        if self.act_rec.isChecked():
            self.act_rec.setChecked(False)
        # auto-conexion: si la placa se ha ido (no fue una parada manual), volver a esperarla
        if self.chk_auto.isChecked() and not getattr(self, "_user_stopped", False) and not self._closing:
            QTimer.singleShot(1000, self._start_connection)

    def _on_stats(self, pps: float, pending: int, kbs: float):
        sps = pps * P.SAMPLES_PER_PACKET
        expected = 1.0 / self.buffer.ts_seconds
        txt = f"{pps:,.0f} paq/s · {sps:,.0f} muestras/s · {kbs:,.0f} kB/s" + (f" · {pending} cmd en cola" if pending else "")
        # si llegan menos muestras de las que genera el ISR, la cola del A53 crece y lo que se ve es cada vez mas antiguo
        lagging = self.buffer.ts_auto_us is not None and sps < 0.9 * expected
        self._lag_since = (self._lag_since or time.monotonic()) if lagging else None
        if self._lag_since and time.monotonic() - self._lag_since > 2.0:
            txt += f"  ⚠ la placa genera {expected:,.0f} muestras/s: se acumula retraso"
            self.lbl_rate.setStyleSheet("color: #E8657C; font-weight: bold;")
        else:
            self.lbl_rate.setStyleSheet("")
        self.lbl_rate.setText(txt)

    def _send_channel_selection(self):
        for slot, idx in enumerate(self.channel_panel.signal_indices()):
            self.client.send_command(P.CMD_SELECT_CHANNEL_1 + slot, float(idx))

    # ------------------------------------------------------------------ datos
    def _on_packet(self, pkt):
        before = self.buffer.total_written
        self.buffer.add_packet(pkt, self.isr_period_id)
        if self.recorder.active:
            n = self.buffer.total_written - before
            if n > 0:
                d, i = self.buffer.latest(n)
                self.recorder.append(i.astype(np.float64) * self.buffer.ts_seconds, d)
        if self._single and self.buffer.new_samples_since_clear * self.buffer.ts_seconds >= self.cmb_window.currentData():
            self._single = False
            self.act_run.setChecked(True)

    def _refresh(self):
        self.client.poll()
        b = self.buffer
        ts = b.ts_seconds
        self.lbl_ts.setText(f"Ts = {ts * 1e6:.2f} µs ({1 / ts:,.0f} Hz)")
        self.plot_stack.set_ts(ts)
        self.lbl_ts_auto.setText(f"{b.ts_auto_us:.2f} µs" if b.ts_auto_us else "— (sin dato ISR_Period_us)")
        self.lbl_gaps.setText(f"{b.dropped} / {b.duplicates}")
        mismatch = b.header_mismatch()
        if mismatch != self._header_mismatch:
            self._header_mismatch = mismatch
            self.lbl_mismatch.setVisible(mismatch)
            if mismatch:
                self.statusBar().showMessage("El javascope.h cargado no coincide con el software de la placa "
                                             "(contador de datos lentos): carga el de esa versión en Conexión", 15000)
        self.lbl_buf.setText(f"buffer {b.count:,} / {b.capacity:,} ({b.count * ts:,.1f} s)")
        if self.recorder.active:
            secs = self.recorder.rows * ts
            self.lbl_rec.setText(f"● REC {secs:,.1f} s · {self.recorder.rows:,} filas → outputs/{self.recorder.path.name}")
        else:
            self.lbl_rec.setText("")
        if b.status != self._last_status:
            self._last_status = b.status
            self.control_panel.update_status(b.status)
        if self.tabs.currentWidget() is self.control_panel:
            self.control_panel.update_slow(b.slow_values)
        if self.paused or b.count == 0:
            return
        if self.trigger_mode:
            self._refresh_trigger()
        else:
            self._refresh_roll()
        if self.fft_panel.isVisible():
            n = min(self.fft_panel.n_points, b.count)
            dd, _ = b.latest(n)
            slot = self.fft_panel.slot
            self.fft_panel.update_spectrum(dd[:, slot], ts, self.channel_panel.labels()[slot])

    def _refresh_roll(self):
        b = self.buffer
        ts = b.ts_seconds
        window = float(self.cmb_window.currentData())
        pre, post = self._warmup_seconds()
        if self.plot_stack.follow or not self.plot_stack.plots:
            span = max(window * 1.5, window + 0.05)
            t, d = b.latest_seconds(span + pre)
            keep = (t[-1] - span, t[-1]) if len(t) else None
        else:
            # sin seguir: solo lo que se ve (mas un margen), no todo el buffer
            (x0, x1), _ = self.plot_stack.plots[0].viewRange()
            margin = 0.25 * (x1 - x0)
            t, d = b.range_seconds(x0 - margin - pre, x1 + margin + post)
            keep = (x0 - margin, x1 + margin)
        self.plot_stack.update_data(t, d, self.settings.channels, window, keep=keep)

    # ------------------------------------------------------------------ acciones
    def _on_pause(self, paused: bool):
        self.paused = paused
        self.act_run.setText("Continuar" if paused else "Pausar")
        if self.trigger_mode:
            if not paused and self.settings.trigger.sweep == "single" and not self.trigger.enabled:
                self.trigger.arm()      # "Continuar" tras una captura unica = rearmar
            self._update_trigger_label()
            return
        if paused:
            # al pausar se muestra todo el buffer para poder navegar
            t, d = self.buffer.latest_seconds(self.buffer.count * self.buffer.ts_seconds)
            follow = self.plot_stack.follow
            self.plot_stack.follow = False
            self.plot_stack.update_data(t, d, self.settings.channels, 0)
            self.plot_stack.follow = follow
        else:
            self.act_follow.setChecked(True)

    def _on_single(self):
        if self.trigger_mode:
            self.settings.trigger.sweep = "single"
            self.trigger_panel.load()
            self.trigger.arm()
            self.act_run.setChecked(False)
            self._update_trigger_label()
            return
        self.buffer.clear()
        self.plot_stack.clear_data()
        self._single = True
        self.act_run.setChecked(False)

    def _on_clear(self):
        self.buffer.clear()
        self.plot_stack.clear_data()

    def _on_ts_manual(self, v: float):
        self.settings.ts_manual_us = v
        self.buffer.ts_manual_us = v or None

    def _on_capacity(self, v: int):
        self.settings.capacity = int(v)
        self.buffer.set_capacity(int(v))
        self._update_capacity_label()

    def _update_capacity_label(self):
        ts = self.buffer.ts_seconds
        mb = self.settings.capacity * P.CHANNELS * 4 / 1e6
        self.lbl_capacity.setText(f"≈ {self.settings.capacity * ts:,.0f} s con Ts = {ts * 1e6:.0f} µs, {mb:,.0f} MB de RAM")

    def _pick_header(self):
        p, _ = QFileDialog.getOpenFileName(self, "javascope.h", self.ed_header.text() or str(Path.cwd()), "javascope.h (*.h)")
        if p:
            self.ed_header.setText(p)
            self._reload_header()

    def _reload_header(self):
        p = self.ed_header.text().strip()
        try:
            self.names = load_header(p)
            self.settings.header_path = p
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "javascope.h", f"No se pudo leer {p}:\n{exc}")
            return
        self.buffer.n_slow = max(len(self.names.slow_data), 2)
        self.isr_period_id = self.names.slow_index("JSSD_FLOAT_ISR_Period_us")
        self._refresh_structure()

    def _toggle_theme(self):
        self.settings.dark_theme = not self.settings.dark_theme
        self.theme = apply_theme(QApplication.instance(), self.settings.dark_theme)
        self.act_theme.setText("Tema claro" if self.settings.dark_theme else "Tema oscuro")
        self.plot_stack.apply_theme(self.theme)
        self.fft_panel.apply_theme(self.theme)
        self.control_panel.apply_theme(self.theme)
        self.control_panel.update_status(self.buffer.status)
        self._refresh_structure()

    # ------------------------------------------------------------------ exportar
    def _visible_channels(self) -> tuple[list[int], list[str]]:
        """Canales asignados a un grafico (para la exportacion del buffer)."""
        labels = self.channel_panel.labels()
        cols = [i for i, c in enumerate(self.settings.channels) if c.plot >= 0]
        return cols, [f"ch{i + 1}_{labels[i]}" for i in cols]

    def _checked_channels(self) -> tuple[list[int], list[str]]:
        """Canales con el check 'Ver' marcado (los que se graban)."""
        labels = self.channel_panel.labels()
        cols = [i for i, c in enumerate(self.settings.channels) if c.visible]
        return cols, [f"ch{i + 1}_{labels[i]}" for i in cols]

    def _calibration(self) -> tuple[list[float], list[float]]:
        return ([c.scale for c in self.settings.channels], [c.offset for c in self.settings.channels])

    def _save_csv(self):
        if self.buffer.count == 0:
            QMessageBox.information(self, "CSV", "No hay datos en el buffer.")
            return
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        path, _ = QFileDialog.getSaveFileName(self, "Guardar CSV", f"mzscope_{stamp}.csv", "CSV (*.csv)")
        if not path:
            return
        cols, names = self._visible_channels()
        if not cols:
            cols = list(range(P.CHANNELS))
            names = [f"ch{i + 1}" for i in cols]
        ans = QMessageBox.question(self, "CSV", "¿Guardar solo la ventana visible?\n(No = todo el buffer)",
                                   QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        if ans == QMessageBox.Cancel:
            return
        if ans == QMessageBox.Yes and self.trigger_mode and self._capture is not None:
            t, d = self._capture                      # ventana capturada, t = 0 en el disparo
            (x0, x1), _ = self.plot_stack.plots[0].viewRange()
            m = (t >= x0) & (t <= x1)                 # sin las muestras extra de arranque de los filtros
            t, d = t[m], d[m]
        elif ans == QMessageBox.Yes:
            (x0, x1), _ = self.plot_stack.plots[0].viewRange()
            t, d = self.buffer.latest_seconds(self.buffer.count * self.buffer.ts_seconds)
            m = (t >= x0) & (t <= x1)
            t, d = t[m], d[m]
        else:
            t, d = self.buffer.latest_seconds(self.buffer.count * self.buffer.ts_seconds)
        scales, offsets = self._calibration()
        write_csv(path, t, d, cols, names, scales, offsets)
        self.statusBar().showMessage(f"CSV guardado: {path} ({len(t):,} filas)", 5000)

    def _toggle_record(self, on: bool):
        if on:
            cols, names = self._checked_channels()
            if not cols:
                QMessageBox.information(self, "Grabar", "No hay ningún canal marcado en 'Ver' (pestaña Canales).")
                self.act_rec.setChecked(False)
                return
            if not self.client.isRunning():
                QMessageBox.information(self, "Grabar", "No hay conexión con la placa.")
                self.act_rec.setChecked(False)
                return
            scales, offsets = self._calibration()
            try:
                self.recorder.start(cols, names, scales, offsets)
            except OSError as exc:
                QMessageBox.warning(self, "Grabar", f"No se pudo crear el fichero en {OUTPUT_DIR}:\n{exc}")
                self.act_rec.setChecked(False)
                return
            self.act_rec.setText("■ Detener")
            self.lbl_rec.setStyleSheet("color: #E8657C; font-weight: bold;")
        else:
            path, rows = self.recorder.path, self.recorder.rows
            self.recorder.stop()
            self.act_rec.setText("● Grabar")
            self.lbl_rec.setStyleSheet("")
            if self.recorder.error:
                QMessageBox.warning(self, "Grabar", f"Error al escribir {path}:\n{self.recorder.error}")
            elif path is not None:
                self.statusBar().showMessage(f"Grabación guardada: {path} ({rows:,} filas)", 8000)

    def _save_png(self):
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        path, _ = QFileDialog.getSaveFileName(self, "Guardar imagen", f"mzscope_{stamp}.png", "PNG (*.png)")
        if not path:
            return
        target = self.center_split if self.fft_panel.isVisible() else self.plot_stack
        target.grab().save(path)
        self.statusBar().showMessage(f"Imagen guardada: {path}", 5000)

    # ------------------------------------------------------------------ cierre
    def closeEvent(self, ev):
        self._closing = True
        self.settings.fft_points = self.fft_panel.n_points
        self.settings.fft_log = self.fft_panel.log.isChecked()
        self.settings.fft_channel = self.fft_panel.slot
        self.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.settings.save()
        self.recorder.stop()
        if self.client.isRunning():
            self.client.stop_connection()
            self.client.wait(2000)
        super().closeEvent(ev)
