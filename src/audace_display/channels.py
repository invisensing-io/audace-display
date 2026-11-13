"""Registry of displayable *channels*.

A channel is a 2-D numpy view ``(rows, positions)`` of a file's content for a
given mode. The *resolver* converts the raw buffer returned by
:meth:`invisensing.File.read_lines` (i16 / u16 / f32, possibly a pair) into a
physical ``float32`` value.

A phase file opened with ``phase_unit="nanostrain"`` (``--nanostrain``,
invisensing >= 1.6.0) is read as strain: the ``phase`` channel is then
labelled ``strain (nm/m)`` and is no longer an angle. Opened with
``phase_unit="strain_rate"`` (``--strain-rate``) it is read as strain rate,
``strain rate (nm/m/s)``: the strain increment from the previous line times
the trigger frequency, NaN on the file's first line and after a gap of its
time axis (invisensing computes it at the full line rate, while reading).

The mode comes from the file's content type (invisensing >= 1.4.0): since the
Audace data format v1 a recording holds one quantity per file (``phase/`` and
``amplitude/``, ``arctan/`` and ``magnitude/``, ``i_component/`` and
``q_component/``); the paired modes are files written before it.

No demodulation here: we only **read** channels already written by the
Filewriter (raw, I, Q, arctan, magnitude/amplitude, phase, archive variances).
``phase_wrapped`` (``arg(I+jQ)``) is just a *view* of already-produced IQ data,
not a demodulation.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
from invisensing import Mode, content

from ._errors import AudaceDisplayError

# Resolver: (File, raw_buffer) -> ndarray (rows, positions) float32
Resolver = Callable[[object, np.ndarray], np.ndarray]

#: The unit of a phase read as strain (invisensing ``File.unit``).
STRAIN_UNIT = "nm/m"
#: The label of the ``phase`` channel of a file read as strain.
STRAIN_LABEL = f"strain ({STRAIN_UNIT})"
#: The unit of a phase read as strain rate (invisensing ``File.unit``).
STRAIN_RATE_UNIT = "nm/m/s"
#: The label of the ``phase`` channel of a file read as strain rate.
STRAIN_RATE_LABEL = f"strain rate ({STRAIN_RATE_UNIT})"


def reads_strain(f) -> bool:
    """Whether ``f`` was opened with ``phase_unit="nanostrain"``: its phase
    samples are strain, in nm/m (invisensing >= 1.6.0)."""
    return getattr(f, "phase_unit", "rad") == "nanostrain"


def reads_strain_rate(f) -> bool:
    """Whether ``f`` was opened with ``phase_unit="strain_rate"``: its phase
    samples are strain rate, in nm/m/s, NaN on the first line and after a
    gap of the time axis (invisensing >= 1.6.0)."""
    return getattr(f, "phase_unit", "rad") == "strain_rate"

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
    # a script's output, as stored (CUSTOM mode)
    "samples": "samples", "value": "samples",
    # zone variances of an archive export (VARIANCE / LOG_VARIANCE modes)
    "variance": "variance", "var": "variance",
    "log_variance": "log_variance", "logvar": "log_variance",
}


def _ch_raw(f, buf: np.ndarray) -> np.ndarray:
    """Raw ADC codes -> volts via the header `range`.

    Float payloads classified as RAW (a file written before the content type
    existed whose flags say FLOAT|DEMODULATED only, such as the ``amplitude/``
    files of an early split) are already in physical units: applying the
    ADC-code scaling would shrink them ~30 000× and mislabel them. Pass them
    through unchanged.
    """
    if getattr(f, "is_float", False) or np.issubdtype(buf.dtype, np.floating):
        return buf.astype(np.float32)
    return buf.astype(np.float32) * np.float32(f.range / 32768.0)


def amplitude_in_volts(f) -> bool:
    """Whether the amplitude of ``f`` is the card's magnitude (shown in volts):
    ``|IQ|``, the magnitude of an arctan/magnitude pair or file, the f32 card
    magnitude a demodulation kept. A software front end's amplitude
    (``sw_amplitude_sac``) is on another scale and is shown as stored."""
    if f.mode in (Mode.IQ, Mode.ARCTAN_MAGNITUDE):
        return True
    header = getattr(f, "header", None)
    ct = getattr(header, "normalized_content_type", getattr(f, "content_type", 0))
    info = content.info(ct)
    if info is None or ct == 0:
        return True
    if info.members is not None:
        member = content.member_of(ct, "amplitude")
        return member is not None and info.members[member[0]].unit == "count"
    return info.unit == "count"


def _ch_magnitude(f, buf: np.ndarray) -> np.ndarray:
    """Magnitude in volts. `|IQ|` in IQ mode, `sqrt(I^2+Q^2)` in ArctanMag and
    for an amplitude file of the card; a software front end's amplitude as
    stored."""
    if f.mode is Mode.IQ:
        return np.abs(f.get_iq_volts(buf)).astype(np.float32)
    if amplitude_in_volts(f):
        return f.get_magnitude_volts(buf)
    return f.get_amplitude(buf).astype(np.float32)


def _ch_phase_wrapped(f, buf: np.ndarray) -> np.ndarray:
    """`arg(I+jQ)` in radians in [-pi, +pi[ from an IQ file."""
    return np.angle(f.get_iq_volts(buf)).astype(np.float32)


def _as_f32(f, buf: np.ndarray) -> np.ndarray:
    return np.asarray(buf).astype(np.float32)


# canonical -> (valid modes, resolver, colorbar/y label, angular?)
CHANNELS: dict[str, tuple[set, Resolver, str, bool]] = {
    "raw":           ({Mode.RAW},                               _ch_raw,                                "ADC (V)",            False),
    "i":             ({Mode.IQ, Mode.I},                        lambda f, b: f.get_i_volts(b),          "I (V)",              False),
    "q":             ({Mode.IQ, Mode.Q},                        lambda f, b: f.get_q_volts(b),          "Q (V)",              False),
    "magnitude":     ({Mode.IQ, Mode.ARCTAN_MAGNITUDE, Mode.AMPLITUDE, Mode.PHASE_AMPLITUDE},
                                                                _ch_magnitude,                          "|signal| (V)",       False),
    "phase_wrapped": ({Mode.IQ},                                _ch_phase_wrapped,                      "arg(I+jQ) (rad)",    True),
    "arctan":        ({Mode.ARCTAN_MAGNITUDE, Mode.ARCTAN},     lambda f, b: f.get_arctan_radians(b),   "arctan(Q/I) (rad)",  True),
    "phase":         ({Mode.PHASE, Mode.PHASE_AMPLITUDE},       lambda f, b: f.get_phase(b),            "phase (rad)",        True),
    "samples":       ({Mode.CUSTOM},                            _as_f32,                                "script output",      False),
    "variance":      ({Mode.VARIANCE, Mode.LOG_VARIANCE},       lambda f, b: f.get_variance(b),         "variance (source unit²)", False),
    "log_variance":  ({Mode.VARIANCE, Mode.LOG_VARIANCE},       lambda f, b: f.get_log_variance(b),     "log10(1 + variance)", False),
}

DEFAULT_CHANNEL: dict[Mode, str] = {
    Mode.RAW:              "raw",
    Mode.IQ:               "magnitude",
    Mode.ARCTAN_MAGNITUDE: "magnitude",
    Mode.PHASE:            "phase",
    Mode.PHASE_AMPLITUDE:  "phase",
    Mode.AMPLITUDE:        "magnitude",
    Mode.ARCTAN:           "arctan",
    Mode.I:                "i",
    Mode.Q:                "q",
    Mode.CUSTOM:           "samples",
    Mode.VARIANCE:         "variance",
    Mode.LOG_VARIANCE:     "log_variance",
}


def resolve_channel(
    name: Optional[str], mode: Mode, f=None
) -> tuple[str, Resolver, str, bool]:
    """Resolve a user name into ``(canonical, resolver, label, is_angular)``.

    ``name=None`` -> the mode's default channel. Raises
    :class:`AudaceDisplayError` if the name is unknown or unavailable for the
    file's mode. With the file ``f``, the label says when an amplitude is not
    the card's magnitude (not in volts), and the phase of a file read as
    strain is ``strain (nm/m)``, as strain rate ``strain rate (nm/m/s)``, not
    an angle.
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
    if canonical == "magnitude" and f is not None and not amplitude_in_volts(f):
        label = "amplitude (front-end count)"
    if canonical == "phase" and f is not None and reads_strain(f):
        label, is_angular = STRAIN_LABEL, False
    if canonical == "phase" and f is not None and reads_strain_rate(f):
        label, is_angular = STRAIN_RATE_LABEL, False
    return canonical, resolver, label, is_angular
