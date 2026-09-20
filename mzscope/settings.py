"""Configuracion persistente (JSON junto a la aplicacion) y modelo de canales/graficos."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import protocol as P
from .theme import TRACE_COLORS
from .trigger import TriggerConfig

SETTINGS_FILE = Path(__file__).resolve().parent.parent / "mzscope_settings.json"


@dataclass
class ChannelConfig:
    signal: str = "JSO_ZEROVALUE"   # nombre del enum JS_OberservableData
    plot: int = 0                   # indice del grafico (-1 = ninguno)
    color: str = "#4C9BE8"
    visible: bool = True
    scale: float = 1.0
    offset: float = 0.0


FILTER_KINDS = (
    ("none", "Ninguno"),
    ("mean", "Media móvil"),
    ("rms", "Valor eficaz (RMS) móvil"),
    ("harmonics", "Armónicos (DC, fundamental, …)"),
    ("lpf", "Pasa bajos"),
    ("hpf", "Pasa altos"),
    ("bpf", "Pasa banda"),
    ("brf", "Rechaza banda"),
    ("notch", "Notch"),
)
MAX_HARMONIC = 100


@dataclass
class FilterConfig:
    """Filtro de un grafico (uno solo, ``kind``), aplicado a todas sus senales tras el acoplamiento
    (ver filters.py). Solo se usan los parametros del tipo elegido."""
    kind: str = "none"           # none | mean | rms | harmonics | lpf | hpf | bpf | brf | notch
    width: int = 32              # mean/rms: ancho [muestras]
    centered: bool = False       # mean/rms: ventana centrada; lpf..notch: fase cero (filtfilt). Sin retardo
    fund_freq_hz: float = 0.0    # harmonics: frecuencia fundamental; 0 = automatica (pico de la FFT)
    k_min: int = 1               # harmonics: primer armonico dibujado (0 = DC)
    k_max: int = 1               # harmonics: ultimo armonico dibujado
    fc: float = 1000.0           # lpf/hpf: frecuencia de corte [Hz]; notch: frecuencia rechazada
    f_lo: float = 500.0          # bpf/brf: limite inferior [Hz]
    f_hi: float = 2000.0         # bpf/brf: limite superior [Hz]
    order: int = 2               # lpf/hpf/bpf/brf: orden Butterworth (pendiente 6·orden dB/octava)
    q: float = 10.0              # notch: factor de calidad (ancho = f0 / Q)
    invert: bool = False         # cambiar el signo (tras el filtro)
    shift: int = 0               # desplazar en muestras (+ = retrasar), antes del filtro


SHOW_MODES = ("original", "filtro", "ambas")


@dataclass
class PlotConfig:
    title: str = "Plot 1"
    y_label: str = ""
    auto_y: bool = True
    y_min: float = -1.0
    y_max: float = 1.0
    grid: bool = True
    show: str = "original"                      # que se dibuja: original | filtro | ambas
    coupling: str = "DC"                        # DC | AC: se aplica a TODAS las trazas (y al disparo) antes del filtro
    filter: FilterConfig = field(default_factory=FilterConfig)
    order: list[str] = field(default_factory=list)   # orden de dibujo (fondo -> frente): "s<slot>" senal, "f<slot>" filtro

    def traces(self, index: int, channels: list[ChannelConfig]) -> list[tuple[str, int, str]]:
        """Trazas que dibuja este grafico, de atras hacia delante: (clave, slot, "s"|"f").

        Se respeta ``order``; las trazas nuevas (canales recien asignados o el filtro recien
        activado) se anaden delante. Las claves que ya no se dibujan se conservan en ``order``
        para recordar su posicion si vuelven.
        """
        wanted = []
        for slot, c in enumerate(channels):
            if c.plot != index or not c.visible:
                continue
            if self.show in ("original", "ambas"):
                wanted.append(f"s{slot}")
            if self.show in ("filtro", "ambas"):
                wanted.append(f"f{slot}")
        keys = [k for k in self.order if k in wanted] + [k for k in wanted if k not in self.order]
        return [(k, int(k[1:]), k[0]) for k in keys]

    def move_trace(self, key: str, delta: int, index: int, channels: list[ChannelConfig]) -> None:
        """Mueve una traza en el orden de dibujo (delta -1 = hacia el fondo, +1 = hacia delante)."""
        keys = [k for k, _, _ in self.traces(index, channels)]
        if key not in keys:
            return
        i = keys.index(key)
        j = min(max(i + delta, 0), len(keys) - 1)
        keys[i], keys[j] = keys[j], keys[i]
        self.order = keys


def _plot_from_dict(d: dict) -> PlotConfig:
    d = {k: v for k, v in d.items() if k in PlotConfig.__dataclass_fields__}
    f = dict(d.pop("filter", None) or {})
    # claves de la primera version del filtro
    if "coupling" in f and "coupling" not in d:
        d["coupling"] = f["coupling"]
    if f.get("fundamental"):
        f.setdefault("kind", "harmonics")
    elif f.get("smooth") in ("mean", "rms"):
        f.setdefault("kind", f["smooth"])
    d["filter"] = FilterConfig(**{k: v for k, v in f.items() if k in FilterConfig.__dataclass_fields__})
    if d["filter"].kind not in dict(FILTER_KINDS):
        d["filter"].kind = "none"
    if d.get("coupling") not in ("DC", "AC"):
        d["coupling"] = "DC"
    if d.get("show") not in SHOW_MODES:
        d["show"] = "original"
    return PlotConfig(**d)


@dataclass
class Settings:
    host: str = P.DEFAULT_HOST
    port: int = P.DEFAULT_PORT
    header_path: str = ""
    dark_theme: bool = True
    time_window_s: float = 0.1
    capacity: int = 600_000
    ts_manual_us: float = 0.0          # 0 = automatico (ISR_Period_us de la placa)
    apply_channels_on_connect: bool = True
    auto_connect: bool = True          # esperar a la placa y conectar solo (al arrancar y tras perder la conexion)
    fft_points: int = 4096
    fft_log: bool = False
    fft_channel: int = 0
    display_mode: str = "roll"         # roll (continuo) | trigger (disparo)
    trigger: TriggerConfig = field(default_factory=TriggerConfig)
    plots: list[PlotConfig] = field(default_factory=lambda: [PlotConfig("Plot 1"), PlotConfig("Plot 2")])
    channels: list[ChannelConfig] = field(default_factory=list)
    window_geometry: str = ""

    def __post_init__(self):
        if not self.channels:
            self.channels = [ChannelConfig(color=TRACE_COLORS[i % len(TRACE_COLORS)],
                                           plot=0 if i < 4 else -1, visible=i < 4)
                             for i in range(P.CHANNELS)]
        while len(self.channels) < P.CHANNELS:
            i = len(self.channels)
            self.channels.append(ChannelConfig(color=TRACE_COLORS[i % len(TRACE_COLORS)], plot=-1, visible=False))
        if not self.plots:
            self.plots = [PlotConfig("Plot 1")]

    # ---- persistencia -----------------------------------------------------
    def save(self, path: Path = SETTINGS_FILE):
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path = SETTINGS_FILE) -> "Settings":
        if not path.is_file():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            plots = [_plot_from_dict(p) for p in raw.pop("plots", [])]
            channels = [ChannelConfig(**c) for c in raw.pop("channels", [])]
            trig = raw.pop("trigger", None) or {}
            trigger = TriggerConfig(**{k: v for k, v in trig.items() if k in TriggerConfig.__dataclass_fields__})
            known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
            return cls(plots=plots, channels=channels, trigger=trigger, **known)
        except (ValueError, TypeError):
            return cls()
