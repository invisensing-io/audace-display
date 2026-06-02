"""Registry of displayable *channels*.

A channel is a 2-D numpy view ``(rows, positions)`` of a file's content for a
given mode. The *resolver* converts the raw buffer returned by
:meth:`invisensing.File.read_lines` (i16 / i32 / f32, possibly interleaved) into
a physical ``float32`` value.

No demodulation here: we only **read** channels already written by the
Filewriter (raw, I/Q, arctan+magnitude, phase). ``phase_wrapped`` (``arg(I+jQ)``)
is just a *view* of already-produced IQ data, not a demodulation.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
from invisensing import Mode

from ._errors import AudaceDisplayError

# Resolver: (File, raw_buffer) -> ndarray (rows, positions) float32
Resolver = Callable[[object, np.ndarray], np.ndarray]

# Common aliases -> canonical name
ALIASES: dict[str, str] = {
    # raw ADC
    "raw": "raw", "adc": "raw",
    # IQ
    "i": "i", "q": "q",
    # magnitude (= |IQ| or sqrt(I^2+Q^2) depending on mode)
    "magnitude": "magnitude", "mag": "magnitude",
    "amp": "magnitude", "amplitude": "magnitude", "|iq|": "magnitude",
    # arctan (ARCTAN_MAGNITUDE mode)
    "arctan": "arctan", "atan": "arctan",
    # phase wrapped from IQ (!= demodulated phase of PHASE mode)
    "phase_wrapped": "phase_wrapped", "wrapped": "phase_wrapped",
    "angle": "phase_wrapped",
    # demodulated phase (PHASE mode)
    "phase": "phase", "p": "phase", "phi": "phase",
}


def _ch_raw(f, buf: np.ndarray) -> np.ndarray:
    """Raw ADC codes -> volts via the header `range`."""
    return buf.astype(np.float32) * np.float32(f.range / 32768.0)


def _ch_magnitude(f, buf: np.ndarray) -> np.ndarray:
    """Magnitude in volts. `|IQ|` in IQ mode, `sqrt(I^2+Q^2)` in ArctanMag."""
    if f.mode is Mode.IQ:
        return np.abs(f.get_iq_volts(buf)).astype(np.float32)
    return f.get_magnitude_volts(buf)


def _ch_phase_wrapped(f, buf: np.ndarray) -> np.ndarray:
    """`arg(I+jQ)` in radians in [-pi, +pi[ from an IQ file."""
    return np.angle(f.get_iq_volts(buf)).astype(np.float32)


# canonical -> (valid modes, resolver, colorbar/y label, angular?)
CHANNELS: dict[str, tuple[set, Resolver, str, bool]] = {
    "raw":           ({Mode.RAW},                       _ch_raw,                                "ADC (V)",            False),
    "i":             ({Mode.IQ},                         lambda f, b: f.get_i_volts(b),          "I (V)",              False),
    "q":             ({Mode.IQ},                         lambda f, b: f.get_q_volts(b),          "Q (V)",              False),
    "magnitude":     ({Mode.IQ, Mode.ARCTAN_MAGNITUDE},  _ch_magnitude,                          "|signal| (V)",       False),
    "phase_wrapped": ({Mode.IQ},                         _ch_phase_wrapped,                      "arg(I+jQ) (rad)",    True),
    "arctan":        ({Mode.ARCTAN_MAGNITUDE},           lambda f, b: f.get_arctan_radians(b),   "arctan(Q/I) (rad)",  True),
    "phase":         ({Mode.PHASE},                      lambda f, b: f.get_phase(b),            "phase (rad)",        True),
}

DEFAULT_CHANNEL: dict[Mode, str] = {
    Mode.RAW:              "raw",
    Mode.IQ:               "magnitude",
    Mode.ARCTAN_MAGNITUDE: "magnitude",
    Mode.PHASE:            "phase",
}


def resolve_channel(name: Optional[str], mode: Mode) -> tuple[str, Resolver, str, bool]:
    """Resolve a user name into ``(canonical, resolver, label, is_angular)``.

    ``name=None`` -> the mode's default channel. Raises
    :class:`AudaceDisplayError` if the name is unknown or unavailable for the
    file's mode.
    """
    if name is None:
        canonical = DEFAULT_CHANNEL[mode]
    else:
        canonical = ALIASES.get(name.lower())
        if canonical is None:
            known = ", ".join(sorted(set(ALIASES.values())))
            raise AudaceDisplayError(f"unknown channel '{name}'. Known: {known}")

    valid_modes, resolver, label, is_angular = CHANNELS[canonical]
    if mode not in valid_modes:
        for_mode = ", ".join(
            sorted(k for k, (modes, *_rest) in CHANNELS.items() if mode in modes)
        )
        raise AudaceDisplayError(
            f"channel '{canonical}' unavailable in mode {mode.value}. "
            f"Available for this mode: {for_mode}"
        )
    return canonical, resolver, label, is_angular
