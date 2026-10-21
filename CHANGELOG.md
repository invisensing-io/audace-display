# Changelog

## 0.6.0

Requires `invisensing >= 1.3.0` (publish it to PyPI first).

- **Actual trigger frequency.** A card fires on an integer divider of its clock,
  so a requested frequency that does not divide it is not met exactly (PCIe7821:
  75,000 Hz requested -> 75,007.5008 Hz). Every line <-> time conversion now
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
