"""Actual (real) trigger frequency -- audace-display 0.6.0.

A card fires on an integer divider of its clock: 75,000 Hz requested on a
PCIe7821 gives 250 MHz / 3,333 = 75,007.5008 Hz. invisensing >= 1.3.0 exposes it
as ``File.trig_frequency_real_hz`` (plus ``trig_clock_hz``, ``trig_period_ticks``,
``trig_frequency_is_exact``, ``line_time_ns``). Every line <-> time conversion of
audace-display must use it; an exact frequency, an old file or an older
invisensing must give exactly the 0.5.2 outputs.

Most tests wrap a real ``invisensing.File`` in a proxy implementing that
contract, so they run whatever invisensing is installed. The HDF5 tests at the
end read files carrying the real attributes through invisensing itself (the
75 kHz ones only when the installed invisensing has the 1.3.0 API).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest
from invisensing import File, FLAG_DEMODULATED, FLAG_PHASE, export_dat

import audace_display
from audace_display import cli, plotting, processing, reader
from audace_display.channels import resolve_channel
from audace_display.demod import load_demodulator

CLOCK_HZ = 250_000_000
REQUESTED_HZ = 75_000
TICKS = CLOCK_HZ // REQUESTED_HZ          # 3333: floor divider, as the card does
REAL_HZ = CLOCK_HZ / TICKS                # 75007.50075007501
N_LINES = 20_000                          # > 1 / 100 ppm: real and nominal line counts diverge
POSITIONS = 8

_NEW_API = (
    "trig_frequency_real_hz", "trig_frequency_is_exact", "trig_clock_hz",
    "trig_period_ticks", "line_time_ns",
)
HAS_REAL_API = hasattr(File, "trig_frequency_real_hz")


class _ActualRate:
    """An invisensing File announcing the card timing ``clock_hz / ticks``,
    exactly as invisensing >= 1.3.0 does for an HDF5 file carrying it."""

    def __init__(self, inner, clock_hz: int, ticks: int):
        self._inner = inner
        self.trig_clock_hz = clock_hz
        self.trig_period_ticks = ticks
        self.trig_frequency_real_hz = clock_hz / ticks
        self.trig_frequency_is_exact = clock_hz == ticks * inner.trig_frequency

    def __getattr__(self, name):
        return getattr(self._inner, name)

    @property
    def duration(self) -> float:
        return self.num_lines / self.trig_frequency_real_hz

    def line_time_ns(self, i: int) -> int:
        acq = getattr(self._inner.header, "acquisition_ns", 0) or 0
        return acq + i * self.trig_period_ticks * 10**9 // self.trig_clock_hz

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._inner.close()


class _OldLib:
    """An invisensing File as releases before 1.3.0 expose it (no real rate)."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        if name in _NEW_API:
            raise AttributeError(name)
        return getattr(self._inner, name)

    @property
    def duration(self) -> float:
        return self._inner.num_lines / self._inner.trig_frequency

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._inner.close()


def _phase_dat(path: Path, trig: int, n_lines: int, positions: int = POSITIONS, seed: int = 7):
    rng = np.random.default_rng(seed)
    data = rng.standard_normal((n_lines, positions)).astype(np.float32)
    export_dat(path, data, sample_rate=125_000_000, trig_frequency=trig,
               range_v=1.0, flags=FLAG_DEMODULATED | FLAG_PHASE)
    return path


@pytest.fixture
def dat_75k(tmp_path):
    return _phase_dat(tmp_path / "p75k.dat", REQUESTED_HZ, N_LINES)


@pytest.fixture
def dat_2k(tmp_path):
    return _phase_dat(tmp_path / "p2k.dat", 2_000, 4_000)


def _transform(f):
    _, resolver, _, _ = resolve_channel(None, f.mode)
    return lambda raw: resolver(f, raw)


def _open_75k(path):
    return _ActualRate(File(str(path)), CLOCK_HZ, TICKS)


