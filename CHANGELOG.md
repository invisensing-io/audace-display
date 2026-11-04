# Changelog

## 0.7.0

Requires `invisensing >= 1.4.0` (publish it to PyPI first).

- **Distances are 3.3 % shorter on every file without a spatial step**
  (every file written before data format v1). invisensing 1.4.0 measures
  the fibre with the acquisition's 2·10⁸ m/s instead of 206 856 796 m/s
  (`File.distance` × 0.96685), so `info`'s fibre length and spatial step,
  the distance axes of every view and the columns `--start-distance` /
  `--end-distance` select follow: 487 positions at 100 MS/s span 487.0 m
  instead of 503.7 m. A file of data format v1 carries its own spatial step,
  already on 2·10⁸ m/s.
- **Corrected absolute times.** The Audace acquisition corrects the drift of
  the card's oscillator and the shots it lost (the card's *time map*); the
  recorder writes each HDF5 file's time axis into its attributes and
  invisensing 1.4.0 reads it (`File.line_time_ns`). The absolute times
  `audace-display` shows come from it (new `audace_display.absolute_time_ns`):
  `info` prints the first and last pulse times and how the times were stamped,
  e.g. `cardclock-v1: corrected, +0.098 ms vs the count (tracking, +/-61 us)`.
- Unchanged on purpose: every rate (`line_rate_hz`: `fs` of the spectra and
  band filters, Nyquist, relative time axes, `scope` speed) stays the
  programmed cadence -- the correction moves time labels, not samples. Files
  without a time map show what 0.6.0 showed, plus the last pulse time in `info`.
- **The content type is the reference** (Audace data format v1). What a file
  holds -- its mode, default channel and default view -- comes from its content
  type, which a v1 file needs since it writes no FLOAT / UNSIGNED / INTERLEAVED
  flag. A recording now holds one quantity per file (`phase/` + `amplitude/`,
  `arctan/` + `magnitude/`, `i_component/` + `q_component/`), and each opens on
  its own: phase -> `phase`, amplitude -> `magnitude` (the card's in volts, a
  software front end's as stored), arctan -> `arctan` (rad), I / Q -> `i` / `q`
  (V), a script's output -> `samples`, all as heatmaps; raw stays the
  oscilloscope. The paired files written before (IQ, arctan/magnitude,
  phase/amplitude) read as before. The `amplitude/` and `magnitude/` files used
  to be classified raw (shown as ADC volts, opened in the oscilloscope).
- `info` prints the file's content type (`Content type : dui_coherent_phase`;
  `sw_mozart (Mozart)`), its quantity and unit, the data format version, the
  code of the card that recorded it (`Card : p21` -- `p18`, `p21`, `s125`,
  `s500`), the other half of a split pair, the duration the writer recorded,
  the line period, and the HDF5 compression (`ZSTD_LOSSY_1E4` with its grid
  and error bound). It no longer names a pipeline from the flags (the SCHUBERT
  flag is deprecated). A legacy capture (content type 0) prints no content type
  line and keeps its flag description and pipeline name; the rest of its
  output changes as described here: fibre length and spatial step 3.3 %
  shorter, the last pulse time and, on a file with a binary time anchor, times
  with their UTC offset, the card code when its header carries a card tag
  written before v1, and the statistics of a single column flagged UNSIGNED
  computed on unsigned values.
- Console archive exports (zone variances, `viz_variance` and its Log scale
  `viz_logvar`): channels `variance` and `log_variance`, and `info` names the
  column the variance was computed from. `--variance log` stays a display
  scale (log10 floored below the maximum), not the console's
  `log10(1 + variance)`.
- `.h5` (the HDF5 name of data format v1) and `.hdf5` are both read.
- Demod plugin contract: new `meta` keys `content_type`, `content_type_name`,
  `dtype`, `card`, `spatial_step_m`, `temporal_step_s`. Existing plugins work
  unchanged.
- The `magnitude/` child of a split arctan/magnitude recording shows its real
  values: invisensing 1.4.0 reads it as `uint16` (values above 32767 were shown
  negative).
- `info` prints absolute times with their UTC offset
  (`2026-09-23 14:03:12.123456789+02:00`).
- `inspect --script` probes the plugin's output width on a throwaway instance:
  the instance that streams sees contiguous, in-order chunks from the start of
  the window, as the plugin contract promises (it used to see line 0 first).
- FFT frequency axes are `float64` (they were rounded to `float32`).
- Python 3.13 and 3.14 in the classifiers and in CI; a CI job runs the Qt
  (`interactive`) tests offscreen.

## 0.6.0

Requires `invisensing >= 1.3.0` (publish it to PyPI first).

- **Actual trigger frequency.** A card fires on an integer divider of its clock,
  so a requested frequency that does not divide it is not met exactly (on a
  250 MHz clock: 75,000 Hz requested -> 75,007.5008 Hz). Every line <-> time conversion now
  uses the actual pulse rate (`File.trig_frequency_real_hz`, new
  `audace_display.line_rate_hz`): time axes, `--start-time` / `--duration`,
  `fs` of `fft` / `inspect` / `bandheatmaps`, Nyquist, `scope` playback speed and
  `t =` label. Exact frequencies, files without the attributes and older
  `invisensing` releases give the same outputs as 0.5.2.
- `info` shows the actual frequency next to the requested one when they differ:
  `75,000 Hz (actual 75,007.5008 Hz, +100.0 ppm)`.
- Demod plugin contract: new `trig_frequency_real_hz` key in `meta` and keyword
  (passed only to a plugin that declares it). `trig_frequency` is unchanged
  (the requested integer). Existing plugins work unchanged.
- `info` shows the trigger delay (m) and names the demodulation pipeline
  (Mozart / Schubert) only when the file is demodulated.

## 0.5.2

- `inspect`: time axis anchored at `--start-time`, selected window shown;
  readable acquisition timestamp in `info`.

Earlier versions: see the git history (`git log v0.5.2`).
