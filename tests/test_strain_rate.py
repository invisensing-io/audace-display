"""``--strain-rate``: a phase shown as strain rate, in nm/m/s (invisensing >= 1.6.0).

The file is opened with ``phase_unit="strain_rate"``: invisensing reads the
strain increment from the previous line times the real trigger frequency,
``(phase[n] - phase[n-1]) * float32(K / gauge * f_trig)``, at the full line
rate, and every view reduces that. Line 0 has no previous line: NaN, a
missing sample the time bins and the scales skip and the spectra fill with
the column's mean. Shown without aggregation, the values are exactly the
reference; decimated, they are the reduction of the full-rate strain rate.
"""
from __future__ import annotations

import numpy as np
import pytest

invisensing = pytest.importorskip("invisensing")
if not hasattr(invisensing.File, "strain_rate_per_rad"):  # pragma: no cover
    pytest.skip("--strain-rate needs invisensing >= 1.6.0", allow_module_level=True)

from invisensing import NANOSTRAIN_PER_RAD_METRE, File, export_dat  # noqa: E402

from audace_display import cli, plotting  # noqa: E402
from audace_display.channels import resolve_channel  # noqa: E402
from audace_display.cli import _variance_label, main  # noqa: E402

GAUGE = 9.6
TRIG = 1000
FACTOR = np.float32(NANOSTRAIN_PER_RAD_METRE / GAUGE * TRIG)
ROWS, POSITIONS = 400, 48


def _rate(rad: np.ndarray) -> np.ndarray:
    """The reference: row 0 NaN, then `(x[n] - x[n-1]) * FACTOR` in float32."""
    want = np.empty_like(rad)
    want[0] = np.nan
    want[1:] = (rad[1:] - rad[:-1]) * FACTOR
    return want


def _write(path, data, **kw):
    export_dat(path, data, sample_rate=62_500_000, trig_frequency=TRIG,
               content_type=19, card="p21", gauge_length_m=GAUGE, **kw)
    return path


@pytest.fixture
def rad():
    rng = np.random.default_rng(11)
    return np.cumsum(rng.standard_normal((ROWS, POSITIONS)), axis=0).astype(np.float32)


@pytest.fixture
def rate_phase(tmp_path, rad):
    """A phase of data format v1 (dui_coherent_phase) that records its gauge."""
    return _write(tmp_path / "phase.dat", rad)


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


def _heatmap(path, capture, *extra, time_bins=ROWS):
    capture.clear()
    rc = main(["heatmap", str(path), "--max-time-bins", str(time_bins),
               "--max-space-bins", str(POSITIONS), *extra])
    assert rc == 0
    (name, args, kwargs), = capture
    return args[0], kwargs


def test_the_phase_channel_reads_as_strain_rate(rate_phase):
    with File(rate_phase, phase_unit="strain_rate") as f:
        canonical, _, label, angular = resolve_channel(None, f.mode, f)
        assert (canonical, label, angular) == ("phase", "strain rate (nm/m/s)", False)


def test_the_variance_of_a_strain_rate_is_in_nm_per_m_per_s_squared():
    assert _variance_label("strain rate (nm/m/s)", "linear") == "Var[strain rate] (nm/m/s)^2"
    assert _variance_label("strain rate (nm/m/s)", "log") == "log10 Var[strain rate] (nm/m/s)^2"


def test_the_heatmap_shows_the_full_rate_strain_rate(rate_phase, rad, capture):
    data, kw = _heatmap(rate_phase, capture, "--strain-rate")
    assert kw["label"] == "strain rate (nm/m/s)"
    assert data.dtype == np.float32
    np.testing.assert_array_equal(data, _rate(rad))  # NaN where the reference is
    assert np.isfinite([kw["vmin"], kw["vmax"]]).all()
    assert np.isnan(data[0]).all() and np.isfinite(data[1:]).all()


