"""Cliente TCP de la placa (proceso propio).

La placa usa "stop-and-wait": tras cada paquete de 1324 bytes espera 8 bytes de
respuesta antes de enviar el siguiente. Si el PC tarda en contestar, la placa no se
retrasa en su ISR: acumula muestras en la cola del A53 (hasta 10^6) y el scope va
mostrando datos cada vez mas antiguos. Por eso la red va en un **proceso** aparte
(sin competir por el GIL con el dibujado) y la respuesta se envia antes de tocar el
paquete. Los paquetes se pasan a la GUI en bloques por una cola; ``poll()`` los
reparte con las mismas senales que antes.
"""
from __future__ import annotations

import collections
import multiprocessing as mp
import queue
import socket
import time

from PySide6.QtCore import QObject, Signal

from . import protocol as P


def _net_worker(host: str, port: int, rx: mp.Queue, tx: mp.Queue, stop, retry: bool) -> None:
    """Bucle de red (proceso hijo). Mensajes a la GUI: (kind, ...).

    Con retry=True se queda intentando conectar (cada segundo) hasta que la placa responde o
    la GUI lo para; con retry=False un fallo de conexion termina el proceso.
    """
    attempt = 0
    while True:
        try:
            sock = socket.create_connection((host, port), timeout=2.0)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(1.0)
            break
        except OSError as exc:
            if not retry:
                rx.put(("disconnected", f"No se pudo conectar con {host}:{port} ({exc})"))
                return
            attempt += 1
            rx.put(("waiting", f"Esperando a la placa en {host}:{port} (intento {attempt})"))
            if stop.wait(1.0):
                rx.put(("disconnected", "Espera cancelada"))
                return
    rx.put(("connected", f"{host}:{port}"))
    buf = bytearray()
    batch: list[bytes] = []
    commands: collections.deque[tuple[int, float]] = collections.deque()
    n_packets = n_bytes = 0
    t_flush = t_stat = time.monotonic()
    reason = "Desconectado"
    try:
        while not stop.is_set():
            try:
                while True:
                    commands.append(tx.get_nowait())
            except queue.Empty:
                pass
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                reason = "La placa cerro la conexion"
                break
            buf += chunk
            n_bytes += len(chunk)
            while len(buf) >= P.PACKET_SIZE:
                raw = bytes(buf[:P.PACKET_SIZE])
                del buf[:P.PACKET_SIZE]
                # responder primero: es lo unico que la placa espera para seguir
                cmd = commands.popleft() if commands else (P.CMD_NONE, 0.0)
                sock.sendall(P.build_command(*cmd))
                batch.append(raw)
                n_packets += 1
            now = time.monotonic()
            if batch and (now - t_flush >= 0.01 or len(batch) >= 64):
                rx.put(("data", b"".join(batch)))
                batch = []
                t_flush = now
            if now - t_stat >= 0.5:
                dt = now - t_stat
                rx.put(("stats", n_packets / dt, len(commands), n_bytes / dt / 1024.0))
                n_packets = n_bytes = 0
                t_stat = now
    except OSError as exc:
        reason = f"Error de red: {exc}"
    finally:
        try:
            sock.close()
        except OSError:
            pass
        if batch:
            rx.put(("data", b"".join(batch)))
        rx.put(("disconnected", reason))


class BoardClient(QObject):
    packet_received = Signal(object)        # protocol.Packet
    connected = Signal(str)
    waiting = Signal(str)                   # reintentando la conexion (auto)
    disconnected = Signal(str)              # motivo
    stats = Signal(float, int, float)       # paquetes/s, cola de comandos, kB/s

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.host = P.DEFAULT_HOST
        self.port = P.DEFAULT_PORT
        self._ctx = mp.get_context("spawn")
        self._proc: mp.Process | None = None
        self._rx = None
        self._tx = None
        self._stop = None

    # ---- API para la GUI --------------------------------------------------
    def isRunning(self) -> bool:
        return self._proc is not None and self._proc.is_alive()

    def start_connection(self, host: str, port: int, retry: bool = False):
        """Arranca el proceso de red. retry=True: espera a la placa en vez de fallar al primer intento."""
        if self.isRunning():
            return
        self.host, self.port = host, int(port)
        self._rx = self._ctx.Queue()
        self._tx = self._ctx.Queue()
        self._stop = self._ctx.Event()
        self._proc = self._ctx.Process(target=_net_worker, name="mzscope-net",
                                       args=(self.host, self.port, self._rx, self._tx, self._stop, retry), daemon=True)
        self._proc.start()

    def stop_connection(self):
        if self._stop is not None:
            self._stop.set()

    def wait(self, ms: int = 1000):
        if self._proc is not None:
            self._proc.join(ms / 1000.0)

    def send_command(self, cmd_id: int, value: float = 0.0):
        """Encola un comando; se envia en la respuesta al siguiente paquete."""
        if self._tx is not None:
            self._tx.put((int(cmd_id), float(value)))

    def poll(self):
        """Reparte lo recibido del proceso de red. Llamar periodicamente desde la GUI."""
        if self._rx is None:
            return
        while True:
            try:
                msg = self._rx.get_nowait()
            except queue.Empty:
                break
            kind = msg[0]
            if kind == "data":
                raw = msg[1]
                for k in range(0, len(raw), P.PACKET_SIZE):
                    self.packet_received.emit(P.parse_packet(raw[k:k + P.PACKET_SIZE]))
            elif kind == "stats":
                self.stats.emit(msg[1], msg[2], msg[3])
            elif kind == "connected":
                self.connected.emit(msg[1])
            elif kind == "waiting":
                self.waiting.emit(msg[1])
            elif kind == "disconnected":
                if self._proc is not None:
                    self._proc.join(1.0)
                self._proc = None
                self._rx = self._tx = self._stop = None
                self.disconnected.emit(msg[1])
                break