# --- line_rate_hz -------------------------------------------------------------


class _Stub:
    def __init__(self, trig, **extra):
        self.trig_frequency = trig
        self.__dict__.update(extra)


def test_line_rate_prefers_the_real_frequency():
    assert reader.line_rate_hz(_Stub(75_000, trig_frequency_real_hz=REAL_HZ)) == REAL_HZ
    assert audace_display.line_rate_hz is reader.line_rate_hz


@pytest.mark.parametrize("real", [None, 0.0, float("nan"), float("inf"), -1.0])
def test_line_rate_falls_back_to_requested(real):
    stub = _Stub(75_000) if real is None else _Stub(75_000, trig_frequency_real_hz=real)
    rate = reader.line_rate_hz(stub)
    assert rate == 75_000.0 and isinstance(rate, float)


def test_line_rate_old_invisensing(dat_75k):
    with _OldLib(File(str(dat_75k))) as f:
        assert reader.line_rate_hz(f) == float(REQUESTED_HZ)


# --- Exact frequency: identical to 0.5.2 --------------------------------------


def _all_outputs(f):
    """Every reader output that depends on the line rate."""
    t = _transform(f)
    dec = reader.load_decimated(f, t, max_time_bins=97, max_space_bins=5,
                                start_time=0.1005, duration=1.2)
    f.seek_lines(0)
    mat = reader.load_time_matrix(f, t, max_space_bins=5, start_time=0.25, duration=0.5)
    f.seek_lines(0)
    cols, eff = reader.load_columns(f, t, [1, 6], start_time=0.3, duration=0.7,
                                    subsample_time=3)
    return dec, mat, cols, eff


def test_exact_frequency_is_bit_identical_to_nominal(dat_2k):
    """2,000 Hz = 250 MHz / 125,000: announcing the timing changes nothing."""
    with _OldLib(File(str(dat_2k))) as f:
        old = _all_outputs(f)
    with _ActualRate(File(str(dat_2k)), CLOCK_HZ, 125_000) as f:
        assert f.trig_frequency_is_exact
        new = _all_outputs(f)

    (od, om, oc, oe), (nd, nm, nc, ne) = old, new
    np.testing.assert_array_equal(nd.data, od.data)
    assert nd.t_extent == od.t_extent and nd.t_factor == od.t_factor
    np.testing.assert_array_equal(nm.data, om.data)
    assert nm.t_extent == om.t_extent and nm.trig == om.trig
    np.testing.assert_array_equal(nc, oc)
    assert ne == oe
    # ... and they are the 0.5.2 formulas (integer trig_frequency).
    p0, p1 = int(0.1005 * 2000), int(0.1005 * 2000) + int(1.2 * 2000)
    assert od.t_extent == (p0 / 2000, p1 / 2000)
    assert oe == 2000 / 3


def test_exact_frequency_info_line_unchanged(dat_2k, monkeypatch, capsys):
    monkeypatch.setattr(cli, "File", lambda p: _ActualRate(File(p), CLOCK_HZ, 125_000))
    assert cli.main(["info", str(dat_2k)]) == 0
    out = capsys.readouterr().out
    assert "  Trig frequency : 2,000 Hz\n" in out
    assert "actual" not in out


# --- 75 kHz: the real rate drives every axis ----------------------------------


def test_window_and_time_axis_use_real_rate(dat_75k):
    with _open_75k(dat_75k) as f:
        # 0.2 s = 15,001.5 real lines (15,000 nominal).
        p0, p1 = reader.time_window_pulses(f, 0.2, 0.05, None)
        assert (p0, p1) == (15_001, 15_001 + 3_750)  # int(0.05 * REAL_HZ) = 3,750
        res = reader.load_decimated(f, _transform(f), max_time_bins=50,
                                    max_space_bins=4, start_time=0.2)
        assert res.t_extent == (15_001 / REAL_HZ, N_LINES / REAL_HZ)


