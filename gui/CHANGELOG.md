# Changelog — audace-display-gui

The desktop viewer, released on PyPI as `audace-display-gui` with
audace-display at the same version (`pip install "audace-display[gui]"`).

## 0.9.1

First release on PyPI. Speaks the worker protocol 1 of audace-display 0.9.0
and later.

- **Wheels for every platform the viewer targets**, one per platform for
  every Python (`py3-none-<platform>`, maturin `bindings = "bin"`): Linux
  x86_64 and aarch64 (manylinux_2_28: glibc 2.28 or later; the executable
  links only the C library and `libgcc_s`, X11, Wayland, xkbcommon and
  Vulkan are loaded at run time), Windows x64, macOS on Apple silicon
  (built, not yet tried on a Mac). The sdist builds it elsewhere with Rust
  1.97.1 or later. `audace-display gui` starts it with its own interpreter.
- **The newest line at the top of a waterfall**, older lines below, as in the
  CLI's figure and every Audace waterfall: the viewer drew t = 0 at the top.
  The time axis increases upwards, with the CLI's label and tick direction;
  the pointer readout, the zoom (wheel, box, `--zoom`) and the zoom window
  under the plot follow. Waterfall and band heatmaps, in every unit.
- **Numbers group their thousands** in the language of the window, as counts
  already did: the info panel's line rate read `10000 Hz` beside a trigger
  frequency of `10 000 Hz`; it reads `10,000 Hz` / `10 000 Hz`, and so do
  the readouts, the zoom window, the Nyquist label and the spectrum's peak.
- **"none" is translated**: a file whose times were not corrected reads
  « aucune : comptage (pas de table de temps) » in French, in the info panel
  and in the info text.
- **The info panel's quantity follows the unit**: in strain and strain-rate
  mode « Grandeur » read `phase (rad)`; it now reads `strain (nm/m)` /
  `strain rate (nm/m/s)`, as the info text below it (members of a pair
  translated one by one in French).

## 0.1.0

The viewer as first shipped in the repository with audace-display 0.9.0, built
from source only (`gui/`, not on PyPI).
