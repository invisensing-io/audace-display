"""Tests for the pyqtgraph backend (widgets built headless via offscreen).

Skipped cleanly if pyqtgraph / Qt are not installed. We never enter the event
loop (`run()`), which would block -- we only check that the widgets build
without error and that CLI routing works.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")


def _app_or_skip():
    try:
        from pyqtgraph.Qt import QtWidgets
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Qt unavailable: {exc}")
    try:
        return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    except Exception as exc:  # pragma: no cover - no offscreen platform
        pytest.skip(f"offscreen QApplication unavailable: {exc}")


def test_build_heatmap_qt():
    _app_or_skip()
    from audace_display import plotting_qt
    data = np.random.default_rng(0).standard_normal((200, 100)).astype(np.float32)
    win = plotting_qt.build_heatmap(
        data, t_extent=(0.0, 2.0), d_extent=(0.0, 250.0),
        label="|signal| (V)", vmin=-2.0, vmax=2.0, cmap="viridis", title="t",
    )
    assert win is not None


def test_qapp_and_window_survive_gc():
    """Regression: the app and window must be retained despite GC, otherwise
    "Must construct a QApplication before a QWidget" -> crash."""
    import gc
    _app_or_skip()
    from pyqtgraph.Qt import QtWidgets
    from audace_display import plotting_qt
    data = np.random.default_rng(0).standard_normal((150, 60)).astype(np.float32)
    n_before = len(plotting_qt._WINDOWS)
    # call without keeping the return value (as the CLI does)
    plotting_qt.build_heatmap(data, t_extent=(0, 2), d_extent=(0, 250),
                              label="x", vmin=-2, vmax=2, cmap="viridis", title="t")
    del data
    gc.collect()
    assert QtWidgets.QApplication.instance() is not None
    assert len(plotting_qt._WINDOWS) == n_before + 1
    # building a new QWidget after GC must not crash
    QtWidgets.QWidget()


def test_build_trace_qt_envelope():
    _app_or_skip()
    from audace_display import plotting_qt
    t = np.linspace(0, 1, 500)
    lo = np.sin(t * 10)
    hi = lo + 0.2
    win = plotting_qt.build_trace(t, lo, hi, label="V", title="trace")
    assert win is not None


def test_build_fft_qt_multi_curve():
    _app_or_skip()
    from audace_display import plotting_qt
    f = np.linspace(0, 500, 300)
    curves = [("50 m", np.abs(np.sin(f / 10))), ("100 m", np.abs(np.cos(f / 8)))]
    win = plotting_qt.build_fft(f, curves, y_label="A", title="fft", nyquist=500, fmax=200)
    assert win is not None


def test_cli_pyqtgraph_backend(iq_dat, monkeypatch):
    """`--backend pyqtgraph` builds the viewer then enters run() -- which we
    neutralize so it does not block. rc == 0."""
    _app_or_skip()
    from audace_display import cli, plotting_qt
    monkeypatch.setattr(plotting_qt, "run", lambda: None)
    rc = cli.main([str(iq_dat), "--backend", "pyqtgraph"])
    assert rc == 0


def test_build_animate_scope_qt():
    _app_or_skip()
    from audace_display import plotting_qt
    lines = np.random.default_rng(0).standard_normal((20, 128)).astype(np.float32)
    win = plotting_qt.animate_scope(
        lambda i: lines[i % 20], 20, trig_freq=1000, speed=1.0, fps=30,
        length_m=100.0, y_label="V", title="scope", ylim=(-3.0, 3.0), line_offset=0,
    )
    assert win is not None


def test_cli_scope_pyqtgraph(raw_dat, monkeypatch):
    _app_or_skip()
    from audace_display import cli, plotting_qt
    monkeypatch.setattr(plotting_qt, "run", lambda: None)
    rc = cli.main(["scope", str(raw_dat), "--backend", "pyqtgraph"])
    assert rc == 0


def test_cli_pyqtgraph_with_save_falls_back_to_mpl(iq_dat, tmp_path, capsys):
    """--backend pyqtgraph + --save: warns and produces the PNG via matplotlib."""
    from audace_display.cli import main
    out = tmp_path / "x.png"
    rc = main([str(iq_dat), "--backend", "pyqtgraph", "--save", str(out)])
    assert rc == 0
    assert out.is_file() and out.stat().st_size > 0
    assert "matplotlib" in capsys.readouterr().err
