"""Files of the Audace data format v1: one quantity per file, described by
the content type alone (no FLOAT / UNSIGNED / INTERLEAVED flag).

The mode, the default channel and the default view come from the content
type (invisensing >= 1.4.0); ``info`` prints what the header says.
"""
from __future__ import annotations

import numpy as np
import pytest
from invisensing import File, Mode, export_dat

from audace_display import cli, reader
from audace_display.channels import resolve_channel

N, P = 40, 16
RATE = 62_500_000  # 1.6 m per position
TRIG = 50


def _write(path, data, content_type, **kw):
    path.parent.mkdir(parents=True, exist_ok=True)
    export_dat(path, data, sample_rate=RATE, trig_frequency=TRIG, range_v=1.0,
               content_type=content_type, card="p21", **kw)
    return path


@pytest.fixture
def v1(tmp_path):
    rng = np.random.default_rng(7)
    phase = rng.standard_normal((N, P)).astype(np.float32)
    files = {
        "phase": _write(tmp_path / "phase" / "s.dat", phase, 19),
        "amplitude": _write(tmp_path / "amplitude" / "s.dat",
                            np.abs(phase).astype(np.float32) * 1000, 16),
        "magnitude": _write(tmp_path / "magnitude" / "s.dat",
                            np.full((N, P), 40_000, np.uint16), 5),
        "arctan": _write(tmp_path / "arctan" / "s.dat",
                         np.full((N, P), 16_384, np.int16), 3),
        "front_end": _write(tmp_path / "sac" / "amplitude" / "s.dat",
                            np.full((N, P), 7.5, np.float32), 11),
        "i": _write(tmp_path / "i_component" / "s.dat",
                    np.full((N, P), 3_277, np.int16), 13),
        "custom": _write(tmp_path / "custom.dat", phase, 26),
        "variance": _write(tmp_path / "viz_variance.dat",
                           np.full((N, P), 99.0, np.float32), 27,
                           ),
        "log_variance": _write(tmp_path / "viz_logvar.dat",
                               np.full((N, P), 2.0, np.float32), 28),
        "raw": _write(tmp_path / "raw.dat", np.zeros((N, P), np.int16), 1),
    }
    return files


@pytest.mark.parametrize(
    "name,mode,channel,heatmap",
    [
        ("phase", Mode.PHASE, "phase", True),
        ("amplitude", Mode.AMPLITUDE, "magnitude", True),
        ("magnitude", Mode.AMPLITUDE, "magnitude", True),
        ("arctan", Mode.ARCTAN, "arctan", True),
        ("front_end", Mode.AMPLITUDE, "magnitude", True),
        ("i", Mode.I, "i", True),
        ("custom", Mode.CUSTOM, "samples", True),
        ("variance", Mode.VARIANCE, "variance", True),
        ("log_variance", Mode.LOG_VARIANCE, "log_variance", True),
        ("raw", Mode.RAW, "raw", False),
    ],
)
def test_each_v1_file_opens_on_its_own_channel(v1, tmp_path, name, mode, channel, heatmap):
    with File(v1[name]) as f:
        assert f.flags == 0 and f.format_version == 1
        assert f.mode is mode
        canonical, resolver, label, _ = resolve_channel(None, f.mode, f)
        assert canonical == channel
        data = resolver(f, f.read_lines(N))
        assert data.shape == (N, P) and data.dtype == np.float32
    if heatmap:
        png = tmp_path / f"{name}.png"
        assert cli.main([str(v1[name]), "--save", str(png)]) == 0
        assert png.stat().st_size > 0


def test_the_physical_values(v1):
    with File(v1["magnitude"]) as f:
        _, resolver, label, _ = resolve_channel(None, f.mode, f)
        assert label == "|signal| (V)"
        assert resolver(f, f.read_lines(1))[0, 0] == pytest.approx(40_000 / 32_768)
    with File(v1["front_end"]) as f:
        # A software front end's amplitude is not on the ADC's scale: as stored.
        _, resolver, label, _ = resolve_channel(None, f.mode, f)
        assert label == "amplitude (front-end count)"
        assert resolver(f, f.read_lines(1))[0, 0] == 7.5
    with File(v1["arctan"]) as f:
        _, resolver, _, angular = resolve_channel(None, f.mode, f)
        assert angular and resolver(f, f.read_lines(1))[0, 0] == pytest.approx(np.pi / 2)
    with File(v1["i"]) as f:
        _, resolver, _, _ = resolve_channel(None, f.mode, f)
        assert resolver(f, f.read_lines(1))[0, 0] == pytest.approx(3_277 / 32_768)


def test_variance_and_its_log_scale(v1):
    with File(v1["log_variance"]) as f:
        _, resolver, _, _ = resolve_channel("variance", f.mode, f)
        assert resolver(f, f.read_lines(1))[0, 0] == pytest.approx(99.0)
    with File(v1["variance"]) as f:
        _, resolver, label, _ = resolve_channel("log_variance", f.mode, f)
        assert label == "log10(1 + variance)"
        assert resolver(f, f.read_lines(1))[0, 0] == pytest.approx(2.0)
        # Its spatial step is its own (a zone spacing would be; here none was
        # given: not derived from the sample rate for a zone variance).
        assert f.spatial_step_m is None


def test_info_says_what_the_header_says(v1, capsys):
    assert cli.main(["info", str(v1["phase"])]) == 0
    out = capsys.readouterr().out
    assert "  Mode           : phase\n" in out
    assert "  Content type   : dui_coherent_phase\n" in out
    assert "  Quantity       : phase (rad)\n" in out
    assert "  Format version : 1\n" in out
    assert "  Card           : p21\n" in out
    assert "  Duration       : 0.800 s\n" in out  # under one second: not written
    assert "  Spatial step   : 1.600 m\n" in out
    assert "  Line period    : 20 ms\n" in out
    assert "  Flags          : 0x000 (0)\n" in out


def test_info_names_the_other_half_of_a_split_pair(tmp_path, capsys):
    phase = np.zeros((2 * TRIG + 10, P), np.float32)
    a = _write(tmp_path / "phase" / "x.dat", phase, 17)
    b = _write(tmp_path / "amplitude" / "x.dat", phase, 16)
    assert cli.main(["info", str(a)]) == 0
    out = capsys.readouterr().out
    assert f"  Other half     : {b}\n" in out
    assert "  Duration       : 2.200 s (file: 2 s)\n" in out


def test_the_spatial_axis_follows_the_files_step(v1):
    with File(v1["phase"]) as f:
        assert reader.position_step_m(f) == pytest.approx(1.6)
        assert f.distance == pytest.approx(P * 1.6)
