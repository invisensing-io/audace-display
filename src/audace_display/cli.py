"""Command-line interface for audace-display.

Zero-config usage: ``audace-display FILE`` automatically picks a **heatmap**
(demodulated file) or an **animated oscilloscope** (raw file). The
``info`` / ``heatmap`` / ``bandheatmaps`` / ``fft`` / ``trace`` / ``scope`` /
``inspect`` / ``demod`` subcommands give fine-grained control.
"""
from __future__ import annotations

import argparse
import datetime
import math
import sys
from typing import Optional

import numpy as np
from invisensing import File, Mode

from . import decimate, processing, reader
from ._errors import AudaceDisplayError
from .channels import (
    DEFAULT_CHANNEL,
    STRAIN_RATE_UNIT,
    STRAIN_UNIT,
    reads_strain,
    reads_strain_rate,
    resolve_channel,
)

try:
    from . import __version__
except Exception:  # pragma: no cover - version is informational only
    __version__ = "0"

try:
    # nm/m per rad/m: invisensing's constant, never copied here.
    from invisensing import NANOSTRAIN_PER_RAD_METRE as _K_NANOSTRAIN
except ImportError:  # invisensing < 1.6.0: --nanostrain then says what to install
    _K_NANOSTRAIN = None

#: The options that read a phase in another unit: option -> (invisensing
#: ``phase_unit``, what the phase becomes, its unit).
_PHASE_UNIT_OPTIONS = {
    "--nanostrain": ("nanostrain", "strain", STRAIN_UNIT),
    "--strain-rate": ("strain_rate", "strain rate", STRAIN_RATE_UNIT),
}

# FFT memory budget (float32 cells) before spatial subsampling.
_FFT_MAX_CELLS = 256 * 1024 * 1024 // 4
_FFT_MAX_POSITIONS = 256

SUBCOMMANDS = {
    "auto", "info", "heatmap", "bandheatmaps", "fft", "trace", "scope",
    "demod", "inspect", "gui",
}


def _warn(msg: str) -> None:
    sys.stderr.write(f"WARNING: {msg}\n")


_QT_BACKENDS = {"pyqtgraph", "qt", "pg"}


def _resolve_backend(args) -> str:
    """Return 'qt' (interactive pyqtgraph) or 'mpl' (matplotlib).

    `--save` forces matplotlib (PNG), even if pyqtgraph was requested.
    """
    requested = getattr(args, "backend", "matplotlib")
    want_qt = requested in _QT_BACKENDS
    if want_qt and getattr(args, "save", None):
        _warn("pyqtgraph is an interactive viewer; --save renders the PNG via matplotlib.")
        return "mpl"
    return "qt" if want_qt else "mpl"


# --- Transform / title construction ------------------------------------------


def _channel_transform(f, resolver):
    return lambda raw: resolver(f, raw)


def _heatmap_title(f, channel_or_label: str) -> str:
    return f"{f.path.name} -- mode={f.mode.value}, {channel_or_label}"


def _split_label(base_label: str) -> tuple[str, str]:
    """``'phase (rad)'`` -> ``('phase', 'rad')``.

    Splits on the *last* ``' ('`` so inner parentheses (e.g. ``arctan(Q/I)``)
    are preserved in the head. Returns ``(label, '')`` when there is no unit.
    """
    if base_label.endswith(")") and " (" in base_label:
        head, unit = base_label.rsplit(" (", 1)
        return head, unit[:-1]
    return base_label, ""


def _variance_label(base_label: str, variance: str) -> str:
    """Colorbar label for a variance / log-variance field (units squared):
    ``rad^2``, and a compound unit squared whole, ``(nm/m)^2``."""
    head, unit = _split_label(base_label)
    if not unit:
        sq = ""
    elif "/" in unit:
        sq = f" ({unit})^2"
    else:
        sq = f" ({unit}^2)"
    prefix = "log10 Var" if variance == "log" else "Var"
    return f"{prefix}[{head}]{sq}"


def _rms_label(base_label: str) -> str:
    """Colorbar label for a band-energy (RMS) field (same units as the signal)."""
    head, unit = _split_label(base_label)
    return f"RMS[{head}]" + (f" ({unit})" if unit else "")


# --- Heatmap rendering (shared by heatmap / demod / auto) ---------------------


def _render_heatmap(f, args, transform, base_label: str, is_angular: bool, subtitle: str) -> None:
    # --variance overrides --reduce: variance is non-negative and gets its own
    # (optionally log) scaling, so it never combines with dB or the angular cmap.
    variance = getattr(args, "variance", None)
    res = reader.load_decimated(
        f, transform,
        max_time_bins=args.max_time_bins,
        max_space_bins=args.max_space_bins,
        reduce="variance" if variance else args.reduce,
        start_time=args.start_time,
        duration=args.duration,
        start_distance=args.start_distance,
        end_distance=args.end_distance,
        max_pulses=args.max_pulses,
        # A strain rate is NaN on the file's first line and after a gap of
        # its time axis: a missing sample, each bin reduced over the others.
        skip_nan=reads_strain_rate(f),
    )
    data = res.data
    use_db = getattr(args, "db", False)
    angular = is_angular
    label = base_label
    if variance:
        angular = False
        use_db = False
        if variance == "log":
            data = processing.to_log_variance(data)
        label = _variance_label(base_label, variance)
    elif use_db:
        data, ref = processing.to_db(data)
        head, unit = _split_label(base_label)
        label = f"{head} (dB, ref={ref:.3g} {unit})".strip()
    vmin, vmax = processing.auto_clim(
        data, is_angular=angular, use_db=use_db, vmin=args.vmin, vmax=args.vmax
    )
    cmap = args.cmap or processing.default_cmap(angular, use_db)
    title = args.title or _heatmap_title(f, subtitle)

    if _resolve_backend(args) == "qt":
        from . import plotting_qt
        plotting_qt.build_heatmap(
            data, t_extent=res.t_extent, d_extent=res.d_extent,
            label=label, vmin=vmin, vmax=vmax, cmap=cmap, title=title,
        )
        plotting_qt.run()
        return

    from . import plotting  # lazy import: picks the backend based on the display
    fig = plotting.build_heatmap(
        data,
        t_extent=res.t_extent,
        d_extent=res.d_extent,
        label=label,
        vmin=vmin,
        vmax=vmax,
        cmap=cmap,
        aspect=args.aspect,
        title=title,
    )
    plotting.show_or_save(fig, args.save, args.dpi)


# --- Command cores (receive an open File) ------------------------------------


def _format_ns(ns: int) -> str:
    """``ns`` since the epoch as a local date-time with its UTC offset,
    nanoseconds kept: ``2026-09-23 14:03:12.123456789+02:00 (… ns)`` -- the
    form of the producer's own timestamp string (HDF5 ``timestamp``), so a
    reader in another time zone knows which instant it is."""
    sec, nsec = divmod(ns, 1_000_000_000)
    dt = datetime.datetime.fromtimestamp(sec, tz=datetime.timezone.utc).astimezone()
    z = dt.strftime("%z")  # "+0200"
    offset = f"{z[:3]}:{z[3:5]}" if len(z) >= 5 else z
    return f"{dt:%Y-%m-%d %H:%M:%S}.{nsec:09d}{offset} ({ns} ns)"


def _format_timestamp(f) -> str:
    """Human-readable acquisition start time.

    Current-format files store the start instant as an i64 nanosecond epoch
    (``header.acquisition_ns``); legacy files carry a free-form ASCII string in
    ``timestamp`` (and ``acquisition_ns == 0``). Render the i64 form as a local
    date-time so the field reads as an actual start time, not a raw integer.
    The time is the first line's (:func:`reader.absolute_time_ns`): corrected
    by the card's time map when the file carries it.
    """
    ns = reader.absolute_time_ns(f, 0)
    if ns:
        return _format_ns(ns)
    return f.timestamp.strip() or "(none)"


def _format_time_correction(f) -> Optional[str]:
    """How the file's times were stamped (invisensing >= 1.4.0), or ``None``
    when the file does not say (older files, older invisensing)::

        cardclock-v1: corrected, +0.098 ms vs the count (tracking, +/-61 us)
        none: count (no time map)
    """
    tc = getattr(f, "time_correction", None)
    if tc is None:
        return None
    if tc != "cardclock-v1":
        return f"{tc}: count (no time map)"
    parts = []
    state = getattr(f, "time_correction_state", None)
    if state is not None:
        # A file carrying its time axis (HDF5) knows the count it replaces;
        # a DAT / SEG-Y / TDMS only says its start time is the corrected one.
        try:
            delta = f.line_time_ns(0) - f.line_time_ns(0, nominal=True)
            parts.append(f"{delta / 1e6:+.3f} ms vs the count")
        except (TypeError, AttributeError):
            pass
    else:
        parts.append("start time")
    detail = []
    if state:
        detail.append(state)
    sigma = getattr(f, "time_uncertainty_ns", None)
    if sigma is not None:
        detail.append(f"+/-{sigma / 1e3:.0f} us")
    text = f"{tc}: corrected"
    if parts:
        text += ", " + ", ".join(parts)
    if detail:
        text += f" ({', '.join(detail)})"
    return text


