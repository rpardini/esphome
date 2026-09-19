"""Tests for the tone_generator frequency validator.

A frequency at or above half the sample rate aliases down to some other tone, which is the one
failure that would be silent: the config builds, the speaker plays, and what comes out is not what
was asked for.
"""

from __future__ import annotations

import pytest

from esphome.components.tone_generator import CONFIG_SCHEMA
import esphome.config_validation as cv
from esphome.core import Lambda
from esphome.types import ConfigType


def _config(**overrides: object) -> ConfigType:
    config = {
        "speaker": "tone_speaker",
        "sample_rate": 16000,
        "frequency": "1000Hz",
    }
    config.update(overrides)
    return CONFIG_SCHEMA(config)


def test_frequency_below_nyquist_is_accepted() -> None:
    assert _config(frequency="7999Hz")["frequency"] == pytest.approx(7999.0)


@pytest.mark.parametrize("frequency", ["8000Hz", "12000Hz"])
def test_frequency_at_or_above_nyquist_is_rejected(frequency: str) -> None:
    with pytest.raises(cv.Invalid, match="below half the sample rate"):
        _config(frequency=frequency)


def test_templated_frequency_skips_the_nyquist_check() -> None:
    """A lambda has no value at config time, so the check cannot run; it must not reject it either."""
    config = _config(frequency=Lambda("return 4.0f;"))
    assert isinstance(config["frequency"], Lambda)