def test_time_matrix_and_columns_use_real_rate(dat_75k):
    with _open_75k(dat_75k) as f:
        mat = reader.load_time_matrix(f, _transform(f), max_space_bins=4)
        assert mat.trig == REAL_HZ
        assert mat.t_extent == (0.0, N_LINES / REAL_HZ)
        f.seek_lines(0)
        _, eff = reader.load_columns(f, _transform(f), [0], subsample_time=5)
        assert eff == REAL_HZ / 5


def test_old_file_75k_stays_nominal(dat_75k):
    """No real value (old file / old invisensing): 0.5.2 behaviour."""
    with _OldLib(File(str(dat_75k))) as f:
        assert reader.time_window_pulses(f, 0.2, 0.05, None) == (15_000, 18_750)
        res = reader.load_decimated(f, _transform(f), max_time_bins=50, max_space_bins=4)
        assert res.t_extent == (0.0, N_LINES / REQUESTED_HZ)


@pytest.fixture
def real_cli(monkeypatch):
    """Route the CLI's File() through the 75 kHz proxy, and capture plots."""
    monkeypatch.setattr(cli, "File", lambda p: _ActualRate(File(p), CLOCK_HZ, TICKS))
    calls: dict = {}

    def capture(name):
        def fn(*args, **kwargs):
            calls[name] = (args, kwargs)
        return fn

    for name in ("build_fft", "build_inspect", "build_line", "build_trace",
                 "build_band_heatmaps", "animate_scope"):
        monkeypatch.setattr(plotting, name, capture(name))
    monkeypatch.setattr(plotting, "show_or_save", lambda *a, **k: None)
    return calls


def test_info_shows_requested_and_actual(dat_75k, real_cli, capsys):
    assert cli.main(["info", str(dat_75k)]) == 0
    out = capsys.readouterr().out
    assert "  Trig frequency : 75,000 Hz (actual 75,007.5008 Hz, +100.0 ppm)\n" in out
    duration = float(re.search(r"Duration\s+: ([0-9.]+) s", out).group(1))
    assert duration == pytest.approx(N_LINES / REAL_HZ, abs=1e-3)


def test_fft_fs_and_nyquist_use_real_rate(dat_75k, real_cli, tmp_path):
    rc = cli.main(["fft", str(dat_75k), "--position", "2", "--max-pulses", "4000",
                   "--save", str(tmp_path / "x.png")])
    assert rc == 0
    args, kw = real_cli["build_fft"]
    assert kw["nyquist"] == REAL_HZ / 2
    freqs = args[0]
    assert freqs[1] - freqs[0] == pytest.approx(REAL_HZ / 4000, rel=1e-12)


def test_inspect_time_axis_uses_real_rate(dat_75k, real_cli, tmp_path):
    rc = cli.main(["inspect", str(dat_75k), "--index", "3", "--max-pulses", "3000",
                   "--save", str(tmp_path / "x.png")])
    assert rc == 0
    times = real_cli["build_inspect"][0][0]
    assert times[-1] == pytest.approx(2999 / REAL_HZ, rel=1e-12)


def test_bandheatmaps_fs_and_nyquist_use_real_rate(dat_75k, real_cli, monkeypatch,
                                                   tmp_path, capsys):
    seen = []
    orig = processing.band_limited

    def spy(matrix, *, fs, f_lo, f_hi):
        seen.append((fs, f_lo, f_hi))
        return orig(matrix, fs=fs, f_lo=f_lo, f_hi=f_hi)

    monkeypatch.setattr(processing, "band_limited", spy)
    # 37,502 Hz is below the real Nyquist (37,503.75 Hz) but above the nominal
    # one (37,500 Hz): it must be kept, not clamped.
    rc = cli.main(["bandheatmaps", str(dat_75k), "--f1", "1000", "--f2", "37502",
                   "--max-pulses", "4000", "--save", str(tmp_path / "x.png")])
    assert rc == 0
    assert "Nyquist" not in capsys.readouterr().err
    assert seen == [(REAL_HZ, 0.0, 1000.0), (REAL_HZ, 1000.0, 37502.0)]


