"""Panel de control: maquina de estados, LEDs, botones de usuario, send/receive fields, datos lentos."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import protocol as P
from ..header import BoardNames


class Led(QWidget):
    def __init__(self, label: str, color: str, theme: dict):
        super().__init__()
        self.color = color
        self.theme = theme
        self.dot = QLabel()
        self.dot.setFixedSize(14, 14)
        self.text = QLabel(label)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self.dot)
        lay.addWidget(self.text)
        self.set_on(False)

    def set_on(self, on: bool):
        c = self.color if on else self.theme["led_off"]
        glow = f"0 0 6px {self.color}" if on else "none"
        self.dot.setStyleSheet(f"background: {c}; border-radius: 7px; border: 1px solid rgba(0,0,0,60);")

    def apply_theme(self, theme):
        self.theme = theme


class ControlPanel(QWidget):
    command = Signal(int, float)   # id, valor

    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.names = BoardNames()
        self._slow_rows: dict[int, int] = {}
        self._rcv_slow_ids: list[int] = []
        self._error_slow_id = -1

        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(8)

        # --- estado del sistema
        g = QGroupBox("Sistema")
        gl = QGridLayout(g)
        self.btn_enable_sys = QPushButton("Enable System")
        self.btn_enable_ctl = QPushButton("Enable Control")
        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setObjectName("danger")
        self.btn_err_reset = QPushButton("Error Reset")
        self.btn_enable_sys.clicked.connect(lambda: self.command.emit(P.CMD_ENABLE_SYSTEM, 0.0))
        self.btn_enable_ctl.clicked.connect(lambda: self.command.emit(P.CMD_ENABLE_CONTROL, 0.0))
        self.btn_stop.clicked.connect(lambda: self.command.emit(P.CMD_STOP, 0.0))
        self.btn_err_reset.clicked.connect(lambda: self.command.emit(P.CMD_ERROR_RESET, 0.0))
        gl.addWidget(self.btn_enable_sys, 0, 0)
        gl.addWidget(self.btn_enable_ctl, 0, 1)
        gl.addWidget(self.btn_stop, 1, 0)
        gl.addWidget(self.btn_err_reset, 1, 1)
        leds = QHBoxLayout()
        self.led_ready = Led("Ready", "#5BC77E", theme)
        self.led_running = Led("Running", "#4C9BE8", theme)
        self.led_error = Led("Error", "#E8657C", theme)
        self.led_user = Led("User", "#F2CF4A", theme)
        for l in (self.led_ready, self.led_running, self.led_error, self.led_user):
            leds.addWidget(l)
        leds.addStretch(1)
        gl.addLayout(leds, 2, 0, 1, 2)
        self.lbl_state = QLabel("—")
        self.lbl_state.setObjectName("value")
        gl.addWidget(self.lbl_state, 3, 0, 1, 2)
        lay.addWidget(g)

        # --- botones de usuario
        self.g_buttons = QGroupBox("Botones de usuario")
        self.buttons_layout = QGridLayout(self.g_buttons)
        self.user_buttons: list[QPushButton] = []
        self.user_leds: list[Led] = []
        lay.addWidget(self.g_buttons)

        # --- send fields
        self.g_send = QGroupBox("Consignas (send fields)")
        self.send_layout = QGridLayout(self.g_send)
        self.send_edits: list[QLineEdit] = []
        lay.addWidget(self.g_send)

        # --- receive fields
        self.g_rcv = QGroupBox("Lecturas (receive fields)")
        self.rcv_layout = QFormLayout(self.g_rcv)
        self.rcv_values: list[QLabel] = []
        lay.addWidget(self.g_rcv)

        # --- error code + slow data
        self.lbl_error = QLabel("Error code: —")
        self.lbl_error.setObjectName("value")
        lay.addWidget(self.lbl_error)
        g5 = QGroupBox("Datos lentos")
        g5l = QVBoxLayout(g5)
        self.slow_table = QTableWidget(0, 2)
        self.slow_table.setHorizontalHeaderLabels(["Nombre", "Valor"])
        self.slow_table.verticalHeader().setVisible(False)
        self.slow_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.slow_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.slow_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.slow_table.setAlternatingRowColors(True)
        self.slow_table.setMinimumHeight(240)
        g5l.addWidget(self.slow_table)
        lay.addWidget(g5, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # ---- modelo -----------------------------------------------------------
    def set_names(self, names: BoardNames):
        self.names = names
        # botones
        for w in self.user_buttons + self.user_leds:
            w.setParent(None)
        self.user_buttons.clear()
        self.user_leds.clear()
        for i, item in enumerate(names.user_buttons):
            b = QPushButton(item.label)
            b.setToolTip(f"{item.name} (id {item.cmd_id})")
            b.clicked.connect(lambda _c, cid=item.cmd_id: self.command.emit(cid, 0.0))
            led = Led("", "#4C9BE8", self.theme)
            led.text.hide()
            self.buttons_layout.addWidget(b, i // 2, (i % 2) * 2)
            self.buttons_layout.addWidget(led, i // 2, (i % 2) * 2 + 1)
            self.user_buttons.append(b)
            self.user_leds.append(led)
        self.g_buttons.setVisible(bool(names.user_buttons))
        # send fields
        while self.send_layout.count():
            item = self.send_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.send_edits.clear()
        for i, item in enumerate(names.send_fields):
            e = QLineEdit("0")
            e.setAlignment(Qt.AlignRight)
            e.setMaximumWidth(110)
            e.setToolTip(f"{item.name} (id {item.cmd_id})")
            btn = QPushButton("set")
            btn.setMaximumWidth(44)
            def send(_c=None, cid=item.cmd_id, edit=e):
                try:
                    v = float(edit.text().replace(",", "."))
                except ValueError:
                    edit.setStyleSheet("border: 1px solid #E8657C;")
                    return
                edit.setStyleSheet("")
                self.command.emit(cid, v)
            btn.clicked.connect(send)
            e.returnPressed.connect(send)
            self.send_layout.addWidget(QLabel(item.label), i, 0)
            self.send_layout.addWidget(e, i, 1)
            self.send_layout.addWidget(QLabel(item.unit), i, 2)
            self.send_layout.addWidget(btn, i, 3)
            self.send_edits.append(e)
        self.g_send.setVisible(bool(names.send_fields))
        # receive fields (datos lentos elegidos en javascope.h)
        while self.rcv_layout.count():
            item = self.rcv_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.rcv_values.clear()
        self._rcv_slow_ids = []
        for item in names.receive_fields:
            self._rcv_slow_ids.append(item.cmd_id)
            v = QLabel("—")
            v.setObjectName("value")
            v.setToolTip(item.name)
            self.rcv_layout.addRow(f"{item.label} [{item.unit}]" if item.unit else item.label, v)
            self.rcv_values.append(v)
        self.g_rcv.setVisible(bool(names.receive_fields))
        self._error_slow_id = names.slow_index("JSSD_FLOAT_Error_Code")
        self.lbl_error.setVisible(self._error_slow_id >= 0)
        # tabla de datos lentos
        self.slow_table.setRowCount(0)
        self._slow_rows.clear()
        for sid, n in enumerate(names.slow_data):
            if sid == 0:
                continue
            r = self.slow_table.rowCount()
            self.slow_table.insertRow(r)
            self.slow_table.setItem(r, 0, QTableWidgetItem(names.pretty(n)))
            it = QTableWidgetItem("—")
            it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.slow_table.setItem(r, 1, it)
            self._slow_rows[sid] = r

    # ---- actualizacion ----------------------------------------------------
    def update_status(self, status: int):
        self.led_ready.set_on(bool(status & (1 << P.STATUS_LED_READY)))
        self.led_running.set_on(bool(status & (1 << P.STATUS_LED_RUNNING)))
        self.led_error.set_on(bool(status & (1 << P.STATUS_LED_ERROR)))
        self.led_user.set_on(bool(status & (1 << P.STATUS_LED_USER)))
        for i, led in enumerate(self.user_leds):
            led.set_on(bool(status & (1 << (P.STATUS_MY_BUTTON_BASE + i))))
        if status & (1 << P.STATUS_LED_ERROR):
            s = "ERROR"
        elif status & (1 << P.STATUS_LED_RUNNING):
            s = "CONTROL"
        else:
            s = "—"
        self.lbl_state.setText(f"Estado: {s}   status = 0x{status:04X}")

    def update_slow(self, values: dict[int, float]):
        for sid, v in values.items():
            r = self._slow_rows.get(sid)
            if r is not None:
                self.slow_table.item(r, 1).setText(f"{v:.6g}")
        for i, sid in enumerate(self._rcv_slow_ids):
            if sid in values:
                self.rcv_values[i].setText(f"{values[sid]:.6g}")
        if self._error_slow_id in values:
            self.lbl_error.setText(f"Error code: {values[self._error_slow_id]:.0f}")

    def set_connected(self, on: bool):
        for w in (self.btn_enable_sys, self.btn_enable_ctl, self.btn_stop, self.btn_err_reset, *self.user_buttons):
            w.setEnabled(on)
        if not on:
            self.update_status(0)

    def apply_theme(self, theme: dict):
        self.theme = theme
        for l in (self.led_ready, self.led_running, self.led_error, self.led_user, *self.user_leds):
            l.apply_theme(theme)
            l.set_on(False)
