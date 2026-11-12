"""``--nanostrain``: a phase shown as strain, in nm/m (invisensing >= 1.6.0).

The file is opened with ``phase_unit="nanostrain"``: every view reads
``phase / gauge_length_m * NANOSTRAIN_PER_RAD_METRE`` and labels it
``strain (nm/m)`` (``(nm/m)^2`` for a variance). Shown without temporal or
spatial aggregation, the values are exactly the radians times
``float32(K / gauge)``.
"""
from __future__ import annotations

import numpy as np
import pytest

invisensing = pytest.importorskip("invisensing")
if not hasattr(invisensing.File, "gauge_length_m"):  # pragma: no cover
    pytest.skip("--nanostrain needs invisensing >= 1.6.0", allow_module_level=True)

from invisensing import NANOSTRAIN_PER_RAD_METRE, File, export_dat  # noqa: E402

from audace_display import cli, plotting  # noqa: E402
from audace_display.channels import resolve_channel  # noqa: E402
from audace_display.cli import _variance_label, main  # noqa: E402

GAUGE = 9.6
FACTOR = np.float32(NANOSTRAIN_PER_RAD_METRE / GAUGE)
ROWS, POSITIONS = 400, 48


@pytest.fixture
def strain_phase(tmp_path):
    """A phase of data format v1 (dui_coherent_phase) that records its gauge."""
    rng = np.random.default_rng(11)
    data = (rng.standard_normal((ROWS, POSITIONS)) * 2.0).astype(np.float32)
    path = tmp_path / "phase.dat"
    export_dat(path, data, sample_rate=62_500_000, trig_frequency=1000,
               content_type=19, card="p21", gauge_length_m=GAUGE)
    return path


@pytest.fixture
def capture(monkeypatch):
    """What the matplotlib builders were handed (no figure is drawn)."""
    seen = []

    def grab(name):
        def builder(*args, **kwargs):
            seen.append((name, args, kwargs))
            return None
        return builder

    for name in ("build_heatmap", "build_band_heatmaps", "build_trace", "build_fft",
                 "build_line", "build_inspect"):
        monkeypatch.setattr(plotting, name, grab(name))
    monkeypatch.setattr(plotting, "show_or_save", lambda *a, **k: None)
    return seen


def test_the_phase_channel_reads_as_strain(strain_phase):
    with File(strain_phase) as f:
        assert resolve_channel(None, f.mode, f)[2:] == ("phase (rad)", True)
    with File(strain_phase, phase_unit="nanostrain") as f:
        canonical, _, label, angular = resolve_channel(None, f.mode, f)
        assert (canonical, label, angular) == ("phase", "strain (nm/m)", False)


def test_the_variance_of_strain_is_in_nm_per_m_squared():
    assert _variance_label("strain (nm/m)", "linear") == "Var[strain] (nm/m)^2"
    assert _variance_label("strain (nm/m)", "log") == "log10 Var[strain] (nm/m)^2"
    assert _variance_label("phase (rad)", "linear") == "Var[phase] (rad^2)"
    assert _variance_label("script output", "linear") == "Var[script output]"


def _heatmap(path, capture, *extra):
    capture.clear()
    rc = main(["heatmap", str(path), "--max-time-bins", str(ROWS),
               "--max-space-bins", str(POSITIONS), *extra])
    assert rc == 0
    (name, args, kwargs), = capture
    return args[0], kwargs["label"]


def test_the_heatmap_shows_the_radians_times_the_factor(strain_phase, capture):
    rad, rad_label = _heatmap(strain_phase, capture)
    strain, label = _heatmap(strain_phase, capture, "--nanostrain")
    assert (rad_label, label) == ("phase (rad)", "strain (nm/m)")
    assert strain.dtype == rad.dtype == np.float32
    np.testing.assert_array_equal(strain, rad * FACTOR)


def test_every_scale_is_labelled_in_nm_per_m(strain_phase, capture):
    _, label = _heatmap(strain_phase, capture, "--nanostrain", "--variance", "linear")
    assert label == "Var[strain] (nm/m)^2"
    _, label = _heatmap(strain_phase, capture, "--nanostrain", "--variance", "log")
    assert label == "log10 Var[strain] (nm/m)^2"
    _, label = _heatmap(strain_phase, capture, "--nanostrain", "--db")
    assert label.startswith("strain (dB, ref=") and label.endswith(" nm/m)")


def test_the_oscilloscope_line_is_the_radians_times_the_factor(strain_phase, capture):
    def frozen(*extra):
        capture.clear()
        assert main(["scope", str(strain_phase), "--line", "7", "--max-points",
                     str(2 * POSITIONS), "--save", "x.png", *extra]) == 0
        (_, args, kwargs), = capture
        return args[1], kwargs["y_label"]

    rad, rad_label = frozen()
    strain, label = frozen("--nanostrain")
    assert (rad_label, label) == ("phase (rad)", "strain (nm/m)")
    np.testing.assert_array_equal(strain, rad * FACTOR)