def _format_hz(hz: float, grouped: bool = True) -> str:
    """``75007.50075`` -> ``'75,007.5008'``; an integral rate keeps no decimals
    (``1000.0`` -> ``'1,000'``, or ``'1000'`` with ``grouped=False``)."""
    return f"{hz:{',' if grouped else ''}.4f}".rstrip("0").rstrip(".")


def _format_trig_frequency(f) -> str:
    """Requested trigger frequency, plus the actual one when they differ.

    A card fires on an integer divider of its clock: 75,000 Hz requested on a
    250 MHz clock gives 250 MHz / 3,333 = 75,007.5008 Hz, which the file carries
    (invisensing >= 1.3.0). An exact frequency (or a file / library without the
    actual value) prints exactly as before: ``'75,000 Hz'``.
    """
    requested = f.trig_frequency
    actual = reader.line_rate_hz(f)
    text = f"{requested:,} Hz"
    if actual != float(requested):
        detail = f"actual {_format_hz(actual)} Hz"
        if requested:
            detail += f", {(actual / requested - 1.0) * 1e6:+.1f} ppm"
        text += f" ({detail})"
    return text


def _content_type(f) -> tuple[int, str]:
    """``(content_type, name)`` of ``f``: ``File.content_type_name`` names a
    value the library does not know by the file's own tag (HDF5 / TDMS)."""
    header = getattr(f, "header", None)
    value = getattr(f, "content_type", None)
    if value is None:
        value = getattr(header, "content_type", 0) or 0
    name = getattr(f, "content_type_name", None) or getattr(header, "content_type_name", "")
    return int(value), str(name)


def _format_compression(f) -> Optional[str]:
    """The HDF5 compression when the file is compressed (invisensing >=
    1.4.0), with the bound of ``ZSTD_LOSSY_1E4``; ``None`` for an
    uncompressed file::

        ZSTD_LOSSY_1E4 (phase on a 1.221e-04 rad grid, error <= 6.104e-05 rad)
    """
    comp = getattr(f, "compression", None)
    if comp is None or comp == "NONE":
        return None
    step = getattr(f, "quantization_step_rad", None)
    err = getattr(f, "quantization_max_error_rad", None)
    # Read as strain: the same bounds in nm/m beside the radians. As strain
    # rate: a difference of two samples, on the same grid, its error up to
    # twice theirs.
    factor, unit, twice = None, "", 1
    if reads_strain(f):
        factor, unit = getattr(f, "nanostrain_per_rad", None), STRAIN_UNIT
    elif reads_strain_rate(f):
        factor, unit, twice = getattr(f, "strain_rate_per_rad", None), STRAIN_RATE_UNIT, 2

    def strain(v: float) -> str:
        return f" = {v * factor:.3e} {unit}" if factor else ""

    detail = []
    if step is not None:
        detail.append(f"phase on a {step:.3e} rad grid{strain(step)}")
    if err is not None:
        detail.append(f"error <= {err:.3e} rad{strain(twice * err)}")
    return comp + (f" ({', '.join(detail)})" if detail else "")


def _format_quantity(f) -> Optional[str]:
    """``phase (rad)``, ``phase+amplitude (rad+count)``: what the content type
    says the samples are, or ``None`` when it does not say."""
    quantity = getattr(f, "quantity", None)
    if not quantity:
        return None
    unit = getattr(f, "unit", None)
    return f"{quantity} ({unit})" if unit else quantity


def do_info(f, args) -> int:
    content_type, content_name = _content_type(f)
    # A file carrying a content type is described by it; a legacy capture
    # (content_type 0) keeps the flag description and the pipeline name it
    # always had.
    flags = _decode_flags(f.flags, legacy=content_type == 0)
    d_step = reader.position_step_m(f)
    format_version = getattr(f, "format_version", 0) or 0
    print(f"File: {f.path}")
    print(f"  Mode           : {f.mode.value}")
    if content_type != 0:
        mozart = " (Mozart)" if content_name in ("sw_mozart", "sw_mozart_plus") else ""
        print(f"  Content type   : {content_name}{mozart}")
        quantity = _format_quantity(f)
        if quantity is not None:
            print(f"  Quantity       : {quantity}")
    source = getattr(f, "source_content_type", None)
    if source is not None:
        from invisensing import content
        info = content.info(source)
        print(f"  Source column  : {info.tag if info else source} ({source})")
    if format_version:
        print(f"  Format version : {format_version}")
    card = getattr(f, "card", None)
    if card:
        print(f"  Card           : {card}")
    sibling = getattr(f, "sibling_path", None)
    if sibling is not None:
        print(f"  Other half     : {sibling}")
    gauge = getattr(f, "gauge_length_m", None)
    if gauge is not None:
        print(f"  Gauge length   : {gauge:g} m")
    if reads_strain(f):
        print(
            f"  Strain         : {STRAIN_UNIT} = rad x {f.nanostrain_per_rad:.6g} "
            f"(= {_K_NANOSTRAIN:g} / {gauge:g} m)"
        )
    if reads_strain_rate(f):
        hz = reader.line_rate_hz(f)
        print(
            f"  Strain rate    : {STRAIN_RATE_UNIT} = (rad[n] - rad[n-1]) x "
            f"{f.strain_rate_per_rad:.6g} (= {_K_NANOSTRAIN:g} / {gauge:g} m x "
            f"{_format_hz(hz)} Hz); line 0 has none (NaN)"
        )
        gaps = getattr(f, "time_gaps", ())
        if gaps:
            n_gap = sum(b - a for a, b in gaps)
            shown = ", ".join(f"{a}" if b == a + 1 else f"{a}-{b - 1}" for a, b in gaps[:5])
            more = ", ..." if len(gaps) > 5 else ""
            print(f"  Time gaps      : {n_gap:,} line(s) after lost shots, NaN: {shown}{more}")
    print(f"  Timestamp      : {_format_timestamp(f)}")
    if f.num_lines > 1:
        last = reader.absolute_time_ns(f, f.num_lines - 1)
        if last:
            print(f"  Last pulse     : {_format_ns(last)}")
    correction = _format_time_correction(f)
    if correction is not None:
        print(f"  Time           : {correction}")
    print(f"  Pulses         : {f.num_lines:,}")
    duration_s = getattr(f, "duration_s", None)
    written = f" (file: {duration_s} s)" if duration_s is not None else ""
    print(f"  Duration       : {f.duration:.3f} s{written}")
    print(f"  Sample rate    : {f.sample_rate:,} Hz")
    print(f"  Trig frequency : {_format_trig_frequency(f)}")
    print(f"  Line size      : {f.line_size:,} samples/pulse")
    print(f"  Positions      : {f.positions_per_line:,} per pulse")
    print(f"  Fiber index    : {_format_fiber_index(f)}")
    print(f"  Spatial step   : {d_step:.3f} m")
    temporal = getattr(f, "temporal_step_s", None)
    if format_version and temporal:
        print(f"  Line period    : {temporal * 1e3:.6g} ms")
    print(f"  Fiber length   : {f.distance:.2f} m")
    print(f"  Trigger delay  : {f.trigger_delay:,} m")
    print(f"  ADC range      : {f.range:.3f} V")
    print(f"  Sample size    : {f.sample_size} B")
    print(f"  Sockets        : {f.header.num_channels}")
    print(f"  Flags          : 0x{f.flags:03X} ({flags})")
    compression = _format_compression(f)
    if compression is not None:
        print(f"  Compression    : {compression}")

    canonical = DEFAULT_CHANNEL[f.mode]
    _, resolver, label, _ = resolve_channel(canonical, f.mode, f)
    n_stat = min(1000, f.num_lines)
    if n_stat > 0:
        buf = f.read_lines(n_stat)
        data = resolver(f, buf)
        shown = (
            f" -- {label}"
            if (reads_strain(f) or reads_strain_rate(f)) and canonical == "phase"
            else ""
        )
        # A missing sample (a strain rate's line 0, NaN) is no statistic.
        finite = np.isfinite(data)
        skipped = int(data.size - np.count_nonzero(finite))
        if skipped:
            data = data[finite]
        note = f" ({skipped:,} NaN/inf samples left out)" if skipped else ""
        if data.size:
            mi, p1, p50, p99, ma = np.percentile(data, [0, 1, 50, 99, 100]).tolist()
            print(
                f"\n  Stats for channel '{canonical}'{shown} over {n_stat:,} pulses{note}:"
                f"\n    min={mi:+.4g}  p1={p1:+.4g}  p50={p50:+.4g}  p99={p99:+.4g}  max={ma:+.4g}"
                f"\n    mean={data.mean():+.4g}  std={data.std():.4g}"
            )
        else:
            print(f"\n  Stats for channel '{canonical}'{shown} over {n_stat:,} pulses: "
                  f"no finite sample{note}")
    return 0


def _format_fiber_index(f) -> str:
    """The fibre group index the file's metres are labelled at, with the
    speed of light in the fibre it gives (invisensing >= 1.7.0):
    ``1.4682 (204,190,477 m/s)``; ``... (legacy: the file states none)`` for
    a recording made before the acquisition wrote it (2e8 m/s); ``...
    (relabelled; the file says 1.49896229)`` with ``--fiber-group-index``."""
    n, v = f.fiber_group_index, f.light_speed_in_fiber_m_s
    text = f"{n!r} ({v:,} m/s"
    recorded = f.recorded_fiber_group_index
    if (n, v) != (recorded, f.recorded_light_speed_in_fiber_m_s):
        return f"{text}; relabelled, the file says {recorded!r})"
    if f.header.light_speed_in_fiber_m_s == 0:
        return f"{text}; legacy: the file states none)"
    return f"{text})"


