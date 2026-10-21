"""audace-display -- visualization of Audace DAS acquisition files.

Reads any format produced by the Filewriter (``.dat`` / ``.hdf5`` / ``.tdms`` /
``.sgy``) via the :mod:`invisensing` library, and shows:

- a **heatmap** (distance x time waterfall) for demodulated files,
- an **animated oscilloscope** for raw files,

with automatic mode detection. *Streaming* decimation to handle multi-GB files
in bounded RAM.

CLI: ``audace-display FILE`` (auto) or subcommands ``info`` / ``heatmap`` /
``bandheatmaps`` / ``fft`` / ``trace`` / ``scope`` / ``demod``.

Programmatic API (stable):

    from audace_display import load_decimated, load_columns, resolve_channel

Time axes and sampling rates use the *actual* pulse rate of the file
(:func:`line_rate_hz`: ``File.trig_frequency_real_hz`` from invisensing >= 1.3.0,
else the requested ``trig_frequency``).
"""
from __future__ import annotations

from ._errors import AudaceDisplayError
from .channels import resolve_channel, CHANNELS, DEFAULT_CHANNEL
from .reader import (
    load_decimated,
    load_columns,
    load_time_matrix,
    line_rate_hz,
    position_step_m,
    time_window_pulses,
    DecimatedResult,
    TimeMatrixResult,
)
from .demod import load_demodulator, DemodPlugin

__version__ = "0.6.0"

__all__ = [
    "AudaceDisplayError",
    "resolve_channel",
    "CHANNELS",
    "DEFAULT_CHANNEL",
    "load_decimated",
    "load_columns",
    "load_time_matrix",
    "line_rate_hz",
    "position_step_m",
    "time_window_pulses",
    "DecimatedResult",
    "TimeMatrixResult",
    "load_demodulator",
    "DemodPlugin",
    "__version__",
]
