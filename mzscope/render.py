"""Hilo de render: calcula las trazas fuera del hilo de la GUI.

Politica "el ultimo gana": ``submit()`` guarda el trabajo mas reciente y descarta el anterior si
todavia no ha empezado; el hilo calcula uno cada vez y emite ``done`` (senal Qt, llega al hilo
de la GUI) con el resultado. Asi, por muy lento que sea un filtro, la GUI sigue ingiriendo
paquetes y respondiendo, y cada frame que se dibuja usa los datos mas recientes disponibles.

El hilo de red es un proceso aparte (client.py) y no depende de nada de esto.
"""
from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal


@dataclass
class RenderJob:
    compute: Callable[[], Any]        # funcion sin argumentos que hace el calculo (numpy puro)
    tag: dict = field(default_factory=dict)   # datos que la GUI necesita al aplicar (generacion, modo, ...)


class RenderThread(QThread):
    done = Signal(object, object)     # (resultado, tag)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._job: RenderJob | None = None
        self._busy = False
        self._quit = False
        self.dropped = 0                  # trabajos sustituidos por otro mas nuevo antes de empezar
        self.last_ms = 0.0                # duracion del ultimo calculo

    def submit(self, job: RenderJob):
        with self._cv:
            if self._job is not None:
                self.dropped += 1
            self._job = job
            self._cv.notify()

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy or self._job is not None

    def stop(self, wait_ms: int = 2000):
        with self._cv:
            self._quit = True
            self._cv.notify()
        self.wait(wait_ms)

    def run(self):
        import time
        while True:
            with self._cv:
                while self._job is None and not self._quit:
                    self._cv.wait()
                if self._quit:
                    return
                job, self._job = self._job, None
                self._busy = True
            try:
                t0 = time.perf_counter()
                result = job.compute()
                self.last_ms = (time.perf_counter() - t0) * 1e3
                self.done.emit(result, job.tag)
            except Exception:             # nunca tumbar el hilo: la GUI sigue y el siguiente frame lo reintenta
                self.failed.emit(traceback.format_exc())
            finally:
                with self._lock:
                    self._busy = False
