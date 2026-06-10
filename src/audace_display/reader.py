"""*Streaming* loading from :class:`invisensing.File`.

All reading goes through invisensing (Rust core: I/O, header parsing,
de-interleaving). Here we only orchestrate **chunked** reading and **decimation**
to screen resolution, to handle files from 200 MB to 10 GB in bounded RAM.

Two entry points:

- :func:`load_decimated` -- decimated 2-D waterfall (heatmap).
- :func:`load_columns`   -- full-resolution time series at a few positions
  (FFT, trace).

``transform`` is a callable ``raw_chunk (rows, line_size) -> (rows, positions)``
``float32``: either a channel resolver (:mod:`audace_display.channels`) or an
external demod plugin (:mod:`audace_display.demod`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

import numpy as np

from . import decimate
from ._errors import AudaceDisplayError

Transform = Callable[[np.ndarray], np.ndarray]

#: Target memory size of a read chunk (~size of the resolved f32 window).
_CHUNK_BYTES = 256 * 1024 * 1024
#: Chunk size for the read-and-discard skip when `seek_lines` is absent.
_DISCARD_CHUNK = 1 << 20
#: Hard ceiling (float32 cells) for the in-RAM matrix of :func:`load_time_matrix`.
#: Band analysis needs the full time series resident, so this caps the request.
_TIME_MATRIX_MAX_CELLS = 2 * 1024 * 1024 * 1024 // 4  # 2 GiB of float32


def position_step_m(f, n_positions: Optional[int] = None) -> float:
    """Spatial step (m) between consecutive positions.

    ``n_positions`` lets the count be overridden (output of a demod plugin); by
    default ``f.positions_per_line`` is used.
    """
    n = n_positions if n_positions is not None else f.positions_per_line
    return f.distance / max(n, 1)


def _chunk_pulses(line_size: int) -> int:
    return max(1, _CHUNK_BYTES // max(line_size * 4, 1))


def _pulse_range(
    f, start_time: Optional[float], duration: Optional[float]
) -> tuple[int, int]:
    trig = f.trig_frequency
    total = f.num_lines
    p0 = 0 if start_time is None else int(start_time * trig)
    p0 = max(0, min(p0, total))
    if duration is not None:
        p1 = min(total, p0 + int(duration * trig))
    else:
        p1 = total
    if p1 <= p0:
        raise AudaceDisplayError(
            f"empty time range after --start-time/--duration "
            f"(p0={p0}, p1={p1}, pulses={total})."
        )
    return p0, p1


def time_window_pulses(
    f,
    start_time: Optional[float],
    duration: Optional[float],
    max_pulses: Optional[int] = None,
) -> tuple[int, int]:
    """Pulse interval ``[p0, p1)`` corresponding to the requested window."""
    p0, p1 = _pulse_range(f, start_time, duration)
    if max_pulses is not None:
        p1 = min(p1, p0 + max_pulses)
    return p0, p1


def _distance_range(
    start_distance: Optional[float],
    end_distance: Optional[float],
    d_step: float,
    n_positions: int,
) -> tuple[int, int]:
    d0 = 0 if start_distance is None else int(start_distance / d_step)
    d0 = max(0, min(d0, n_positions - 1))
    if end_distance is not None:
        d1 = min(n_positions, max(d0 + 1, int(end_distance / d_step)))
    else:
        d1 = n_positions
    if d1 <= d0:
        raise AudaceDisplayError(
            f"empty spatial slice after --start-distance/--end-distance "
            f"(d0={d0}, d1={d1}, positions={n_positions})."
        )
    return d0, d1


def _advance_to(f, p0: int) -> None:
    """Move the cursor to pulse ``p0`` (seek if available, else read-discard)."""
    if p0 <= 0:
        return
    seek = getattr(f, "seek_lines", None)
    if callable(seek):
        seek(p0)
        return
    remaining = p0
    while remaining > 0:
        got = f.read_lines(min(remaining, _DISCARD_CHUNK))
        if got.shape[0] == 0:
            break
        remaining -= got.shape[0]


@dataclass
class DecimatedResult:
    """Result of a 2-D decimation + axis metadata."""

    data: np.ndarray            # (n_time, n_space) float32
    t_extent: tuple[float, float]   # (t0, t1) seconds
    d_extent: tuple[float, float]   # (d0, d1) meters
    t_factor: int               # pulses aggregated per time bin
    d_factor: float             # positions aggregated per spatial bin (mean)
    n_positions: int            # positions available after transform


def load_decimated(
    f,
    transform: Transform,
    *,
    max_time_bins: int = 2000,
    max_space_bins: int = 2000,
    reduce: str = "mean",
    start_time: Optional[float] = None,
    duration: Optional[float] = None,
    start_distance: Optional[float] = None,
    end_distance: Optional[float] = None,
    max_pulses: Optional[int] = None,
) -> DecimatedResult:
    """Decimated waterfall, computed in a single streaming pass.

    Space is *pooled* by mean (or max|.| if ``reduce='peak'``); time is
    aggregated by ``reduce`` in :data:`audace_display.decimate.REDUCERS`.
    """
    p0, p1 = _pulse_range(f, start_time, duration)
    if max_pulses is not None:
        p1 = min(p1, p0 + max_pulses)
    total_p = p1 - p0

    space_op = "peak" if reduce == "peak" else "mean"
    _advance_to(f, p0)
    chunk = _chunk_pulses(f.line_size)

    acc: Optional[decimate.TimeBinAccumulator] = None
    geom: dict = {}
    read = 0
    while read < total_p:
        raw = f.read_lines(min(chunk, total_p - read))
        if raw.shape[0] == 0:
            break
        resolved = np.asarray(transform(raw))
        if resolved.ndim != 2:
            raise AudaceDisplayError(
                f"the transform must return a 2-D array, got {resolved.shape}."
            )

        if acc is None:  # first iteration: lock the geometry
            n_pos = resolved.shape[1]
            d_step = position_step_m(f, n_pos)
            d0, d1 = _distance_range(start_distance, end_distance, d_step, n_pos)
            width = d1 - d0
            n_space = min(max_space_bins, width)
            col_edges = decimate.bin_edges(width, n_space)
            t_factor = max(1, math.ceil(total_p / max_time_bins))
            n_time = math.ceil(total_p / t_factor)
            acc = decimate.TimeBinAccumulator(n_time, n_space, t_factor, reduce)
            geom = dict(
                d0=d0, d1=d1, d_step=d_step, n_space=n_space,
                col_edges=col_edges, t_factor=t_factor, n_pos=n_pos, width=width,
            )

        sub = resolved[:, geom["d0"]:geom["d1"]]
        cols = decimate.reduce_cols(sub, geom["col_edges"], op=space_op)
        acc.add(cols)
        read += raw.shape[0]

    if acc is None:
        raise AudaceDisplayError("no data read.")

    data = acc.result()
    trig = f.trig_frequency
    d_step = geom["d_step"]
    return DecimatedResult(
        data=data,
        t_extent=(p0 / trig, p1 / trig),
        d_extent=(geom["d0"] * d_step, geom["d1"] * d_step),
        t_factor=geom["t_factor"],
        d_factor=geom["width"] / max(geom["n_space"], 1),
        n_positions=geom["n_pos"],
    )


@dataclass
class TimeMatrixResult:
    """Full-time-resolution, spatially-binned matrix + axis metadata.

    Unlike :class:`DecimatedResult`, the **time axis is not reduced**: ``data`` is
    ``(n_pulses, n_space)``, suitable for a per-position temporal FFT (band
    decomposition). Memory is ``n_pulses * n_space * 4`` bytes.
    """

    data: np.ndarray                # (n_pulses, n_space) float32
    trig: float                     # effective pulse rate (Hz)
    t_extent: tuple[float, float]   # (t0, t1) seconds
    d_extent: tuple[float, float]   # (d0, d1) meters
    n_positions: int                # positions available after transform


def load_time_matrix(
    f,
    transform: Transform,
    *,
    max_space_bins: int = 1000,
    start_time: Optional[float] = None,
    duration: Optional[float] = None,
    start_distance: Optional[float] = None,
    end_distance: Optional[float] = None,
    max_pulses: Optional[int] = None,
    max_cells: int = _TIME_MATRIX_MAX_CELLS,
) -> TimeMatrixResult:
    """Spatially-binned matrix at **full temporal resolution** (streamed read).

    Space is pooled by mean to ``max_space_bins`` columns; time keeps every pulse
    so a temporal FFT can decompose it into frequency bands. Raises if the
    requested window would exceed ``max_cells`` float32 cells -- restrict it with
    ``start_time``/``duration``/``max_pulses`` or a smaller ``max_space_bins``.
    """
    p0, p1 = _pulse_range(f, start_time, duration)
    if max_pulses is not None:
        p1 = min(p1, p0 + max_pulses)
    total_p = p1 - p0

    _advance_to(f, p0)
    chunk = _chunk_pulses(f.line_size)

    parts: list[np.ndarray] = []
    geom: dict = {}
    read = 0
    while read < total_p:
        raw = f.read_lines(min(chunk, total_p - read))
        if raw.shape[0] == 0:
            break
        resolved = np.asarray(transform(raw))
        if resolved.ndim != 2:
            raise AudaceDisplayError(
                f"the transform must return a 2-D array, got {resolved.shape}."
            )

        if not geom:  # first iteration: lock the geometry + bound the memory
            n_pos = resolved.shape[1]
            d_step = position_step_m(f, n_pos)
            d0, d1 = _distance_range(start_distance, end_distance, d_step, n_pos)
            width = d1 - d0
            n_space = min(max_space_bins, width)
            col_edges = decimate.bin_edges(width, n_space)
            cells = total_p * n_space
            if cells > max_cells:
                raise AudaceDisplayError(
                    f"band analysis needs the whole time series in RAM "
                    f"(~{cells * 4 / 1e9:.1f} GB: {total_p:,} pulses x {n_space} "
                    f"bins). Restrict with --duration / --max-pulses, or lower "
                    f"--max-space-bins."
                )
            geom = dict(d0=d0, d1=d1, d_step=d_step, n_space=n_space,
                        col_edges=col_edges, n_pos=n_pos)

        sub = resolved[:, geom["d0"]:geom["d1"]]
        cols = decimate.reduce_cols(sub, geom["col_edges"], op="mean")
        parts.append(cols.astype(np.float32, copy=False))
        read += raw.shape[0]

    if not geom:
        raise AudaceDisplayError("no data read.")

    data = (
        np.vstack(parts) if parts
        else np.empty((0, geom["n_space"]), np.float32)
    )
    trig = f.trig_frequency
    d_step = geom["d_step"]
    return TimeMatrixResult(
        data=data,
        trig=trig,
        t_extent=(p0 / trig, p1 / trig),
        d_extent=(geom["d0"] * d_step, geom["d1"] * d_step),
        n_positions=geom["n_pos"],
    )


def load_columns(
    f,
    transform: Transform,
    indices: Sequence[int],
    *,
    start_time: Optional[float] = None,
    duration: Optional[float] = None,
    subsample_time: int = 1,
    max_pulses: Optional[int] = None,
) -> tuple[np.ndarray, float]:
    """Full-resolution time series at columns ``indices``.

    Returns ``(data (rows, len(indices)) float32, effective_trig_hz)``. RAM is
    ~ ``rows * len(indices) * 4`` (small: a handful of columns).
    """
    if not indices:
        raise AudaceDisplayError("no position selected.")
    idx = np.asarray(indices, dtype=np.intp)

    p0, p1 = _pulse_range(f, start_time, duration)
    if max_pulses is not None:
        p1 = min(p1, p0 + max_pulses)
    total_p = p1 - p0

    _advance_to(f, p0)
    chunk = _chunk_pulses(f.line_size)
    parts: list[np.ndarray] = []
    read = 0
    while read < total_p:
        raw = f.read_lines(min(chunk, total_p - read))
        if raw.shape[0] == 0:
            break
        resolved = np.asarray(transform(raw))
        parts.append(resolved[:, idx].astype(np.float32, copy=False))
        read += raw.shape[0]

    data = (
        np.vstack(parts)
        if parts
        else np.empty((0, idx.size), np.float32)
    )
    if subsample_time > 1:
        data = data[::subsample_time]
    eff_trig = f.trig_frequency / max(subsample_time, 1)
    return data, eff_trig