def _decode_flags(flags: int, legacy: bool = True) -> str:
    """The set flag bits by name. ``legacy`` (a file without a content type):
    the pipeline name is appended the way it always was; otherwise the
    content type says what the samples are, and no pipeline is named from
    the flags (the SCHUBERT flag is deprecated)."""
    from invisensing import (
        FLAG_DEMODULATED, FLAG_FLOAT, FLAG_PHASE,
        FLAG_INTERLEAVED, FLAG_UNSIGNED, FLAG_AC, FLAG_HIZ, FLAG_MOZART,
    )
    bits = [
        name
        for name, bit in [
            ("DEMODULATED", FLAG_DEMODULATED), ("FLOAT", FLAG_FLOAT),
            ("PHASE", FLAG_PHASE), ("INTERLEAVED", FLAG_INTERLEAVED),
            ("UNSIGNED", FLAG_UNSIGNED), ("AC", FLAG_AC), ("HIZ", FLAG_HIZ),
        ]
        if flags & bit
    ]
    # Legacy files only (content_type 0), unchanged: which demodulation
    # pipeline ran, appended ONLY when DEMODULATED is set -- Mozart-vs-
    # Schubert is carried by the presence or absence of a single bit, so on
    # a raw capture "SCHUBERT" would name a pipeline that never executed. A
    # file with a content type prints that instead (``Content type`` line).
    if legacy and flags & FLAG_DEMODULATED:
        bits.append("MOZART" if flags & FLAG_MOZART else "SCHUBERT")
    return " | ".join(bits) if bits else "0"


def do_heatmap(f, args) -> int:
    canonical, resolver, label, is_ang = resolve_channel(args.channel, f.mode, f)
    _render_heatmap(
        f, args, _channel_transform(f, resolver), label, is_ang,
        subtitle=f"channel={canonical}",
    )
    return 0


def do_demod(f, args) -> int:
    from .demod import load_demodulator
    plugin = load_demodulator(args.script, f, getattr(args, "plugin_args", None))
    _render_heatmap(
        f, args, plugin.transform, plugin.label, plugin.is_angular,
        subtitle=f"demod={args.script}",
    )
    return 0


def _parse_bands(args, nyquist: float) -> list[tuple[float, float]]:
    """Build the ``[(f_lo, f_hi), ...]`` band list from ``--f0/--f1/--f2/--f3``.

    ``--f1`` is required; ``--f2`` requires ``--f1`` and ``--f3`` requires
    ``--f2`` (contiguous bands). ``--f0`` (default 0, i.e. include DC) is the
    lower edge of the first band. Edges must be strictly increasing; any edge
    above Nyquist is clamped (with a warning) and bands made degenerate by the
    clamp are dropped.
    """
    if args.f1 is None:
        raise AudaceDisplayError(
            "bandheatmaps needs at least --f1 (upper edge of band 1, in Hz)."
        )
    if args.f3 is not None and args.f2 is None:
        raise AudaceDisplayError("--f3 requires --f2 (bands must be contiguous).")

    f0 = 0.0 if args.f0 is None else args.f0
    if f0 < 0:
        raise AudaceDisplayError("--f0 must be >= 0.")
    edges = [f0] + [t for t in (args.f1, args.f2, args.f3) if t is not None]
    for lo, hi in zip(edges, edges[1:]):
        if hi <= lo:
            raise AudaceDisplayError(
                f"band edges must be strictly increasing, got {edges} Hz."
            )
    if edges[-1] > nyquist:
        _warn(
            f"band edge {edges[-1]:.0f} Hz exceeds Nyquist ({nyquist:.0f} Hz); "
            f"clamping to Nyquist."
        )
        edges = [min(e, nyquist) for e in edges]
    bands = [(lo, hi) for lo, hi in zip(edges, edges[1:]) if hi > lo]
    if not bands:
        raise AudaceDisplayError("no valid frequency band after clamping to Nyquist.")
    return bands


def do_bandheatmaps(f, args) -> int:
    """Per-frequency-band waterfalls: a global heatmap + one panel per band.

    Each band panel band-pass filters the temporal signal (FFT along the pulse
    axis) to ``[f_lo, f_hi)`` Hz, then shows its energy -- temporal RMS per time
    bin, or variance / log-variance with ``--variance`` -- as a distance x time
    heatmap. The global panel (always shown) is the plain channel heatmap (mean),
    or its temporal variance when ``--variance`` is set.
    """
    canonical, resolver, base_label, is_ang = resolve_channel(args.channel, f.mode, f)
    # Actual pulse rate: the band filters' fs and Nyquist.
    trig = reader.line_rate_hz(f)
    bands = _parse_bands(args, nyquist=trig / 2.0)

    # The whole time series must be resident for the temporal FFT: warn (like
    # `fft`) when the requested window is large before actually loading it.
    p0, p1 = reader.time_window_pulses(f, args.start_time, args.duration, args.max_pulses)
    est_space = min(args.max_space_bins, f.positions_per_line)
    est_bytes = (p1 - p0) * est_space * 4
    if est_bytes > 512 * 1024 * 1024:
        _warn(
            f"bandheatmaps: ~{est_bytes / 1e9:.1f} GB to load "
            f"({p1 - p0:,} pulses x {est_space} bins). Use --duration / "
            f"--max-pulses / --max-space-bins if memory is limited."
        )

    res = reader.load_time_matrix(
        f, _channel_transform(f, resolver),
        max_space_bins=args.max_space_bins,
        start_time=args.start_time, duration=args.duration,
        start_distance=args.start_distance, end_distance=args.end_distance,
        max_pulses=args.max_pulses,
    )
    matrix = res.data
    if matrix.shape[0] < 2:
        raise AudaceDisplayError("not enough pulses for a band analysis.")

    variance = args.variance
    # A strain rate is NaN on the file's first line and after a gap of its
    # time axis: missing in the panels' time bins, and the column's mean (0
    # once detrended) for the band filters, which need evenly spaced samples.
    rate = reads_strain_rate(f)

    def _bin_time(sig: np.ndarray, op: str) -> np.ndarray:
        n_pulses = sig.shape[0]
        t_factor = max(1, math.ceil(n_pulses / args.max_time_bins))
        n_time = math.ceil(n_pulses / t_factor)
        acc = decimate.TimeBinAccumulator(n_time, sig.shape[1], t_factor, op, skip_nan=rate)
        acc.add(sig)  # whole matrix is already in RAM -> one pass
        return acc.result()

    def _panel(data2d: np.ndarray, *, title: str, label: str, angular: bool) -> dict:
        vmin, vmax = processing.auto_clim(data2d, is_angular=angular, use_db=False)
        cmap = args.cmap or processing.default_cmap(angular, False)
        return dict(data=data2d, title=title, label=label, vmin=vmin, vmax=vmax, cmap=cmap)

    panels: list[dict] = []

    # Global reference panel.
    g_data = _bin_time(matrix, "variance" if variance else "mean")
    if variance == "log":
        g_data = processing.to_log_variance(g_data)
    if variance:
        panels.append(_panel(
            g_data, title="global (whole band)",
            label=_variance_label(base_label, variance), angular=False))
    else:
        panels.append(_panel(
            g_data, title="global (mean)", label=base_label, angular=is_ang))

    # One panel per band: energy of the band-passed signal. Detrend each
    # position first (default): a demodulated channel carries a DC offset + slow
    # drift whose start/end discontinuity would otherwise leak broadband ringing
    # into every band (use --no-detrend to keep the raw signal, incl. DC).
    filled = processing.fill_missing(matrix) if rate else matrix
    band_source = filled if args.no_detrend else processing.detrend_columns(filled)
    for f_lo, f_hi in bands:
        band_sig = processing.band_limited(band_source, fs=trig, f_lo=f_lo, f_hi=f_hi)
        b_data = _bin_time(band_sig, "variance" if variance else "rms")
        if variance == "log":
            b_data = processing.to_log_variance(b_data)
        if variance:
            label = _variance_label(base_label, variance)
            kind = "log variance" if variance == "log" else "variance"
        else:
            label = _rms_label(base_label)
            kind = "RMS"
        panels.append(_panel(
            b_data, title=f"{f_lo:.0f}-{f_hi:.0f} Hz ({kind})",
            label=label, angular=False))

    suptitle = args.title or (
        f"{f.path.name} -- band heatmaps (mode={f.mode.value}, channel={canonical})"
    )
    if getattr(args, "backend", "matplotlib") in _QT_BACKENDS and not args.save:
        _warn("bandheatmaps is rendered with matplotlib (multi-panel); "
              "--backend pyqtgraph is ignored.")

    from . import plotting
    fig = plotting.build_band_heatmaps(
        panels, t_extent=res.t_extent, d_extent=res.d_extent, suptitle=suptitle,
    )
    plotting.show_or_save(fig, args.save, args.dpi)
    return 0


