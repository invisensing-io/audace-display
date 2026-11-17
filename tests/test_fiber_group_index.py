"""The fibre group index: every metre at the speed of light in the fibre the
file states (invisensing >= 1.7.0), shown by ``info``, and
``--fiber-group-index N`` to label a recording at another index.

At 1.4682 (SMF-28 at 1550 nm) light travels at 204 190 477 m/s: one position
at 250 MS/s is 0.408381 m, 2.0952 % longer than the legacy 0.4 m (2e8 m/s,
index 1.49896229) every recording made before the acquisition wrote it was
labelled at.
"""
from __future__ import annotations

import json

import numpy as np
import pytest
from invisensing import File, export_dat

from audace_display import cli, gui_worker, reader
from audace_display.channels import resolve_channel
from audace_display.cli import main
from audace_display.demod import load_demodulator

SMF28 = 204_190_477
LEGACY = 200_000_000
POSITIONS = 64
PULSES = 200


@pytest.fixture
def smf28_h5(tmp_path):
    """A v1 phase (content type 19) at 250 MS/s, its metres at 1.4682."""
    data = np.linspace(-1, 1, PULSES * POSITIONS, dtype=np.float32).reshape(PULSES, POSITIONS)
    path = tmp_path / "phase_smf28.dat"
    export_dat(path, data, sample_rate=250_000_000, trig_frequency=1000, content_type=19,
               card="p21", gauge_length_m=24 * SMF28 / (2 * 250_000_000), fiber_group_index=1.4682)
    return path


@pytest.fixture
def legacy_dat(tmp_path):
    """The same phase recorded before the speed existed: 2e8 m/s."""
    data = np.linspace(-1, 1, PULSES * POSITIONS, dtype=np.float32).reshape(PULSES, POSITIONS)
    path = tmp_path / "phase_legacy.dat"
    export_dat(path, data, sample_rate=250_000_000, trig_frequency=1000, content_type=19,
               card="p21", gauge_length_m=10.0)
    return path


def _info(argv, capsys) -> str:
    assert main(argv) == 0
    return capsys.readouterr().out


def test_info_prints_the_file_s_index(smf28_h5, capsys):
    out = _info(["info", str(smf28_h5)], capsys)
    assert "  Fiber index    : 1.4682 (204,190,477 m/s)\n" in out
    assert "  Spatial step   : 0.408 m\n" in out
    assert f"  Fiber length   : {POSITIONS * SMF28 / 5e8:.2f} m\n" in out
    assert "  Gauge length   : 9.80114 m\n" in out


def test_info_of_a_legacy_recording(legacy_dat, capsys):
    out = _info(["info", str(legacy_dat)], capsys)
    assert "  Fiber index    : 1.49896229 (200,000,000 m/s; legacy: the file states none)\n" in out
    assert "  Spatial step   : 0.400 m\n" in out
    assert f"  Fiber length   : {POSITIONS * 0.4:.2f} m\n" in out


def test_info_of_a_relabelled_recording(legacy_dat, capsys):
    out = _info(["info", str(legacy_dat), "--fiber-group-index", "1.4682"], capsys)
    assert ("  Fiber index    : 1.4682 (204,190,477 m/s; relabelled, the file says 1.49896229)\n"
            in out)
    assert "  Spatial step   : 0.408 m\n" in out
    assert f"  Fiber length   : {POSITIONS * SMF28 / 5e8:.2f} m\n" in out
    # 25 positions of 0.4 m, at the new speed.
    assert f"  Gauge length   : {25 * SMF28 / 5e8:g} m\n" in out


