"""Test fixtures: synthetic ``.dat`` files for each mode.

Files are produced via :func:`invisensing.export_dat`, with the flags that make
the correct :class:`invisensing.Mode` be decoded (see ``Mode.from_flags``).
"""
from __future__ import annotations

import numpy as np
import pytest
from invisensing import (
    export_dat,
    FLAG_DEMODULATED,
    FLAG_INTERLEAVED,
    FLAG_UNSIGNED,
    FLAG_PHASE,
)

SAMPLE_RATE = 125_000_000
TRIG = 1000
POSITIONS = 64
N_PULSES = 500


@pytest.fixture
def raw_dat(tmp_path):
    """Raw file: int16 (N_PULSES, POSITIONS), flags=0 -> Mode.RAW."""
    rng = np.random.default_rng(0)
    data = (rng.standard_normal((N_PULSES, POSITIONS)) * 1000).astype(np.int16)
    path = tmp_path / "raw.dat"
    export_dat(path, data, sample_rate=SAMPLE_RATE, trig_frequency=TRIG,
               range_v=1.0, flags=0)
    return path


@pytest.fixture
def iq_dat(tmp_path):
    """Interleaved IQ file: int16 (N_PULSES, 2*POSITIONS) -> Mode.IQ."""
    rng = np.random.default_rng(1)
    data = (rng.standard_normal((N_PULSES, 2 * POSITIONS)) * 1000).astype(np.int16)
    path = tmp_path / "iq.dat"
    export_dat(path, data, sample_rate=SAMPLE_RATE, trig_frequency=TRIG,
               range_v=1.0, flags=FLAG_DEMODULATED | FLAG_INTERLEAVED)
    return path


@pytest.fixture
def arctan_dat(tmp_path):
    """Interleaved arctan/magnitude file -> Mode.ARCTAN_MAGNITUDE."""
    rng = np.random.default_rng(2)
    data = (rng.standard_normal((N_PULSES, 2 * POSITIONS)) * 1000).astype(np.int16)
    path = tmp_path / "arctan.dat"
    export_dat(path, data, sample_rate=SAMPLE_RATE, trig_frequency=TRIG,
               range_v=1.0, flags=FLAG_DEMODULATED | FLAG_INTERLEAVED | FLAG_UNSIGNED)
    return path


@pytest.fixture
def phase_dat(tmp_path):
    """Phase file: float32 (N_PULSES, POSITIONS) -> Mode.PHASE."""
    rng = np.random.default_rng(3)
    data = (rng.standard_normal((N_PULSES, POSITIONS))).astype(np.float32)
    path = tmp_path / "phase.dat"
    export_dat(path, data, sample_rate=SAMPLE_RATE, trig_frequency=TRIG,
               range_v=1.0, flags=FLAG_DEMODULATED | FLAG_PHASE)
    return path
