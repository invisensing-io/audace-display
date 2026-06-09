# Writing a demodulation plugin

`audace-display` ships **no demodulation code**. It only defines an **API
contract** and loads a Python script *you* provide. Your script turns a **raw**
acquisition (ADC codes, or interleaved I/Q) into a 2-D field `(time, position)`
that the viewer renders as a heatmap.

This keeps the published package generic and safe: the actual signal-processing
recipe (which is often proprietary) never leaves your machine.

```bash
audace-display demod raw.dat --script my_demod.py
audace-display demod raw.dat --script my_demod.py --db --save demod.png
```

---

## Contents

1. [The contract in one minute](#the-contract-in-one-minute)
2. [Input: the raw chunk](#input-the-raw-chunk)
3. [The keyword arguments](#the-keyword-arguments)
4. [The `meta` dictionary](#the-meta-dictionary)
5. [Output expectations](#output-expectations)
6. [Streaming guarantees](#streaming-guarantees)
7. [Optional display attributes](#optional-display-attributes)
8. [Stateless function vs stateful class](#stateless-function-vs-stateful-class)
9. [Worked examples](#worked-examples)
10. [How your plugin plugs into the pipeline](#how-your-plugin-plugs-into-the-pipeline)
11. [Testing your plugin](#testing-your-plugin)
12. [Common pitfalls / FAQ](#common-pitfalls--faq)

---

## The contract in one minute

Your script must define **either** a `demodulate` function (stateless) **or** a
`Demodulator` class (stateful). Both receive raw pulse chunks and return a
`(rows, positions)` array.

```python
# my_demod.py  --  Option A: stateless function
import numpy as np

OUTPUT_LABEL = "magnitude (V)"   # optional: colorbar label
IS_ANGULAR   = False             # optional: diverging colormap, centered on 0

def demodulate(chunk, *, sample_rate, trig_frequency, line_size, meta):
    """chunk: (rows, line_size) raw  ->  (rows, positions) float32."""
    i = chunk[:, 0::2].astype(np.float32)
    q = chunk[:, 1::2].astype(np.float32)
    return np.sqrt(i * i + q * q)
```

```python
# my_demod.py  --  Option B: stateful class
import numpy as np

class Demodulator:
    OUTPUT_LABEL = "phase (rad)"
    IS_ANGULAR   = True

    def __init__(self, *, sample_rate, trig_frequency, line_size, meta):
        # build filters / allocate state here
        ...

    def process(self, chunk):
        # called sequentially, in time order, on contiguous chunks
        ...
        return out   # (rows, positions)
```

That's the whole interface. Everything below is detail.

---

## Input: the raw chunk

`chunk` is **exactly** the buffer returned by
[`invisensing.File.read_lines`](https://pypi.org/project/invisensing/) — no
scaling, no de-interleaving applied:

| Property | Value |
|----------|-------|
| Shape    | `(rows, line_size)` — `rows` consecutive pulses, `line_size` samples per pulse |
| dtype    | the **file's native dtype** (`int16`, `int32`, or `float32` depending on the acquisition) |
| Layout   | for **interleaved I/Q** files, samples alternate `I0, Q0, I1, Q1, …` → `chunk[:, 0::2]` is I, `chunk[:, 1::2]` is Q |

So for a typical interleaved raw I/Q file, `line_size = 2 * positions_per_line`,
and:

```python
i = chunk[:, 0::2]   # (rows, positions)  -- in-phase
q = chunk[:, 1::2]   # (rows, positions)  -- quadrature
```

> **Cast before arithmetic.** Integer dtypes overflow silently
> (`int16 * int16` wraps around). Always `.astype(np.float32)` (or `float64`)
> first.

**Volts, not codes.** The chunk holds raw ADC codes. To convert to volts, use
`meta["range_v"]` (full-scale voltage) the same way the built-in raw channel
does: `volts = codes * (range_v / 32768.0)` for a 16-bit signed ADC. Whether
you need physical units depends on your demodulation; magnitude/phase are often
computed directly on codes.

---

## The keyword arguments

Both `demodulate(chunk, …)` and `Demodulator.__init__(self, …)` are called with
the **same keyword-only** arguments:

| kwarg            | Type  | Meaning |
|------------------|-------|---------|
| `sample_rate`    | int   | ADC sample rate within a pulse (Hz) — the *intra-pulse* (fast-time / distance) rate |
| `trig_frequency` | int   | Pulse repetition rate (Hz) — the *inter-pulse* (slow-time / temporal) rate, i.e. one row per `1/trig_frequency` s |
| `line_size`      | int   | Samples per pulse (the width of `chunk`) |
| `meta`           | dict  | Full header metadata (see below) |
| `plugin_args`    | list[str] | Extra command-line args the CLI didn't recognise, forwarded verbatim (see [Per-invocation options](#per-invocation-options-plugin_args)) |

You only need to declare the ones you use — the loader **filters kwargs to
match your signature**. All of these are valid:

```python
def demodulate(chunk, *, sample_rate, trig_frequency, line_size, meta): ...
def demodulate(chunk, *, sample_rate, line_size): ...   # subset is fine
def demodulate(chunk, **kw): ...                         # catch-all is fine
def demodulate(chunk): ...                               # no kwargs at all
```

The same holds for `Demodulator.__init__`. `Demodulator.process(self, chunk)`
takes **only** the chunk — bind everything else in `__init__`.

---

## Per-invocation options (`plugin_args`)

`demod` and `inspect --script` forward any flags they **don't recognise**
straight to your plugin, so it can be configured on the command line without
environment variables. Declare a `plugin_args` keyword and parse it yourself —
audace-display never interprets these flags, it only passes them through:

```bash
audace-display demod raw.dat --script my_demod.py --gain 2.5 --mode fast
```

```python
import argparse
import numpy as np

class Demodulator:
    def __init__(self, *, line_size, plugin_args=None, **kw):
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument("--gain", type=float, default=1.0)
        p.add_argument("--mode", default="normal")
        self.opts, _unknown = p.parse_known_args(plugin_args or [])

    def process(self, chunk):
        return chunk[:, 0::2].astype(np.float32) * self.opts.gain
```

Plugins that don't declare `plugin_args` are unaffected — the kwarg is filtered
out, and any extra flags are simply ignored for that plugin. (Subcommands
*without* a `--script`, like `heatmap`, keep argparse's strict
"unrecognized arguments" error.)

---

## The `meta` dictionary

`meta` carries the full header, useful when you need geometry or acquisition
flags your kwargs don't expose:

| Key                   | Meaning |
|-----------------------|---------|
| `sample_rate`         | ADC sample rate (Hz) |
| `trig_frequency`      | Pulse repetition rate (Hz) |
| `line_size`           | Samples per pulse |
| `positions_per_line`  | Spatial positions per pulse (for interleaved I/Q, `line_size / 2`) |
| `sample_size`         | Bytes per sample (2 = int16, 4 = int32/float32) |
| `range_v`             | ADC full-scale range in volts (for code→volt scaling) |
| `flags`               | Raw header flags bitfield (interleaved / float / phase / …) |
| `mode`                | File mode string (`"raw"`, `"iq"`, `"arctan_magnitude"`, `"phase"`) |
| `timestamp`           | Acquisition start timestamp |
| `num_lines`           | Total pulses in the file |

---

## Output expectations

Return a **2-D** array shaped `(rows, positions)`:

- **`rows` must equal the input `rows`.** You map each input pulse to one output
  row — never resample the time axis yourself (the viewer decimates time for
  you). A mismatch raises `AudaceDisplayError`.
- **`positions` must be constant** across every chunk. Pick a spatial width in
  `__init__` (or implicitly via your slicing) and keep it stable. The first
  chunk locks the geometry; later chunks must match.
- Any array-like is accepted and **cast to `float32`** for display. Return real
  values — magnitude, phase in radians, strain rate, etc. (not complex).

There is no requirement that `positions == positions_per_line`. A plugin may
output fewer positions (e.g. after spatial averaging) or a derived quantity per
position — just keep it constant.

---

## Streaming guarantees

`audace-display` reads multi-GB files in **bounded RAM** by streaming chunks of
consecutive pulses. The contract the viewer guarantees to your plugin:

- **In order.** Chunks arrive strictly in increasing time (pulse) order.
- **Contiguous.** Chunk *k+1* starts at the pulse right after chunk *k* ends —
  no gaps, no overlap (within the requested `--start-time` / `--duration`
  window).
- **Same `positions`** every call.

These three together mean a **stateful filter is safe**: an IIR filter, a
running mean, a phase unwrapper, or a carry-over of the last pulse all work
correctly across chunk boundaries — *if* you use the class form and keep state
on `self`. (A stateless `demodulate` function sees each chunk independently, so
filter state resets at every chunk boundary — fine for memoryless transforms
like magnitude, wrong for anything with temporal memory.)

Chunk size is chosen automatically (~256 MB of resolved float32). Do **not**
assume a particular `rows` per call.

---

## Optional display attributes

Two optional hints control rendering. Set them as class attributes, function
attributes, or module-level globals (checked in that order):

| Attribute      | Type | Effect |
|----------------|------|--------|
| `OUTPUT_LABEL` | str  | Colorbar label (e.g. `"phase (rad)"`). Default `"demodulated"`. |
| `IS_ANGULAR`   | bool | `True` → diverging colormap `RdBu_r` centered on 0 and ±-symmetric auto-scaling, suitable for phase/angle. `False` → sequential `viridis`. Default `False`. |

```python
# module-level globals (work with the function form)
OUTPUT_LABEL = "strain rate (1/s)"
IS_ANGULAR = False

def demodulate(chunk, **kw): ...
```

The user can always override with `--cmap`, `--vmin`, `--vmax`, `--db`.

---

## Stateless function vs stateful class

| | `demodulate` function | `Demodulator` class |
|---|---|---|
| Inter-chunk memory | **No** — resets each chunk | **Yes** — state on `self` |
| Use for | magnitude, instantaneous phase, per-pulse math | IIR/FIR filters, running statistics, phase unwrapping across pulses |
| Setup cost | none | `__init__` runs once |

If your transform is memoryless, prefer the function — it's simpler. If it has
any temporal memory, you **must** use the class, or results will be wrong at
every chunk boundary.

---

## Worked examples

### 1. I/Q magnitude (stateless)

```python
# mag_demod.py
import numpy as np

OUTPUT_LABEL = "|signal| (V)"

def demodulate(chunk, *, meta):
    scale = np.float32(meta["range_v"] / 32768.0)
    i = chunk[:, 0::2].astype(np.float32) * scale
    q = chunk[:, 1::2].astype(np.float32) * scale
    return np.sqrt(i * i + q * q)
```

### 2. Instantaneous phase (stateless, angular)

```python
# phase_demod.py
import numpy as np

OUTPUT_LABEL = "phase (rad)"
IS_ANGULAR = True   # diverging colormap centered on 0

def demodulate(chunk, **kw):
    i = chunk[:, 0::2].astype(np.float32)
    q = chunk[:, 1::2].astype(np.float32)
    return np.arctan2(q, i)          # wrapped phase in [-pi, pi]
```

### 3. Temporal low-pass on phase (stateful IIR)

A one-pole IIR low-pass along the **time** axis (across pulses). State carries
the last filtered pulse from one chunk to the next — only correct with the
class form.

```python
# lowpass_phase.py
import numpy as np

class Demodulator:
    OUTPUT_LABEL = "phase, LP filtered (rad)"
    IS_ANGULAR = True

    def __init__(self, *, trig_frequency, line_size, meta):
        # one-pole low-pass, cutoff 10 Hz on the temporal (slow-time) axis
        fc = 10.0
        dt = 1.0 / trig_frequency
        self.alpha = np.float32(dt / (dt + 1.0 / (2 * np.pi * fc)))
        self.prev = None             # last filtered pulse, shape (positions,)

    def process(self, chunk):
        i = chunk[:, 0::2].astype(np.float32)
        q = chunk[:, 1::2].astype(np.float32)
        phase = np.arctan2(q, i)     # (rows, positions)

        out = np.empty_like(phase)
        y = self.prev if self.prev is not None else phase[0]
        a = self.alpha
        for r in range(phase.shape[0]):
            y = y + a * (phase[r] - y)   # IIR step across pulses
            out[r] = y
        self.prev = y                    # carry state into the next chunk
        return out
```

> The Python loop over `rows` is illustrative. For speed, vectorize with
> `scipy.signal.lfilter(..., zi=...)` along `axis=0` and store the returned
> `zf` as `self.prev`. (`audace-display` itself never imports scipy — but your
> plugin may depend on whatever you like.)

---

## How your plugin plugs into the pipeline

The viewer wraps your callable into a `transform: raw_chunk -> (rows, positions)`
and feeds it to the streaming reader:

```
File.read_lines(chunk)  ──▶  your transform  ──▶  spatial binning  ──▶  time accumulator  ──▶  heatmap
   (rows, line_size)          (rows, positions)     (rows, n_space)       (n_time, n_space)
```

- The reader **locks geometry on the first chunk** (number of positions, spatial
  step, distance range). Keep `positions` constant after that.
- Output is reduced to a `--max-time-bins × --max-space-bins` grid
  (default 2000×2000). All the time/distance windowing and dB/colormap options
  (`--start-time`, `--duration`, `--start-distance`, `--end-distance`,
  `--reduce`, `--db`, `--cmap`, …) apply to your demodulated output exactly as
  they do to built-in channels.

The spatial step shown on the Y axis is recomputed as
`fiber_length / your_positions`, so a plugin that changes the position count
still gets correct distance axes.

---

## Testing your plugin

You don't need the CLI to validate a plugin. Load it directly:

```python
from invisensing import File
from audace_display.demod import load_demodulator

with File("raw.dat") as f:
    plugin = load_demodulator("my_demod.py", f)
    raw = f.read_lines(16)
    out = plugin.transform(raw)            # raises AudaceDisplayError on a bad shape
    assert out.shape[0] == raw.shape[0]    # rows preserved
    print(out.shape, out.dtype, plugin.label, plugin.is_angular)
```

Or run the whole streaming/decimation path the heatmap uses:

```python
from audace_display import load_decimated

with File("raw.dat") as f:
    plugin = load_demodulator("my_demod.py", f)
    res = load_decimated(f, plugin.transform, max_time_bins=500, max_space_bins=500)
    print(res.data.shape, res.t_extent, res.d_extent)
```

A headless smoke test:

```bash
audace-display demod raw.dat --script my_demod.py --max-pulses 5000 --save /tmp/check.png
```

---

## Common pitfalls / FAQ

**My values are wrong / overflow.** You're doing integer arithmetic on the raw
codes. Cast to `float32` *before* squaring or multiplying.

**"expected 2-D output, got (N,)".** You collapsed an axis (e.g. returned a
single pulse, or reduced over positions). Return `(rows, positions)`; index with
`chunk[:, 0::2]`, not `chunk[0]`.

**"output has N rows for M input rows".** You resampled the time axis. Map one
input pulse to one output row; let the viewer decimate time.

**My IIR filter looks discontinuous at regular intervals.** You used the
`demodulate` function form, so state resets every chunk. Switch to the
`Demodulator` class and keep state on `self`.

**Where does the script run / is it sandboxed?** It is imported and executed in
the same Python process as `audace-display`, with your interpreter's full
privileges. Only run scripts you trust. It may import any third-party library
installed in your environment (scipy, numba, torch, …); `audace-display` itself
stays numpy-only.

**Can the plugin return complex values?** No — return a real field. Compute
magnitude / phase / real part yourself.

**Do I need `invisensing >= 1.1.0`?** For `demod` specifically, any 1.x works
(it reads sequentially). The 1.1.0 O(1) seek matters for `scope` and fast
`--start-time` seeking, not for the demod streaming pass.
