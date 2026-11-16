"""``audace-display gui``: the desktop viewer.

The viewer is a separate program, shipped in its own package
(``audace-display-gui``, installed by ``pip install audace-display[gui]``).
This command finds its binary and starts it with the CURRENT interpreter
(``--python``), so the window's worker (``python -m
audace_display.gui_worker``) is this audace-display, in this environment.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
from typing import Optional, Sequence

from ._errors import AudaceDisplayError

#: The viewer's executable, as its package installs it.
BINARY = "audace-display-gui"

#: An explicit path to the viewer's executable (a build of gui/, a copy
#: outside the environment), tried before anything else.
BINARY_ENV = "AUDACE_DISPLAY_GUI"

INSTALL_HINT = 'pip install "audace-display[gui]"'

#: The viewer's options that take a value (the viewer's own parser).
_VALUE_OPTIONS = frozenset(
    {"--python", "--view", "--screenshot", "--unit", "--lang", "--zoom", "--set"}
)

USAGE = "audace-display gui [FILE] [options]"

DESCRIPTION = """\
Open the desktop viewer: one window with every view and every option of the
CLI (waterfall, band heatmaps, spectrum, time trace, oscilloscope, single
location, info), in English or French.

The viewer is the separate package audace-display-gui; install it with:
  pip install "audace-display[gui]"
It is started with this Python, so it computes with this audace-display."""

OPTIONS = """\
options (the viewer's own):
  FILE                  a recording to open (.dat / .h5 / .hdf5 / .tdms / .sgy)
  --lang en|fr          the language of the window for this run
  --view VIEW           info, heatmap, bandheatmaps, fft, trace, scope or inspect
  --unit UNIT           rad, nanostrain or strain_rate (when the file allows it)
  --set DEST=VALUE      an option of the view, by its CLI name (max_time_bins=500)
  --zoom T0:T1[:D0:D1]  open the view zoomed on seconds T0..T1 (and metres D0..D1)
  --settings            open the Settings window at start
  --screenshot OUT.png  save the window once the view is drawn, then quit
  --python CMD          another interpreter for the worker (default: this one)

environment:
  AUDACE_DISPLAY_GUI    the path of the viewer's executable, tried first"""


def _executable_name() -> str:
    return BINARY + (".exe" if os.name == "nt" else "")


def _script_dirs() -> list[str]:
    """Where pip puts a wheel's executables for this interpreter: its
    environment's scripts directory, then the user scheme's."""
    dirs = [sysconfig.get_path("scripts")]
    try:
        user = sysconfig.get_preferred_scheme("user")
        dirs.append(sysconfig.get_path("scripts", user))
    except (AttributeError, KeyError):  # pragma: no cover - Python < 3.10
        pass
    # The directory of the interpreter itself (a venv's bin/ or Scripts/).
    if sys.executable:
        dirs.append(os.path.dirname(sys.executable))
    seen, out = set(), []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _is_executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def find_binary() -> Optional[str]:
    """The viewer's executable: ``$AUDACE_DISPLAY_GUI``, the scripts directory
    of this interpreter (where ``pip install audace-display[gui]`` puts it),
    then ``PATH``. ``None`` when it is not installed."""
    explicit = os.environ.get(BINARY_ENV)
    if explicit:
        return explicit if _is_executable(explicit) else None
    name = _executable_name()
    for d in _script_dirs():
        candidate = os.path.join(d, name)
        if _is_executable(candidate):
            return candidate
    return shutil.which(BINARY)


def quote_command(path: str) -> Optional[str]:
    """``path`` as one word of the viewer's ``--python`` command, which splits
    on spaces and takes ``"..."`` or ``'...'`` as one word (no escapes).
    ``None`` when the path holds both quote characters."""
    if '"' not in path:
        return f'"{path}"'
    if "'" not in path:
        return f"'{path}'"
    return None


def _scan(argv: Sequence[str]) -> tuple[bool, bool]:
    """``(help asked, --python given)``, skipping the values of the options
    that take one."""
    wants_help = has_python = False
    it = iter(argv)
    for a in it:
        if a in ("-h", "--help"):
            wants_help = True
        elif a == "--python":
            has_python = True
        if a in _VALUE_OPTIONS:
            next(it, None)
    return wants_help, has_python


def command(binary: str, argv: Sequence[str]) -> list[str]:
    """The viewer's command line: ``argv`` as given, with ``--python`` set to
    this interpreter unless ``argv`` names one."""
    _, has_python = _scan(argv)
    cmd = [binary, *argv]
    if not has_python and sys.executable:
        python = quote_command(sys.executable)
        if python is not None:
            cmd += ["--python", python]
    return cmd


def print_help(file=None) -> None:
    out = file or sys.stdout
    out.write(f"usage: {USAGE}\n\n{DESCRIPTION}\n\n{OPTIONS}\n")


def main(argv: Sequence[str]) -> int:
    """Run the viewer with ``argv`` (what follows ``gui``); its exit code."""
    wants_help, _ = _scan(argv)
    if wants_help:
        # Printed here: on Windows the viewer has no console to print to.
        print_help()
        return 0
    binary = find_binary()
    if binary is None:
        where = os.environ.get(BINARY_ENV)
        if where:
            raise AudaceDisplayError(
                f"{BINARY_ENV}={where} is not an executable file."
            )
        raise AudaceDisplayError(
            f"the desktop viewer is not installed; install it with: {INSTALL_HINT}"
        )
    try:
        return subprocess.run(command(binary, argv)).returncode
    except KeyboardInterrupt:
        return 130
    except OSError as e:
        raise AudaceDisplayError(f"cannot start the desktop viewer {binary}: {e}") from e
