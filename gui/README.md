# audace-display-gui

A desktop viewer for Audace DAS recordings (`.dat`, `.h5` / `.hdf5`,
`.tdms`, `.sgy`), for Windows and Linux. It opens a file, lets you pick the
view and every option the `audace-display` CLI offers for it, and draws the
result: waterfalls, band waterfalls, spectra, time traces, the oscilloscope,
the single-location inspection and the file's metadata.

The window is a [Bevy](https://bevy.org) application with an
[egui](https://github.com/emilk/egui) interface (`bevy_egui`, `egui_plot`,
native file dialogs through `rfd`). The numbers are not computed in Rust:
the viewer runs audace-display's own Python code in a worker process
(`python -m audace_display.gui_worker`), so **what the viewer shows is what
the CLI computes**, bit for bit (the worker runs the CLI's `do_<view>`
functions and hands their arrays over instead of drawing them with
matplotlib).

This folder is not part of the Python package: the sdist and the wheel of
audace-display leave it out (`MANIFEST.in`, `packages.find` under `src/`).

## What you need

1. **Python 3.10 or later with audace-display and invisensing installed**, in
   the interpreter the viewer will run (it does not use a Python of its own):

   ```bash
   python3 -m pip install audace-display          # from PyPI, once released with the GUI worker
   python3 -m pip install -e /path/to/audace-display   # or from a checkout of this repository
   ```

   invisensing comes with it (`invisensing >= 1.6.0`; `--strain-rate` needs
   the invisensing that has `phase_unit="strain_rate"`). On Windows use the
   same commands with `py -3` instead of `python3`. A virtual environment is
   fine: give the viewer its interpreter's full path (below).

2. **To build the viewer: Rust 1.97.1 or later** (Bevy 0.20's minimum),
   installed with [rustup](https://rustup.rs). `rustup update stable` brings
   an older toolchain up to date.

## Build and run on Linux

System packages (Debian / Ubuntu names). The viewer has no audio and no
gamepad support, so ALSA and udev are not needed:

```bash
sudo apt install build-essential pkg-config \
    libx11-dev libxcursor-dev libxrandr-dev libxi-dev libxkbcommon-dev \
    libwayland-dev libxcb-render0-dev libxcb-shape0-dev libxcb-xfixes0-dev \
    mesa-vulkan-drivers
```

X11, Wayland and xkbcommon are loaded at run time, so only their run-time
libraries are strictly needed; the `-dev` packages above cover every
distribution layout. A Vulkan driver (`mesa-vulkan-drivers`, or the GPU
vendor's) or OpenGL is needed to draw the window. The native file dialog
goes through the XDG desktop portal (GNOME, KDE and most desktops run it);
without one, type the path in the bar or drop the file on the window.

```bash
cd gui
cargo build --release
./target/release/audace-display-gui                 # then Open…
./target/release/audace-display-gui /data/phase/2026-11-03_10-00-00.h5
```

## Build and run on Windows

1. Install the **Visual Studio Build Tools** with the "Desktop development
   with C++" workload (the MSVC linker and the Windows SDK), then rustup with
   the default `x86_64-pc-windows-msvc` toolchain.
2. Install Python from python.org (it installs the `py` launcher) and
   audace-display in it: `py -3 -m pip install audace-display` (or
   `py -3 -m pip install -e C:\path\to\audace-display`).
3. Build and run:

   ```powershell
   cd gui
   cargo build --release
   .\target\release\audace-display-gui.exe
   ```

The release build opens no console window. Graphics go through DirectX 12
or Vulkan, whichever the machine has.

## Using it

- **Open** a file with *Open…*, by typing its path in the bar (Enter or
  *Load*), from *Recent*, by dropping it on the window, or on the command line.
- **View**: Info, Waterfall, Band heatmaps, Spectrum (FFT), Time trace,
  Oscilloscope, Inspect location. The view a file opens on is the CLI's
  automatic one: the oscilloscope for a raw file, the waterfall otherwise.
- **Channel and unit**: the channels the file's mode has; the phase as
  stored (rad), as strain (nm/m, `--nanostrain`) or as strain rate (nm/m/s,
  `--strain-rate`), the two being exclusive as in the CLI. A unit the file
  cannot be shown in is greyed out, with the CLI's reason in its tooltip
  (no phase, no gauge length recorded...). Units the installed
  audace-display does not offer are not listed.
- **Options**: each view lists the CLI options that apply to it, grouped,
  starting at the CLI's defaults (read from the CLI's own parser, so they
  follow it); hover an option for its CLI help and flag. A number whose CLI
  default is "automatic" has a check box: unchecked, the CLI decides.
  *CLI defaults* puts every option back. A changed option is shown in bold.
  The view is recomputed a moment after the last change (turn this off in
  Settings for very large files, then press *Render*).
- **Waterfalls**: time runs downwards, distance left to right. Scroll to
  zoom, drag to pan, drag with the right button to zoom into a box,
  double-click to come back: the worker recomputes the decimated window you
  are looking at (more detail when zoomed in). The colour bar carries the
  quantity and its unit; the value under the pointer is shown next to it.
- **Time trace**: zooming the time axis recomputes the trace over that window.
- **Oscilloscope**: plays the lines at the actual pulse rate times the speed,
  looping over the window; pause and pick a line with the slider.
- Long computations show their progress (pulses read) in the status bar,
  with a *Cancel* button. A computation that cannot be stopped between two
  chunks within 5 s has its worker restarted.
- Errors (a refusal of the CLI, an invalid option, an unreadable file, a
  worker that does not start) are shown in the window, never as a crash.
  *Log* shows what the worker wrote to stderr.

### Settings

*Settings* sets the **Python interpreter**: `python3` on Linux and `py -3`
on Windows by default (then `python` when the launcher is missing), or any
command, e.g. `/home/me/venvs/das/bin/python` or
`"C:\Program Files\Python312\python.exe"` (quote a path with spaces). It is
saved in `%APPDATA%\audace-display-gui\settings.json` on Windows and
`~/.config/audace-display-gui/settings.json` on Linux
(`$XDG_CONFIG_HOME` when set), with the recent files.

### Command line

```text
audace-display-gui [FILE] [--python CMD] [--view VIEW] [--set DEST=VALUE]... [--screenshot OUT.png]
```

`--python` overrides the saved interpreter for one run; `--view` is one of
`info`, `heatmap`, `bandheatmaps`, `fft`, `trace`, `scope`, `inspect`;
`--set` gives an option of that view by its CLI name (`--set f1=50`,
`--set db=true`); `--screenshot` saves the window once the view is drawn,
then quits.

## What is not in the viewer

- `demod` and `inspect --script`: they run a Python demodulation script; use
  the CLI.
- The figure options of the CLI (`--save`, `--dpi`, `--title`, `--aspect`,
  `--backend`): the viewer draws on screen. `fft` and `trace` accept
  `--start-distance` / `--end-distance` in the CLI but do not use them; the
  viewer does not show them.

## How it works

```text
 viewer (Rust, Bevy + egui)                     worker (Python)
 ───────────────────────────                    ───────────────────────────────
 options panel ── request (JSON line) ────────► cli.build_parser().parse_args()
                                                cli._open(): the file, its unit
 plots, textures ◄── result (JSON line +        cli.do_<view>(): read, decimate,
                     float32 / float64 arrays)  compute; the matplotlib builders
                ◄── progress (JSON lines)       are recorded instead of drawn
```

The protocol (version 1) is described in the docstring of
`src/audace_display/gui_worker.py`: one JSON object per line each way; a
result carrying arrays lists their name, dtype (`<f4`, `<f8`, `|u1`), shape
and byte count, and exactly those bytes follow the line, little-endian, C
order. Requests are `hello`, `open`, `render`, `scope_frame`, `cancel` and
`shutdown`. The worker writes nothing else to stdout (stray prints go to
stderr), reads its input on a thread of its own so that a `cancel` stops the
current request between two chunks of the read, and reports the pulses read
as `progress`.

The colours are matplotlib's: the worker sends the colormap's lookup table
and the colour limits the CLI computed, and the viewer maps the values as
matplotlib's `Normalize` and `Colormap` do.

## Development

```bash
cd gui
cargo test          # framing, parsing, colours, options, settings
cargo clippy --all-targets
AUDACE_GUI_TEST_PYTHON=python3 cargo test   # also talks to a real worker
```

The worker's tests are in the Python suite (`tests/test_gui_worker.py`).
