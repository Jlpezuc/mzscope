"""Exportacion a CSV y grabacion continua en un hilo aparte.

* ``write_csv``: volcado de un bloque (buffer o ventana) a un fichero.
* ``CsvRecorder``: grabacion desde que se pulsa *Grabar* hasta que se detiene. La GUI solo
  encola bloques de muestras (copias numpy); un hilo de fondo los formatea y escribe, asi el
  hilo principal y la red no se detienen aunque el disco vaya lento. Los ficheros van a
  ``OUTPUT_DIR`` (``mzscope/outputs``) con nombre ``mzscope_<fecha>_<hora>.csv``.

Formato: separador ``;``, primera columna ``time_s`` y una columna por canal grabado con el
nombre ``chN_<senal>``. Los valores llevan aplicados la escala y el offset del canal (los
mismos que se ven en el grafico). Las muestras perdidas (huecos) salen como celdas vacias.
"""
from __future__ import annotations

import csv
import io
import queue
import threading
import time
from pathlib import Path

import numpy as np

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "outputs"


def apply_calibration(data: np.ndarray, columns: list[int], scales: list[float], offsets: list[float]) -> np.ndarray:
    """Devuelve (n, len(columns)) float64 con y = escala * x + offset por columna."""
    out = data[:, columns].astype(np.float64)
    for k, c in enumerate(columns):
        if scales[c] != 1.0 or offsets[c] != 0.0:
            out[:, k] = out[:, k] * scales[c] + offsets[c]
    return out


def write_csv(path: str | Path, t: np.ndarray, data: np.ndarray, columns: list[int], names: list[str],
              scales: list[float] | None = None, offsets: list[float] | None = None, decimate: int = 1):
    """Guarda ``t`` y las columnas ``columns`` de ``data`` (n, 20) con cabecera ``names``."""
    d = max(int(decimate), 1)
    if scales is None:
        vals = data[::d, columns].astype(np.float64)
    else:
        vals = apply_calibration(data[::d], columns, scales, offsets)
    block = np.column_stack([t[::d]] + [vals[:, k] for k in range(len(columns))])
    np.savetxt(path, block, delimiter=";", fmt="%.9g", header=";".join(["time_s"] + names), comments="")


def new_output_path(prefix: str = "mzscope") -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / f"{prefix}_{time.strftime('%Y-%m-%d_%H-%M-%S')}.csv"


class CsvRecorder:
    """Grabacion continua en un hilo de fondo (ver cabecera del modulo)."""

    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._rows = 0
        self._lock = threading.Lock()
        self.columns: list[int] = []
        self.scales: list[float] = []
        self.offsets: list[float] = []
        self.path: Path | None = None
        self.t0: float | None = None       # tiempo de la primera muestra grabada (time_s empieza en 0)
        self.error: str | None = None

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def rows(self) -> int:
        with self._lock:
            return self._rows

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    # ---- control --------------------------------------------------------------
    def start(self, columns: list[int], names: list[str], scales: list[float], offsets: list[float],
              path: str | Path | None = None):
        self.stop()
        self.path = Path(path) if path else new_output_path()
        self.columns = list(columns)
        self.scales = list(scales)
        self.offsets = list(offsets)
        self.t0 = None
        self.error = None
        with self._lock:
            self._rows = 0
        self._queue = queue.Queue()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(self.path, ["time_s"] + list(names)),
                                        name="mzscope-csv", daemon=True)
        self._thread.start()

    def append(self, t: np.ndarray, data: np.ndarray):
        """Encola un bloque (t en s, datos (n, 20)); no bloquea."""
        if not self.active or len(t) == 0:
            return
        if self.t0 is None:
            self.t0 = float(t[0])
        vals = apply_calibration(data, self.columns, self.scales, self.offsets)
        self._queue.put((np.asarray(t, dtype=np.float64) - self.t0, vals))

    def stop(self):
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=10.0)
        self._thread = None

    # ---- hilo -----------------------------------------------------------------
    def _run(self, path: Path, header: list[str]):
        try:
            with open(path, "w", newline="", encoding="utf-8", buffering=1 << 16) as fh:
                writer = csv.writer(fh, delimiter=";")
                writer.writerow(header)
                while True:
                    try:
                        t, vals = self._queue.get(timeout=0.2)
                    except queue.Empty:
                        if self._stop.is_set():
                            break
                        continue
                    self._write_block(writer, t, vals)
                    if self._stop.is_set() and self._queue.empty():
                        break
        except OSError as exc:
            self.error = str(exc)

    def _write_block(self, writer, t: np.ndarray, vals: np.ndarray):
        block = np.column_stack([t, vals])
        writer.writerows(
            ["" if np.isnan(v) else f"{v:.9g}" for v in row] for row in block
        )
        with self._lock:
            self._rows += len(t)
