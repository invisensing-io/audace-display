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


def test_band_limited_isolates_tone_in_band():
    # Two tones; a band around only one of them must keep that one and kill the
    # other (brick-wall band-pass along axis 0).
    fs = 1000.0
    n = 2000
    t = np.arange(n) / fs
    keep, drop = 50.0, 300.0
    sig = (np.sin(2 * np.pi * keep * t) + np.sin(2 * np.pi * drop * t)).astype(np.float32)
    out = processing.band_limited(sig[:, None], fs=fs, f_lo=30.0, f_hi=120.0)[:, 0]
    # RMS of a unit sine is 1/sqrt(2) ~ 0.707; only the in-band tone survives.
    assert np.sqrt(np.mean(out ** 2)) == pytest.approx(1 / np.sqrt(2), rel=0.05)
    # the 300 Hz component is gone -> residual at that bin is negligible
    spec = np.abs(np.fft.rfft(out))
    freqs = np.fft.rfftfreq(n, d=1 / fs)
    assert spec[np.argmin(np.abs(freqs - drop))] < 1e-3 * spec.max()


def test_band_limited_lowband_keeps_dc():
    fs = 1000.0
    n = 1024
    sig = (5.0 + np.zeros(n)).astype(np.float32)[:, None]  # pure DC
    kept = processing.band_limited(sig, fs=fs, f_lo=0.0, f_hi=10.0)[:, 0]
    np.testing.assert_allclose(kept, 5.0, atol=1e-4)
    dropped = processing.band_limited(sig, fs=fs, f_lo=1.0, f_hi=10.0)[:, 0]
    assert np.allclose(dropped, 0.0, atol=1e-4)  # f_lo > 0 removes DC


def test_to_log_variance_is_log10_and_floors_zeros():
    var = np.array([[1.0, 100.0, 0.0]], dtype=np.float32)
    out = processing.to_log_variance(var)
    assert out[0, 0] == pytest.approx(0.0)      # log10(1) = 0
    assert out[0, 1] == pytest.approx(2.0)      # log10(100) = 2
    assert np.isfinite(out[0, 2]) and out[0, 2] < 0.0  # zero floored, not -inf


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
