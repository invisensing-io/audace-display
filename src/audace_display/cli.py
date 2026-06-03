"""Command-line interface for audace-display.

Zero-config usage: ``audace-display FILE`` automatically picks a **heatmap**
(demodulated file) or an **animated oscilloscope** (raw file). The
``info`` / ``heatmap`` / ``fft`` / ``trace`` / ``scope`` / ``inspect`` /
``demod`` subcommands give fine-grained control.
"""
from __future__ import annotations

import argparse
import math
import sys
from typing import Optional

import numpy as np
from invisensing import File, Mode

from . import decimate, processing, reader
from ._errors import AudaceDisplayError
from .channels import DEFAULT_CHANNEL, resolve_channel

try:
    from . import __version__
except Exception:  # pragma: no cover - version is informational only
    __version__ = "0"

# FFT memory budget (float32 cells) before spatial subsampling.
_FFT_MAX_CELLS = 256 * 1024 * 1024 // 4
_FFT_MAX_POSITIONS = 256

SUBCOMMANDS = {"auto", "info", "heatmap", "fft", "trace", "scope", "demod", "inspect"}


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


# --- Heatmap rendering (shared by heatmap / demod / auto) ---------------------


def _render_heatmap(f, args, transform, base_label: str, is_angular: bool, subtitle: str) -> None:
    res = reader.load_decimated(
        f, transform,
        max_time_bins=args.max_time_bins,
        max_space_bins=args.max_space_bins,
        reduce=args.reduce,
        start_time=args.start_time,
        duration=args.duration,
        start_distance=args.start_distance,
        end_distance=args.end_distance,
        max_pulses=args.max_pulses,
    )
    data = res.data
    use_db = getattr(args, "db", False)
    label = base_label
    if use_db:
        data, ref = processing.to_db(data)
        head = base_label.split(" (")[0]
        unit = base_label.split("(")[-1].rstrip(")") if "(" in base_label else ""
        label = f"{head} (dB, ref={ref:.3g} {unit})".strip()
    vmin, vmax = processing.auto_clim(
        data, is_angular=is_angular, use_db=use_db, vmin=args.vmin, vmax=args.vmax
    )
    cmap = args.cmap or processing.default_cmap(is_angular, use_db)
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


def do_info(f, args) -> int:
    flags = _decode_flags(f.flags)
    d_step = reader.position_step_m(f)
    print(f"File: {f.path}")
    print(f"  Mode           : {f.mode.value}")
    print(f"  Timestamp      : {f.timestamp}")
    print(f"  Pulses         : {f.num_lines:,}")
    print(f"  Duration       : {f.duration:.3f} s")
    print(f"  Sample rate    : {f.sample_rate:,} Hz")
    print(f"  Trig frequency : {f.trig_frequency:,} Hz")
    print(f"  Line size      : {f.line_size:,} samples/pulse")
    print(f"  Positions      : {f.positions_per_line:,} per pulse")
    print(f"  Spatial step   : {d_step:.3f} m")
    print(f"  Fiber length   : {f.distance:.2f} m")
    print(f"  ADC range      : {f.range:.3f} V")
    print(f"  Sample size    : {f.sample_size} B")
    print(f"  Sockets        : {f.header.num_channels}")
    print(f"  Flags          : 0x{f.flags:03X} ({flags})")

    canonical = DEFAULT_CHANNEL[f.mode]
    _, resolver, _, _ = resolve_channel(canonical, f.mode)
    n_stat = min(1000, f.num_lines)
    if n_stat > 0:
        buf = f.read_lines(n_stat)
        data = resolver(f, buf)
        mi, p1, p50, p99, ma = np.percentile(data, [0, 1, 50, 99, 100]).tolist()
        print(
            f"\n  Stats for channel '{canonical}' over {n_stat:,} pulses:"
            f"\n    min={mi:+.4g}  p1={p1:+.4g}  p50={p50:+.4g}  p99={p99:+.4g}  max={ma:+.4g}"
            f"\n    mean={data.mean():+.4g}  std={data.std():.4g}"
        )
    return 0


