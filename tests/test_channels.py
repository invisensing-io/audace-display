import pytest
from invisensing import Mode

from audace_display.channels import resolve_channel, DEFAULT_CHANNEL
from audace_display._errors import AudaceDisplayError


def test_default_channel_per_mode():
    assert DEFAULT_CHANNEL[Mode.RAW] == "raw"
    assert DEFAULT_CHANNEL[Mode.IQ] == "magnitude"
    assert DEFAULT_CHANNEL[Mode.ARCTAN_MAGNITUDE] == "magnitude"
    assert DEFAULT_CHANNEL[Mode.PHASE] == "phase"


def test_resolve_none_uses_default():
    canonical, _, _, _ = resolve_channel(None, Mode.IQ)
    assert canonical == "magnitude"


@pytest.mark.parametrize("alias,canonical", [
    ("mag", "magnitude"), ("amp", "magnitude"), ("atan", "arctan"),
    ("angle", "phase_wrapped"), ("adc", "raw"), ("phi", "phase"),
])
def test_aliases(alias, canonical):
    # mode chosen so the channel is valid
    mode = {
        "magnitude": Mode.IQ, "arctan": Mode.ARCTAN_MAGNITUDE,
        "phase_wrapped": Mode.IQ, "raw": Mode.RAW, "phase": Mode.PHASE,
    }[canonical]
    got, _, _, _ = resolve_channel(alias, mode)
    assert got == canonical


def test_unknown_channel():
    with pytest.raises(AudaceDisplayError, match="unknown"):
        resolve_channel("bogus", Mode.IQ)


def test_channel_invalid_for_mode():
    with pytest.raises(AudaceDisplayError, match="unavailable"):
        resolve_channel("phase", Mode.RAW)


def test_angular_flag():
    *_, is_angular = resolve_channel("phase", Mode.PHASE)
    assert is_angular is True
    *_, is_angular = resolve_channel("magnitude", Mode.IQ)
    assert is_angular is False
