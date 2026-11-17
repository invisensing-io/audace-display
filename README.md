# audace-display

Visualization of **Audace DAS** acquisition files -- simple, fast, safe.

`audace-display` reads any format produced by the Filewriter
(`.dat`, `.h5` / `.hdf5`, `.tdms`, `.sgy`) via the [`invisensing`](https://pypi.org/project/invisensing/)
library, automatically detects what the file holds (its content type), and shows:

- a **heatmap** (distance x time waterfall) for **demodulated** files,
- an **animated oscilloscope** (lines replayed in real time, looping) for **raw**
  files,
- **per-frequency-band heatmaps** (`bandheatmaps`) and a temporal **variance /
  log-variance** color mode (`--variance`),
- a phase as **strain, in nm/m** (`--nanostrain`), or as **strain rate, in
  nm/m/s** (`--strain-rate`), when the file records its gauge length.

Reading is **streamed and decimated**: a multi-GB file is reduced to screen
resolution without ever being fully loaded into memory.

## Installation

```bash
pip install audace-display              # matplotlib (static + interactive + PNG)
pip install audace-display[interactive] # + pyqtgraph backend (fast interactive)
pip install "audace-display[gui]"       # + the desktop viewer (audace-display gui)
```

Dependencies: `numpy`, `matplotlib`, `invisensing` >= 1.7.0 (the speed of light
in the fibre a file states, and `--fiber-group-index`, since 1.7.0; gauge length and
phase as strain and strain rate since 1.6.0; content-type registry revision 4 since 1.5.0;
corrected line times since 1.4.0; actual trigger frequency since 1.3.0; O(1)
seek and TDMS/SEG-Y streaming since 1.1.0). Optional
`interactive` extra: `pyqtgraph` + `PyQt5`; optional `gui` extra: the desktop
viewer, `audace-display-gui` at the same version.

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

### Desktop viewer

```bash
pip install "audace-display[gui]"
audace-display gui acq.dat
audace-display gui phase.h5 --lang fr --view heatmap --unit nanostrain
```

A desktop application for Windows, Linux and macOS opens a file and offers
every option of every view in one window: the channel, the unit (rad,
strain, strain rate), the time and distance window, the decimation, the
colours, the bands, the spectrum... Zooming a waterfall recomputes the window
on screen; the newest line is at the top, as in the figures above. In English
or French (`--lang`, or Settings).

It is a separate package, `audace-display-gui`: a Rust program (Bevy + egui)
in platform wheels (Linux x86_64 and aarch64, Windows x64, macOS arm64),
installed by the `gui` extra at the same version. `audace-display gui [FILE]
[options]` finds it in the environment and starts it with the current Python,
whose `python -m audace_display.gui_worker` runs this package's views: the
window shows exactly what the CLI computes. Without the package, the command
says to run `pip install "audace-display[gui]"`. Options, settings and
building from source:
[gui/README.md](https://github.com/invisensing-io/audace-display/blob/main/gui/README.md).

## Raw mode: real-time oscilloscope

For a **raw** file, the default is an **animated oscilloscope**: each frame draws
the current line (pulse) -- amplitude vs distance along the fiber -- and the
cursor **advances at the actual pulse rate** in lines/second (real time, see
[Actual trigger frequency](#actual-trigger-frequency)), **looping**.
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
| `--speed F`   | Playback speed (1.0 = real time = actual pulse rate, in lines/s) |
| `--fps N`     | Render frames/s (default 30) |
| `--line N`    | Frozen line exported with `--save` (default 0) |
| `--max-points N` | Max points drawn per line (peak decimation). Default 4000 |

Uses invisensing's O(1) per-line seek (there since invisensing 1.1.0; this
release requires invisensing >= 1.5.0 anyway).

## Subcommands

| Command        | Role |
|----------------|------|
| *(none)*       | Auto: heatmap (demodulated) or animated oscilloscope (raw) |
| `info`         | Header metadata + stats of the default channel |
| `heatmap`      | Distance x time waterfall, color = channel |
| `bandheatmaps` | Per-frequency-band waterfalls (one panel per band + global) |
| `scope`        | Oscilloscope: replays the lines (pulses) in real time, looping |
| `fft`          | Temporal spectrum (FFT along the pulses) at 1+ position(s) |
| `trace`        | 1-D time trace at one position |
| `inspect`      | One location: time waveform + FFT spectrum in a single figure |
| `demod`        | Demodulate via an **external script**, then show as heatmap |

```bash
audace-display info acquisition.dat
audace-display heatmap acquisition.dat --channel magnitude --db
audace-display heatmap acquisition.dat --variance log         # temporal log-variance
audace-display heatmap acquisition.dat --start-time 0.5 --duration 1.0
audace-display heatmap acquisition.dat --start-distance 50 --end-distance 200
audace-display bandheatmaps acquisition.dat --f1 200 --f2 500 --f3 1500
audace-display bandheatmaps acquisition.dat --f0 100 --f1 200 --f2 500
audace-display fft   acquisition.dat --position 120
audace-display fft   acquisition.dat --position 50,100,150 --db
audace-display fft   acquisition.dat --position-range 100:200 --window blackman
audace-display trace acquisition.dat --position 100
audace-display inspect acquisition.dat --index 120
audace-display inspect acquisition.dat --position 60 --fft-log --fmax 200
audace-display inspect arctan_mag.dat  --script plugins/dui_rust.py --index 120
```

## Channels per mode

The mode comes from the file's content type (from its flags on a file written
before the content type existed).

| File mode          | Valid channels                                      | Default     |
|--------------------|-----------------------------------------------------|-------------|
| `raw`              | `raw`                                               | `raw`       |
| `phase`            | `phase`                                             | `phase`     |
| `amplitude`        | `magnitude`                                         | `magnitude` |
| `arctan`           | `arctan`                                            | `arctan`    |
| `i` / `q`          | `i` / `q`                                           | `i` / `q`   |
| `custom`           | `samples` (a script's output, as stored)            | `samples`   |
| `variance`, `log_variance` | `variance`, `log_variance`                  | the file's  |
| `iq`               | `i`, `q`, `magnitude` (\|IQ\|), `phase_wrapped`     | `magnitude` |
| `arctan_magnitude` | `arctan`, `magnitude` (sqrt(I^2+Q^2))               | `magnitude` |
| `phase_amplitude`  | `phase`, `magnitude`                                | `phase`     |

The last three are files of pairs written before the Audace data format v1;
since then each member is its own file. Common aliases: `mag`/`amp` ->
`magnitude`, `atan` -> `arctan`, `angle` -> `phase_wrapped`, `var` ->
`variance`. **No demodulation** is applied: channels are read as the
Filewriter wrote them.

## Large files (200 MB - 10 GB)

The heatmap is computed in a **single streaming pass**: pulses are read in chunks
and aggregated on the fly into a `--max-time-bins x --max-space-bins` grid
(2000 x 2000 by default). RAM does not depend on file size. Line plots use a
**min/max-preserving decimation** (no visual aliasing).

| Option | Effect |
|--------|--------|
| `--max-time-bins N`  | Temporal (Y) resolution of the waterfall. Default 2000 |
| `--max-space-bins N` | Spatial (X) resolution of the waterfall. Default 2000 |
| `--reduce {mean,rms,std,variance,peak}` | Temporal aggregation statistic. Default `mean` |
| `--variance {linear,log}` | Color each time bin by its temporal **variance** (`log` = log10, floored 120 dB below the maximum -- a display scale, not the console's `log10(1 + variance)` of the archive exports, content type 28). Overrides `--reduce`/`--db`. Off by default |
| `--max-pulses N`     | Bound the number of pulses read (default: whole file) |
| `--subsample-time N` | (fft/trace) keep only one pulse out of N |

`.dat` and `.h5` / `.hdf5` stream natively; `.tdms` and `.sgy` stream too, and
`--start-time` seeks directly (O(1) seek) -- both from invisensing 1.1.0, below
the required invisensing >= 1.7.0.

## Actual trigger frequency

A card fires on an **integer divider of its clock**, so a requested trigger
frequency that does not divide the clock is not met exactly: on a 250 MHz
clock, 75,000 Hz requested gives 250 MHz / 3,333 = 75,007.5008 Hz
(+100 ppm, i.e. 0.36 s per hour). Recent HDF5 files carry the actual value
(`trig_frequency_real_hz`, `trig_clock_hz`, `trig_period_ticks`), exposed by
`invisensing >= 1.3.0` as `File.trig_frequency_real_hz`.

Since 0.6.0 every line <-> time conversion uses the **actual** rate: time axes
(`heatmap`, `bandheatmaps`, `trace`, `inspect`), the `--start-time` /
`--duration` window, the sampling rate `fs` of `fft` / `inspect` / the band
filters, Nyquist, and the `scope` playback speed and `t =` label. `info` shows
both when they differ:

```
  Trig frequency : 75,000 Hz (actual 75,007.5008 Hz, +100.0 ppm)
```

For an exact frequency (e.g. 2,000 Hz), a file without the attributes (`.dat`,
older HDF5) or an older `invisensing`, the actual rate is the requested one and
every output is unchanged. `audace_display.line_rate_hz(f)` returns the rate
used. Demod plugins receive it as `trig_frequency_real_hz` (keyword and `meta`
key); `trig_frequency` stays the requested integer (see
[docs/DEMOD_PLUGIN.md](https://github.com/invisensing-io/audace-display/blob/main/docs/DEMOD_PLUGIN.md#requested-vs-actual-trigger-frequency)).

### Corrected absolute times (0.7.0)

The acquisition corrects the drift of the card's oscillator and the shots it
lost (the card's *time map*), and the recorder writes each HDF5 file's time axis
into its attributes. The absolute times shown -- `info`'s start and last pulse
-- are the file's line times (`invisensing.File.line_time_ns`,
`audace_display.absolute_time_ns(f, i)`), corrected when the file carries the
axis. `info` says how the times were stamped:

```
  Time           : cardclock-v1: corrected, +0.098 ms vs the count (tracking, +/-61 us)
```

Every rate stays the programmed cadence (`line_rate_hz`): the correction moves
time labels, never the `fs` of a spectrum or a filter. Absolute times are local
date-times with their UTC offset (`2026-09-23 14:03:12.123456789+02:00`), the
form of the producer's own timestamp string.

### What the file is (0.7.0)

A file carrying a content type (every recent capture) is described by it, and
what it holds -- the mode, the default channel, the default view -- comes from
it (invisensing 1.4.0, Audace data format v1). Since data format v1 a recording
holds one quantity per file: an interleaved product is written as two files,
same name, sibling directories (`phase/` + `amplitude/`, `arctan/` +
`magnitude/`, `i_component/` + `q_component/`). Each opens on its own:

| Mode | Default channel | Default view |
|---|---|---|
| `raw` | `raw` (V) | oscilloscope |
| `phase` | `phase` (rad) | heatmap |
| `amplitude` | `magnitude` (V for the card's magnitude; a software front end's amplitude as stored) | heatmap |
| `arctan` | `arctan` (rad) | heatmap |
| `i` / `q` | `i` / `q` (V) | heatmap |
| `custom` | `samples` (a script's output, as stored) | heatmap |
| `variance` / `log_variance` | `variance` / `log_variance` (archive exports) | heatmap |

`info` prints the content type (`Content type : dui_coherent_phase`,
`sw_mozart (Mozart)` for the Mozart phase) and its quantity and unit, the data
format version, the code of the card that recorded the file (`Card : p21` --
`p18`, `p21`, `s125` or `s500`, on every v1 file; an older file carries none,
or the tag of a two-card run, read as its code), the other half of a split
pair, the duration the writer recorded, the line period, the column a variance
export was computed from, and the HDF5 compression when there is one (`ZSTD`,
or `ZSTD_LOSSY_1E4` with its grid and error bound). The pipeline is never named
from the flags any more (the SCHUBERT flag is deprecated). A legacy capture
(content type 0) prints exactly what it always did, `MOZART` / `SCHUBERT`
included.

The `magnitude/` child of a split arctan/magnitude recording (`hw_amplitude`)
is read as `uint16`, so its values above 32767 show as they are (40000 -> 1.22 V
at a 1 V range), not negative.

### The fibre group index (0.10.0)

Every metre -- the distance axes, `--start-distance` / `--end-distance`,
`--position`, the spatial step, the gauge length -- is at the speed of light
in the fibre the file states: the Audace acquisition takes the fibre's group
index `n` as a parameter (default 1.4682, SMF-28 at 1550 nm) and writes
`v = round(c / n)` into every file (invisensing >= 1.7.0 reads it). `info`
prints it:

```
  Fiber index    : 1.4682 (204,190,477 m/s)
  Spatial step   : 0.408 m
```

A recording made before the acquisition wrote it is at the legacy 2e8 m/s
(`Fiber index : 1.49896229 (200,000,000 m/s; legacy: the file states
none)`), as before. When its fibre is known, `--fiber-group-index N` (every
view; any number from 1 to 100) labels it at that index: the samples are
the same, the metres follow (2.1 % longer at 1.4682 than at 2e8 m/s), and
`info` says `relabelled, the file says 1.49896229`:

```bash
audace-display info old.dat --fiber-group-index 1.4682
audace-display heatmap old.dat --fiber-group-index 1.4682 --start-distance 1000
```

The desktop viewer shows the file's index in its info text; relabelling is
on the command line only for now.

## Phase as strain, in nm/m (0.9.0)

`--nanostrain` shows a phase as **strain, in nm/m**, in every view (auto,
`info`, `heatmap`, `bandheatmaps`, `fft`, `trace`, `scope`, `inspect`; not
`demod`, whose input is raw). The file is opened with invisensing's
`phase_unit="nanostrain"` (invisensing >= 1.6.0), which converts the phase
while it is read:

```text
strain [nm/m] = phase [rad] / gauge_length_m [m] x NANOSTRAIN_PER_RAD_METRE
```

```bash
audace-display phase.h5 --nanostrain                        # heatmap, nm/m
audace-display heatmap phase.h5 --nanostrain --variance log # log10 Var[strain] (nm/m)^2
audace-display scope phase.h5 --nanostrain --line 100 --save line.png
audace-display info phase.h5 --nanostrain
```

- `NANOSTRAIN_PER_RAD_METRE` is invisensing's constant: 110.37 nm/m per
  rad/m, from `ΔΦ = 2β(n + Cε)·ε·L` at λ = 1550.12 nm and n = 1.46 -- the
  core's phase index, not the fibre group index the metres are at: it does
  not follow `--fiber-group-index` (the gauge length, a distance, does).
- The gauge length is the one the file records (`info` prints it: `Gauge
  length : 9.6 m`), the gauge the producer applied. Audace writes it on a
  phase only, and only from the release that adds it: `--nanostrain` and
  `--strain-rate` need a recording made with that release or a later one.
- Labels: `strain (nm/m)` (colorbar, y axis), `Var[strain] (nm/m)^2` and
  `log10 Var[strain] (nm/m)^2` with `--variance`, `RMS[strain] (nm/m)` for a
  band, `strain (dB, ref=... nm/m)` with `--db`, `Amplitude (nm/m)` for a
  spectrum. Strain is not an angle: the colormap and the color limits are
  those of any other quantity (`viridis`, 1/99 percentiles).
- On a file of phase/amplitude pairs only the phase is converted; the
  `magnitude` channel is unchanged.
- **Refused** (the command exits with `ERROR: ...`, code 1, no traceback):
  a file that holds no phase (raw, I/Q, arctan, amplitude, variances), and a
  phase that does not record its gauge length (every recording made before
  the Audace release that writes it, a MOZART / MOZART_PLUS phase, a file
  without a content type). No gauge length is ever assumed.

## Phase as strain rate, in nm/m/s (0.9.0)

`--strain-rate` shows a phase as **strain rate, in nm/m/s**, in the same
views as `--nanostrain` (the two options exclude each other). A phase file
holds the accumulated phase of each line; the strain rate of a line is its
strain increment from the previous line times the line rate:

```text
strain_rate[n] [nm/m/s] = (strain[n] - strain[n-1]) [nm/m] x f_trig [Hz]
                        = (phase[n] - phase[n-1]) [rad] / gauge_length_m x 110.37 x f_trig
```

`f_trig` is the rate the card really fired at (`info`'s "actual" trigger
frequency; the requested one on a file that does not carry the exact
clock). The file is opened with invisensing's `phase_unit="strain_rate"`
(invisensing >= 1.6.0), which computes it while it reads, at the full line
rate, carrying the previous line across chunks and seeks.

```bash
audace-display phase.h5 --strain-rate                          # heatmap, nm/m/s
audace-display heatmap phase.h5 --strain-rate --variance log   # log10 Var[strain rate] (nm/m/s)^2
audace-display fft phase.h5 --strain-rate --position 120       # Amplitude (nm/m/s)
audace-display info phase.h5 --strain-rate                     # the factor applied
```

- **Decimation comes after**: every view reduces the strain rate computed
  line by line (a time bin's mean is the mean of its lines' strain rates),
  never a strain rate derived from decimated strain. `--subsample-time`
  keeps one full-rate strain rate out of N.
- **Missing lines (NaN)**: the file's first line has no previous line, and
  a line after a gap of an HDF5's time axis (lost shots: `info` lists them
  as `Time gaps`) spans more than one period; both read NaN. A time bin and
  the color limits skip them (a bin of NaN only is left blank), the
  oscilloscope draws nothing for such a line, `info` leaves them out of its
  statistics (and says how many), and the FFT, `inspect` and the band
  filters, which need evenly spaced samples, take a missing sample as the
  column's mean (0 once the mean is removed).
- Labels: `strain rate (nm/m/s)`, `Var[strain rate] (nm/m/s)^2` and
  `log10 Var[strain rate] (nm/m/s)^2` with `--variance`, `RMS[strain rate]
  (nm/m/s)` for a band, `strain rate (dB, ref=... nm/m/s)` with `--db`,
  `Amplitude (nm/m/s)` for a spectrum, in matplotlib and pyqtgraph alike;
  `viridis` and the 1/99 percentiles.
- **Refused** like `--nanostrain`, and for a file whose trigger frequency is
  unknown (0). `--nanostrain --strain-rate` is a usage error.

## Frequency-band heatmaps

`bandheatmaps` decomposes the **temporal** frequency axis (the FFT along the
pulses, Nyquist = actual pulse rate / 2) into up to 3 contiguous bands and renders
one distance x time waterfall per band, plus the global heatmap. A band panel
band-pass filters the signal to its `[f_lo, f_hi)` range, then colors each time
bin by its **energy** (temporal RMS; or variance / log-variance with
`--variance`) -- showing *where* (distance) and *when* (time) activity appears in
that frequency range.

```bash
# 3 bands: 0-200, 200-500, 500-1500 Hz, plus the global heatmap
audace-display bandheatmaps acq.dat --f1 200 --f2 500 --f3 1500

# Drop everything below 100 Hz: first band becomes 100-200 Hz
audace-display bandheatmaps acq.dat --f0 100 --f1 200 --f2 500 --f3 1500

# 1 band + global, energy as log-variance, bounded window
audace-display bandheatmaps acq.dat --f1 50 --variance log --duration 2
```

| Option | Effect |
|--------|--------|
| `--f1 HZ` | Upper edge of band 1 (Hz). **Required** |
| `--f2 HZ` / `--f3 HZ` | Upper edges of bands 2 / 3 (optional, contiguous) |
| `--f0 HZ` | Lower edge of the **first** band. Default 0 (keeps DC) |
| `--variance {linear,log}` | Band color = variance / log-variance instead of RMS |

The full time series is held in RAM for the FFT, so band analysis bounds memory
with `--duration` / `--max-pulses` / `--max-space-bins` (default 1000 spatial
bins) and warns past ~0.5 GB.

## Demodulation via external plugin

`audace-display` contains **no demodulation code**: it defines an **API
contract** and loads a Python script that **you** provide (never published). The
result is displayed as a heatmap. This keeps the published package generic and
safe — your (often proprietary) signal-processing recipe never leaves your
machine.

Your script defines **either** a stateless `demodulate` function **or** a
stateful `Demodulator` class. Both turn a raw pulse chunk `(rows, line_size)`
into a `(rows, positions)` field:

```python
# my_demod.py  --  stateless function (memoryless transforms)
import numpy as np

OUTPUT_LABEL = "magnitude (V)"   # colorbar label (optional)
IS_ANGULAR   = False             # diverging colormap centered on 0 (optional)

def demodulate(chunk, *, sample_rate, trig_frequency, line_size, meta):
    # interleaved I/Q raw -> |IQ|
    i = chunk[:, 0::2].astype(np.float32)
    q = chunk[:, 1::2].astype(np.float32)
    return np.sqrt(i * i + q * q)
```

```python
# my_demod.py  --  stateful class (filters with temporal memory)
class Demodulator:
    OUTPUT_LABEL = "phase (rad)"
    IS_ANGULAR   = True

    def __init__(self, *, sample_rate, trig_frequency, line_size, meta):
        ...                        # build filters / allocate state

    def process(self, chunk):      # called on contiguous time chunks, in order
        # chunk: (rows, line_size) raw -> (rows, positions) float32
        ...
```

```bash
audace-display demod raw.dat --script my_demod.py --save demod.png
audace-display demod raw.dat --script my_demod.py --db --start-distance 50 --end-distance 200
```

Any flag the CLI doesn't recognise is **forwarded to the plugin** (if it declares
a `plugin_args` keyword), so a plugin can be configured per-invocation without
environment variables — `demod` and `inspect --script` only:

```bash
audace-display demod raw.dat --script my_demod.py --gain 2.5 --mode fast
```

See [`docs/DEMOD_PLUGIN.md`](https://github.com/invisensing-io/audace-display/blob/main/docs/DEMOD_PLUGIN.md#per-invocation-options-plugin_args).

**The contract in short:**

- **Input** `chunk`: the buffer from `invisensing.File.read_lines`, shape
  `(rows, line_size)`, in the **file's native dtype** (`int16`/`int32`/`float32`).
  For interleaved I/Q, `chunk[:, 0::2]` is I and `chunk[:, 1::2]` is Q. *Cast to
  float before arithmetic — integer dtypes overflow silently.*
- **kwargs** (keyword-only, all optional — declare only what you use):
  `sample_rate` (intra-pulse Hz), `trig_frequency` (requested pulse rate Hz),
  `trig_frequency_real_hz` (actual pulse rate Hz, since 0.6.0), `line_size`,
  and `meta` (full header dict: `positions_per_line`, `range_v`, `flags`,
  `mode`, `num_lines`, `trig_frequency_real_hz`, `spatial_step_m`,
  `light_speed_in_fiber_m_s`, `fiber_group_index`, …).
- **Output**: a 2-D `(rows, positions)` array — `rows` **must** match the input
  (don't resample time), `positions` **constant** across chunks. Cast to
  `float32` for display.
- **Streaming guarantees**: chunks arrive **in time order, contiguous, same
  `positions`** → a stateful filter (IIR, running mean, phase unwrap) is safe in
  the *class* form (state on `self`). The *function* form sees each chunk
  independently (state resets per chunk) — use it only for memoryless math.

📖 **Full guide with worked examples** (I/Q magnitude, instantaneous phase,
stateful IIR low-pass), the complete `meta` reference, the pipeline diagram, how
to test a plugin, and FAQ:
[docs/DEMOD_PLUGIN.md](https://github.com/invisensing-io/audace-display/blob/main/docs/DEMOD_PLUGIN.md).

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
- **Auto colormap**: `viridis` (sequential) for magnitude/raw and for a phase
  shown as strain or strain rate (`--nanostrain`, `--strain-rate`), `RdBu_r`
  (diverging, centered on 0) for phase/arctan. Override with `--cmap`.
- **Auto-scaling**: 1/99 percentiles in linear, `median - 30 dB -> max` in dB,
  centered on 0 for angular channels. Override with `--vmin`/`--vmax`.

## License

MIT.