@pytest.mark.parametrize("bad", ["0.5", "0", "-1.4682", "100.5", "nan", "inf"])
def test_an_index_outside_1_to_100_is_refused(legacy_dat, capsys, bad):
    assert main(["heatmap", str(legacy_dat), "--fiber-group-index", bad, "--save", "x.png"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR: --fiber-group-index: fibre group index ")
    assert "must be a number from 1 to 100" in err


@pytest.mark.parametrize("ok", ["1", "1.4682", "100"])
def test_any_index_from_1_to_100_is_taken(legacy_dat, capsys, ok):
    out = _info(["info", str(legacy_dat), "--fiber-group-index", ok], capsys)
    assert f"  Fiber index    : {float(ok)!r} (" in out


@pytest.mark.parametrize(
    "cmd", ["auto", "info", "heatmap", "bandheatmaps", "fft", "trace", "scope", "inspect", "demod"]
)
def test_every_view_offers_the_option(cmd, capsys):
    with pytest.raises(SystemExit):
        main([cmd, "--help"])
    assert "--fiber-group-index" in capsys.readouterr().out


def test_the_distance_axis_and_window_follow_the_index(legacy_dat, tmp_path):
    """The waterfall's distance extent and a --start-distance window are in
    the relabelled metres; the samples are the same."""
    args = cli.build_parser().parse_args(["heatmap", str(legacy_dat), "--fiber-group-index", "1.4682"])
    with cli._open(args) as f:
        assert reader.position_step_m(f) == pytest.approx(SMF28 / 5e8, rel=1e-15)
        _, resolver, _, _ = resolve_channel(None, f.mode)
        relabelled = reader.load_decimated(f, lambda raw: resolver(f, raw),
                                           max_time_bins=PULSES, max_space_bins=POSITIONS,
                                           start_distance=10.0)
    with File(legacy_dat) as f:
        _, resolver, _, _ = resolve_channel(None, f.mode)
        plain = reader.load_decimated(f, lambda raw: resolver(f, raw),
                                      max_time_bins=PULSES, max_space_bins=POSITIONS,
                                      start_distance=10.0)
    # 10 m is position 25 at 0.4 m, position 24 at 0.408381 m.
    assert plain.d_extent[0] == pytest.approx(25 * 0.4)
    assert relabelled.d_extent[0] == pytest.approx(24 * SMF28 / 5e8)
    assert relabelled.d_extent[1] == pytest.approx(POSITIONS * SMF28 / 5e8)
    # The same samples: the relabelled window starts one position earlier.
    np.testing.assert_array_equal(relabelled.data[:, 1:], plain.data)


def test_a_heatmap_png_of_a_relabelled_recording(legacy_dat, tmp_path):
    out = tmp_path / "relabelled.png"
    assert main(["heatmap", str(legacy_dat), "--fiber-group-index", "1.4682", "--save", str(out)]) == 0
    assert out.stat().st_size > 0


PLUGIN = '''
import json
import numpy as np
def demodulate(chunk, *, meta):
    with open(__file__ + ".json", "w") as fp:
        json.dump({k: meta[k] for k in ("light_speed_in_fiber_m_s", "fiber_group_index",
                                        "spatial_step_m")}, fp)
    return chunk.astype(np.float32)
'''


def test_a_plugin_gets_the_speed(tmp_path, smf28_h5, legacy_dat):
    script = tmp_path / "plugin.py"
    script.write_text(PLUGIN)
    for path, kw, speed, index in [
        (smf28_h5, {}, SMF28, 1.4682),
        (legacy_dat, {}, LEGACY, 1.49896229),
        (legacy_dat, {"fiber_group_index": 1.4682}, SMF28, 1.4682),
    ]:
        with File(path, **kw) as f:
            plugin = load_demodulator(script, f)
            plugin.transform(f.read_lines(2))
        meta = json.loads((tmp_path / "plugin.py.json").read_text())
        assert meta["light_speed_in_fiber_m_s"] == speed
        assert meta["fiber_group_index"] == index
        assert meta["spatial_step_m"] == pytest.approx(speed / 5e8, rel=1e-15)


def test_the_viewer_shows_the_index_but_does_not_offer_the_option(smf28_h5):
    schema = gui_worker.view_schema()
    for view in schema.values():
        assert "fiber_group_index" not in {o["dest"] for o in view["options"]}
    assert "fiber_group_index" in gui_worker.HIDDEN_OPTIONS