def test_scope_plays_at_real_rate(dat_75k, real_cli):
    assert cli.main(["scope", str(dat_75k), "--backend", "matplotlib"]) == 0
    _, kw = real_cli["animate_scope"]
    assert kw["trig_freq"] == REAL_HZ
    assert "75007.5008 lines/s" in kw["title"]


def test_scope_frozen_frame_time_label(dat_75k, real_cli, tmp_path):
    rc = cli.main(["scope", str(dat_75k), "--line", "15001",
                   "--save", str(tmp_path / "x.png")])
    assert rc == 0
    title = real_cli["build_line"][1]["title"]
    assert f"line 15001 (t={15001 / REAL_HZ:.3f} s)" in title


# --- Demod plugin contract ----------------------------------------------------

# Records what it receives next to itself (JSON floats round-trip exactly).
RECORDING_PLUGIN = '''
import json
from pathlib import Path
import numpy as np
class Demodulator:
    def __init__(self, *, trig_frequency, trig_frequency_real_hz, meta):
        seen = dict(trig=trig_frequency, real=trig_frequency_real_hz,
                    meta_trig=meta["trig_frequency"],
                    meta_real=meta["trig_frequency_real_hz"])
        Path(__file__).with_suffix(".json").write_text(json.dumps(seen))
    def process(self, chunk):
        return chunk.astype(np.float32)
'''

# 0.5.x-style strict signature: must keep loading (new kwarg filtered out).
LEGACY_PLUGIN = '''
import numpy as np
def demodulate(chunk, *, sample_rate, trig_frequency, line_size, meta):
    return chunk.astype(np.float32) * 0 + trig_frequency
'''


def test_plugin_receives_real_rate(dat_75k, tmp_path):
    script = tmp_path / "rec.py"
    script.write_text(RECORDING_PLUGIN)
    with _open_75k(dat_75k) as f:
        plugin = load_demodulator(script, f)
        assert plugin.transform(f.read_lines(3)).shape == (3, POSITIONS)
    seen = json.loads(script.with_suffix(".json").read_text())
    assert seen["trig"] == REQUESTED_HZ and isinstance(seen["trig"], int)
    assert seen["meta_trig"] == REQUESTED_HZ
    assert seen["real"] == seen["meta_real"] == REAL_HZ


def test_plugin_meta_falls_back_to_requested(dat_75k, tmp_path):
    script = tmp_path / "rec.py"
    script.write_text(RECORDING_PLUGIN)
    with _OldLib(File(str(dat_75k))) as f:
        load_demodulator(script, f)
    seen = json.loads(script.with_suffix(".json").read_text())
    assert seen["real"] == seen["meta_real"] == float(REQUESTED_HZ)


def test_legacy_plugin_signature_still_loads(dat_75k, tmp_path):
    script = tmp_path / "legacy.py"
    script.write_text(LEGACY_PLUGIN)
    with _open_75k(dat_75k) as f:
        out = load_demodulator(script, f).transform(f.read_lines(2))
    assert np.all(out == REQUESTED_HZ)


# --- HDF5 files read by invisensing -------------------------------------------

try:  # a dependency of invisensing; skip only these tests if it is missing
    import h5py
except ImportError:  # pragma: no cover
    h5py = None
needs_h5py = pytest.mark.skipif(h5py is None, reason="h5py missing")


