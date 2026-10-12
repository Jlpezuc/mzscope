"""Exportacion a CSV y grabacion continua en un hilo aparte.

* ``write_csv``: volcado de un bloque (buffer o ventana) a un fichero.
* ``CsvRecorder``: grabacion desde que se pulsa *Grabar* hasta que se detiene. La GUI solo
  encola bloques de muestras (copias numpy); un hilo de fondo los formatea y escribe, asi el
  hilo principal y la red no se detienen aunque el disco vaya lento. Los ficheros van a
  ``OUTPUT_DIR`` (``mzscope/outputs``) con nombre ``mzscope_<fecha>_<hora>.csv``.

Formato: separador ``;``, primera columna ``time_s`` y una columna por canal grabado con el
nombre ``chN_<senal>``. Los valores llevan aplicados la escala y el offset del canal (los
mismos que se ven en el grafico). Las muestras perdidas (huecos) salen como celdas vacias.

Base de tiempo de la grabacion: la placa no envia marcas de tiempo, cada fila es una
interrupcion. ``time_s = (indice_ISR - indice_ISR_de_la_primera_fila) * Ts`` con un ``Ts``
**fijo durante toda la grabacion** (el que habia al pulsar Grabar), de modo que el tiempo es
estrictamente creciente con paso constante y una fila vacia ocupa exactamente un paso. Junto al
CSV se escribe ``<nombre>.json`` con Ts, su origen, filas, muestras perdidas y canales.
"""
from __future__ import annotations

import csv
import io
import json
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
        self.i0: int | None = None         # indice ISR de la primera muestra grabada (time_s empieza en 0)
        self.ts: float = 1e-4              # periodo de muestreo fijo de la grabacion [s]
        self.meta: dict = {}
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
              ts: float, path: str | Path | None = None, meta: dict | None = None):
        """``ts``: periodo de muestreo [s] que se usa para toda la grabacion. ``meta``: datos que
        se anaden al ``.json`` de la grabacion."""
        self.stop()
        self.path = Path(path) if path else new_output_path()
        self.columns = list(columns)
        self.scales = list(scales)
        self.offsets = list(offsets)
        self.i0 = None
        self.ts = float(ts)
        self.error = None
        self.meta = {
            "csv": self.path.name,
            "started": time.strftime("%Y-%m-%d %H:%M:%S"),
            "time_s": "(indice_ISR - indice_ISR_inicial) * Ts, Ts fijo durante la grabacion; "
                      "las filas vacias son muestras perdidas y ocupan un paso",
            "Ts_us": self.ts * 1e6,
            "f_sample_Hz": 1.0 / self.ts,
            "columns": [{"name": n, "slot": c + 1, "scale": self.scales[c], "offset": self.offsets[c]}
                        for n, c in zip(names, self.columns)],
            **(meta or {}),
        }
        self._write_meta()
        with self._lock:
            self._rows = 0
        self._queue = queue.Queue()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(self.path, ["time_s"] + list(names)),
                                        name="mzscope-csv", daemon=True)
        self._thread.start()

    def append(self, index: np.ndarray, data: np.ndarray):
        """Encola un bloque (indices ISR (n,), datos (n, 20)); no bloquea."""
        if not self.active or len(index) == 0:
            return
        if self.i0 is None:
            self.i0 = int(index[0])
        t = (np.asarray(index, dtype=np.int64) - self.i0).astype(np.float64) * self.ts
        vals = apply_calibration(data, self.columns, self.scales, self.offsets)
        self._queue.put((t, vals))

    def stop(self, **meta):
        """Termina la grabacion; ``meta`` se anade al ``.json`` (p. ej. muestras perdidas)."""
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=10.0)
        self._thread = None
        self.meta.update(stopped=time.strftime("%Y-%m-%d %H:%M:%S"), rows=self.rows,
                         duration_s=self.rows * self.ts, **meta)
        self._write_meta()

    @property
    def meta_path(self) -> Path | None:
        return self.path.with_suffix(".json") if self.path else None

    def _write_meta(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.meta_path.write_text(json.dumps(self.meta, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            self.error = str(exc)

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
        # tiempo con 10 cifras significativas: a 10 kHz el paso de 0,1 ms se resuelve hasta ~10^5 s
        writer.writerows(
            [f"{tt:.10g}"] + ["" if np.isnan(v) else f"{v:.9g}" for v in row] for tt, row in zip(t, vals)
        )
        with self._lock:
            self._rows += len(t)
