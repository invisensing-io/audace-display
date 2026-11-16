# audace-display-gui

A desktop viewer for Audace DAS recordings (`.dat`, `.h5` / `.hdf5`,
`.tdms`, `.sgy`), for Windows, Linux and macOS. It opens a file, lets you
pick the view and every option the `audace-display` CLI offers for it, and
draws the result: waterfalls, band waterfalls, spectra, time traces, the
oscilloscope, the single-location inspection and the file's metadata.

The window is a [Bevy](https://bevy.org) application with an
[egui](https://github.com/emilk/egui) interface (`bevy_egui`, `egui_plot`,
native file dialogs through `rfd`). The numbers are not computed in Rust:
the viewer runs audace-display's own Python code in a worker process
(`python -m audace_display.gui_worker`), so **what the viewer shows is what
the CLI computes**, bit for bit (the worker runs the CLI's `do_<view>`
functions and hands their arrays over instead of drawing them with
matplotlib).

## Install

```bash
pip install "audace-display[gui]"
audace-display gui                       # then Open…
audace-display gui phase.h5 --lang fr --view heatmap --unit nanostrain
```

The `gui` extra of [audace-display](https://pypi.org/project/audace-display/)
installs this package, `audace-display-gui`, at the same version. It holds
the viewer's executable only, in one wheel per platform for every Python
(`py3-none-<platform>`): Linux x86_64 and aarch64 (glibc 2.28 or later),
Windows x64, macOS on Apple silicon (built, not yet tried on a Mac). On
another platform pip builds it from the sdist, which needs Rust 1.97.1 or
later (below).

`audace-display gui [FILE] [options]` finds the executable in the
environment and starts it with the same Python (`--python`), so the window
computes with that audace-display; the options are the viewer's own (see
*Command line* below). Started on its own (`audace-display-gui`,
also on the environment's `PATH`), the viewer runs the interpreter of its
Settings (`python3`, or `py -3` on Windows, by default).

To draw the window the viewer needs a Vulkan driver (`mesa-vulkan-drivers`,
or the GPU vendor's) or OpenGL on Linux, DirectX 12 or Vulkan on Windows,
Metal on macOS. X11, Wayland, xkbcommon and Vulkan are loaded at run time:
the Linux executable links only the C library and `libgcc_s`. The native file
dialog goes through the XDG desktop portal (GNOME, KDE and most desktops run
it); without one, type the path in the bar or drop the file on the window.

## Build from source

1. **Python 3.10 or later with audace-display and invisensing installed**, in
   the interpreter the viewer will run (it does not use a Python of its own):

   ```bash
   python3 -m pip install audace-display
   python3 -m pip install -e /path/to/audace-display   # or from a checkout of the repository
   ```

   invisensing comes with it (`invisensing >= 1.6.0`). On Windows use the
   same commands with `py -3` instead of `python3`. A virtual environment is
   fine: give the viewer its interpreter's full path (below).

2. **Rust 1.97.1 or later** (Bevy 0.20's minimum), installed with
   [rustup](https://rustup.rs). `rustup update stable` brings an older
   toolchain up to date.

The wheels are built with [maturin](https://www.maturin.rs) from
`gui/pyproject.toml` (`bindings = "bin"`):
`maturin build --release --locked --compatibility manylinux_2_28` in the
`quay.io/pypa/manylinux_2_28_x86_64` image gives the Linux wheel; the
release workflow is `.github/workflows/publish-gui.yml` (tag `gui-v<version>`).

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
distribution layout.

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
- **Waterfalls**: distance left to right, time on the vertical axis with
  the newest line at the top and older lines below, as in the CLI's figure
  (the time axis increases upwards). Ctrl+scroll
  (or a pinch) zooms, a drag with the right button zooms into a box, a drag
  or a scroll pans, a double-click comes back: the worker recomputes the
  decimated window you are looking at (more detail when zoomed in). The colour bar carries the
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
(`$XDG_CONFIG_HOME` when set), with the language and the recent files.

### Language

The window is in **English or French**. *Settings* → *Language* offers
*System*, *English* and *Français*; the choice applies at once (no restart)
and is saved (`"language": "en"`, `"fr"`, or `null` for the system's).
*System*, the default, follows the operating system's locale: French when it
starts with `fr` (`fr_FR.UTF-8`, `fr-CA`...), English otherwise. On Linux the
locale is the first of `LANGUAGE`, `LC_ALL`, `LC_MESSAGES` and `LANG` that is
set; on Windows, the user's display language. `--lang en` or `--lang fr` on
the command line picks the language of one run without saving it.

In French everything the window writes is French: menus, buttons, views,
option groups, names and help, tooltips, the status bar and progress,
Settings, the log window, notices and error banners, and the texts that come
from the CLI: plot titles, axis and colour-bar labels (`strain rate
(nm/m/s)` reads `vitesse de déformation (nm/m/s)`), the inspection's
statistics, the `info` text (its keys re-aligned), the CLI's refusals and
warnings. Numbers are written the French way (`0,25`, `20 000`); a number
field shows a decimal comma and takes a comma or a point. The values sent
to the CLI are the same numbers in either language.

What stays as the CLI writes it, in both languages: units (`nm/m`, `rad`,
`Hz`), option flags inside a sentence (`--f1`), the CLI's names of modes,
channels, content types, colormaps and choices (`phase`, `raw`, `hann`,
`mean`), file names and paths, and the text of an option that is CLI syntax
(the FFT's position list `10,20,30` keeps its decimal point). A message the
viewer does not know stays in English: an error of the operating system or of
a library (invisensing, numpy) inside a French sentence, a Python warning, a
refusal or a help text a newer CLI words differently, a title or label of
another shape.

How it works: the window's texts are one catalogue (`src/i18n.rs`, an entry
per message id with its English and its French; a test fails when an entry
lacks one, or when the French breaks its typography). The worker sends the
CLI's texts in English and attaches a stable code with parameters to each
refusal, error and warning it knows (protocol below); the viewer renders the
code in its language, the worker's English when the code is unknown. The
fixed titles and labels are rebuilt from the patterns the CLI writes them with
(`src/cli_text.rs`).

### Command line

```text
audace-display-gui [FILE] [--python CMD] [--lang en|fr] [--view VIEW] [--unit UNIT]
                   [--set DEST=VALUE]... [--zoom T0:T1[:D0:D1]] [--settings]
                   [--screenshot OUT.png]
```

`--python` overrides the saved interpreter for one run, `--lang` the
language (above); `--view` is one of
`info`, `heatmap`, `bandheatmaps`, `fft`, `trace`, `scope`, `inspect`;
`--unit` is `rad`, `nanostrain` or `strain_rate`; `--set` gives an option
of that view by its CLI name (`--set f1=50`, `--set db=true`); `--zoom`
opens a waterfall (or a trace) on seconds `T0..T1` (and metres `D0..D1`);
`--settings` opens the Settings window; `--screenshot` saves the window once
the view is drawn, then quits once the image is written.

To look at the viewer on a machine without a display (or to keep it off a
GPU), run it on a virtual display with Mesa's software Vulkan driver:

```bash
WGPU_BACKEND=vulkan WGPU_FORCE_FALLBACK_ADAPTER=1 \
VK_DRIVER_FILES=/usr/share/vulkan/icd.d/lvp_icd.json VK_LOADER_LAYERS_DISABLE='~implicit~' \
xvfb-run -a -s "-screen 0 1600x1000x24" \
  ./target/release/audace-display-gui phase.h5 --view heatmap --screenshot heatmap.png
```

The log line `AdapterInfo { name: "llvmpipe ...", device_type: Cpu, ... }`
confirms the CPU renderer.

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
`shutdown`. The texts are English; an error, a warning (`warning_codes`) and
the reason a view or unit is unavailable also carry a message `code` and its
`params` when the worker knows the message, which the viewer renders in its
language (optional fields: an older worker without them, or a viewer that
ignores them, still speaks protocol 1). The worker writes nothing else to stdout (stray prints go to
stderr), reads its input on a thread of its own so that a `cancel` stops the
current request between two chunks of the read, and reports the pulses read
as `progress`.

The colours are matplotlib's: the worker sends the colormap's lookup table
and the colour limits the CLI computed, and the viewer maps the values as
matplotlib's `Normalize` and `Colormap` do.

## Development

```bash
cd gui
cargo test          # framing, parsing, colours, options, settings, the catalogue
cargo clippy --all-targets
AUDACE_GUI_TEST_PYTHON=python3 cargo test   # also talks to a real worker
```

With `AUDACE_GUI_TEST_PYTHON`, the tests also check that every message code
the installed worker can send (`gui_worker.message_codes()`) has its
catalogue entry, and that the entry uses only the parameters sent with it.
A new code needs an entry in `worker_msg` (`src/i18n.rs`).

The worker's tests are in the Python suite (`tests/test_gui_worker.py`).
