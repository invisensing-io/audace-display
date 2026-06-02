"""Fast interactive display backend via **pyqtgraph** (Qt + OpenGL).

Optional: installed with `pip install audace-display[interactive]`. Much more
responsive than matplotlib for pan/zoom on large heatmaps (GPU re-render,
automatic downsampling of curves). Reserved for interactive display; PNG export
(`--save`) always goes through matplotlib (see :mod:`plotting`).

pyqtgraph/Qt are imported only on actual use, so the package stays usable
without Qt.
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from ._errors import AudaceDisplayError

_INSTALL_HINT = (
    "pyqtgraph backend unavailable. Install it with "
    "`pip install audace-display[interactive]` (pyqtgraph + PyQt)."
)


def require_pyqtgraph():
    """Import pyqtgraph + Qt, or raise a clear error with the install hint."""
    try:
        import pyqtgraph as pg
        from pyqtgraph.Qt import QtWidgets, QtGui
    except ImportError as exc:
        raise AudaceDisplayError(_INSTALL_HINT) from exc
    return pg, QtWidgets, QtGui


# Retained references: without them the QApplication object (refcount 0) and the
# windows would be collected by CPython before being shown -> crash
# "Must construct a QApplication before a QWidget".
_WINDOWS: list = []


def _ensure_app():
    # `mkQApp` creates (or retrieves) the QApplication and keeps it in
    # pyqtgraph.Qt.QAPP, avoiding any premature GC.
    import pyqtgraph as pg
    return pg.mkQApp("audace-display")


def _register(win):
    _WINDOWS.append(win)
    return win


def _colormap(pg, name: str):
    try:
        return pg.colormap.get(name, source="matplotlib")
    except Exception:
        return pg.colormap.get("viridis", source="matplotlib")


def build_heatmap(
    data: np.ndarray,
    *,
    t_extent: tuple[float, float],
    d_extent: tuple[float, float],
    label: str,
    vmin: float,
    vmax: float,
    cmap: str,
    title: str,
):
    """Interactive waterfall: X = time (s), Y = distance (m), color = ``data``.

    ``data`` is ``(n_time, n_space)`` -- ImageItem maps axis 0 -> X, axis 1 -> Y.
    """
    pg, QtWidgets, QtGui = require_pyqtgraph()
    _ensure_app()
    data = np.asarray(data)

    win = pg.GraphicsLayoutWidget()
    win.setWindowTitle(title)
    plot = win.addPlot()
    plot.setTitle(title)
    plot.setLabel("bottom", "Time (s)")
    plot.setLabel("left", "Distance (m)")

    img = pg.ImageItem()
    img.setImage(data, levels=(vmin, vmax))
    nt, nd = data.shape
    t0, t1 = t_extent
    d0, d1 = d_extent
    tr = QtGui.QTransform()
    tr.translate(t0, d0)
    tr.scale((t1 - t0) / max(nt, 1), (d1 - d0) / max(nd, 1))
    img.setTransform(tr)
    plot.addItem(img)

    cm = _colormap(pg, cmap)
    bar = pg.ColorBarItem(values=(vmin, vmax), colorMap=cm, label=label)
    bar.setImageItem(img, insert_in=plot)

    win.show()
    return _register(win)


def build_trace(
    times: np.ndarray,
    lo: np.ndarray,
    hi: np.ndarray,
    *,
    label: str,
    title: str,
):
    """Interactive 1-D time trace (min/max envelope if ``lo != hi``)."""
    pg, QtWidgets, QtGui = require_pyqtgraph()
    _ensure_app()
    times = np.asarray(times)
    lo = np.asarray(lo)
    hi = np.asarray(hi)

    win = pg.PlotWidget()
    win.setWindowTitle(title)
    win.setTitle(title)
    win.setLabel("bottom", "Time (s)")
    win.setLabel("left", label)
    win.showGrid(x=True, y=True, alpha=0.3)

    if np.array_equal(lo, hi):
        win.plot(times, lo, pen=pg.mkPen(width=1))
    else:
        c_lo = win.plot(times, lo, pen=pg.mkPen((90, 120, 255, 160)))
        c_hi = win.plot(times, hi, pen=pg.mkPen((90, 120, 255, 160)))
        win.addItem(pg.FillBetweenItem(c_lo, c_hi, brush=(90, 120, 255, 70)))
        win.plot(times, 0.5 * (lo + hi), pen=pg.mkPen((20, 40, 200), width=1))

    win.setDownsampling(auto=True)
    win.setClipToView(True)
    win.show()
    return _register(win)


def build_fft(
    freqs: np.ndarray,
    curves: Sequence[tuple[Optional[str], np.ndarray]],
    *,
    y_label: str,
    title: str,
    nyquist: float,
    fmax: Optional[float] = None,
):
    """Interactive temporal spectrum/spectra. ``curves`` = list of ``(label, y)``."""
    pg, QtWidgets, QtGui = require_pyqtgraph()
    _ensure_app()
    freqs = np.asarray(freqs)

    win = pg.PlotWidget()
    win.setWindowTitle(title)
    win.setTitle(title)
    win.setLabel("bottom", f"Frequency (Hz) -- Nyquist = {nyquist:.0f} Hz")
    win.setLabel("left", y_label)
    win.showGrid(x=True, y=True, alpha=0.3)
    if any(name for name, _ in curves):
        win.addLegend()

    n = max(len(curves), 1)
    for i, (name, y) in enumerate(curves):
        win.plot(freqs, np.asarray(y), pen=pg.intColor(i, hues=n), name=name)

    upper = fmax if fmax is not None else (float(freqs[-1]) if freqs.size else nyquist)
    win.setXRange(0, upper)
    win.setDownsampling(auto=True)
    win.setClipToView(True)
    win.show()
    return _register(win)


def animate_scope(
    get_y,
    n_window: int,
    *,
    trig_freq: float,
    speed: float,
    fps: int,
    length_m: float,
    y_label: str,
    title: str,
    ylim: tuple[float, float],
    line_offset: int,
):
    """Real-time oscilloscope via pyqtgraph (QTimer + GPU peak-downsampling).

    The line index is derived from elapsed wall-clock time -> rate
    ``trig_freq*speed`` lines/s, looping over ``[line_offset, +n_window)``.
    pyqtgraph downsamples very large lines itself ('peak' mode).
    """
    import time

    pg, QtWidgets, QtGui = require_pyqtgraph()
    from pyqtgraph.Qt import QtCore
    _ensure_app()

    win = pg.PlotWidget()
    win.setWindowTitle(title)
    win.setTitle(title)
    win.setLabel("bottom", "Distance (m)")
    win.setLabel("left", y_label)
    win.showGrid(x=True, y=True, alpha=0.3)
    win.setXRange(0, length_m)
    win.setYRange(*ylim)
    win.setDownsampling(auto=True, mode="peak")
    win.setClipToView(True)
    curve = win.plot([], [], pen=pg.mkPen(width=1))

    n_samples = len(np.asarray(get_y(0)))
    x = np.linspace(0, length_m, n_samples)
    span = max(n_window, 1)
    t0 = time.monotonic()

    def update():
        local = int((time.monotonic() - t0) * trig_freq * speed) % span
        curve.setData(x, np.asarray(get_y(local)))
        win.setTitle(
            f"{title} -- line {line_offset + local}  t={(line_offset + local) / trig_freq:.3f}s"
        )

    timer = QtCore.QTimer()
    timer.timeout.connect(update)
    timer.start(max(1, int(1000 / fps)))
    win.show()
    _register(win)
    _WINDOWS.append(timer)  # keep the timer alive
    return win


def run() -> None:
    """Enter the Qt event loop (blocks until the window is closed)."""
    import pyqtgraph as pg
    pg.exec()
