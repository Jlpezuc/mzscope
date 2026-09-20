"""Simulador de la placa para probar el scope sin hardware.

Emula el servidor TCP del A53 (puerto 1000): genera 10 000 muestras/s (ISR de 100 us)
para los 20 canales segun la senal seleccionada en cada uno, cicla los datos lentos y
responde a los comandos de la GUI (maquina de estados, botones, send fields).

    python -m mzscope.sim [--port 1000] [--fs 10000] [--drop 0.001] [--header ruta | --old]

``--old`` simula una placa con el software original (mz-sw (old) / JavaScope): usa la copia
``headers/javascope_original.h`` (formato antiguo de gui_button_mapping, 58 datos lentos,
senales Vdc_m, Van_m, ia_m, h1...). El scope tiene que cargar el mismo javascope.h que la
placa (real o simulada): el numero de datos lentos fija el contador de secuencia y, si no
coincide, casi todas las muestras se dan por perdidas.
"""
from __future__ import annotations

import argparse
import math
import socket
import sys
import time

import numpy as np

from . import protocol as P
from .header import BoardNames, default_names, find_header, load_header
from pathlib import Path


class FakeBoard:
    def __init__(self, names: BoardNames, fs: float = 10_000.0, drop_prob: float = 0.0):
        self.names = names
        self.fs = fs
        self.ts_us = 1e6 / fs
        self.drop_prob = drop_prob
        self.n_obs = len(names.observables)
        self.n_slow = len(names.slow_data)
        self.selected = [0] * P.CHANNELS
        self.state = "idle"          # idle / running / control / error
        self.enable_system = False
        self.enable_control = False
        self.user_led = False
        self.button_bits = 0
        self._button_ids = [b.cmd_id for b in names.user_buttons]
        self._field_ids = [f.cmd_id for f in names.send_fields]
        self.send_fields = [0.0] * max(2, len(self._field_ids))
        self.isr = 0
        self.slow_cnt = 0
        self.t0 = time.monotonic()
        self.rng = np.random.default_rng(1)

    # ---- senales sinteticas ----------------------------------------------
    def signal(self, obs_index: int, t: np.ndarray) -> np.ndarray:
        if obs_index <= 0 or obs_index >= self.n_obs:
            return np.zeros_like(t)
        name = self.names.pretty(self.names.observables[obs_index]).lower()
        k = obs_index
        if "lifecheck" in name:
            return (self.isr_range(t) % 1000).astype(np.float32)
        if "exectime" in name:
            return 12.0 + 0.5 * np.sin(2 * np.pi * 3 * t) + self.rng.normal(0, 0.1, t.shape)
        if "period" in name:
            return np.full_like(t, self.ts_us)
        if self.state != "control":
            return np.zeros_like(t) + 0.02 * self.rng.normal(0, 1, t.shape)
        amp = self.send_fields[0] or 1.0
        f = self.send_fields[1] or 50.0
        if "v" in name and "_m" in name:            # tensiones medidas: 50 Hz + armonicos
            ph = 2 * np.pi * (k % 3) / 3
            return amp * 100 * (np.sin(2 * np.pi * f * t + ph) + 0.15 * np.sin(2 * np.pi * 5 * f * t + ph)
                                + 0.08 * np.sin(2 * np.pi * 7 * f * t + ph)) + self.rng.normal(0, 0.5, t.shape)
        if "i" in name and "_m" in name:
            ph = 2 * np.pi * (k % 3) / 3 - 0.5
            return amp * 5 * np.sin(2 * np.pi * f * t + ph) + self.rng.normal(0, 0.05, t.shape)
        if "h" in name and name[1:].isdigit():       # armonicos "estimados"
            return amp * 100 / int(name[1:]) + self.rng.normal(0, 0.2, t.shape)
        if name.startswith("a1_") or name.startswith("a2_"):
            return amp * np.sin(2 * np.pi * f * k / 4 * t) + self.rng.normal(0, 0.01, t.shape)
        # cualquier otra: seno con frecuencia dependiente del indice
        return amp * np.sin(2 * np.pi * (10 + 7 * (k % 9)) * t + k)

    def isr_range(self, t):
        return np.arange(self.isr, self.isr + len(t))

    # ---- comandos ---------------------------------------------------------
    def handle_command(self, cmd_id: int, value: float):
        if cmd_id == P.CMD_NONE:
            return
        if P.CMD_SELECT_CHANNEL_1 <= cmd_id < P.CMD_SELECT_CHANNEL_1 + P.CHANNELS:
            self.selected[cmd_id - P.CMD_SELECT_CHANNEL_1] = int(value)
        elif cmd_id == P.CMD_ENABLE_SYSTEM and self.state == "idle":
            self.state = "running"
        elif cmd_id == P.CMD_ENABLE_CONTROL and self.state == "running":
            self.state = "control"
        elif cmd_id == P.CMD_STOP:
            self.state = "idle"
        elif cmd_id in self._field_ids:
            self.send_fields[self._field_ids.index(cmd_id)] = value
        elif cmd_id in self._button_ids:
            b = self._button_ids.index(cmd_id)
            if b == 0:
                self.state = "error"
            self.button_bits ^= (1 << b)
            self.user_led = bool(self.button_bits & 0b10)
        elif cmd_id == P.CMD_ERROR_RESET and self.state == "error":
            self.state = "idle"

    def status_word(self) -> int:
        t = time.monotonic() - self.t0
        ready = (int(t * 2) % 2 == 0) if self.state == "idle" else (int(t * 8) % 2 == 0)
        s = 0
        if self.state != "error" and ready:
            s |= 1 << P.STATUS_LED_READY
        if self.state == "control":
            s |= 1 << P.STATUS_LED_RUNNING
        if self.state == "error":
            s |= 1 << P.STATUS_LED_ERROR
        if self.user_led:
            s |= 1 << P.STATUS_LED_USER
        s |= self.button_bits << P.STATUS_MY_BUTTON_BASE
        return s

    def slow_value(self, sid: int) -> float:
        name = self.names.slow_data[sid] if sid < self.n_slow else ""
        if "SecondsSinceSystemStart" in name:
            return time.monotonic() - self.t0
        if "ISR_ExecTime" in name:
            return 12.3
        if "ISR_Period" in name:
            return self.ts_us
        if "Milliseconds" in name:
            return (time.monotonic() - self.t0) * 1000
        if "Error_Code" in name:
            return 42.0 if self.state == "error" else 0.0
        if "polePairs" in name:
            return 4.0
        return float(sid) + math.sin(time.monotonic()) * 0.1

    # ---- paquete ----------------------------------------------------------
    def next_packet(self) -> bytes:
        n = P.SAMPLES_PER_PACKET
        samples = np.zeros((n, P.CHANNELS), dtype=np.float32)
        slow_id = np.zeros(n)
        slow_content = np.zeros(n)
        for k in range(n):
            if self.drop_prob and self.rng.random() < self.drop_prob:   # muestra perdida
                self.isr += 1
                self.slow_cnt = (self.slow_cnt + 1) % self.n_slow
            t = np.array([self.isr / self.fs])
            for ch in range(P.CHANNELS):
                samples[k, ch] = self.signal(self.selected[ch], t)[0]
            slow_id[k] = self.slow_cnt
            slow_content[k] = self.slow_value(self.slow_cnt)
            self.slow_cnt = (self.slow_cnt + 1) % self.n_slow
            self.isr += 1
        return P.build_packet(self.status_word(), samples, slow_id, slow_content)


