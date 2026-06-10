import numpy as np
import pytest

from audace_display import decimate
from audace_display._errors import AudaceDisplayError


def test_bin_edges_strictly_increasing():
    for n, nb in [(100, 10), (97, 13), (64, 64), (10, 3)]:
        edges = decimate.bin_edges(n, nb)
        assert edges[0] == 0 and edges[-1] == n
        assert np.all(np.diff(edges) >= 1)  # strictly increasing
        assert len(edges) == min(nb, n) + 1


def test_reduce_cols_mean_matches_blockmean():
    arr = np.arange(4 * 12, dtype=np.float64).reshape(4, 12)
    edges = decimate.bin_edges(12, 3)  # 3 bins of 4 columns
    out = decimate.reduce_cols(arr, edges, op="mean")
    assert out.shape == (4, 3)
    expected = arr.reshape(4, 3, 4).mean(axis=2)
    np.testing.assert_allclose(out, expected)


def test_reduce_cols_peak_is_max_abs():
    arr = np.array([[-5.0, 2.0, 1.0, -1.0]])
    edges = decimate.bin_edges(4, 2)
    out = decimate.reduce_cols(arr, edges, op="peak")
    np.testing.assert_allclose(out, [[5.0, 1.0]])


def test_reduce_cols_unknown_op():
    with pytest.raises(AudaceDisplayError):
        decimate.reduce_cols(np.zeros((1, 4)), decimate.bin_edges(4, 2), op="nope")


def test_minmax_envelope_preserves_extremes():
    x = np.sin(np.linspace(0, 20 * np.pi, 10_000)).astype(np.float32)
    centers, lo, hi = decimate.minmax_envelope(x, 200)
    assert lo.shape == hi.shape == (200,)
    # the overall envelope must contain the signal's extremes
    assert hi.max() >= x.max() - 1e-6
    assert lo.min() <= x.min() + 1e-6
    assert np.all(lo <= hi)


def test_minmax_envelope_passthrough_when_small():
    x = np.arange(50, dtype=np.float32)
    centers, lo, hi = decimate.minmax_envelope(x, 200)
    np.testing.assert_array_equal(lo, x)
    np.testing.assert_array_equal(hi, x)


def test_peak_line_preserves_extremes():
    x = np.zeros(3001, dtype=np.float32)
    x[500] = 5.0
    x[1500] = -7.0
    xi, yv = decimate.peak_line(x, 100)
    assert yv.shape[0] == 200            # 2 points (min,max) per bin
    assert xi.shape[0] == 200
    assert yv.max() >= 5.0 - 1e-6
    assert yv.min() <= -7.0 + 1e-6


def test_peak_line_passthrough_when_small():
    x = np.arange(50, dtype=np.float32)
    xi, yv = decimate.peak_line(x, 100)
    np.testing.assert_array_equal(yv, x)
    np.testing.assert_array_equal(xi, np.arange(50))


@pytest.mark.parametrize("op", ["mean", "rms", "std", "variance", "peak"])
def test_time_accumulator_single_bin(op):
    # 1 time bin covering all lines: compare against direct numpy
    rows, cols = 40, 8
    rng = np.random.default_rng(42)
    data = rng.standard_normal((rows, cols))
    acc = decimate.TimeBinAccumulator(n_time_bins=1, n_space_bins=cols, t_factor=rows, op=op)
    # feed in 3 chunks
    for chunk in (data[:13], data[13:27], data[27:]):
        acc.add(chunk)
    out = acc.result()
    assert out.shape == (1, cols)
    if op == "mean":
        np.testing.assert_allclose(out[0], data.mean(axis=0), rtol=1e-5)
    elif op == "rms":
        np.testing.assert_allclose(out[0], np.sqrt((data ** 2).mean(axis=0)), rtol=1e-5)
    elif op == "std":
        np.testing.assert_allclose(out[0], data.std(axis=0), rtol=1e-5)
    elif op == "variance":
        np.testing.assert_allclose(out[0], data.var(axis=0), rtol=1e-5)
    else:  # peak
        np.testing.assert_allclose(out[0], np.abs(data).max(axis=0), rtol=1e-5)


def test_variance_is_std_squared():
    # variance must be exactly (std)^2 on the same chunked accumulation
    rows, cols, t_factor = 60, 5, 12
    rng = np.random.default_rng(11)
    data = rng.standard_normal((rows, cols))
    n_time = (rows + t_factor - 1) // t_factor

    def run(op):
        acc = decimate.TimeBinAccumulator(n_time, cols, t_factor, op)
        acc.add(data)
        return acc.result()

    np.testing.assert_allclose(run("variance"), run("std") ** 2, rtol=1e-5, atol=1e-7)


def test_time_accumulator_multi_bin_chunk_independence():
    # chunk splitting must not change the result
    rows, cols, t_factor = 100, 4, 10
    rng = np.random.default_rng(7)
    data = rng.standard_normal((rows, cols))
    n_time = (rows + t_factor - 1) // t_factor

    def run(chunk_sizes):
        acc = decimate.TimeBinAccumulator(n_time, cols, t_factor, "mean")
        i = 0
        for cs in chunk_sizes:
            acc.add(data[i:i + cs])
            i += cs
        return acc.result()

    a = run([100])
    b = run([7, 13, 30, 50])
    np.testing.assert_allclose(a, b, rtol=1e-6)
    # check the first bin = mean of the first 10 lines
    np.testing.assert_allclose(a[0], data[:t_factor].mean(axis=0), rtol=1e-6)
