"""Disparo (trigger) tipo osciloscopio sobre el buffer de muestras.

El detector funciona como un comparador con histeresis (Schmitt): para un flanco
de subida se *arma* cuando la senal baja de ``level - hysteresis`` y *dispara* en
la primera muestra que alcanza ``level``. Con ``hysteresis = 0`` es un cruce
simple del nivel. La captura se compone de ``pre`` muestras anteriores al disparo
y ``window - pre`` posteriores; hasta que no han llegado las posteriores no se
muestra (como en un osciloscopio real). Tras cada captura la busqueda continua a
partir del final de la ventana capturada (holdoff = ventana).

El nivel se compara con la senal **tras el acoplamiento** del grafico del canal fuente
(``ac=True``: se le resta la media de la ultima ventana), sin escala ni offset. La captura
puede llevar ``pad_pre``/``pad_post`` muestras extra alrededor de la ventana para que los
filtros del grafico lleguen a regimen fuera de la pantalla.

Las posiciones se expresan en ``SampleBuffer.total_written`` (una por muestra
escrita, incluidas las NaN de los huecos), no en indice ISR, para poder extraer
del anillo por conteo; el eje de tiempo se calcula con el indice ISR real.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .buffer import SampleBuffer

EDGES = (("rising", "Subida"), ("falling", "Bajada"), ("either", "Cualquiera"))
SWEEPS = (("auto", "Auto"), ("normal", "Normal"), ("single", "Único"))


@dataclass
class TriggerConfig:
    source: int = 0            # canal (0..19)
    edge: str = "rising"       # rising | falling | either
    level: float = 0.0         # en unidades de la senal acoplada (sin escala/offset del canal)
    hysteresis: float = 0.0
    pretrigger: float = 0.2    # fraccion de la ventana anterior al disparo (0..0.95)
    sweep: str = "auto"        # auto | normal | single


class Trigger:
    def __init__(self, buffer: SampleBuffer, cfg: TriggerConfig):
        self.buf = buffer
        self.cfg = cfg
        self.scan_pos = 0             # total_written hasta donde se ha buscado
        self.armed = {"rising": False, "falling": False}
        self.pending: int | None = None   # posicion de la muestra de disparo, esperando post-muestras
        self.capture: tuple[np.ndarray, np.ndarray] | None = None   # (t relativo, datos)
        self.captures = 0
        self.enabled = True           # False tras una captura en modo "single"
        self.dc = 0.0                 # media restada a la fuente (acoplamiento AC), para informacion

    # ---- control ------------------------------------------------------------
    def reset(self):
        """Olvida el estado y empieza a buscar desde las muestras nuevas."""
        self.scan_pos = self.buf.total_written
        self.armed = {"rising": False, "falling": False}
        self.pending = None

    def arm(self):
        self.reset()
        self.enabled = True

    @property
    def state(self) -> str:
        if not self.enabled:
            return "parado"
        if self.pending is not None:
            return "adquiriendo"
        return "esperando"

    # ---- deteccion ----------------------------------------------------------
    def _scan(self, y: np.ndarray, edge: str) -> int | None:
        """Primer indice (>=1) de ``y`` que dispara para ``edge``; actualiza el armado."""
        lvl, h = self.cfg.level, abs(self.cfg.hysteresis)
        if edge == "rising":
            above, below = y >= lvl, y < lvl - h
        else:
            above, below = y <= lvl, y > lvl + h
        below_idx = np.flatnonzero(below)
        cand = np.flatnonzero(above[1:] & ~above[:-1]) + 1
        armed = self.armed[edge]
        for c in cand:
            if armed or (len(below_idx) and below_idx[0] < c):
                self.armed[edge] = False
                return int(c)
        self.armed[edge] = armed or len(below_idx) > 0
        return None

    def source_dc(self, window_n: int, ac: bool) -> float:
        """Media de la fuente en la ultima ventana (lo que resta el acoplamiento AC); 0 en DC."""
        if not ac:
            return 0.0
        d, _ = self.buf.latest(max(2, int(window_n)))
        y = d[:, self.cfg.source] if len(d) else np.empty(0)
        return float(np.nanmean(y)) if np.isfinite(y).any() else 0.0

    def poll(self, window_n: int, pre_n: int, ac: bool = False, pad_pre: int = 0, pad_post: int = 0) -> bool:
        """Busca disparo en las muestras nuevas; True si hay una captura nueva en ``capture``."""
        b = self.buf
        if not self.enabled or b.count == 0:
            return False
        window_n = max(2, min(int(window_n), b.capacity - 1))
        pre_n = max(0, min(int(pre_n), window_n - 1))
        post_n = window_n - pre_n
        pad_pre = max(0, min(int(pad_pre), b.capacity - window_n - 1))
        pad_post = max(0, min(int(pad_post), b.capacity - window_n - pad_pre - 1))

        if self.pending is None:
            n_new = b.total_written - self.scan_pos
            if n_new <= 0:
                return False
            n = min(n_new + 1, b.count)          # una muestra de solape para ver el flanco
            d, _ = b.latest(n)
            self.dc = self.source_dc(window_n, ac)
            y = d[:, self.cfg.source] - self.dc
            edges = ("rising", "falling") if self.cfg.edge == "either" else (self.cfg.edge,)
            hits = [k for k in (self._scan(y, e) for e in edges) if k is not None]
            self.scan_pos = b.total_written
            if not hits:
                return False
            self.pending = b.total_written - n + min(hits)

        if b.total_written < self.pending + post_n + pad_post:
            return False                          # faltan muestras posteriores

        oldest = b.total_written - b.count
        start = max(self.pending - pre_n - pad_pre, oldest)
        d, i = b.latest(b.total_written - start)
        k = self.pending - start
        n_take = k + post_n + pad_post
        d, i = d[:n_take], i[:n_take]
        t = (i - i[k]).astype(np.float64) * b.ts_seconds
        self.capture = (t, d.copy())
        self.captures += 1
        self.scan_pos = self.pending + post_n     # holdoff: seguir tras la ventana capturada
        self.armed = {"rising": False, "falling": False}
        self.pending = None
        if self.cfg.sweep == "single":
            self.enabled = False
        return True

    def free_run(self, window_n: int, pre_n: int, pad_pre: int = 0) -> tuple[np.ndarray, np.ndarray] | None:
        """Ultima ventana sin disparo (modo auto), con t = 0 en la posicion del disparo."""
        b = self.buf
        d, i = b.latest(window_n + max(0, int(pad_pre)))
        if len(i) == 0:
            return None
        k = min(len(i) - window_n + pre_n, len(i) - 1) if len(i) > window_n else min(pre_n, len(i) - 1)
        return (i - i[k]).astype(np.float64) * b.ts_seconds, d
