"""Pure display processing (numpy only, no scipy).

dB, FFT windows, temporal FFT, position parsing, colormap choice and automatic
color limits. Everything here is **display / spectral analysis** of
already-produced data -- no demodulation.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from ._errors import AudaceDisplayError

DB_EPS = 1e-12  # floor to avoid log(0)


# --- dB ----------------------------------------------------------------------


def to_db(data: np.ndarray, ref: Optional[float] = None) -> tuple[np.ndarray, float]:
    """``20*log10(|x|/ref)`` with ``ref`` defaulting to the observed max.

    Returns ``(data_db, ref)``.
    """
    abs_data = np.abs(data)
    if ref is None:
        ref = float(abs_data.max()) if abs_data.size else 1.0
    ref = max(ref, DB_EPS)
    db = 20.0 * np.log10(np.maximum(abs_data, DB_EPS) / ref)
    return db.astype(np.float32), ref


# --- FFT windows -------------------------------------------------------------


def make_window(kind: str, n: int) -> np.ndarray:
    """Standard windows in pure numpy. CG (coherent gain) = ``win.mean()``."""
    if kind in ("rect", "none"):
        return np.ones(n, dtype=np.float32)
    if n == 1:
        return np.array([1.0], dtype=np.float32)
    i = np.arange(n, dtype=np.float32)
    if kind == "hann":
        return (0.5 - 0.5 * np.cos(2 * np.pi * i / (n - 1))).astype(np.float32)
    if kind == "hamming":
        return (0.54 - 0.46 * np.cos(2 * np.pi * i / (n - 1))).astype(np.float32)
    if kind == "blackman":
        return (0.42
                - 0.5 * np.cos(2 * np.pi * i / (n - 1))
                + 0.08 * np.cos(4 * np.pi * i / (n - 1))).astype(np.float32)
    raise AudaceDisplayError(
        f"unknown window '{kind}'. Known: rect, hann, hamming, blackman."
    )


def temporal_fft(
    data_2d: np.ndarray,
    *,
    fs: float,
    window: str,
    detrend: bool,
) -> tuple[np.ndarray, np.ndarray]:
    """Temporal FFT (axis 0 = time) over each column (position).

    Returns ``(freqs_hz, amplitude)``. With several positions, amplitude =
    *incoherent average* (mean of ``|FFT|^2`` then square root) -> preserves
    peaks even out of phase. Normalized by the window's coherent gain.
    """
    if data_2d.ndim == 1:
        data_2d = data_2d[:, None]
    n_rows, n_cols = data_2d.shape
    if n_rows < 2:
        raise AudaceDisplayError("not enough time samples for an FFT.")

    sig = data_2d.astype(np.float32, copy=True)
    if detrend:
        sig -= sig.mean(axis=0, keepdims=True)

    win = make_window(window, n_rows).reshape(-1, 1)
    sig *= win

    spec = np.fft.rfft(sig, axis=0)
    freqs = np.fft.rfftfreq(n_rows, d=1.0 / fs)

    if n_cols == 1:
        amp = np.abs(spec[:, 0])
    else:
        power = (np.abs(spec) ** 2).mean(axis=1)
        amp = np.sqrt(power)

    cg = float(win.mean()) or 1.0
    amp = amp / (n_rows * cg)
    return freqs.astype(np.float32), amp.astype(np.float32)


# --- 1-D signal conditioning -------------------------------------------------


def remove_dc_and_trend(signal: np.ndarray, *, detrend: bool = True) -> np.ndarray:
    """Remove the mean and (optionally) a linear trend from a 1-D signal.

    Mirrors the conditioning applied before a single-location waveform/FFT: the
    DC offset is always removed; with ``detrend`` a least-squares line is also
    subtracted. NaNs are zeroed out so the plot and FFT stay finite.
    """
    sig = np.asarray(signal, dtype=np.float64)
    sig = sig - np.nanmean(sig)
    if detrend and sig.size > 1:
        x = np.linspace(-1.0, 1.0, sig.size)
        slope, intercept = np.polyfit(x, sig, deg=1)
        sig = sig - (slope * x + intercept)
    return np.nan_to_num(sig)


def percentile_limits(
    values: np.ndarray, percentile: Optional[float]
) -> tuple[Optional[float], Optional[float]]:
    """Symmetric percentile clip ``(vmin, vmax)`` for a y-axis, or ``(None, None)``.

    ``percentile=99`` keeps the central 99 % of the finite samples. ``None``
    disables clipping.
    """
    if percentile is None:
        return None, None
    if percentile <= 0 or percentile >= 100:
        raise AudaceDisplayError("clip percentile must be between 0 and 100.")
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return None, None
    lower = (100.0 - percentile) / 2.0
    vmin, vmax = np.percentile(finite, [lower, 100.0 - lower])
    if abs(vmax - vmin) < 1e-12:
        vmax = vmin + 1e-12
    return float(vmin), float(vmax)


# --- Positions ---------------------------------------------------------------


def parse_position_spec(
    arg_value: str,
    pos_step_m: float,
    total_positions: int,
) -> tuple[list[int], list[float]]:
    """Parse ``'12.5'``, ``'10,20,30'`` or ``'10:50'`` -> ``(indices, meters)``.

    - ``'12.5'``      : 1 position at 12.5 m (nearest index)
    - ``'10,20,30'``  : list of positions
    - ``'10:50'``     : inclusive range [10 m, 50 m]
    """
    indices: list[int] = []
    if ":" in arg_value:
        a, b = arg_value.split(":", 1)
        a_idx = max(0, min(int(round(float(a) / pos_step_m)), total_positions - 1))
        b_idx = max(0, min(int(round(float(b) / pos_step_m)), total_positions - 1))
        if b_idx < a_idx:
            a_idx, b_idx = b_idx, a_idx
        indices = list(range(a_idx, b_idx + 1))
    else:
        for token in arg_value.split(","):
            idx = int(round(float(token) / pos_step_m))
            if not (0 <= idx < total_positions):
                raise AudaceDisplayError(
                    f"position {token} m outside the fiber "
                    f"(range: 0 to {(total_positions - 1) * pos_step_m:.2f} m)."
                )
            indices.append(idx)

    if not indices:
        raise AudaceDisplayError(f"empty position spec: '{arg_value}'.")
    return indices, [i * pos_step_m for i in indices]


# --- Color -------------------------------------------------------------------


def default_cmap(is_angular: bool, use_db: bool) -> str:
    """Default colormap: diverging centered on 0 for angular, else viridis."""
    if is_angular and not use_db:
        return "RdBu_r"
    return "viridis"


def auto_clim(
    data: np.ndarray,
    *,
    is_angular: bool,
    use_db: bool,
    vmin: Optional[float] = None,
    vmax: Optional[float] = None,
) -> tuple[float, float]:
    """Automatic color limits (overridable via ``vmin``/``vmax``)."""
    if data.size == 0:
        return (vmin if vmin is not None else 0.0, vmax if vmax is not None else 1.0)
    if use_db:
        hi = float(data.max()) if vmax is None else vmax
        lo = float(np.median(data) - 30.0) if vmin is None else vmin
    elif is_angular:
        lim = float(np.percentile(np.abs(data), 99.5))
        hi = lim if vmax is None else vmax
        lo = -lim if vmin is None else vmin
    else:
        lo = float(np.percentile(data, 1)) if vmin is None else vmin
        hi = float(np.percentile(data, 99)) if vmax is None else vmax
    return lo, hi
