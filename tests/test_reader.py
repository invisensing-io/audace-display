import numpy as np
import pytest
from invisensing import File, Mode

from audace_display import reader
from audace_display.channels import resolve_channel
from audace_display._errors import AudaceDisplayError
from conftest import N_PULSES, POSITIONS, TRIG


def _transform(f):
    _, resolver, _, _ = resolve_channel(None, f.mode)
    return lambda raw: resolver(f, raw)


def test_modes_detected(raw_dat, iq_dat, arctan_dat, phase_dat):
    assert File(str(raw_dat)).mode is Mode.RAW
    assert File(str(iq_dat)).mode is Mode.IQ
    assert File(str(arctan_dat)).mode is Mode.ARCTAN_MAGNITUDE
    assert File(str(phase_dat)).mode is Mode.PHASE


def test_load_decimated_shape_and_extent(raw_dat):
    with File(str(raw_dat)) as f:
        res = reader.load_decimated(f, _transform(f), max_time_bins=50, max_space_bins=32)
        assert res.data.shape[0] <= 50
        assert res.data.shape[1] <= 32
        assert res.t_extent[0] == 0.0
        assert res.t_extent[1] == pytest.approx(N_PULSES / TRIG)
        assert res.n_positions == POSITIONS


def test_decimated_no_downsample_equals_mean(raw_dat):
    # max bins >= dimensions -> no decimation: must equal the per-pulse mean
    with File(str(raw_dat)) as f:
        res = reader.load_decimated(
            f, _transform(f), max_time_bins=N_PULSES, max_space_bins=POSITIONS
        )
        assert res.t_factor == 1
        assert res.data.shape == (N_PULSES, POSITIONS)
    # reload raw to compare
    with File(str(raw_dat)) as f:
        _, resolver, _, _ = resolve_channel(None, f.mode)
        full = resolver(f, f.read_lines(N_PULSES))
    np.testing.assert_allclose(res.data, full, rtol=1e-4, atol=1e-4)


def test_chunk_independence(iq_dat):
    # Chunk splitting must not change the result: force a small chunk.
    import audace_display.reader as r
    orig = r._CHUNK_BYTES
    try:
        with File(str(iq_dat)) as f:
            big = reader.load_decimated(f, _transform(f), max_time_bins=37, max_space_bins=16)
        r._CHUNK_BYTES = 8 * 1024  # force several chunks
        with File(str(iq_dat)) as f:
            small = reader.load_decimated(f, _transform(f), max_time_bins=37, max_space_bins=16)
        np.testing.assert_allclose(big.data, small.data, rtol=1e-4, atol=1e-4)
    finally:
        r._CHUNK_BYTES = orig


def test_load_columns_window(raw_dat):
    with File(str(raw_dat)) as f:
        data, eff = reader.load_columns(
            f, _transform(f), [POSITIONS // 2],
            start_time=None, duration=0.1,  # 0.1 s @ 1000 Hz = 100 pulses
        )
        assert data.shape == (100, 1)
        assert eff == TRIG


def test_load_columns_subsample(raw_dat):
    with File(str(raw_dat)) as f:
        data, eff = reader.load_columns(f, _transform(f), [0], subsample_time=5)
        assert data.shape[0] == (N_PULSES + 4) // 5
        assert eff == TRIG / 5


def test_empty_time_window(raw_dat):
    with File(str(raw_dat)) as f:
        with pytest.raises(AudaceDisplayError):
            reader.load_decimated(f, _transform(f), start_time=999.0)


def test_position_step(raw_dat):
    with File(str(raw_dat)) as f:
        step = reader.position_step_m(f)
        assert step == pytest.approx(f.distance / POSITIONS)