def do_trace(f, args) -> int:
    canonical, resolver, label, _ = resolve_channel(args.channel, f.mode, f)
    n_pos = f.positions_per_line
    d_step = reader.position_step_m(f)
    if args.position is None:
        idx = n_pos // 2
    else:
        idx = int(round(args.position / d_step))
    if not (0 <= idx < n_pos):
        raise AudaceDisplayError(
            f"position {args.position} m outside the fiber "
            f"(range: 0 to {(n_pos - 1) * d_step:.2f} m)."
        )

    data, eff_trig = reader.load_columns(
        f, _channel_transform(f, resolver), [idx],
        start_time=args.start_time, duration=args.duration,
        subsample_time=args.subsample_time, max_pulses=args.max_pulses,
    )
    if data.shape[0] == 0:
        raise AudaceDisplayError("no data read for the trace.")
    sig = data[:, 0]
    centers, lo, hi = decimate.minmax_envelope(sig, args.max_points)
    t0 = args.start_time or 0.0
    times = t0 + centers / eff_trig
    title = args.title or (
        f"Time trace -- {f.path.name}\n"
        f"channel={canonical}, position={idx * d_step:.2f} m"
    )

    if _resolve_backend(args) == "qt":
        from . import plotting_qt
        plotting_qt.build_trace(times, lo, hi, label=label, title=title)
        plotting_qt.run()
        return 0

    from . import plotting
    fig = plotting.build_trace(times, lo, hi, label=label, title=title)
    plotting.show_or_save(fig, args.save, args.dpi)
    return 0