OLD_HEADER = Path(__file__).resolve().parent.parent / "headers" / "javascope_original.h"


def serve(port: int, fs: float, drop: float, header: str | None):
    if sys.platform.startswith("win"):
        # resolucion de 1 ms para time.sleep (por defecto ~15 ms en Windows)
        import ctypes
        ctypes.windll.winmm.timeBeginPeriod(1)
    names = None
    hp = Path(header) if header else find_header(Path(__file__).resolve().parent)
    if hp and hp.is_file():
        names = load_header(hp)
        print(f"sim: nombres de {hp} (formato {names.format}: {len(names.observables) - 1} senales, "
              f"{len(names.slow_data) - 1} datos lentos, {len(names.user_buttons)} botones, {len(names.send_fields)} consignas)")
    else:
        names = default_names()
        print("sim: javascope.h no encontrado, nombres por defecto")
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(1)
    print(f"sim: placa simulada escuchando en puerto {port} (fs = {fs:.0f} Hz)")
    while True:
        conn, addr = srv.accept()
        print(f"sim: cliente {addr}")
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        board = FakeBoard(names, fs=fs, drop_prob=drop)
        t_next = time.monotonic()
        period = P.SAMPLES_PER_PACKET / fs
        try:
            while True:
                conn.sendall(board.next_packet())
                # como la placa real: espera la respuesta del cliente tras cada paquete
                reply = conn.recv(1460)
                if not reply:
                    break
                if len(reply) >= P.COMMAND_SIZE:
                    cmd_id, value = P.COMMAND_STRUCT.unpack(reply[:P.COMMAND_SIZE])
                    board.handle_command(cmd_id, value)
                t_next += period
                delay = t_next - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                elif delay < -1.0:
                    t_next = time.monotonic()
        except OSError:
            pass
        finally:
            conn.close()
            print("sim: cliente desconectado")


def main():
    ap = argparse.ArgumentParser(description="Placa MicroZohm simulada")
    ap.add_argument("--port", type=int, default=P.DEFAULT_PORT)
    ap.add_argument("--fs", type=float, default=10_000.0, help="frecuencia del ISR simulado [Hz]")
    ap.add_argument("--drop", type=float, default=0.0, help="probabilidad de perder una muestra")
    ap.add_argument("--header", default=None, help="ruta a javascope.h (por defecto el del repositorio)")
    ap.add_argument("--old", action="store_true", help=f"placa con el software original: usa {OLD_HEADER}")
    a = ap.parse_args()
    if a.old and a.header:
        ap.error("--old y --header son excluyentes")
    serve(a.port, a.fs, a.drop, str(OLD_HEADER) if a.old else a.header)


if __name__ == "__main__":
    main()