def test_decimation_reduces_the_full_rate_strain_rate(rate_phase, rad, capture):
    """10 lines per time bin: each bin is the mean (variance) of the strain
    rate computed line by line -- the first bin over its 9 lines that have
    one -- never the difference of decimated strain."""
    full = _rate(rad).astype(np.float64).reshape(ROWS // 10, 10, POSITIONS)
    data, _ = _heatmap(rate_phase, capture, "--strain-rate", time_bins=ROWS // 10)
    np.testing.assert_allclose(data, np.nanmean(full, axis=1), rtol=1e-6)
    assert np.isfinite(data).all()
    # Not the strain decimated first, then differenced.
    strain = (rad * np.float32(NANOSTRAIN_PER_RAD_METRE / GAUGE)).reshape(ROWS // 10, 10, POSITIONS)
    coarse = np.diff(strain.mean(axis=1), axis=0) * (TRIG / 10)
    assert not np.allclose(data[1:], coarse, rtol=1e-2)
    var, kw = _heatmap(rate_phase, capture, "--strain-rate", "--variance", "linear",
                       time_bins=ROWS // 10)
    assert kw["label"] == "Var[strain rate] (nm/m/s)^2"
    np.testing.assert_allclose(var, np.nanvar(full, axis=1), rtol=1e-4)


def test_every_scale_is_labelled_in_nm_per_m_per_s(rate_phase, capture):
    data, kw = _heatmap(rate_phase, capture, "--strain-rate", "--variance", "log", time_bins=40)
    assert kw["label"] == "log10 Var[strain rate] (nm/m/s)^2" and np.isfinite(data).all()
    data, kw = _heatmap(rate_phase, capture, "--strain-rate", "--db")
    assert kw["label"].startswith("strain rate (dB, ref=") and kw["label"].endswith(" nm/m/s)")
    assert np.isnan(data[0]).all() and np.isfinite(data[1:]).all()
    assert np.isfinite([kw["vmin"], kw["vmax"]]).all()
    for reduce in ("rms", "std", "peak"):
        data, kw = _heatmap(rate_phase, capture, "--strain-rate", "--reduce", reduce, time_bins=40)
        assert np.isfinite(data).all(), reduce


def test_the_oscilloscope_line_is_the_strain_rate_of_that_line(rate_phase, rad, capture):
    def frozen(line, *extra):
        capture.clear()
        assert main(["scope", str(rate_phase), "--line", str(line), "--max-points",
                     str(2 * POSITIONS), "--save", "x.png", *extra]) == 0
        (_, args, kwargs), = capture
        return args[1], kwargs["y_label"]

    for line in (7, 1, 399):
        got, label = frozen(line, "--strain-rate")
        assert label == "strain rate (nm/m/s)"
        np.testing.assert_array_equal(got, _rate(rad)[line])
    got, _ = frozen(0, "--strain-rate")  # line 0: nothing to draw, no crash
    assert np.isnan(got).all()
    got, _ = frozen(3, "--strain-rate", "--start-time", "0.1")  # a seek: line 100 + 3
    np.testing.assert_array_equal(got, _rate(rad)[103])


def test_the_other_views_say_nm_per_m_per_s(rate_phase, capture):
    path = str(rate_phase)
    assert main(["trace", path, "--position", "20", "--strain-rate"]) == 0
    assert capture[-1][2]["label"] == "strain rate (nm/m/s)"
    lo, hi = capture[-1][1][1:3]
    assert np.isnan(lo[0]) and np.isfinite(lo[1:]).all() and np.isfinite(hi[1:]).all()
    assert main(["trace", path, "--position", "20", "--strain-rate", "--max-points", "40"]) == 0
    lo, hi = capture[-1][1][1:3]
    assert np.isfinite(lo).all() and np.isfinite(hi).all()  # NaN skipped in a bin
    assert main(["fft", path, "--position", "20", "--strain-rate"]) == 0
    assert capture[-1][2]["y_label"] == "Amplitude (nm/m/s)"
    assert np.isfinite(capture[-1][1][1][0][1]).all()
    assert main(["fft", path, "--strain-rate", "--db"]) == 0
    assert np.isfinite(capture[-1][1][1][0][1]).all()
    assert main(["inspect", path, "--index", "10", "--strain-rate"]) == 0
    assert capture[-1][2]["y_label"] == "strain rate (nm/m/s)"
    assert np.isfinite(capture[-1][1][1]).all() and np.isfinite(capture[-1][1][3]).all()
    assert main(["bandheatmaps", path, "--f1", "100", "--f2", "300", "--strain-rate"]) == 0
    panels = capture[-1][1][0]
    assert [p["label"] for p in panels] == [
        "strain rate (nm/m/s)", "RMS[strain rate] (nm/m/s)", "RMS[strain rate] (nm/m/s)"]
    assert all(np.isfinite(p["data"][1:]).all() for p in panels)
    assert all(np.isfinite(p["data"]).all() for p in panels[1:])
    assert main(["bandheatmaps", path, "--f1", "100", "--variance", "log", "--strain-rate"]) == 0
    assert {p["label"] for p in capture[-1][1][0]} == {"log10 Var[strain rate] (nm/m/s)^2"}


def test_a_sine_reads_as_a_cosine_in_the_spectrum(tmp_path, capture):
    """A phase sine of amplitude A at f0 is a strain rate of amplitude
    2 f sin(pi f0 / f) A K / g (about 2 pi f0 A K / g) at f0."""
    f0, amp = 50.0, 0.5
    t = np.arange(4000) / TRIG
    sine = np.repeat((amp * np.sin(2 * np.pi * f0 * t)).astype(np.float32)[:, None], 4, axis=1)
    path = _write(tmp_path / "sine.dat", sine)
    assert main(["fft", str(path), "--position", "3", "--strain-rate", "--window", "hann"]) == 0
    _, args, _ = capture[-1]
    freqs, ((_, y),) = args[0], args[1]
    peak = int(np.argmax(y))
    assert freqs[peak] == pytest.approx(f0)
    want = 2 * TRIG * np.sin(np.pi * f0 / TRIG) * amp * NANOSTRAIN_PER_RAD_METRE / GAUGE
    assert y[peak] == pytest.approx(want, rel=2e-3)


def test_the_png_is_written(rate_phase, tmp_path):
    out = tmp_path / "rate.png"
    assert main([str(rate_phase), "--strain-rate", "--save", str(out)]) == 0
    assert out.is_file() and out.stat().st_size > 0


def test_the_qt_view_is_labelled_in_nm_per_m_per_s(rate_phase, monkeypatch):
    pytest.importorskip("pyqtgraph")
    from audace_display import plotting_qt

    seen = {}
    monkeypatch.setattr(plotting_qt, "build_heatmap", lambda data, **kw: seen.update(kw, data=data))
    monkeypatch.setattr(plotting_qt, "run", lambda: None)
    assert main(["heatmap", str(rate_phase), "--strain-rate", "--backend", "pyqtgraph"]) == 0
    assert seen["label"] == "strain rate (nm/m/s)"
    assert np.isfinite([seen["vmin"], seen["vmax"]]).all()


def test_the_qt_widgets_take_a_nan_line(monkeypatch):
    """The pyqtgraph builders draw a heatmap row and a scope line of NaN."""
    pytest.importorskip("pyqtgraph")
    from audace_display import plotting_qt

    monkeypatch.setattr(plotting_qt, "_register", lambda w: w)
    data = np.ones((10, 8), np.float32)
    data[0] = np.nan
    plotting_qt.build_heatmap(data, t_extent=(0, 1), d_extent=(0, 8), label="x",
                              vmin=0.0, vmax=2.0, cmap="viridis", title="t")
    win = plotting_qt.animate_scope(lambda i: data[i % 10], 10, trig_freq=1000.0, speed=1.0,
                                    fps=30, length_m=8.0, y_label="x", title="t",
                                    ylim=(0.0, 2.0), line_offset=0)
    assert win is not None


def test_info_gives_the_factor_and_skips_line_0(rate_phase, capsys):
    assert main(["info", str(rate_phase), "--strain-rate"]) == 0
    out = capsys.readouterr().out
    assert "Quantity       : strain rate (nm/m/s)" in out
    assert "Gauge length   : 9.6 m" in out
    assert (f"Strain rate    : nm/m/s = (rad[n] - rad[n-1]) x {float(FACTOR):.6g} "
            "(= 110.37 / 9.6 m x 1,000 Hz); line 0 has none (NaN)") in out
    assert (f"Stats for channel 'phase' -- strain rate (nm/m/s) over 400 pulses "
            f"({POSITIONS} NaN/inf samples left out)") in out
    assert "nan" not in out.split("Stats for channel")[1]


def test_the_two_units_are_exclusive(rate_phase, capsys):
    with pytest.raises(SystemExit) as e:
        main(["heatmap", str(rate_phase), "--strain-rate", "--nanostrain"])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "not allowed with argument" in err and "Traceback" not in err


def test_a_file_without_a_phase_is_refused(tmp_path, capsys):
    raw = tmp_path / "raw.dat"
    export_dat(raw, np.zeros((10, 8), np.int16), sample_rate=125_000_000,
               trig_frequency=1000, content_type=1)
    assert main(["heatmap", str(raw), "--strain-rate", "--save", str(tmp_path / "x.png")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR: --strain-rate shows a phase as strain rate (nm/m/s)")
    assert "holds 'raw' (content type 1" in err and "Drop --strain-rate" in err
    assert "Traceback" not in err
    assert not (tmp_path / "x.png").exists()


def test_a_phase_without_its_gauge_is_refused(phase_dat, tmp_path, capsys):
    # conftest's phase file: written before the content type, no gauge length.
    assert main([str(phase_dat), "--strain-rate", "--save", str(tmp_path / "x.png")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR: --strain-rate needs the gauge length")
    assert "No gauge length is assumed" in err and "Traceback" not in err


def test_a_phase_without_a_trigger_frequency_is_refused(rate_phase, capsys):
    raw = bytearray(rate_phase.read_bytes())
    raw[36:40] = (0).to_bytes(4, "little")  # header trig_frequency = 0
    rate_phase.write_bytes(bytes(raw))
    assert main(["info", str(rate_phase), "--strain-rate"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR: --strain-rate multiplies each line's strain increment")
    assert "trigger frequency 0 Hz" in err and "Traceback" not in err


def test_an_invisensing_refusal_is_a_clean_error(rate_phase, monkeypatch, capsys):
    """A refusal the CLI's own check does not foresee still exits cleanly."""
    monkeypatch.setattr(cli, "_check_strain_rate", lambda f: None)
    raw = rate_phase.with_name("raw.dat")
    export_dat(raw, np.zeros((10, 8), np.int16), sample_rate=125_000_000,
               trig_frequency=1000, content_type=1)
    assert main(["info", str(raw), "--strain-rate"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR: --strain-rate: ") and "not a phase" in err


def test_a_plugin_s_output_is_not_converted(rate_phase, tmp_path, capsys):
    script = tmp_path / "ident.py"
    script.write_text("def demodulate(chunk, **kw):\n    return chunk\n")
    assert main(["inspect", str(rate_phase), "--script", str(script), "--strain-rate"]) == 1
    err = capsys.readouterr().err
    assert "--strain-rate" in err and "--script" in err and "Traceback" not in err


def test_a_gap_of_the_time_axis_is_shown_missing(tmp_path, capture, capsys):
    """An HDF5 stamped from the card's time map, two shots lost before line
    10: that line has no strain rate at the line rate (NaN), info says so."""
    h5py = pytest.importorskip("h5py")
    from invisensing._core import CONTENT_TYPE_NAMES

    rows, period_ns = 40, 1_000_000_000 // TRIG
    rad = np.cumsum(np.ones((rows, 4), np.float32) * 0.01, axis=0)
    t0 = 1_790_000_000_000_000_000
    path = tmp_path / "gap.h5"
    with h5py.File(str(path), "w") as fp:
        fp.create_dataset("acoustic_data", data=rad)
        a = fp.attrs
        for k, v in {"line_size": 4, "trig_frequency": TRIG, "sample_size": 4,
                     "sample_rate": 62_500_000, "flags": 0, "range": 1000,
                     "pulse_width": 100, "num_channels": 1}.items():
            a[k] = v
        a["timestamp"] = "2026-11-13 10:00:00.000+01:00"
        a["timestamp_ns"] = np.int64(t0)
        a["content_type"] = np.uint32(19)
        a["content_type_name"] = CONTENT_TYPE_NAMES[19]
        a["format_version"] = 1
        a["gauge_length_m"] = np.float64(GAUGE)
        a["time_correction"] = "cardclock-v1"
        a["time_map_line"] = np.array([0, 10], np.int64)
        a["time_map_ns"] = np.array([t0, t0 + 12 * period_ns], np.int64)
        a["time_map_period_fs"] = np.array([period_ns * 10**6] * 2, np.uint64)
    assert main(["info", str(path), "--strain-rate"]) == 0
    assert "Time gaps      : 1 line(s) after lost shots, NaN: 10" in capsys.readouterr().out
    data, kw = _heatmap(path, capture, "--strain-rate", time_bins=rows)
    assert [r for r in range(rows) if np.isnan(data[r]).all()] == [0, 10]
    np.testing.assert_allclose(data[1:10], 0.01 * FACTOR, rtol=1e-3)
