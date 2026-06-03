import numpy as np
import pytest

from audace_display import processing
from audace_display._errors import AudaceDisplayError


def test_to_db_max_ref_is_zero_db():
    data = np.array([1.0, 0.5, 0.1])
    db, ref = processing.to_db(data)
    assert ref == pytest.approx(1.0)
    assert db[0] == pytest.approx(0.0)            # max -> 0 dB
    assert db[1] == pytest.approx(-6.0206, abs=1e-3)  # half -> -6 dB


def test_make_window_known_and_unknown():
    assert processing.make_window("rect", 8).tolist() == [1.0] * 8
    hann = processing.make_window("hann", 16)
    assert hann[0] == pytest.approx(0.0) and hann[-1] == pytest.approx(0.0)
    with pytest.raises(AudaceDisplayError):
        processing.make_window("triangle", 8)


def test_temporal_fft_locates_sinusoid_peak():
    fs = 1000.0
    n = 2048
    f0 = 50.0
    t = np.arange(n) / fs
    sig = np.sin(2 * np.pi * f0 * t).astype(np.float32)[:, None]
    freqs, amp = processing.temporal_fft(sig, fs=fs, window="hann", detrend=True)
    peak_freq = freqs[np.argmax(amp)]
    assert peak_freq == pytest.approx(f0, abs=fs / n * 2)


def test_temporal_fft_peak_reads_true_amplitude():
    # On-bin tone (f0 = k·fs/n) so there is no scalloping loss: the single-sided
    # amplitude spectrum must report the sinusoid's true amplitude A, not A/2.
    fs = 1000.0
    n = 2000
    f0 = 50.0  # 50 = 100·1000/2000 -> exactly on bin 100
    amplitude = 3.0
    t = np.arange(n) / fs
    sig = (amplitude * np.cos(2 * np.pi * f0 * t)).astype(np.float64)[:, None]
    freqs, amp = processing.temporal_fft(sig, fs=fs, window="hann", detrend=True)
    assert freqs[np.argmax(amp)] == pytest.approx(f0, abs=1e-6)
    assert amp.max() == pytest.approx(amplitude, rel=2e-2)  # ~A, not A/2


def test_temporal_fft_too_short():
    with pytest.raises(AudaceDisplayError):
        processing.temporal_fft(np.zeros((1, 4)), fs=1000.0, window="hann", detrend=False)


def test_parse_position_spec_single_list_range():
    step = 0.5
    total = 100
    idx, m = processing.parse_position_spec("12.5", step, total)
    assert idx == [25] and m == [12.5]
    idx, m = processing.parse_position_spec("5,10,15", step, total)
    assert idx == [10, 20, 30]
    idx, m = processing.parse_position_spec("5:6", step, total)
    assert idx == list(range(10, 13))  # 5.0, 5.5, 6.0 m -> idx 10,11,12


def test_parse_position_spec_out_of_range():
    with pytest.raises(AudaceDisplayError):
        processing.parse_position_spec("999", 0.5, 100)


def test_auto_clim_modes():
    data = np.linspace(-1, 1, 1000).astype(np.float32)
    lo, hi = processing.auto_clim(data, is_angular=True, use_db=False)
    assert lo == pytest.approx(-hi, rel=1e-3)  # centered on 0
    lo, hi = processing.auto_clim(data, is_angular=False, use_db=False, vmin=-2.0)
    assert lo == -2.0  # override respected


def test_default_cmap():
    assert processing.default_cmap(True, False) == "RdBu_r"
    assert processing.default_cmap(False, False) == "viridis"
    assert processing.default_cmap(True, True) == "viridis"
