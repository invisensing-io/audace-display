"""Waterfalls put the newest line at the top (user rule, 2026-11-16): time on
the vertical axis, increasing upwards, distance horizontal. The desktop viewer
(gui/) pins the same orientation in its own tests (``plots::tests``), so the
two draw a file the same way up.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import matplotlib

matplotlib.use("Agg")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from audace_display import plotting  # noqa: E402

N_TIME, N_SPACE = 4, 3


def _rows_numbered() -> np.ndarray:
    """``(N_TIME, N_SPACE)``: row ``r`` (time sample ``r``) holds ``r``."""
    return np.repeat(np.arange(N_TIME, dtype=np.float32)[:, None], N_SPACE, axis=1)


def _top_and_bottom(fig, ax):
    """RGB of the image at the middle of its top and bottom rows."""
    fig.canvas.draw()
    rgba = np.asarray(fig.canvas.buffer_rgba())
    box = ax.get_window_extent()          # display pixels, origin bottom-left
    x = int((box.x0 + box.x1) / 2)
    row = box.height / N_TIME
    top = int(rgba.shape[0] - (box.y1 - row / 2))
    bottom = int(rgba.shape[0] - (box.y0 + row / 2))
    return tuple(rgba[top, x, :3]), tuple(rgba[bottom, x, :3])


def test_the_heatmap_draws_the_newest_line_at_the_top():
    fig = plotting.build_heatmap(
        _rows_numbered(), t_extent=(0.0, 4.0), d_extent=(0.0, 30.0), label="v",
        vmin=0.0, vmax=3.0, cmap="gray", aspect="auto", title="t",
    )
    ax = fig.axes[0]
    top, bottom = _top_and_bottom(fig, ax)
    assert top == (255, 255, 255)       # the last time sample
    assert bottom == (0, 0, 0)          # the first one
    assert ax.get_ylim() == (0.0, 4.0)  # time increases upwards
    assert ax.get_xlim() == (0.0, 30.0)  # distance left to right


def test_every_band_panel_draws_the_newest_line_at_the_top():
    panel = {"data": _rows_numbered(), "title": "p", "label": "v",
             "vmin": 0.0, "vmax": 3.0, "cmap": "gray"}
    fig = plotting.build_band_heatmaps(
        [panel, dict(panel), dict(panel)], t_extent=(0.0, 4.0),
        d_extent=(0.0, 30.0), suptitle="s",
    )
    images = [ax for ax in fig.axes if ax.images]
    assert len(images) == 3
    for ax in images:
        top, bottom = _top_and_bottom(fig, ax)
        assert (top, bottom) == ((255, 255, 255), (0, 0, 0))
        assert ax.get_ylim() == (0.0, 4.0)


def test_the_pyqtgraph_heatmap_draws_the_newest_line_at_the_top():
    pytest.importorskip("pyqtgraph")
    try:
        from pyqtgraph.Qt import QtCore, QtWidgets
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    except Exception as exc:  # pragma: no cover - no Qt or no offscreen platform
        pytest.skip(f"Qt unavailable: {exc}")
    import pyqtgraph as pg

    from audace_display import plotting_qt

    win = plotting_qt.build_heatmap(
        _rows_numbered(), t_extent=(0.0, 4.0), d_extent=(0.0, 30.0), label="v",
        vmin=0.0, vmax=3.0, cmap="gray", title="t",
    )
    plot = win.getItem(0, 0)
    assert not plot.getViewBox().yInverted()  # y, the time, increases upwards
    image = next(i for i in plot.items if isinstance(i, pg.ImageItem))
    # Image pixel (distance column, time row): the last row sits above the first.
    last = image.mapToView(QtCore.QPointF(0.5, N_TIME - 0.5))
    first = image.mapToView(QtCore.QPointF(0.5, 0.5))
    assert 3.0 < last.y() < 4.0 and 0.0 < first.y() < 1.0
    win.close()
