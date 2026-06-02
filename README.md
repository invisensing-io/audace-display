# audace-display

Visualization of **Audace DAS** acquisition files -- simple, fast, safe.

`audace-display` reads any format produced by the Filewriter
(`.dat`, `.hdf5`, `.tdms`, `.sgy`) via the [`invisensing`](https://pypi.org/project/invisensing/)
library, automatically detects the file mode, and shows:

- a **heatmap** (time x distance waterfall) for **demodulated** files,
- an **animated oscilloscope** (lines replayed in real time, looping) for **raw**
  files.

Reading is **streamed and decimated**: a multi-GB file is reduced to screen
resolution without ever being fully loaded into memory.

## Installation

```bash
pip install audace-display              # matplotlib (static + interactive + PNG)
pip install audace-display[interactive] # + pyqtgraph backend (fast interactive)
```

Dependencies: `numpy`, `matplotlib`, `invisensing` (>= 1.1.0 recommended for the
O(1) seek and TDMS/SEG-Y streaming; works from 1.0.0). Optional `interactive`
extra: `pyqtgraph` + `PyQt5`.

## Quick start

```bash
# Auto: heatmap if demodulated, oscilloscope if raw -- zero options
audace-display acquisition.dat

# PNG export (server / headless mode, no X11/Wayland)
audace-display acquisition.dat --save output.png
```

That's all for everyday use. The rest is fine-grained control.

## Display (backends)

| Mode | Command | For |
|------|---------|-----|
| interactive matplotlib | *(default)* | quick look, publication quality |
| headless PNG | `--save out.png` | server without X11/Wayland, batch scripts |
| interactive pyqtgraph | `--backend pyqtgraph` | smooth exploration (GPU pan/zoom) on large files |

matplotlib stays the default (simple, no heavy dependency). For smooth pan/zoom
on long acquisitions, `--backend pyqtgraph` is far more responsive (GPU
re-render + on-the-fly downsampling) -- install the extra
`pip install audace-display[interactive]`. `--save` always renders the PNG via
matplotlib, regardless of `--backend`.

```bash
audace-display heatmap acq.dat --backend pyqtgraph   # fast viewer
audace-display acq.dat --backend pyqtgraph           # same, in auto mode
audace-display acq.dat --save out.png                # headless PNG
```

## Raw mode: real-time oscilloscope

For a **raw** file, the default is an **animated oscilloscope**: each frame draws
the current line (pulse) -- amplitude vs distance along the fiber -- and the
cursor **advances at `trigger_frequency` lines/second** (real time), **looping**.
At high rates, lines are skipped to stay real-time (the pace stays exact, locked
to the clock). Optimized: O(1) per-line seek (invisensing >= 1.1.0), "peak"
decimation of large lines, reused curve, frozen axes. For maximum smoothness,
`--backend pyqtgraph`.

```bash
audace-display scope acq.dat                  # real time, looping
audace-display scope acq.dat --speed 0.25     # 4x slow motion
audace-display scope acq.dat --fps 60 --backend pyqtgraph
audace-display scope acq.dat --start-time 1.0 --duration 0.5   # loop over 0.5 s
audace-display scope acq.dat --line 1000 --save frame.png      # 1 frozen line (PNG)
```

| Option | Effect |
|--------|--------|
| `--speed F`   | Playback speed (1.0 = real time = `trig_frequency` lines/s) |
| `--fps N`     | Render frames/s (default 30) |
| `--line N`    | Frozen line exported with `--save` (default 0) |
| `--max-points N` | Max points drawn per line (peak decimation). Default 4000 |

Requires `invisensing >= 1.1.0` (O(1) per-line seek).

## Subcommands

| Command   | Role |
|-----------|------|
| *(none)*  | Auto: heatmap (demodulated) or animated oscilloscope (raw) |
| `info`    | Header metadata + stats of the default channel |
| `heatmap` | Time x distance waterfall, color = channel |
| `scope`   | Oscilloscope: replays the lines (pulses) in real time, looping |
| `fft`     | Temporal spectrum (FFT along the pulses) at 1+ position(s) |
| `trace`   | 1-D time trace at one position |
| `demod`   | Demodulate via an **external script**, then show as heatmap |

```bash
audace-display info acquisition.dat
audace-display heatmap acquisition.dat --channel magnitude --db
audace-display heatmap acquisition.dat --start-time 0.5 --duration 1.0
audace-display heatmap acquisition.dat --start-distance 50 --end-distance 200
audace-display fft   acquisition.dat --position 120
audace-display fft   acquisition.dat --position 50,100,150 --db
audace-display fft   acquisition.dat --position-range 100:200 --window blackman
audace-display trace acquisition.dat --position 100
```

## Channels per mode

| File mode          | Valid channels                                      | Default     |
|--------------------|-----------------------------------------------------|-------------|
| `raw`              | `raw`                                               | `raw`       |
| `iq`               | `i`, `q`, `magnitude` (\|IQ\|), `phase_wrapped`     | `magnitude` |
| `arctan_magnitude` | `arctan`, `magnitude` (sqrt(I^2+Q^2))               | `magnitude` |
| `phase`            | `phase`                                             | `phase`     |

Common aliases: `mag`/`amp` -> `magnitude`, `atan` -> `arctan`, `angle` ->
`phase_wrapped`. **No demodulation** is applied: channels are read as the
Filewriter wrote them.

## Large files (200 MB - 10 GB)

The heatmap is computed in a **single streaming pass**: pulses are read in chunks
and aggregated on the fly into a `--max-time-bins x --max-space-bins` grid
(2000 x 2000 by default). RAM does not depend on file size. Line plots use a
**min/max-preserving decimation** (no visual aliasing).

| Option | Effect |
|--------|--------|
| `--max-time-bins N`  | Temporal (X) resolution of the waterfall. Default 2000 |
| `--max-space-bins N` | Spatial (Y) resolution of the waterfall. Default 2000 |
| `--reduce {mean,rms,std,peak}` | Temporal aggregation statistic. Default `mean` |
| `--max-pulses N`     | Bound the number of pulses read (default: whole file) |
| `--subsample-time N` | (fft/trace) keep only one pulse out of N |

`.dat` and `.hdf5` stream natively; `.tdms` and `.sgy` also stream with
`invisensing >= 1.1.0`. `--start-time` seeks directly (O(1) seek) with 1.1.0.

## Demodulation via external plugin

`audace-display` contains **no demodulation code**: it defines a contract and
loads a Python script that **you** provide (never published). The result is
displayed as a heatmap.

```python
# my_demod.py
import numpy as np

class Demodulator:
    OUTPUT_LABEL = "phase (rad)"   # colorbar label (optional)
    IS_ANGULAR   = True            # diverging colormap (optional)

    def __init__(self, *, sample_rate, trig_frequency, line_size, meta):
        ...                        # init filters / state

    def process(self, chunk):      # called on contiguous time chunks
        # chunk: (rows, line_size) raw -> (rows, positions) float32
        ...
```

```bash
audace-display demod raw.dat --script my_demod.py --save demod.png
```

Stateless alternative: a function
`demodulate(chunk, *, sample_rate, trig_frequency, line_size, meta) -> ndarray`.
Chunks arrive **in time order** (compatible with stateful filters).

## Programmatic API

```python
from invisensing import File
from audace_display import load_decimated, resolve_channel

with File("acquisition.dat") as f:
    _, resolver, label, is_angular = resolve_channel(None, f.mode)
    res = load_decimated(f, lambda raw: resolver(f, raw),
                         max_time_bins=1000, max_space_bins=1000)
    # res.data: (n_time, n_space) float32 ; res.t_extent, res.d_extent
```

## Default behavior

- **Interactive display** by default; `--save out.png` for headless mode (the
  `Agg` backend is chosen automatically when there is no display).
- **Auto colormap**: `viridis` (sequential) for magnitude/raw, `RdBu_r`
  (diverging, centered on 0) for phase/arctan. Override with `--cmap`.
- **Auto-scaling**: 1/99 percentiles in linear, `median - 30 dB -> max` in dB,
  centered on 0 for angular channels. Override with `--vmin`/`--vmax`.

## License

MIT.
