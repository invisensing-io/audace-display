"""matplotlib figure builders.

Automatically selects the ``Agg`` backend when there is no display (headless
server): PNG export via ``--save`` works without X11/Wayland.
"""
from __future__ import annotations

import os
from typing import Optional, Sequence

import matplotlib

if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    matplotlib.use("Agg")  # noqa: E402 -- must precede the pyplot import

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402


def show_or_save(fig, save: Optional[str], dpi: int) -> None:
    """Show interactively or save to PNG depending on ``save``."""
    if save:
        fig.savefig(save, dpi=dpi)
        print(f"saved -> {save}")
        plt.close(fig)
    else:
        plt.show()


def build_heatmap(
    data: np.ndarray,
    *,
    t_extent: tuple[float, float],
    d_extent: tuple[float, float],
    label: str,
    vmin: float,
    vmax: float,
    cmap: str,
    aspect: str,
    title: str,
):
    """Waterfall: X = time (s), Y = distance (m), color = ``data``.

    ``data`` is ``(n_time, n_space)`` -- its transpose is displayed.
    """
    fig, ax = plt.subplots(figsize=(12, 6))
    im = ax.imshow(
        data.T,
        aspect=aspect,
        origin="lower",
        extent=[t_extent[0], t_extent[1], d_extent[0], d_extent[1]],
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
        interpolation="nearest",
    )
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label(label)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Distance (m)")
    ax.set_title(title)
    fig.tight_layout()
    return fig


def build_trace(
    centers_s: np.ndarray,
    lo: np.ndarray,
    hi: np.ndarray,
    *,
    label: str,
    title: str,
):
    """1-D time trace. If ``lo != hi`` (decimated signal), draw the min/max
    envelope; otherwise a plain line."""
    fig, ax = plt.subplots(figsize=(12, 5))
    if np.array_equal(lo, hi):
        ax.plot(centers_s, lo, linewidth=0.8)
    else:
        ax.fill_between(centers_s, lo, hi, alpha=0.5, linewidth=0)
        mid = 0.5 * (lo + hi)
        ax.plot(centers_s, mid, linewidth=0.5)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel(label)
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig


def build_line(x: np.ndarray, y: np.ndarray, *, x_label: str, y_label: str, title: str):
    """Generic static line plot (e.g. a single line/pulse frozen for --save)."""
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(x, y, linewidth=0.8)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig


def build_inspect(
    times: np.ndarray,
    waveform: np.ndarray,
    freqs: np.ndarray,
    magnitude: np.ndarray,
    *,
    y_label: str,
    ylim: tuple[Optional[float], Optional[float]],
    stats_text: str,
    fft_y_label: str,
    fft_log: bool,
    fmax: Optional[float],
    title: str,
):
    """Single-location inspection: waveform (top) + FFT spectrum (bottom).

    ``ylim`` is an optional ``(vmin, vmax)`` percentile clip for the waveform.
    The dominant FFT peak (over ``freqs > 0``, within ``fmax``) is annotated.
    """
    fig, (ax_wave, ax_fft) = plt.subplots(2, 1, figsize=(13, 8))

    ax_wave.plot(times, waveform, linewidth=0.8, color="tab:blue")
    if ylim[0] is not None and ylim[1] is not None:
        ax_wave.set_ylim(ylim[0], ylim[1])
    ax_wave.set_xlabel("Time from selected start (s)")
    ax_wave.set_ylabel(y_label)
    ax_wave.set_title(title)
    ax_wave.grid(True, alpha=0.3)
    ax_wave.text(
        0.01, 0.98, stats_text, transform=ax_wave.transAxes, va="top",
        bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.85},
    )

    mask = freqs > 0
    if fmax is not None:
        mask &= freqs <= fmax
    if magnitude.size and np.any(mask):
        f_m, m_m = freqs[mask], magnitude[mask]
        ax_fft.plot(f_m, m_m, linewidth=0.8, color="tab:purple")
        ax_fft.set_xlabel("Frequency (Hz)")
        ax_fft.set_ylabel(fft_y_label)
        ax_fft.set_title("FFT spectrum")
        ax_fft.grid(True, alpha=0.3, which="both" if fft_log else "major")
        if fft_log:
            ax_fft.set_yscale("log")
        peak = int(np.argmax(m_m))
        ax_fft.axvline(f_m[peak], color="tab:red", linestyle="--", linewidth=0.8)
        ax_fft.annotate(
            f"{f_m[peak]:.2f} Hz", xy=(f_m[peak], m_m[peak]),
            xytext=(8, 8), textcoords="offset points",
        )
    else:
        ax_fft.text(0.5, 0.5, "FFT unavailable: signal too short",
                    ha="center", va="center", transform=ax_fft.transAxes)
        ax_fft.set_axis_off()

    fig.tight_layout()
    return fig


# Keep the FuncAnimation objects alive: otherwise the GC stops them immediately.
_ANIMATIONS: list = []


def animate_scope(
    get_y,
    n_window: int,
    *,
    trig_freq: float,
    speed: float,
    fps: int,
    d_step: float,
    length_m: float,
    y_label: str,
    title: str,
    ylim: tuple[float, float],
    max_points: int,
    line_offset: int,
):
    """Oscilloscope: replays lines in real time (matplotlib + blitting).

    The line index is derived from elapsed wall-clock time -> the rate stays
    ``trig_freq*speed`` lines/s even if rendering lags (lines are skipped).
    Loops over ``[line_offset, line_offset + n_window)``.
    """
    import time
    from matplotlib.animation import FuncAnimation

    from . import decimate

    fig, ax = plt.subplots(figsize=(12, 5))
    (line,) = ax.plot([], [], linewidth=0.8)
    ax.set_xlim(0, length_m)
    ax.set_ylim(*ylim)
    ax.set_xlabel("Distance (m)")
    ax.set_ylabel(y_label)
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    txt = ax.text(0.99, 0.97, "", transform=ax.transAxes, ha="right", va="top",
                  fontsize=9, bbox=dict(boxstyle="round", fc="white", alpha=0.6))

    t0 = time.monotonic()
    span = max(n_window, 1)

    def update(_frame):
        local = int((time.monotonic() - t0) * trig_freq * speed) % span
        y = np.asarray(get_y(local))
        xi, yv = decimate.peak_line(y, max_points)
        line.set_data(xi * d_step, yv)
        txt.set_text(
            f"line {line_offset + local} / {line_offset + n_window}\n"
            f"t = {(line_offset + local) / trig_freq:.3f} s"
        )
        return line, txt

    anim = FuncAnimation(
        fig, update, interval=max(1, int(1000 / fps)),
        blit=True, cache_frame_data=False,
    )
    _ANIMATIONS.append(anim)
    fig.tight_layout()
    plt.show()


def build_fft(
    freqs: np.ndarray,
    curves: Sequence[tuple[Optional[str], np.ndarray]],
    *,
    y_label: str,
    title: str,
    nyquist: float,
    fmax: Optional[float] = None,
):
    """Temporal spectrum/spectra. ``curves`` = list of ``(label_or_None, y)``."""
    fig, ax = plt.subplots(figsize=(12, 6))
    show_legend = False
    for curve_label, y in curves:
        ax.plot(freqs, y, linewidth=1, label=curve_label)
        show_legend = show_legend or (curve_label is not None)
    if show_legend:
        ax.legend()
    ax.set_xlim(0, fmax if fmax is not None else (freqs[-1] if freqs.size else nyquist))
    ax.set_xlabel(f"Frequency (Hz)   --   Nyquist = {nyquist:.0f} Hz")
    ax.set_ylabel(y_label)
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig
