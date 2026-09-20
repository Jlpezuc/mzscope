"""Procesado de las trazas de un grafico: acoplamiento y filtro (FilterConfig).

Se aplica en la GUI, sobre el tramo de muestras que se va a dibujar (no sobre todo el
buffer), asi que cambiar un parametro se ve al instante y no toca lo que se graba en CSV.

Para que un filtro causal no arranque "en frio" al principio del tramo, quien dibuja pide al
buffer ``warmup_samples()`` muestras anteriores a la ventana visible (y posteriores si el filtro
es centrado / de fase cero); el transitorio queda fuera de la pantalla, como si el filtro
llevase corriendo desde antes. Todo es tolerante a NaN (los huecos por muestras perdidas se
conservan como huecos: se rellenan por interpolacion solo para filtrar y se vuelven a vaciar).
"""
from __future__ import annotations

import numpy as np
from scipy import signal as sps

from .settings import FilterConfig, MAX_HARMONIC

FUND_MAX_FFT = 1 << 18    # muestras usadas para estimar la frecuencia de la fundamental
HARM_MAX_FIT = 1 << 16    # muestras usadas para calcular los coeficientes de los armonicos
HARM_GRID = 4096          # puntos por periodo con los que se reconstruye la serie
MAX_WARMUP = 200_000


# ---- acoplamiento -------------------------------------------------------------------------
def couple(y: np.ndarray, coupling: str) -> np.ndarray:
    """AC: resta la media del tramo (centra la senal en 0). DC: tal cual."""
    if coupling == "AC" and np.isfinite(y).any():
        return y - np.nanmean(y)
    return y