def _decode_flags(flags: int) -> str:
    from invisensing import (
        FLAG_DEMODULATED, FLAG_FLOAT, FLAG_PHASE,
        FLAG_INTERLEAVED, FLAG_UNSIGNED, FLAG_AC, FLAG_HIZ,
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
    return " | ".join(bits) if bits else "0"


def do_heatmap(f, args) -> int:
    canonical, resolver, label, is_ang = resolve_channel(args.channel, f.mode)
    _render_heatmap(
        f, args, _channel_transform(f, resolver), label, is_ang,
        subtitle=f"channel={canonical}",
    )
    return 0


def do_demod(f, args) -> int:
    from .demod import load_demodulator
    plugin = load_demodulator(args.script, f)
    _render_heatmap(
        f, args, plugin.transform, plugin.label, plugin.is_angular,
        subtitle=f"demod={args.script}",
    )
    return 0


def do_trace(f, args) -> int:
    canonical, resolver, label, _ = resolve_channel(args.channel, f.mode)
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
    canonical, resolver, label, _ = resolve_channel(args.channel, f.mode)
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
    advances at ``trig_frequency * speed`` lines/s. With ``--save``, exports a
    frozen line to PNG.
    """
    if not hasattr(f, "seek_lines"):
        raise AudaceDisplayError(
            "oscilloscope mode requires invisensing >= 1.1.0 (O(1) per-line seek)."
        )
    canonical, resolver, label, _ = resolve_channel(args.channel, f.mode)
    trig = f.trig_frequency
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
    lo = float(np.percentile(sample, 0.1))
    hi = float(np.percentile(sample, 99.9))
    pad = 0.1 * ((hi - lo) or abs(hi) or 1.0)
    ylim = (lo - pad, hi + pad)

    title = args.title or (
        f"{f.path.name} -- {canonical} (oscilloscope x{args.speed:g}, {trig} lines/s)"
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
        from .demod import load_demodulator
        plugin = load_demodulator(args.script, f)
        probe = np.asarray(plugin.transform(f.read_lines(1)))
        if probe.ndim != 2:
            raise AudaceDisplayError(
                f"the demod script must return a 2-D array, got {probe.shape}."
            )
        if hasattr(f, "seek_lines"):
            f.seek_lines(0)
        return plugin.transform, plugin.label, f"demod={args.script}", probe.shape[1]

    canonical, resolver, label, _ = resolve_channel(args.channel, f.mode)
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
    times = np.arange(waveform.size) / eff_trig

    clip = None if args.clip_percentile == 0 else args.clip_percentile
    ylim = processing.percentile_limits(waveform, clip)

    freqs, magnitude = processing.temporal_fft(
        waveform, fs=eff_trig, window=args.window, detrend=detrend
    )

    stats_text = (
        f"Location index: {idx}\n"
        f"Position: {idx * d_step:.2f} m\n"
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


def _open_and_run(do_func, args) -> int:
    with File(args.path) as f:
        return do_func(f, args)


# --- argparse ----------------------------------------------------------------


def _add_io(p: argparse.ArgumentParser) -> None:
    p.add_argument("path", help="File (.dat / .hdf5 / .tdms / .sgy)")
    p.add_argument("--save", metavar="PNG", default=None,
                   help="Save to PNG (otherwise interactive display).")
    p.add_argument("--dpi", type=int, default=150, help="PNG DPI (default 150).")
    p.add_argument("--title", default=None, help="Custom figure title.")


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


def _add_heatmap_style(p: argparse.ArgumentParser) -> None:
    p.add_argument("--db", action="store_true", help="dB scale (ref = observed max).")
    p.add_argument("--vmin", type=float, default=None, help="Lower colorbar bound (auto).")
    p.add_argument("--vmax", type=float, default=None, help="Upper colorbar bound (auto).")
    p.add_argument("--cmap", default=None, help="matplotlib colormap (default auto).")
    p.add_argument("--aspect", choices=["auto", "equal"], default="auto",
                   help="Aspect ratio (default auto).")


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
                   help="Playback speed (1.0 = real time = trig_frequency lines/s).")
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

Reads any file produced by the Filewriter (.dat / .hdf5 / .tdms / .sgy) via the
"invisensing" library, auto-detects the mode, and shows:
    demodulated file  ->  heatmap (time x distance waterfall)
    raw file          ->  animated oscilloscope (lines in real time, looping)

Simplest form -- no subcommand needed:
    audace-display acq.dat                 # auto: heatmap or oscilloscope
    audace-display acq.dat --save out.png  # PNG export (headless mode)

Subcommands for fine control: info, heatmap, fft, trace, scope, inspect, demod.
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

  # Demodulation via an external script (no demod code shipped) -> heatmap
  audace-display demod raw.dat --script my_demod.py --save demod.png

Channels available per file mode:
  raw               raw
  iq                i, q, magnitude (|IQ|), phase_wrapped (arg(I+jQ))
  arctan_magnitude  arctan, magnitude (sqrt(I^2+Q^2))
  phase             phase
  (aliases: mag/amp -> magnitude, atan -> arctan, angle -> phase_wrapped)

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
advances at trig_frequency * --speed lines/s. At high rates, lines are skipped
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
Time x distance waterfall: X = time, Y = distance along the fiber, color =
channel. Streamed and decimated to screen resolution.
"""
_HEATMAP_EPILOG = """\
Examples:
  audace-display heatmap acq.dat
  audace-display heatmap acq.dat --channel phase --cmap RdBu_r
  audace-display heatmap acq.dat --db --vmin -60 --vmax 0
  audace-display heatmap acq.dat --start-time 0.5 --duration 1.0
  audace-display heatmap acq.dat --start-distance 50 --end-distance 200
  audace-display heatmap acq.dat --reduce rms --max-time-bins 4000
  audace-display heatmap acq.dat --save wf.png --dpi 200
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
    _add_scope_opts(p_auto)   # raw -> animated oscilloscope
    _add_backend(p_auto)
    p_auto.set_defaults(func=lambda a: _open_and_run(do_auto, a))

    # info
    p_info = _subparser(sub, "info",
                        help="Metadata + stats of the default channel.",
                        description=_INFO_DESC, epilog=_INFO_EPILOG)
    p_info.add_argument("path", help="File (.dat / .hdf5 / .tdms / .sgy)")
    p_info.set_defaults(func=lambda a: _open_and_run(do_info, a))

    # heatmap
    p_heat = _subparser(sub, "heatmap",
                        help="Time x distance waterfall.",
                        description=_HEATMAP_DESC, epilog=_HEATMAP_EPILOG)
    _add_io(p_heat)
    p_heat.add_argument("--channel", "-c", default=None, help="Channel (default depends on mode).")
    _add_window(p_heat)
    _add_decim(p_heat)
    _add_heatmap_style(p_heat)
    _add_backend(p_heat)
    p_heat.set_defaults(func=lambda a: _open_and_run(do_heatmap, a))

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
    p_ins.set_defaults(func=lambda a: _open_and_run(do_inspect, a))

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
    _add_backend(p_dem)
    p_dem.set_defaults(func=lambda a: _open_and_run(do_demod, a))

    return p


def main(argv: Optional[list[str]] = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    # No explicit subcommand -> auto mode.
    if raw_argv and not raw_argv[0].startswith("-") and raw_argv[0] not in SUBCOMMANDS:
        raw_argv = ["auto"] + raw_argv

    parser = build_parser()
    args = parser.parse_args(raw_argv)
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
