"""Decimation kernels (numpy).

To display files from 200 MB to 10 GB without loading everything, we reduce the
data to screen resolution. Two primitives:

- :func:`reduce_cols` -- reduce the spatial axis (positions) by *binning*.
- :func:`minmax_envelope` -- extrema-preserving decimation for line plots
  (avoids visual aliasing when drawing millions of points).

Plus :class:`TimeBinAccumulator`, an **incremental** reducer of the time axis,
fed chunk by chunk during streaming (RAM independent of file size).
"""
from __future__ import annotations

import numpy as np

from ._errors import AudaceDisplayError

REDUCERS = ("mean", "rms", "std", "peak")


def bin_edges(n: int, n_bins: int) -> np.ndarray:
    """Integer edges of ``n_bins`` near-equal *bins* covering ``[0, n)``.

    Returns an array of length ``n_bins + 1``, strictly increasing as long as
    ``n_bins <= n`` (guaranteed by the caller).
    """
    if n_bins < 1:
        raise AudaceDisplayError("n_bins must be >= 1.")
    n_bins = min(n_bins, n)
    return np.linspace(0, n, n_bins + 1).astype(np.int64)


def reduce_cols(arr: np.ndarray, edges: np.ndarray, op: str = "mean") -> np.ndarray:
    """Reduce ``arr`` ``(rows, width)`` along columns according to ``edges``.

    ``op`` in {``mean``, ``sum``, ``peak``}. ``peak`` = max of the absolute value.
    Returns ``(rows, len(edges) - 1)``.
    """
    starts = edges[:-1]
    if op in ("mean", "sum"):
        s = np.add.reduceat(arr, starts, axis=1)
        if op == "sum":
            return s
        counts = np.diff(edges).astype(np.float64)
        return s / counts
    if op == "peak":
        return np.maximum.reduceat(np.abs(arr), starts, axis=1)
    raise AudaceDisplayError(f"unknown reduction operator: '{op}'.")


def minmax_envelope(x: np.ndarray, n_out: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Min/max-preserving decimation of a 1-D signal.

    Returns ``(centers, lo, hi)``: the bins' center indices and the per-bin
    min/max. If ``len(x) <= n_out``, returns the signal as-is (``lo == hi == x``).
    """
    x = np.asarray(x)
    n = x.shape[0]
    if n_out < 1:
        raise AudaceDisplayError("n_out must be >= 1.")
    if n <= n_out:
        idx = np.arange(n, dtype=np.float64)
        return idx, x.astype(np.float32), x.astype(np.float32)
    edges = bin_edges(n, n_out)
    starts = edges[:-1]
    lo = np.minimum.reduceat(x, starts)
    hi = np.maximum.reduceat(x, starts)
    centers = (edges[:-1] + (edges[1:] - 1)) / 2.0
    return centers, lo.astype(np.float32), hi.astype(np.float32)


def peak_line(y: np.ndarray, n_out: int) -> tuple[np.ndarray, np.ndarray]:
    """Decimate a 1-D signal for a line plot while preserving extrema.

    Returns ``(x_idx, y)`` where each bin yields two points (min then max) -> an
    "oscilloscope" polyline of ``2*n_out`` points. If the signal already fits in
    ``2*n_out`` points, returns it as-is.
    """
    y = np.asarray(y)
    n = y.shape[0]
    if n_out < 1:
        raise AudaceDisplayError("n_out must be >= 1.")
    if n <= 2 * n_out:
        return np.arange(n, dtype=np.float64), y.astype(np.float32)
    edges = bin_edges(n, n_out)
    starts = edges[:-1]
    mins = np.minimum.reduceat(y, starts)
    maxs = np.maximum.reduceat(y, starts)
    centers = (edges[:-1] + edges[1:] - 1) / 2.0
    x = np.repeat(centers, 2)
    out = np.empty(2 * centers.shape[0], dtype=np.float32)
    out[0::2] = mins
    out[1::2] = maxs
    return x, out


class TimeBinAccumulator:
    """Incremental reducer of the time axis.

    Lines (pulses) are supplied in order via :meth:`add`. Each time bin ``b``
    covers pulses ``[b*t_factor, (b+1)*t_factor)``. Statistics are accumulated in
    ``float64`` for numerical stability.

    ``op`` in :data:`REDUCERS`. For ``peak``, the input is assumed to be already
    reduced to absolute value on the spatial side (see ``space_op`` in the reader).
    """

    def __init__(self, n_time_bins: int, n_space_bins: int, t_factor: int, op: str):
        if op not in REDUCERS:
            raise AudaceDisplayError(
                f"unknown reducer: '{op}'. Known: {', '.join(REDUCERS)}."
            )
        self.op = op
        self.t_factor = int(t_factor)
        self.n_time = int(n_time_bins)
        self.n_space = int(n_space_bins)
        shape = (self.n_time, self.n_space)
        self._sum = np.zeros(shape, np.float64) if op in ("mean", "rms", "std") else None
        self._sumsq = np.zeros(shape, np.float64) if op in ("rms", "std") else None
        self._peak = np.zeros(shape, np.float64) if op == "peak" else None
        self._count = np.zeros(self.n_time, np.int64)
        self._row = 0  # global count of consumed lines

    def add(self, block: np.ndarray) -> None:
        """Accumulate a block ``(rows, n_space)`` of consecutive lines."""
        r = block.shape[0]
        if r == 0:
            return
        block = block.astype(np.float64, copy=False)
        tb = (np.arange(self._row, self._row + r) // self.t_factor)
        # A block's lines fall into contiguous, increasing time bins: split at
        # each bin change.
        change = np.flatnonzero(np.diff(tb)) + 1
        for seg in np.split(np.arange(r), change):
            b = int(tb[seg[0]])
            if b >= self.n_time:  # guard (partial last bin)
                b = self.n_time - 1
            sub = block[seg]
            if self._sum is not None:
                self._sum[b] += sub.sum(axis=0)
            if self._sumsq is not None:
                self._sumsq[b] += np.square(sub).sum(axis=0)
            if self._peak is not None:
                np.maximum(self._peak[b], np.abs(sub).max(axis=0), out=self._peak[b])
            self._count[b] += seg.size
        self._row += r

    def result(self) -> np.ndarray:
        """Final ``(n_time, n_space)`` float32 array."""
        cnt = np.maximum(self._count, 1)[:, None]
        if self.op == "mean":
            out = self._sum / cnt
        elif self.op == "rms":
            out = np.sqrt(self._sumsq / cnt)
        elif self.op == "std":
            mean = self._sum / cnt
            var = self._sumsq / cnt - np.square(mean)
            out = np.sqrt(np.maximum(var, 0.0))
        else:  # peak
            out = self._peak
        return out.astype(np.float32)