# ---- media / RMS movil ----------------------------------------------------------------------
def moving(y: np.ndarray, width: int, centered: bool = False, rms: bool = False) -> np.ndarray:
    """Media movil (o valor eficaz movil) de ancho ``width`` muestras, ignorando NaN.

    Causal (la ventana termina en la muestra actual, como un filtro en tiempo real) o
    centrada (sin retardo). Se calcula con sumas acumuladas: coste O(n) sea cual sea el ancho.
    """
    n = len(y)
    width = max(int(width), 1)
    if n == 0 or width == 1 and not rms:
        return y.astype(np.float64, copy=True)
    valid = np.isfinite(y)
    x = np.where(valid, y, 0.0).astype(np.float64)
    if rms:
        x = x * x
    cs = np.concatenate(([0.0], np.cumsum(x)))
    cn = np.concatenate(([0], np.cumsum(valid.astype(np.int64))))
    idx = np.arange(n)
    if centered:
        lo = idx - width // 2
        hi = lo + width
    else:
        hi = idx + 1
        lo = hi - width
    lo = np.clip(lo, 0, n)
    hi = np.clip(hi, 0, n)
    cnt = cn[hi] - cn[lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        out = (cs[hi] - cs[lo]) / cnt
    out[cnt == 0] = np.nan
    return np.sqrt(out) if rms else out


# ---- armonicos ------------------------------------------------------------------------------
def estimate_frequency(y: np.ndarray, ts: float) -> float:
    """Frecuencia del pico de la FFT (sin continua), con interpolacion parabolica del pico.
    Devuelve 0 si no hay senal suficiente."""
    yy = y[np.isfinite(y)]
    n = min(len(yy), FUND_MAX_FFT)
    if n < 16 or ts <= 0:
        return 0.0
    seg = yy[-n:].astype(np.float64)
    seg = (seg - seg.mean()) * np.hanning(n)
    sp = np.abs(np.fft.rfft(seg))
    sp[0] = 0.0
    k = int(np.argmax(sp))
    if k == 0 or sp[k] == 0.0:
        return 0.0
    if 0 < k < len(sp) - 1:
        a, b, c = sp[k - 1], sp[k], sp[k + 1]
        den = a - 2.0 * b + c
        if den != 0.0:
            k += 0.5 * (a - c) / den
    return float(k / (n * ts))


def harmonics(t: np.ndarray, y: np.ndarray, ts: float, freq_hz: float, k_min: int, k_max: int) -> tuple[np.ndarray, float]:
    """Serie de Fourier de la senal entre los armonicos ``k_min`` y ``k_max`` de la fundamental
    (k = 0 es la continua). Devuelve la reconstruccion evaluada en todo ``t`` y la frecuencia usada.

    Los coeficientes se calculan por proyeccion sobre un numero entero de periodos del final
    del tramo (hasta HARM_MAX_FIT muestras); la serie, periodica, se evalua en una rejilla de un
    periodo y se interpola, asi que el coste no crece con el numero de armonicos ni con la ventana.
    """
    nan = np.full(len(t), np.nan)
    f = float(freq_hz) if freq_hz > 0 else estimate_frequency(y, ts)
    k_min = max(0, min(int(k_min), MAX_HARMONIC))
    k_max = max(k_min, min(int(k_max), MAX_HARMONIC))
    if f <= 0 or ts <= 0:
        return nan, 0.0
    period_n = 1.0 / (f * ts)
    m = np.isfinite(y)
    n_fit = min(int(m.sum()), HARM_MAX_FIT, len(y))
    n_per = int(n_fit / period_n)
    if n_per < 1:
        return nan, 0.0
    n_fit = int(round(n_per * period_n))
    tt = t[-n_fit:].astype(np.float64)
    yy = y[-n_fit:].astype(np.float64)
    mm = np.isfinite(yy)
    if mm.sum() < 8:
        return nan, 0.0
    tt, yy = tt[mm], yy[mm]
    w = 2.0 * np.pi * f
    ks = np.arange(k_min, k_max + 1)
    ang = np.outer(ks, w * tt)                       # (K, n_fit)
    a = (2.0 / len(yy)) * (np.cos(ang) @ yy)
    b = (2.0 / len(yy)) * (np.sin(ang) @ yy)
    if ks[0] == 0:
        a[0] *= 0.5                                  # la continua no lleva el factor 2
        b[0] = 0.0
    # reconstruccion sobre un periodo e interpolacion periodica
    theta = np.linspace(0.0, 2.0 * np.pi, HARM_GRID, endpoint=False)
    grid = a @ np.cos(np.outer(ks, theta)) + b @ np.sin(np.outer(ks, theta))
    ph = np.mod(w * t.astype(np.float64), 2.0 * np.pi)
    out = np.interp(ph, np.append(theta, 2.0 * np.pi), np.append(grid, grid[0]))
    return out, f


# ---- filtros IIR (scipy) --------------------------------------------------------------------
def _sos(cfg: FilterConfig, fs: float):
    """Secciones de segundo orden del filtro configurado, o None si los parametros no son validos."""
    nyq = 0.5 * fs
    lim = lambda f: min(max(float(f), 1e-3), 0.999 * nyq)
    order = max(1, min(int(cfg.order), 12))
    if cfg.kind in ("lpf", "hpf"):
        return sps.butter(order, lim(cfg.fc), "lowpass" if cfg.kind == "lpf" else "highpass", fs=fs, output="sos")
    if cfg.kind in ("bpf", "brf"):
        lo, hi = sorted((lim(cfg.f_lo), lim(cfg.f_hi)))
        if hi <= lo:
            hi = min(lo * 1.01 + 1e-3, 0.999 * nyq)
        return sps.butter(order, [lo, hi], "bandpass" if cfg.kind == "bpf" else "bandstop", fs=fs, output="sos")
    if cfg.kind == "notch":
        b, a = sps.iirnotch(lim(cfg.fc), max(float(cfg.q), 0.1), fs=fs)
        return sps.tf2sos(b, a)
    return None


def iir(y: np.ndarray, cfg: FilterConfig, fs: float) -> np.ndarray:
    """Aplica el filtro IIR (Butterworth o notch) causal o de fase cero (``cfg.centered``).

    Los huecos (NaN) se rellenan por interpolacion para filtrar y se vacian despues. En modo
    causal el filtro arranca en su estado estacionario para el primer valor, para no meter un
    escalon desde 0 al principio del tramo.
    """
    m = np.isfinite(y)
    if m.sum() < 4:
        return np.full(len(y), np.nan)
    sos = _sos(cfg, fs)
    if sos is None:
        return y.astype(np.float64, copy=True)
    idx = np.arange(len(y))
    yf = np.interp(idx, idx[m], y[m]).astype(np.float64)
    if cfg.centered:
        pad = min(3 * len(sos) * 8, len(yf) - 1)
        out = sps.sosfiltfilt(sos, yf, padlen=pad)
    else:
        zi = sps.sosfilt_zi(sos) * yf[0]
        out, _ = sps.sosfilt(sos, yf, zi=zi)
    out[~m] = np.nan
    return out


# ---- cadena completa ------------------------------------------------------------------------
def warmup_samples(cfg: FilterConfig, ts: float) -> int:
    """Muestras anteriores a la ventana que necesita el filtro para llegar a regimen (0 = ninguna)."""
    fs = 1.0 / ts if ts > 0 else 1e4
    if cfg.kind in ("mean", "rms"):
        n = int(cfg.width)
    elif cfg.kind in ("lpf", "hpf"):
        n = int(4 * max(cfg.order, 1) * fs / max(cfg.fc, 1e-3))
    elif cfg.kind in ("bpf", "brf"):
        n = int(4 * max(cfg.order, 1) * fs / max(min(cfg.f_lo, cfg.f_hi), 1e-3))
    elif cfg.kind == "notch":
        n = int(4 * max(cfg.q, 0.1) * fs / max(cfg.fc, 1e-3))
    else:
        n = 0
    return min(n + abs(int(cfg.shift)), MAX_WARMUP)


def apply_filter(t: np.ndarray, y: np.ndarray, cfg: FilterConfig, ts: float) -> tuple[np.ndarray, dict]:
    """Aplica el filtro ``cfg`` a una traza (ya escalada y acoplada). Devuelve (y_filtrada, info);
    ``info`` lleva p. ej. la frecuencia de la fundamental encontrada."""
    y = y.astype(np.float64, copy=True)
    info: dict = {}
    if cfg.shift:
        s = int(cfg.shift)
        out = np.full_like(y, np.nan)
        if abs(s) < len(y):
            if s > 0:
                out[s:] = y[:-s]
            else:
                out[:s] = y[-s:]
        y = out
    if cfg.kind in ("mean", "rms"):
        y = moving(y, cfg.width, cfg.centered, rms=(cfg.kind == "rms"))
        if cfg.centered and cfg.width > 1:
            y[len(y) - cfg.width // 2:] = np.nan      # el final aun no tiene muestras "futuras": no se inventa
    elif cfg.kind == "harmonics":
        y, f = harmonics(t, y, ts, cfg.fund_freq_hz, cfg.k_min, cfg.k_max)
        info["fund_hz"] = f
    elif cfg.kind in ("lpf", "hpf", "bpf", "brf", "notch"):
        y = iir(y, cfg, 1.0 / ts if ts > 0 else 1e4)
    if cfg.invert:
        y = -y
    return y, info


def describe(cfg: FilterConfig, info: dict | None = None) -> str:
    """Texto corto para la leyenda: "media 32", "arm 1–5 @ 85.0 kHz", "LPF 1 kHz n2"..."""
    parts = []
    if cfg.shift:
        parts.append(f"{cfg.shift:+d} m")
    k = cfg.kind
    if k == "mean":
        parts.append(f"media {cfg.width}{'c' if cfg.centered else ''}")
    elif k == "rms":
        parts.append(f"RMS {cfg.width}")
    elif k == "harmonics":
        f = (info or {}).get("fund_hz", cfg.fund_freq_hz)
        rng = "DC" if cfg.k_max == 0 else ("fund" if cfg.k_min == cfg.k_max == 1 else
                                          f"arm {'DC' if cfg.k_min == 0 else cfg.k_min}–{cfg.k_max}")
        parts.append(f"{rng} @ {_hz(f)}" if f else rng)
    elif k in ("lpf", "hpf"):
        parts.append(f"{k.upper()} {_hz(cfg.fc)} n{cfg.order}")
    elif k in ("bpf", "brf"):
        parts.append(f"{k.upper()} {_hz(cfg.f_lo)}–{_hz(cfg.f_hi)} n{cfg.order}")
    elif k == "notch":
        parts.append(f"notch {_hz(cfg.fc)} Q{cfg.q:g}")
    if k in ("lpf", "hpf", "bpf", "brf", "notch") and cfg.centered:
        parts[-1] += " fase 0"
    if cfg.invert:
        parts.append("inv")
    return " · ".join(parts) if parts else "filtro"


def _hz(f: float) -> str:
    return f"{f / 1e3:.4g} kHz" if f >= 1e3 else f"{f:.4g} Hz"