def do_fft(f, args) -> int:
    canonical, resolver, label, _ = resolve_channel(args.channel, f.mode, f)
    n_pos = f.positions_per_line
    d_step = reader.position_step_m(f)

    per_position = False
    if args.position is not None:
        indices, meters = processing.parse_position_spec(args.position, d_step, n_pos)
        per_position = len(indices) > 1
        pos_label = (
            f"position={meters[0]:.2f} m" if len(indices) == 1
            else "positions=" + ",".join(f"{m:.1f}" for m in meters) + " m"
        )
    elif args.position_range is not None:
        indices, meters = processing.parse_position_spec(args.position_range, d_step, n_pos)
        pos_label = f"range {meters[0]:.1f}-{meters[-1]:.1f} m ({len(indices)} positions, averaged)"
    else:
        indices = list(range(n_pos))
        pos_label = f"whole fiber ({n_pos} positions, averaged)"

    # Memory bound: limit the number of positions and warn if truncating.
    p0, p1 = reader.time_window_pulses(f, args.start_time, args.duration, args.max_pulses)
    rows = math.ceil((p1 - p0) / max(args.subsample_time, 1))
    if not per_position and len(indices) > _FFT_MAX_POSITIONS:
        keep = min(_FFT_MAX_POSITIONS, max(1, _FFT_MAX_CELLS // max(rows, 1)))
        if keep < len(indices):
            step = max(1, len(indices) // keep)
            dropped = len(indices)
            indices = indices[::step]
            _warn(
                f"FFT: {dropped} positions subsampled to {len(indices)} "
                f"(1 in {step}) to bound RAM. Restrict with --position-range "
                f"or --duration for an exact average."
            )
    if rows * len(indices) > _FFT_MAX_CELLS:
        _warn(
            f"FFT: ~{rows * len(indices) * 4 / 1e9:.1f} GB to load "
            f"({rows:,} pulses x {len(indices)} positions). Use --duration "
            f"or --max-pulses if memory is limited."
        )

    data, eff_trig = reader.load_columns(
        f, _channel_transform(f, resolver), indices,
        start_time=args.start_time, duration=args.duration,
        subsample_time=args.subsample_time, max_pulses=args.max_pulses,
    )
    if data.shape[0] < 2:
        raise AudaceDisplayError("not enough pulses for an FFT.")
    if reads_strain_rate(f):
        # NaN on the file's first line and after a gap of its time axis: a
        # spectrum needs evenly spaced samples, a missing one enters as the
        # column's mean (0 once the mean is removed).
        data = processing.fill_missing(data)

    def _y(amp: np.ndarray) -> tuple[np.ndarray, str]:
        if args.db:
            ref = float(amp.max()) or processing.DB_EPS
            return (20.0 * np.log10(np.maximum(amp, processing.DB_EPS) / ref),
                    f"Amplitude (dB, ref={ref:.3g})")
        unit = label.split("(")[-1].rstrip(")") if "(" in label else ""
        return amp, f"Amplitude ({unit})"

    curves: list[tuple[Optional[str], np.ndarray]] = []
    if per_position:
        for col, m in enumerate(meters):
            freqs, amp = processing.temporal_fft(
                data[:, col], fs=eff_trig, window=args.window, detrend=not args.no_detrend
            )
            y, y_label = _y(amp)
            curves.append((f"{m:.1f} m", y))
    else:
        freqs, amp = processing.temporal_fft(
            data, fs=eff_trig, window=args.window, detrend=not args.no_detrend
        )
        y, y_label = _y(amp)
        curves.append((None, y))

    title = args.title or (
        f"Temporal FFT -- {f.path.name}\n"
        f"mode={f.mode.value}, channel={canonical}, {pos_label}, window={args.window}"
    )

    if _resolve_backend(args) == "qt":
        from . import plotting_qt
        plotting_qt.build_fft(
            freqs, curves, y_label=y_label, title=title,
            nyquist=eff_trig / 2, fmax=args.fmax,
        )
        plotting_qt.run()
        return 0

    from . import plotting
    fig = plotting.build_fft(
        freqs, curves, y_label=y_label, title=title,
        nyquist=eff_trig / 2, fmax=args.fmax,
    )
    plotting.show_or_save(fig, args.save, args.dpi)
    return 0


def do_scope(f, args) -> int:
    """Oscilloscope: replays the lines (pulses) in real time, looping.

    Each frame draws the current line (amplitude vs distance); the cursor
    advances at ``line_rate_hz(f) * speed`` lines/s (the actual pulse rate,
    which is also the clock of the ``t =`` label). With ``--save``, exports a
    frozen line to PNG.
    """
    if not hasattr(f, "seek_lines"):
        raise AudaceDisplayError(
            "oscilloscope mode requires invisensing >= 1.1.0 (O(1) per-line seek)."
        )
    canonical, resolver, label, _ = resolve_channel(args.channel, f.mode, f)
    trig = reader.line_rate_hz(f)
    d_step = reader.position_step_m(f)
    length_m = f.positions_per_line * d_step

    p0, p1 = reader.time_window_pulses(
        f, args.start_time, args.duration, getattr(args, "max_pulses", None)
    )
    n_window = p1 - p0

    def get_y(local_idx: int) -> np.ndarray:
        f.seek_lines(p0 + (local_idx % max(n_window, 1)))
        return resolver(f, f.read_lines(1))[0]

    # Frozen Y bounds (otherwise autoscale makes the plot jitter) from a sample.
    f.seek_lines(p0)
    sample = resolver(f, f.read_lines(min(64, n_window)))
    if not np.isfinite(sample).all():  # a strain rate's line 0 is NaN
        sample = sample[np.isfinite(sample)]
    if sample.size == 0:
        sample = np.zeros(1, np.float32)
    lo = float(np.percentile(sample, 0.1))
    hi = float(np.percentile(sample, 99.9))
    pad = 0.1 * ((hi - lo) or abs(hi) or 1.0)
    ylim = (lo - pad, hi + pad)

    title = args.title or (
        f"{f.path.name} -- {canonical} (oscilloscope x{args.speed:g}, "
        f"{_format_hz(trig, grouped=False)} lines/s)"
    )

    if args.save:  # frozen frame -> PNG
        idx = max(0, min(args.line, n_window - 1))
        xi, yv = decimate.peak_line(get_y(idx), args.max_points)
        from . import plotting
        fig = plotting.build_line(
            xi * d_step, yv, x_label="Distance (m)", y_label=label,
            title=f"{f.path.name} -- {canonical}, line {p0 + idx} (t={(p0 + idx) / trig:.3f} s)",
        )
        plotting.show_or_save(fig, args.save, args.dpi)
        return 0

    if _resolve_backend(args) == "qt":
        from . import plotting_qt
        plotting_qt.animate_scope(
            get_y, n_window, trig_freq=trig, speed=args.speed, fps=args.fps,
            length_m=length_m, y_label=label, title=title, ylim=ylim, line_offset=p0,
        )
        plotting_qt.run()
        return 0

    from . import plotting
    plotting.animate_scope(
        get_y, n_window, trig_freq=trig, speed=args.speed, fps=args.fps,
        d_step=d_step, length_m=length_m, y_label=label, title=title, ylim=ylim,
        max_points=args.max_points, line_offset=p0,
    )
    return 0


def _inspect_source(f, args):
    """Resolve the (transform, label, source_label, n_positions) for inspect.

    With ``--script`` the signal is the demod plugin output (e.g. DUI phase);
    otherwise a built-in channel. ``n_positions`` is read from the actual
    transform output, which may differ from ``f.positions_per_line``.
    """
    if args.script:
        if reads_strain(f) or reads_strain_rate(f):
            option = "--nanostrain" if reads_strain(f) else "--strain-rate"
            what = "strain" if reads_strain(f) else "strain rate"
            raise AudaceDisplayError(
                f"{option} shows a built-in phase channel as {what}; a --script "
                "plugin reads the file's own samples and labels its own output. "
                "Drop one of the two."
            )
        from .demod import load_demodulator
        plugin_args = getattr(args, "plugin_args", None)
        # The output width is probed on a THROWAWAY instance: the plugin that
        # streams must see contiguous, in-order chunks from the start of the
        # window (the contract in demod.py), never a lone line 0 first -- a
        # stateful filter would carry that line's state into the window.
        prober = load_demodulator(args.script, f, plugin_args)
        probe = np.asarray(prober.transform(f.read_lines(1)))
        if probe.ndim != 2:
            raise AudaceDisplayError(
                f"the demod script must return a 2-D array, got {probe.shape}."
            )
        del prober
        if hasattr(f, "seek_lines"):
            f.seek_lines(0)
        plugin = load_demodulator(args.script, f, plugin_args)
        return plugin.transform, plugin.label, f"demod={args.script}", probe.shape[1]

    canonical, resolver, label, _ = resolve_channel(args.channel, f.mode, f)
    return _channel_transform(f, resolver), label, f"channel={canonical}", f.positions_per_line


def do_inspect(f, args) -> int:
    """Single location: waveform + FFT spectrum stacked in one figure."""
    transform, label, source_label, n_pos = _inspect_source(f, args)
    d_step = reader.position_step_m(f, n_pos)

    if args.index is not None:
        idx = args.index
    elif args.position is not None:
        idx = int(round(args.position / d_step))
    else:
        idx = n_pos // 2
    if not (0 <= idx < n_pos):
        raise AudaceDisplayError(
            f"location index {idx} outside the fiber "
            f"(0 to {n_pos - 1}, i.e. 0 to {(n_pos - 1) * d_step:.2f} m)."
        )

    data, eff_trig = reader.load_columns(
        f, transform, [idx],
        start_time=args.start_time, duration=args.duration,
        subsample_time=args.subsample_time, max_pulses=args.max_pulses,
    )
    if data.shape[0] < 2:
        raise AudaceDisplayError("not enough pulses to inspect this location.")

    detrend = not args.no_detrend
    waveform = processing.remove_dc_and_trend(data[:, 0], detrend=detrend)
    # Anchor the time axis at --start-time so the selected window is visible on
    # the plot (consistent with `trace`); the FFT itself is offset-invariant.
    t0 = args.start_time or 0.0
    times = t0 + np.arange(waveform.size) / eff_trig

    clip = None if args.clip_percentile == 0 else args.clip_percentile
    ylim = processing.percentile_limits(waveform, clip)

    freqs, magnitude = processing.temporal_fft(
        waveform, fs=eff_trig, window=args.window, detrend=detrend
    )

    stats_text = (
        f"Location index: {idx}\n"
        f"Position: {idx * d_step:.2f} m\n"
        f"Window: {t0:g}-{t0 + waveform.size / eff_trig:g} s\n"
        f"Samples: {waveform.size}\n"
        f"Rate: {eff_trig:g} Hz\n"
        f"Mean: {waveform.mean():.6g}\n"
        f"Std: {waveform.std():.6g}\n"
        f"Min: {waveform.min():.6g}\n"
        f"Max: {waveform.max():.6g}"
    )
    title = args.title or (
        f"{f.path.name} -- {source_label}, location index {idx} ({idx * d_step:.2f} m)"
    )

    from . import plotting
    fig = plotting.build_inspect(
        times, waveform, freqs, magnitude,
        y_label=label, ylim=ylim, stats_text=stats_text,
        fft_y_label="Amplitude" + (" (log)" if args.fft_log else ""),
        fft_log=args.fft_log, fmax=args.fmax, title=title,
    )
    plotting.show_or_save(fig, args.save, args.dpi)
    return 0


def do_auto(f, args) -> int:
    """Zero-config: heatmap if demodulated, animated oscilloscope if raw."""
    if f.mode is Mode.RAW:
        return do_scope(f, args)
    return do_heatmap(f, args)


# --- File-opening wrappers ---------------------------------------------------


def _check_phase_unit(f, option: str) -> None:
    """Refuse ``option`` (``--nanostrain``, ``--strain-rate``) on a file that
    cannot be read so, in the CLI's words: a file that holds no phase, a
    phase that does not record its gauge length (no gauge length is ever
    assumed), and for ``--strain-rate`` a file whose trigger frequency is
    unknown."""
    _, what, unit = _PHASE_UNIT_OPTIONS[option]
    if not hasattr(f, "gauge_length_m") or (
        option == "--strain-rate" and not hasattr(f, "strain_rate_per_rad")
    ):
        raise AudaceDisplayError(
            f"{option} needs invisensing >= 1.6.0 (pip install -U invisensing)."
        )
    content_type, name = _content_type(f)
    if f.mode not in (Mode.PHASE, Mode.PHASE_AMPLITUDE):
        raise AudaceDisplayError(
            f"{option} shows a phase as {what} ({unit}), and {f.path.name} "
            f"holds '{name}' (content type {content_type}, mode {f.mode.value}), "
            f"not a phase. Drop {option}."
        )
    if f.gauge_length_m is None:
        raise AudaceDisplayError(
            f"{option} needs the gauge length the phase was measured over, and "
            f"{f.path.name} ('{name}', content type {content_type}) does not record "
            "its gauge length. Audace writes the gauge length into a phase file only "
            f"from the release that adds it, so {option} needs a recording made with "
            "that release or a later one; a MOZART or MOZART_PLUS phase and a file "
            "converted without it carry none. No gauge length is assumed: drop "
            f"{option} to show the phase in radians."
        )
    if option == "--strain-rate" and not reader.line_rate_hz(f) > 0.0:
        raise AudaceDisplayError(
            f"--strain-rate multiplies each line's strain increment by the trigger "
            f"frequency, and {f.path.name} does not know its own (trigger frequency "
            f"{f.trig_frequency} Hz). No rate is assumed: use --nanostrain to show "
            "the strain."
        )


def _check_nanostrain(f) -> None:
    """:func:`_check_phase_unit` for ``--nanostrain``."""
    _check_phase_unit(f, "--nanostrain")


def _check_strain_rate(f) -> None:
    """:func:`_check_phase_unit` for ``--strain-rate``."""
    _check_phase_unit(f, "--strain-rate")


def _requested_unit(args) -> Optional[str]:
    """The ``--nanostrain`` / ``--strain-rate`` option of ``args``, or
    ``None`` (radians, as stored)."""
    if getattr(args, "strain_rate", False):
        return "--strain-rate"
    if getattr(args, "nanostrain", False):
        return "--nanostrain"
    return None


def _fiber_kwargs(args) -> dict:
    """``File(...)`` keywords of ``--fiber-group-index N``: ``{}`` without it;
    else ``{"fiber_group_index": N}`` — the recording's metres labelled at
    that index (invisensing >= 1.7.0). Only an index below 1, above 100 or
    not finite is refused, in invisensing's words."""
    n = getattr(args, "fiber_group_index", None)
    if n is None:
        return {}
    try:
        from invisensing import light_speed_of_group_index
    except ImportError:
        raise AudaceDisplayError(
            "--fiber-group-index needs invisensing >= 1.7.0 (pip install -U invisensing)."
        ) from None
    try:
        light_speed_of_group_index(n)
    except ValueError as e:
        raise AudaceDisplayError(f"--fiber-group-index: {e}") from None
    return {"fiber_group_index": n}


def _open(args) -> File:
    """The file of ``args``: read as strain with ``--nanostrain``
    (``phase_unit="nanostrain"``), as strain rate with ``--strain-rate``
    (``phase_unit="strain_rate"``), as stored otherwise; its metres labelled
    at ``--fiber-group-index`` when given, else at the file's own speed of
    light in the fibre."""
    fiber = _fiber_kwargs(args)
    option = _requested_unit(args)
    if option is None:
        return File(args.path, **fiber)
    check = _check_strain_rate if option == "--strain-rate" else _check_nanostrain
    with File(args.path, **fiber) as probe:
        check(probe)
    try:
        return File(args.path, phase_unit=_PHASE_UNIT_OPTIONS[option][0], **fiber)
    except ValueError as e:  # a refusal the check above did not foresee
        raise AudaceDisplayError(f"{option}: {e}") from None


def _open_and_run(do_func, args) -> int:
    with _open(args) as f:
        return do_func(f, args)


# --- argparse ----------------------------------------------------------------


def _add_io(p: argparse.ArgumentParser) -> None:
    p.add_argument("path", help="File (.dat / .h5 / .hdf5 / .tdms / .sgy)")
    p.add_argument("--save", metavar="PNG", default=None,
                   help="Save to PNG (otherwise interactive display).")
    p.add_argument("--dpi", type=int, default=150, help="PNG DPI (default 150).")
    p.add_argument("--title", default=None, help="Custom figure title.")
    _add_fiber(p)


def _add_fiber(p: argparse.ArgumentParser) -> None:
    p.add_argument("--fiber-group-index", type=float, default=None, metavar="N",
                   help="Label the recording's metres at this fibre group index "
                        "(e.g. 1.4682, SMF-28 at 1550 nm) instead of the speed of "
                        "light the file states (2e8 m/s, index 1.49896229, on a "
                        "recording made before the acquisition wrote it). Only the "
                        "labels change, never the samples. Any number from 1 to 100.")


def _add_window(p: argparse.ArgumentParser) -> None:
    p.add_argument("--start-time", type=float, default=None, help="Skip the first seconds.")
    p.add_argument("--duration", type=float, default=None, help="Duration to process (s).")
    p.add_argument("--start-distance", type=float, default=None, help="Lower spatial bound (m).")
    p.add_argument("--end-distance", type=float, default=None, help="Upper spatial bound (m).")


def _add_decim(p: argparse.ArgumentParser) -> None:
    p.add_argument("--max-time-bins", type=int, default=2000,
                   help="Max time bins (X resolution). Default 2000.")
    p.add_argument("--max-space-bins", type=int, default=2000,
                   help="Max spatial bins (Y resolution). Default 2000.")
    p.add_argument("--reduce", choices=list(decimate.REDUCERS), default="mean",
                   help="Temporal bin reducer (default mean).")
    p.add_argument("--max-pulses", type=int, default=None,
                   help="Limit the number of pulses read (default: whole file).")


def _add_variance_opt(p: argparse.ArgumentParser) -> None:
    p.add_argument("--variance", choices=["linear", "log"], default=None,
                   metavar="{linear,log}",
                   help="Color each time bin by its temporal VARIANCE instead of "
                        "the mean ('log' = log10 of the variance, floored 120 dB "
                        "below its maximum: a display scale, not the console's "
                        "log10(1 + variance) of archive exports). Off by default; "
                        "overrides --reduce and --db.")


def _add_heatmap_style(p: argparse.ArgumentParser) -> None:
    p.add_argument("--db", action="store_true", help="dB scale (ref = observed max).")
    p.add_argument("--vmin", type=float, default=None, help="Lower colorbar bound (auto).")
    p.add_argument("--vmax", type=float, default=None, help="Upper colorbar bound (auto).")
    p.add_argument("--cmap", default=None, help="matplotlib colormap (default auto).")
    p.add_argument("--aspect", choices=["auto", "equal"], default="auto",
                   help="Aspect ratio (default auto).")


def _add_unit(p: argparse.ArgumentParser) -> None:
    k = "NANOSTRAIN_PER_RAD_METRE" if _K_NANOSTRAIN is None else f"{_K_NANOSTRAIN:g}"
    unit = p.add_mutually_exclusive_group()
    unit.add_argument("--nanostrain", action="store_true",
                      help=f"Show a phase as strain, in nm/m: phase / gauge length x {k} "
                           "(invisensing's constant), with the gauge length "
                           "the file records. Refused for a file that holds no phase or "
                           "does not record its gauge length.")
    unit.add_argument("--strain-rate", action="store_true",
                      help="Show a phase as strain rate, in nm/m/s: the strain increment "
                           "from the previous line times the real trigger frequency, "
                           f"(phase[n] - phase[n-1]) / gauge length x {k} x f_trig, "
                           "computed at the full line rate before any decimation. The "
                           "file's first line, and a line after a gap of its time axis "
                           "(lost shots), have none (NaN). Refused as --nanostrain, and "
                           "for a file without a trigger frequency.")


def _add_backend(p: argparse.ArgumentParser) -> None:
    p.add_argument("--backend", choices=["matplotlib", "mpl", "pyqtgraph", "qt", "pg"],
                   default="matplotlib", metavar="{matplotlib,pyqtgraph}",
                   help="Interactive backend: matplotlib (default) or pyqtgraph "
                        "(fast, smooth pan/zoom; needs the [interactive] extra). "
                        "Ignored with --save (PNG always via matplotlib).")


def _add_scope_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("--fps", type=int, default=30,
                   help="Render frames/s (default 30). Higher = smoother, more CPU.")
    p.add_argument("--speed", type=float, default=1.0,
                   help="Playback speed (1.0 = real time = actual pulse rate, "
                        "in lines/s).")
    p.add_argument("--line", type=int, default=0,
                   help="Line to freeze with --save (default 0).")
    p.add_argument("--max-points", type=int, default=4000,
                   help="Max points drawn per line (peak decimation). Default 4000.")


def _add_trace_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("--subsample-time", type=int, default=1,
                   help="Keep only one pulse out of N (default 1).")
    p.add_argument("--max-points", type=int, default=4000,
                   help="Max points in the line plot (min/max envelope). Default 4000.")
    p.add_argument("--max-pulses", type=int, default=None,
                   help="Limit the number of pulses read (default: whole file).")


# --- Help text ---------------------------------------------------------------

_DESCRIPTION = """\
audace-display -- visualization of Audace DAS acquisition files.

Reads any file produced by the Filewriter (.dat / .h5 / .hdf5 / .tdms / .sgy)
via the "invisensing" library, auto-detects what it holds from its content
type, and shows:
    demodulated file  ->  heatmap (distance x time waterfall)
    raw file          ->  animated oscilloscope (lines in real time, looping)

Simplest form -- no subcommand needed:
    audace-display acq.dat                 # auto: heatmap or oscilloscope
    audace-display acq.dat --save out.png  # PNG export (headless mode)

Subcommands for fine control: info, heatmap, bandheatmaps, fft, trace, scope,
inspect, demod. The desktop viewer: gui (pip install "audace-display[gui]").
"""

_EPILOG = """\
Examples
--------
  # Auto-detection (heatmap if demodulated, oscilloscope if raw)
  audace-display acq.dat
  audace-display acq.dat --save waterfall.png --dpi 200

  # File metadata + stats
  audace-display info acq.dat

  # Waterfall: channel, dB scale, time/distance window, aggregation
  audace-display heatmap acq.dat --channel magnitude --db
  audace-display heatmap acq.dat --start-time 0.5 --duration 1.0
  audace-display heatmap acq.dat --start-distance 50 --end-distance 200 --reduce rms

  # Temporal spectrum (FFT along the pulses)
  audace-display fft acq.dat --position 120
  audace-display fft acq.dat --position 50,100,150 --db
  audace-display fft acq.dat --position-range 100:200 --window blackman

  # 1-D time trace at one position (meters)
  audace-display trace acq.dat --position 100

  # One location: waveform + FFT spectrum in a single figure
  audace-display inspect acq.dat --index 120

  # A phase as strain, in nm/m (the file must record its gauge length)
  audace-display phase.h5 --nanostrain
  audace-display heatmap phase.h5 --nanostrain --variance log

  # ...or as strain rate, in nm/m/s
  audace-display phase.h5 --strain-rate
  audace-display fft phase.h5 --strain-rate --position 120

  # An old recording (2e8 m/s) labelled at its fibre's group index
  audace-display info old.dat --fiber-group-index 1.4682
  audace-display heatmap old.dat --fiber-group-index 1.4682 --start-distance 1000

  # Demodulation via an external script (no demod code shipped) -> heatmap
  audace-display demod raw.dat --script my_demod.py --save demod.png

  # The desktop viewer (pip install "audace-display[gui]")
  audace-display gui acq.dat
  audace-display gui phase.h5 --lang fr --view heatmap --unit nanostrain

Channels available per file mode:
  raw               raw
  phase             phase
  amplitude         magnitude (the card's, in volts)
  arctan            arctan
  i / q             i / q
  custom            samples (a script's output, as stored)
  variance          variance, log_variance (log10(1 + variance))
  log_variance      variance, log_variance
  Files written before the Audace data format v1 may hold pairs:
  iq                i, q, magnitude (|IQ|), phase_wrapped (arg(I+jQ))
  arctan_magnitude  arctan, magnitude (sqrt(I^2+Q^2))
  phase_amplitude   phase, magnitude
  (aliases: mag/amp -> magnitude, atan -> arctan, angle -> phase_wrapped)

--nanostrain (every view but demod): a phase is shown as strain, in nm/m,
  phase / gauge_length_m x NANOSTRAIN_PER_RAD_METRE (invisensing's constant,
  110.37), with the gauge length the file records (invisensing >= 1.6.0).
  A file that holds no phase, or a phase without its gauge length, is refused.

--strain-rate (every view but demod; not with --nanostrain): a phase is shown
  as strain rate, in nm/m/s: the strain increment from the previous line
  times the real trigger frequency,
      (strain[n] - strain[n-1]) x f_trig
    = (phase[n] - phase[n-1]) / gauge_length_m x 110.37 x f_trig,
  computed by invisensing at the full line rate, then decimated. The file's
  first line, and a line after a gap of its time axis (lost shots), have no
  increment over one period: NaN, shown as missing (skipped by the time bins
  and the scales; the column's mean in the FFT and band filters). Refused as
  --nanostrain, and for a file without a trigger frequency.

Distances: every metre (axes, --start-distance / --end-distance,
  --position, the gauge length) is at the speed of light in the fibre the
  file states -- round(c / n) of the fibre group index n the acquisition was
  configured with (default 1.4682) -- shown by `info` ("Fiber index"); a
  recording made before the acquisition wrote it is at 2e8 m/s (index
  1.49896229), as before. --fiber-group-index N (every view) labels the
  recording at N instead (invisensing >= 1.7.0): the samples are the same,
  the metres are 2.1 % longer at 1.4682 than at 2e8 m/s.

Large files (200 MB - 10 GB): streamed, decimated reading, bounded RAM.

Display:
  (default)             interactive matplotlib
  --save file.png       headless PNG export (server, no X11/Wayland)
  --backend pyqtgraph   fast interactive viewer, smooth pan/zoom
                        (pip install audace-display[interactive])

Per-command options: audace-display <command> --help
"""

_AUTO_DESC = """\
Default mode: no need to type "auto", "audace-display FILE" uses it. Shows a
heatmap if the file is demodulated, an animated oscilloscope (lines in real
time, looping) if raw.
"""
_AUTO_EPILOG = """\
Examples:
  audace-display acq.dat                    # demod -> heatmap ; raw -> oscilloscope
  audace-display acq.dat --db --save out.png
  audace-display raw.dat --speed 0.25       # oscilloscope in slow motion
  audace-display raw.dat --backend pyqtgraph --fps 60
"""

_SCOPE_DESC = """\
Oscilloscope: replays the lines (pulses) in REAL TIME and LOOPING. Each frame
draws the current line (amplitude vs distance along the fiber); the cursor
advances at the actual pulse rate * --speed lines/s. At high rates, lines are skipped
to stay real-time. This is the default mode for a raw file.

With --save, a single line (--line N) is exported to PNG (frozen).
"""
_SCOPE_EPILOG = """\
Examples:
  audace-display scope raw.dat                     # real time, looping
  audace-display scope raw.dat --speed 0.25        # 4x slow motion
  audace-display scope raw.dat --fps 60 --backend pyqtgraph
  audace-display scope raw.dat --start-time 1.0 --duration 0.5   # loop over 0.5 s
  audace-display scope raw.dat --line 1000 --save frame.png      # 1 frozen line
"""

_INFO_DESC = """\
Shows the header metadata (mode, duration, sample rate, positions, flags...) and
quick stats (min / percentiles / mean / std) on the default channel.
"""
_INFO_EPILOG = """\
Example:
  audace-display info acq.dat
"""

_HEATMAP_DESC = """\
Distance x time waterfall: X = distance along the fiber, Y = time, color =
channel. Streamed and decimated to screen resolution.
"""
_HEATMAP_EPILOG = """\
Examples:
  audace-display heatmap acq.dat
  audace-display heatmap acq.dat --channel phase --cmap RdBu_r
  audace-display heatmap acq.dat --db --vmin -60 --vmax 0
  audace-display heatmap acq.dat --variance log       # temporal log-variance
  audace-display heatmap acq.dat --start-time 0.5 --duration 1.0
  audace-display heatmap acq.dat --start-distance 50 --end-distance 200
  audace-display heatmap acq.dat --reduce rms --max-time-bins 4000
  audace-display heatmap acq.dat --save wf.png --dpi 200
"""

_BAND_DESC = """\
Per-frequency-band waterfalls. Splits the temporal frequency axis (FFT along the
pulses) into up to 3 contiguous bands and shows one distance x time heatmap per
band, plus the global heatmap. A band panel band-pass filters the signal to its
[f_lo, f_hi) range, then colors each time bin by its energy (temporal RMS, or
variance / log-variance with --variance).

Bands are defined by their upper edges --f1 < --f2 < --f3 (only --f1 is
required): [f0, f1), [f1, f2), [f2, f3). --f0 (default 0, i.e. keep DC) sets the
lower edge of the first band, e.g. --f0 100 drops everything below 100 Hz.

Each position is detrended (mean + linear trend removed) before the FFT so the
DC offset / slow drift of a demodulated channel does not leak broadband ringing
into the bands; pass --no-detrend to keep the raw signal (incl. DC).

The full time series is held in RAM for the FFT: restrict big files with
--duration / --max-pulses / --max-space-bins.
"""
_BAND_EPILOG = """\
Examples:
  audace-display bandheatmaps acq.dat --f1 200 --f2 500 --f3 1500
  audace-display bandheatmaps acq.dat --f0 100 --f1 200 --f2 500 --f3 1500
  audace-display bandheatmaps acq.dat --f1 50                      # 1 band + global
  audace-display bandheatmaps acq.dat --f1 200 --f2 500 --variance log
  audace-display bandheatmaps acq.dat --f1 200 --f2 500 --duration 2 --save bands.png
"""

_FFT_DESC = """\
Temporal spectrum: FFT along the pulse axis, at one or more positions. Several
positions / a range = incoherent average (preserves peaks). Without --position
or --position-range: average over the whole fiber.
"""
_FFT_EPILOG = """\
Examples:
  audace-display fft acq.dat --position 120
  audace-display fft acq.dat --position 50,100,150 --db   # one curve per position
  audace-display fft acq.dat --position-range 100:200     # average over the range
  audace-display fft acq.dat                              # average over whole fiber
  audace-display fft acq.dat --window blackman --fmax 200
"""

_TRACE_DESC = """\
1-D time trace at a given position (in meters). Min/max-preserving decimation to
avoid visual aliasing over millions of samples.
"""
_TRACE_EPILOG = """\
Examples:
  audace-display trace acq.dat --position 100
  audace-display trace acq.dat --position 100 --start-time 1.0 --duration 0.5
  audace-display trace acq.dat --position 100 --subsample-time 5
"""

_DEMOD_DESC = """\
Demodulates a raw file via an EXTERNAL PYTHON SCRIPT that you provide, then shows
the result as a heatmap. No demodulation code is shipped in audace-display: it
only loads and applies your script.

The script (--script) must define EITHER a `Demodulator` class with a
process(chunk) method, OR a function
demodulate(chunk, *, sample_rate, trig_frequency, line_size, meta), returning a
(rows, positions) float32 array. Optional attributes: OUTPUT_LABEL, IS_ANGULAR.
"""
_DEMOD_EPILOG = """\
Example script (my_demod.py):
  import numpy as np
  OUTPUT_LABEL = "magnitude (u)"
  def demodulate(chunk, *, sample_rate, trig_frequency, line_size, meta):
      i = chunk[:, 0::2].astype(np.float32)
      q = chunk[:, 1::2].astype(np.float32)
      return np.sqrt(i * i + q * q)

Usage:
  audace-display demod raw.dat --script my_demod.py
  audace-display demod raw.dat --script my_demod.py --db --save demod.png
"""


def _subparser(sub, name: str, *, help: str, description: str, epilog: str):
    return sub.add_parser(
        name,
        help=help,
        description=description,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


_INSPECT_DESC = """\
Single location: one figure with the time waveform (top) and its FFT spectrum
(bottom), for one location index / position. The waveform has its DC offset and
a linear trend removed; the dominant FFT peak is annotated. Mirrors the
standalone phase_diff_location_visualize script.

Works on a built-in channel, or -- with --script -- on the output of a demod
plugin (same contract as the 'demod' command): inspect one location of, e.g.,
the DUI phase produced from an ArctanMagnitude file.
"""
_INSPECT_EPILOG = """\
Examples:
  audace-display inspect acq.dat --index 120
  audace-display inspect acq.dat --position 60 --duration 10
  audace-display inspect acq.dat --index 120 --fft-log --fmax 200
  audace-display inspect acq.dat --index 120 --no-detrend --clip-percentile 0
  audace-display inspect acq.dat --index 120 --save loc120.png

  # On a demod plugin output (DUI phase), one location:
  audace-display inspect arctan_mag.dat --script plugins/dui_rust.py --index 120
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="audace-display",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "-V", "--version", action="version",
        version=f"audace-display {__version__}",
    )
    sub = p.add_subparsers(dest="command", metavar="<command>")

    # auto (default, injected when no subcommand is given)
    p_auto = _subparser(sub, "auto",
                        help="Auto: heatmap (demodulated) or animated oscilloscope (raw).",
                        description=_AUTO_DESC, epilog=_AUTO_EPILOG)
    _add_io(p_auto)
    p_auto.add_argument("--channel", "-c", default=None, help="Channel (default depends on mode).")
    _add_window(p_auto)
    _add_decim(p_auto)
    _add_heatmap_style(p_auto)
    _add_variance_opt(p_auto)
    _add_scope_opts(p_auto)   # raw -> animated oscilloscope
    _add_unit(p_auto)
    _add_backend(p_auto)
    p_auto.set_defaults(func=lambda a: _open_and_run(do_auto, a))

    # info
    p_info = _subparser(sub, "info",
                        help="Metadata + stats of the default channel.",
                        description=_INFO_DESC, epilog=_INFO_EPILOG)
    p_info.add_argument("path", help="File (.dat / .h5 / .hdf5 / .tdms / .sgy)")
    _add_fiber(p_info)
    _add_unit(p_info)
    p_info.set_defaults(func=lambda a: _open_and_run(do_info, a))

    # heatmap
    p_heat = _subparser(sub, "heatmap",
                        help="Distance x time waterfall.",
                        description=_HEATMAP_DESC, epilog=_HEATMAP_EPILOG)
    _add_io(p_heat)
    p_heat.add_argument("--channel", "-c", default=None, help="Channel (default depends on mode).")
    _add_window(p_heat)
    _add_decim(p_heat)
    _add_heatmap_style(p_heat)
    _add_variance_opt(p_heat)
    _add_unit(p_heat)
    _add_backend(p_heat)
    p_heat.set_defaults(func=lambda a: _open_and_run(do_heatmap, a))

    # bandheatmaps
    p_band = _subparser(sub, "bandheatmaps",
                        help="Per-frequency-band waterfalls (+ global).",
                        description=_BAND_DESC, epilog=_BAND_EPILOG)
    _add_io(p_band)
    p_band.add_argument("--channel", "-c", default=None, help="Channel (default depends on mode).")
    _add_window(p_band)
    p_band.add_argument("--max-time-bins", type=int, default=2000,
                        help="Max time bins (Y resolution). Default 2000.")
    p_band.add_argument("--max-space-bins", type=int, default=1000,
                        help="Max spatial bins (X resolution). Default 1000 "
                             "(the full time series is held in RAM).")
    p_band.add_argument("--max-pulses", type=int, default=None,
                        help="Limit the number of pulses read (default: whole file).")
    p_band.add_argument("--f0", type=float, default=None, metavar="HZ",
                        help="Lower edge of the FIRST band (Hz). Default 0 (keeps DC).")
    p_band.add_argument("--f1", type=float, default=None, metavar="HZ",
                        help="Upper edge of band 1 (Hz). REQUIRED.")
    p_band.add_argument("--f2", type=float, default=None, metavar="HZ",
                        help="Upper edge of band 2 (Hz). Optional.")
    p_band.add_argument("--f3", type=float, default=None, metavar="HZ",
                        help="Upper edge of band 3 (Hz). Optional.")
    p_band.add_argument("--no-detrend", action="store_true",
                        help="Keep the raw signal (incl. DC) before the band FFT. "
                             "By default each position is detrended (mean + linear "
                             "trend removed) to avoid edge-ringing artifacts.")
    p_band.add_argument("--cmap", default=None, help="matplotlib colormap (default auto).")
    _add_variance_opt(p_band)
    _add_unit(p_band)
    _add_backend(p_band)
    p_band.set_defaults(func=lambda a: _open_and_run(do_bandheatmaps, a))

    # fft
    p_fft = _subparser(sub, "fft",
                       help="Temporal spectrum (FFT along the pulses).",
                       description=_FFT_DESC, epilog=_FFT_EPILOG)
    _add_io(p_fft)
    p_fft.add_argument("--channel", "-c", default=None, help="Channel (default depends on mode).")
    _add_window(p_fft)
    grp = p_fft.add_mutually_exclusive_group()
    grp.add_argument("--position", "-p", default=None, metavar="M_OR_LIST",
                     help="Position(s) in m: '12.5' or '10,20,30'.")
    grp.add_argument("--position-range", default=None, metavar="A:B",
                     help="Range of positions to average (m): '50:100'.")
    p_fft.add_argument("--window", default="hann",
                       choices=["rect", "hann", "hamming", "blackman"],
                       help="Time window (default hann).")
    p_fft.add_argument("--no-detrend", action="store_true",
                       help="Do not remove the per-position mean before the FFT.")
    p_fft.add_argument("--fmax", type=float, default=None, help="Limit X axis (Hz). Default Nyquist.")
    p_fft.add_argument("--db", action="store_true", help="Y axis in dB (ref = max).")
    p_fft.add_argument("--subsample-time", type=int, default=1, help="One pulse out of N.")
    p_fft.add_argument("--max-pulses", type=int, default=None, help="Limit the number of pulses read.")
    _add_unit(p_fft)
    _add_backend(p_fft)
    p_fft.set_defaults(func=lambda a: _open_and_run(do_fft, a))

    # trace
    p_tr = _subparser(sub, "trace",
                      help="1-D time trace at one position.",
                      description=_TRACE_DESC, epilog=_TRACE_EPILOG)
    _add_io(p_tr)
    p_tr.add_argument("--channel", "-c", default=None, help="Channel (default depends on mode).")
    _add_window(p_tr)
    p_tr.add_argument("--position", "-p", type=float, required=True, help="Position (m).")
    _add_trace_opts(p_tr)
    _add_unit(p_tr)
    _add_backend(p_tr)
    p_tr.set_defaults(func=lambda a: _open_and_run(do_trace, a))

    # scope (animated oscilloscope)
    p_sc = _subparser(sub, "scope",
                      help="Oscilloscope: replays the lines in real time, looping.",
                      description=_SCOPE_DESC, epilog=_SCOPE_EPILOG)
    _add_io(p_sc)
    p_sc.add_argument("--channel", "-c", default=None, help="Channel (default depends on mode).")
    p_sc.add_argument("--start-time", type=float, default=None,
                      help="Loop start (s).")
    p_sc.add_argument("--duration", type=float, default=None,
                      help="Loop duration (s). Default: whole file.")
    _add_scope_opts(p_sc)
    _add_unit(p_sc)
    _add_backend(p_sc)
    p_sc.set_defaults(func=lambda a: _open_and_run(do_scope, a))

    # inspect (waveform + FFT for one location)
    p_ins = _subparser(sub, "inspect",
                       help="One location: waveform + FFT spectrum in one figure.",
                       description=_INSPECT_DESC, epilog=_INSPECT_EPILOG)
    _add_io(p_ins)
    p_ins.add_argument("--channel", "-c", default=None,
                       help="Channel (default depends on mode). Ignored with --script.")
    p_ins.add_argument("--script", default=None,
                       help="Demod script (same contract as 'demod'): inspect one "
                            "location of the plugin output (e.g. DUI phase).")
    p_ins.add_argument("--start-time", type=float, default=None, help="Skip the first seconds.")
    p_ins.add_argument("--duration", type=float, default=None, help="Duration to read (s).")
    grp_ins = p_ins.add_mutually_exclusive_group()
    grp_ins.add_argument("--index", type=int, default=None, metavar="N",
                         help="Location/column index (default: middle of the fiber).")
    grp_ins.add_argument("--position", "-p", type=float, default=None, metavar="M",
                         help="Position in meters (converted to the nearest index).")
    p_ins.add_argument("--subsample-time", type=int, default=1,
                       help="Keep only one pulse out of N (default 1).")
    p_ins.add_argument("--max-pulses", type=int, default=None,
                       help="Limit the number of pulses read (default: whole file).")
    p_ins.add_argument("--no-detrend", action="store_true",
                       help="Only remove DC, keep the linear trend.")
    p_ins.add_argument("--clip-percentile", type=float, default=99.0,
                       help="Waveform y-axis percentile clip (default 99; 0 disables).")
    p_ins.add_argument("--window", default="hann",
                       choices=["rect", "hann", "hamming", "blackman"],
                       help="FFT time window (default hann).")
    p_ins.add_argument("--fmax", type=float, default=None,
                       help="Limit the FFT frequency axis (Hz). Default Nyquist.")
    p_ins.add_argument("--fft-log", action="store_true",
                       help="Logarithmic FFT magnitude axis.")
    _add_unit(p_ins)
    p_ins.set_defaults(func=lambda a: _open_and_run(do_inspect, a), _accepts_plugin_args=True)

    # demod (external plugin)
    p_dem = _subparser(sub, "demod",
                       help="Demodulate via an external script, then heatmap.",
                       description=_DEMOD_DESC, epilog=_DEMOD_EPILOG)
    _add_io(p_dem)
    p_dem.add_argument("--script", required=True,
                       help="Python script implementing the demod contract (see below).")
    _add_window(p_dem)
    _add_decim(p_dem)
    _add_heatmap_style(p_dem)
    _add_variance_opt(p_dem)
    _add_backend(p_dem)
    p_dem.set_defaults(func=lambda a: _open_and_run(do_demod, a), _accepts_plugin_args=True)

    # gui: listed here for the help; `main` hands its arguments to the viewer
    # as they are (gui_launcher), its own options included.
    from . import gui_launcher
    p_gui = _subparser(sub, "gui",
                       help="Desktop viewer (the audace-display-gui package).",
                       description=gui_launcher.DESCRIPTION,
                       epilog=gui_launcher.OPTIONS)
    p_gui.add_argument("viewer_args", nargs=argparse.REMAINDER, metavar="[FILE] [options]")
    p_gui.set_defaults(func=lambda a: gui_launcher.main(a.viewer_args))

    return p


def main(argv: Optional[list[str]] = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    # The desktop viewer parses its own options: everything after `gui` is
    # handed over as it is.
    if raw_argv[:1] == ["gui"]:
        from . import gui_launcher
        try:
            return gui_launcher.main(raw_argv[1:])
        except AudaceDisplayError as e:
            sys.stderr.write(f"ERROR: {e}\n")
            return 1
    # No explicit subcommand -> auto mode.
    if raw_argv and not raw_argv[0].startswith("-") and raw_argv[0] not in SUBCOMMANDS:
        raw_argv = ["auto"] + raw_argv

    parser = build_parser()
    # Subcommands that take a demod plugin (`demod`, `inspect --script`) forward
    # any unrecognised flags to the plugin, so it can be configured on the
    # command line (e.g. `--script mozart.py --heterodyne 20000`) without env
    # vars. Every other case keeps argparse's strict "unrecognized arguments".
    args, unknown = parser.parse_known_args(raw_argv)
    if unknown:
        if getattr(args, "_accepts_plugin_args", False) and getattr(args, "script", None):
            args.plugin_args = unknown
        else:
            parser.error("unrecognized arguments: " + " ".join(unknown))
    else:
        args.plugin_args = []
    if not getattr(args, "func", None):
        parser.print_help()
        return 2

    try:
        return args.func(args)
    except AudaceDisplayError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 1
    except FileNotFoundError as e:
        sys.stderr.write(f"ERROR: file not found: {e}\n")
        return 1
    except BrokenPipeError:
        return 0
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"ERROR: unexpected {e.__class__.__name__}: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
