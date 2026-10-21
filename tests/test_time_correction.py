"""Absolute times shown come from the file's (corrected) line times.

The Audace acquisition corrects the drift of the card's oscillator and the
shots it lost (the card's *time map*); the recorder writes each HDF5 file's
axis into its attributes and invisensing >= 1.4.0 reads it
(``File.line_time_ns``, ``File.time_correction``). audace-display LABELS with
those times (``info``: start, last pulse, how the times were stamped) and
keeps the programmed cadence (``line_rate_hz``) for every rate: spectra,
filters, Nyquist, the relative time axes.
"""
from __future__ import annotations

import numpy as np
import pytest
from invisensing import File

from audace_display import absolute_time_ns, line_rate_hz
from audace_display import cli

h5py = pytest.importorskip("h5py")

CLOCK = 250_000_000
ANCHOR = 1_760_000_000_000_000_000
ROWS = 40
LINE = 16
# A 2 kHz file whose first line sits 10 lines past a knot of the card's map
# running 4.8 ppm slow, a loss step of 3.25 ms at file line 20.
P1 = 500_002_400_000
T_KNOT = ANCHOR + 98_000


def _t(i: int) -> int:
    if i < 20:
        return T_KNOT + ((i + 10) * P1) // 1_000_000
    t20 = T_KNOT + (30 * P1) // 1_000_000 + 3_250_000
    return t20 + ((i - 20) * P1) // 1_000_000


def _write(path, *, corrected: bool, nominal_start: int = ANCHOR):
    data = np.zeros((ROWS, LINE), dtype=np.float32)
    with h5py.File(str(path), "w") as fp:
        fp.create_dataset("acoustic_data", data=data)
        for k, v in {
            "line_size": LINE, "trig_frequency": 2_000, "sample_size": 4,
            "sample_rate": 125_000_000, "flags": 0x082, "range": 1_000,
            "pulse_width": 40, "num_channels": 1,
        }.items():
            fp.attrs[k] = np.uint32(v)
        fp.attrs["trig_clock_hz"] = np.uint32(CLOCK)
        fp.attrs["trig_period_ticks"] = np.uint32(125_000)
        fp.attrs["timestamp"] = "2026-10-21 08:00:00.000+02:00"
        if corrected:
            fp.attrs["timestamp_ns"] = np.int64(_t(0))
            fp.attrs["time_correction"] = "cardclock-v1"
            fp.attrs["timestamp_nominal_ns"] = np.int64(nominal_start)
            fp.attrs["timestamp_last_ns"] = np.int64(_t(ROWS - 1))
            fp.attrs["time_map_line"] = np.asarray([-10, 20], dtype=np.int64)
            fp.attrs["time_map_ns"] = np.asarray([T_KNOT, _t(20)], dtype=np.int64)
            fp.attrs["time_map_period_fs"] = np.asarray([P1, P1], dtype=np.uint64)
            fp.attrs["time_correction_state"] = "tracking"
            fp.attrs["time_uncertainty_ns"] = np.int64(61_000)
            fp.attrs["trig_frequency_measured_hz"] = np.float64(1_999.990_4)
        else:
            fp.attrs["timestamp_ns"] = np.int64(ANCHOR)
    return path


needs_14 = pytest.mark.skipif(
    not hasattr(File, "times_ns"), reason="invisensing < 1.4.0 has no corrected line times"
)


@needs_14
def test_absolute_times_are_the_corrected_line_times(tmp_path):
    path = _write(tmp_path / "c.hdf5", corrected=True)
    with File(str(path)) as f:
        for i in (0, 1, 19, 20, ROWS - 1):
            assert absolute_time_ns(f, i) == _t(i) == f.line_time_ns(i)
        # Rates stay the programmed cadence: the correction moves labels only.
        assert line_rate_hz(f) == 2_000.0


@needs_14
def test_info_says_how_the_times_were_stamped(tmp_path, capsys):
    path = _write(tmp_path / "c.hdf5", corrected=True)
    assert cli.main(["info", str(path)]) == 0
    out = capsys.readouterr().out
    assert f"({_t(0)} ns)" in out, out
    assert f"  Last pulse     : " in out and f"({_t(ROWS - 1)} ns)" in out, out
    delta_ms = (_t(0) - ANCHOR) / 1e6
    assert f"  Time           : cardclock-v1: corrected, {delta_ms:+.3f} ms vs the count (tracking, +/-61 us)\n" in out, out


def test_a_file_without_a_time_map_shows_what_it_always_did(tmp_path, capsys):
    path = _write(tmp_path / "plain.hdf5", corrected=False)
    with File(str(path)) as f:
        assert absolute_time_ns(f, 7) == ANCHOR + 7 * 500_000
        assert line_rate_hz(f) == 2_000.0
    assert cli.main(["info", str(path)]) == 0
    out = capsys.readouterr().out
    assert f"({ANCHOR} ns)" in out
    assert "  Time           :" not in out


def test_a_legacy_file_has_no_absolute_time():
    class _H:
        acquisition_ns = 0

    class _F:
        header = _H()
        timestamp = "Thu Sep 12 13:06:24 2024"

    assert absolute_time_ns(_F(), 3) is None
    assert cli._format_time_correction(_F()) is None