def test_the_other_views_say_nm_per_m(strain_phase, capture):
    path = str(strain_phase)
    assert main(["trace", path, "--position", "20", "--nanostrain"]) == 0
    assert capture[-1][2]["label"] == "strain (nm/m)"
    assert main(["fft", path, "--position", "20", "--nanostrain"]) == 0
    assert capture[-1][2]["y_label"] == "Amplitude (nm/m)"
    assert main(["inspect", path, "--index", "10", "--nanostrain"]) == 0
    assert capture[-1][2]["y_label"] == "strain (nm/m)"
    assert main(["bandheatmaps", path, "--f1", "100", "--f2", "300", "--nanostrain"]) == 0
    panels = capture[-1][1][0]
    assert [p["label"] for p in panels] == ["strain (nm/m)", "RMS[strain] (nm/m)", "RMS[strain] (nm/m)"]
    assert main(["bandheatmaps", path, "--f1", "100", "--variance", "log", "--nanostrain"]) == 0
    assert {p["label"] for p in capture[-1][1][0]} == {"log10 Var[strain] (nm/m)^2"}


def test_the_png_is_written(strain_phase, tmp_path):
    out = tmp_path / "strain.png"
    assert main([str(strain_phase), "--nanostrain", "--save", str(out)]) == 0
    assert out.is_file() and out.stat().st_size > 0


def test_the_qt_view_is_labelled_in_nm_per_m(strain_phase, monkeypatch):
    pytest.importorskip("pyqtgraph")
    from audace_display import plotting_qt

    seen = {}
    monkeypatch.setattr(plotting_qt, "build_heatmap", lambda data, **kw: seen.update(kw))
    monkeypatch.setattr(plotting_qt, "run", lambda: None)
    assert main(["heatmap", str(strain_phase), "--nanostrain", "--backend", "pyqtgraph"]) == 0
    assert seen["label"] == "strain (nm/m)"


def test_info_gives_the_gauge_and_the_factor(strain_phase, capsys):
    assert main(["info", str(strain_phase), "--nanostrain"]) == 0
    out = capsys.readouterr().out
    assert "Quantity       : strain (nm/m)" in out
    assert "Gauge length   : 9.6 m" in out
    assert f"Strain         : nm/m = rad x {float(FACTOR):.6g}" in out
    assert "Stats for channel 'phase' -- strain (nm/m) over" in out
    assert main(["info", str(strain_phase)]) == 0
    out = capsys.readouterr().out
    assert "Quantity       : phase (rad)" in out and "Gauge length   : 9.6 m" in out
    assert "Strain " not in out


def test_a_file_without_a_phase_is_refused(tmp_path, capsys):
    raw = tmp_path / "raw.dat"
    export_dat(raw, np.zeros((10, 8), np.int16), sample_rate=125_000_000,
               trig_frequency=1000, content_type=1)
    assert main(["heatmap", str(raw), "--nanostrain", "--save", str(tmp_path / "x.png")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR: --nanostrain shows a phase as strain (nm/m)")
    assert "holds 'raw' (content type 1" in err and "Drop --nanostrain" in err
    assert "Traceback" not in err
    assert not (tmp_path / "x.png").exists()


def test_a_phase_without_its_gauge_is_refused(phase_dat, tmp_path, capsys):
    # conftest's phase file: written before the content type, no gauge length.
    assert main([str(phase_dat), "--nanostrain", "--save", str(tmp_path / "x.png")]) == 1
    err = capsys.readouterr().err
    assert "does not record its gauge length" in err and "No gauge length is assumed" in err
    assert "Traceback" not in err


def test_a_plugin_s_output_is_not_converted(strain_phase, tmp_path, capsys):
    script = tmp_path / "ident.py"
    script.write_text("def demodulate(chunk, **kw):\n    return chunk\n")
    assert main(["inspect", str(strain_phase), "--script", str(script), "--nanostrain"]) == 1
    err = capsys.readouterr().err
    assert "--script" in err and "Traceback" not in err


def test_an_invisensing_refusal_is_a_clean_error(strain_phase, monkeypatch, capsys):
    """A refusal the CLI's own check does not foresee still exits cleanly."""
    monkeypatch.setattr(cli, "_check_nanostrain", lambda f: None)
    raw = strain_phase.with_name("raw.dat")
    export_dat(raw, np.zeros((10, 8), np.int16), sample_rate=125_000_000,
               trig_frequency=1000, content_type=1)
    assert main(["info", str(raw), "--nanostrain"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR: --nanostrain: ") and "not a phase" in err
