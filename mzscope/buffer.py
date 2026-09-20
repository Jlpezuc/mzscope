"""Buffer circular de muestras con base de tiempo correcta.

* Cada muestra corresponde a una llamada del ISR del R5. El periodo real del ISR
  (``ISR_Period_us``) lo mide la propia placa y llega como dato lento; se usa como
  base de tiempo (o un valor manual).
* ``slowDataID`` avanza 1 por ISR (modulo el numero de datos lentos), de modo que
  sirve como contador de secuencia: si entre dos muestras consecutivas el salto no
  es 1 se han perdido muestras (cola llena en el A53) y se avanza el indice de
  tiempo, rellenando con NaN si el hueco es pequeno. Un salto 0 es una muestra
  repetida (la placa reenvia la ultima cuando el R5 esta parado) y se descarta.
"""
from __future__ import annotations

import numpy as np

from . import protocol as P


class SampleBuffer:
    MAX_GAP_FILL = 200   # huecos mayores no se rellenan con NaN (solo se salta el tiempo)

    def __init__(self, capacity: int = 600_000, n_slow: int = 58):
        self.capacity = int(capacity)
        self.n_slow = max(int(n_slow), 2)
        self.data = np.full((self.capacity, P.CHANNELS), np.nan, dtype=np.float32)
        self.index = np.zeros(self.capacity, dtype=np.int64)   # numero de ISR de cada muestra
        self.write = 0            # posicion de escritura
        self.count = 0            # muestras validas
        self.total_written = 0    # muestras escritas desde el inicio (incluye NaN de huecos)
        self.next_isr = 0         # indice ISR esperado de la siguiente muestra
        self.last_slow_id = None
        self.dropped = 0
        self.duplicates = 0
        self.max_slow_id = -1     # mayor slowDataID recibido (para detectar un javascope.h que no coincide)
        self.slow_values: dict[int, float] = {}
        self.status = 0
        # base de tiempo
        self.ts_auto_us: float | None = None
        self.ts_manual_us: float | None = None
        self.new_samples_since_clear = 0

    # ---- configuracion ---------------------------------------------------
    def set_capacity(self, capacity: int):
        capacity = int(capacity)
        if capacity == self.capacity:
            return
        d, i = self.latest(self.count)
        self.capacity = capacity
        self.data = np.full((capacity, P.CHANNELS), np.nan, dtype=np.float32)
        self.index = np.zeros(capacity, dtype=np.int64)
        n = min(len(i), capacity)
        self.data[:n] = d[-n:]
        self.index[:n] = i[-n:]
        self.write = n % capacity
        self.count = n

    def clear(self):
        self.write = 0
        self.count = 0
        self.total_written = 0
        self.next_isr = 0
        self.last_slow_id = None
        self.dropped = 0
        self.duplicates = 0
        self.max_slow_id = -1
        self.new_samples_since_clear = 0

    def header_mismatch(self) -> bool:
        """True si los slowDataID recibidos no encajan con el numero de datos lentos del javascope.h
        cargado: llegan ids fuera de rango, o tras muchas muestras nunca aparece el ultimo."""
        if self.max_slow_id >= self.n_slow:
            return True
        return self.count > 20 * self.n_slow and 0 <= self.max_slow_id < self.n_slow - 1

    @property
    def ts_seconds(self) -> float:
        us = self.ts_manual_us if self.ts_manual_us else self.ts_auto_us
        return (us or 100.0) * 1e-6

    # ---- entrada ----------------------------------------------------------
    def add_packet(self, pkt: P.Packet, isr_period_slow_index: int):
        self.status = pkt.status
        for k in range(P.SAMPLES_PER_PACKET):
            sid = int(pkt.slow_id[k])
            self.slow_values[sid] = float(pkt.slow_content[k])
            if sid > self.max_slow_id:
                self.max_slow_id = sid
            if sid == isr_period_slow_index:
                v = float(pkt.slow_content[k])
                if 1.0 <= v <= 1e6:
                    self.ts_auto_us = v if self.ts_auto_us is None else 0.9 * self.ts_auto_us + 0.1 * v
            if self.last_slow_id is not None:
                gap = (sid - self.last_slow_id) % self.n_slow
                if gap == 0:
                    self.duplicates += 1
                    continue
                if gap > 1:
                    missing = gap - 1
                    self.dropped += missing
                    if missing <= self.MAX_GAP_FILL:
                        for _ in range(missing):
                            self._push(np.full(P.CHANNELS, np.nan, dtype=np.float32))
                    else:
                        self.next_isr += missing
            self.last_slow_id = sid
            self._push(pkt.samples[k])

    def _push(self, row: np.ndarray):
        self.data[self.write] = row
        self.index[self.write] = self.next_isr
        self.next_isr += 1
        self.write = (self.write + 1) % self.capacity
        self.count = min(self.count + 1, self.capacity)
        self.total_written += 1
        self.new_samples_since_clear += 1

    # ---- salida -----------------------------------------------------------
    def latest(self, n: int) -> tuple[np.ndarray, np.ndarray]:
        """Ultimas ``n`` muestras (datos (n, 20), indices ISR (n,)) en orden temporal."""
        n = int(min(max(n, 0), self.count))
        if n == 0:
            return self.data[:0], self.index[:0]
        start = (self.write - n) % self.capacity
        if start + n <= self.capacity:
            return self.data[start:start + n], self.index[start:start + n]
        first = self.capacity - start
        return (np.concatenate((self.data[start:], self.data[:n - first])),
                np.concatenate((self.index[start:], self.index[:n - first])))

    def latest_seconds(self, seconds: float) -> tuple[np.ndarray, np.ndarray]:
        """Ultimos ``seconds`` segundos: (tiempo en s, datos (n, 20))."""
        n = int(seconds / self.ts_seconds) + 1
        d, i = self.latest(n)
        return i.astype(np.float64) * self.ts_seconds, d

    def range_seconds(self, t0: float, t1: float) -> tuple[np.ndarray, np.ndarray]:
        """Muestras con ``t0 <= t <= t1`` (tiempo absoluto en s), sin recorrer todo el anillo."""
        if self.count == 0:
            return np.zeros(0), self.data[:0]
        ts = self.ts_seconds
        newest = self.next_isr - 1
        n = int(newest - t0 / ts) + 2           # desde t0 hasta la ultima muestra
        d, i = self.latest(n)
        t = i.astype(np.float64) * ts
        m = (t >= t0) & (t <= t1)
        return t[m], d[m]