def _phase_h5(path: Path, trig: int, n_lines: int, timing=None, seed: int = 11):
    """Minimal Audace HDF5 (root attributes + ``acoustic_data``); ``timing`` =
    ``(clock_hz, ticks)`` adds the 1.3.0 attributes the Filewriter writes."""
    rng = np.random.default_rng(seed)
    data = rng.standard_normal((n_lines, POSITIONS)).astype(np.float32)
    with h5py.File(path, "w") as h:
        h.create_dataset("acoustic_data", data=data)
        a = h.attrs
        a["line_size"] = np.uint32(POSITIONS)
        a["trig_frequency"] = np.uint32(trig)
        a["sample_size"] = np.uint32(4)
        a["sample_rate"] = np.uint32(125_000_000)
        a["flags"] = np.uint32(FLAG_DEMODULATED | FLAG_PHASE)
        a["range"] = np.uint32(1000)
        a["num_channels"] = np.uint32(1)
        a["timestamp"] = ""
        if timing is not None:
            clock, ticks = timing
            a["trig_clock_hz"] = np.uint32(clock)
            a["trig_period_ticks"] = np.uint32(ticks)
            a["trig_frequency_real_hz"] = np.float64(clock / ticks)
    return path


@needs_h5py
def test_hdf5_old_file_stays_nominal(tmp_path, capsys):
    """A pre-1.3.0 HDF5 (no timing attributes), any invisensing version."""
    path = _phase_h5(tmp_path / "old.hdf5", REQUESTED_HZ, 2_000)
    with File(str(path)) as f:
        assert reader.line_rate_hz(f) == float(REQUESTED_HZ)
    assert cli.main(["info", str(path)]) == 0
    assert "  Trig frequency : 75,000 Hz\n" in capsys.readouterr().out


@needs_h5py
def test_hdf5_exact_matches_file_without_attributes(tmp_path):
    exact = _phase_h5(tmp_path / "exact.hdf5", 2_000, 3_000, timing=(CLOCK_HZ, 125_000))
    plain = _phase_h5(tmp_path / "plain.hdf5", 2_000, 3_000)
    with File(str(exact)) as fe, File(str(plain)) as fp:
        assert reader.line_rate_hz(fe) == reader.line_rate_hz(fp) == 2000.0
        a = reader.load_decimated(fe, _transform(fe), max_time_bins=40, start_time=0.3)
        b = reader.load_decimated(fp, _transform(fp), max_time_bins=40, start_time=0.3)
    np.testing.assert_array_equal(a.data, b.data)
    assert a.t_extent == b.t_extent == (600 / 2000, 3000 / 2000)


@needs_h5py
@pytest.mark.skipif(not HAS_REAL_API, reason="needs invisensing >= 1.3.0")
def test_hdf5_75k_real_rate_through_invisensing(tmp_path, capsys):
    path = _phase_h5(tmp_path / "real.hdf5", REQUESTED_HZ, N_LINES, timing=(CLOCK_HZ, TICKS))
    with File(str(path)) as f:
        assert f.trig_frequency == REQUESTED_HZ
        assert f.trig_frequency_is_exact is False
        assert reader.line_rate_hz(f) == REAL_HZ
        assert f.duration == pytest.approx(N_LINES / REAL_HZ, rel=1e-12)
        assert reader.time_window_pulses(f, 0.2, None, None) == (15_001, N_LINES)
        res = reader.load_decimated(f, _transform(f), max_time_bins=50, max_space_bins=4)
        assert res.t_extent == (0.0, N_LINES / REAL_HZ)
        # Same clock as invisensing's exact timeline (relative to line 0).
        t_ns = f.line_time_ns(15_001) - f.line_time_ns(0)
        assert t_ns == 15_001 * TICKS * 10**9 // CLOCK_HZ
        assert abs(15_001 / REAL_HZ - t_ns / 1e9) < 1e-9
    assert cli.main(["info", str(path)]) == 0
    assert "75,000 Hz (actual 75,007.5008 Hz, +100.0 ppm)" in capsys.readouterr().out


# --- Version ------------------------------------------------------------------


def test_version_and_dependency_metadata():
    root = Path(__file__).resolve().parents[1]
    text = (root / "pyproject.toml").read_text()
    assert re.search(r'^version = "([^"]+)"', text, re.M).group(1) == audace_display.__version__
    assert audace_display.__version__ == "0.6.0"
    assert '"invisensing>=1.3.0"' in text
