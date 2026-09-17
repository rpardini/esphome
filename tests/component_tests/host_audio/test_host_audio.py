"""Schema validation for the host_audio component and its platforms."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

from esphome.components import host_audio
from esphome.components.host_audio import (
    microphone as host_audio_microphone,
    speaker as host_audio_speaker,
)
import esphome.config_validation as cv
from esphome.const import PlatformFramework
from esphome.host.pkg_config import PkgConfigPackage
from tests.component_tests.types import SetCoreConfigCallable

PORTAUDIO = PkgConfigPackage("portaudio-2.0", "19.7.0", "", "-lportaudio")


@pytest.fixture
def host(set_core_config: SetCoreConfigCallable) -> Generator[None]:
    set_core_config(PlatformFramework.HOST_NATIVE)
    with patch("esphome.host.pkg_config.find_package", return_value=PORTAUDIO):
        yield


def test_hub_requires_portaudio(set_core_config: SetCoreConfigCallable) -> None:
    set_core_config(PlatformFramework.HOST_NATIVE)
    with (
        patch("esphome.host.pkg_config.find_package", return_value=None),
        pytest.raises(cv.Invalid, match="portaudio19-dev"),
    ):
        host_audio.CONFIG_SCHEMA({})


def test_hub_only_on_host(set_core_config: SetCoreConfigCallable) -> None:
    set_core_config(PlatformFramework.ESP32_IDF)
    with pytest.raises(cv.Invalid, match="only available on"):
        host_audio.CONFIG_SCHEMA({})


@pytest.mark.usefixtures("host")
@pytest.mark.parametrize(
    ("device", "expected"),
    [(2, 2), ("pipewire", "pipewire")],
)
def test_speaker_device_by_index_or_name(
    device: int | str, expected: int | str
) -> None:
    config = host_audio_speaker.CONFIG_SCHEMA(
        {"id": "spk", "host_audio_id": "hub", "device": device}
    )
    assert config["device"] == expected
    assert config["latency"] == "high"
    assert config["max_sample_rate"] == 192000


@pytest.mark.usefixtures("host")
@pytest.mark.parametrize(("latency", "expected"), [("LOW", "low"), ("80ms", 80)])
def test_speaker_latency(latency: str, expected: str | int) -> None:
    config = host_audio_speaker.CONFIG_SCHEMA(
        {"id": "spk", "host_audio_id": "hub", "latency": latency}
    )
    value = config["latency"]
    assert (value if isinstance(value, str) else value.total_milliseconds) == expected


@pytest.mark.usefixtures("host")
def test_speaker_rejects_negative_device_index() -> None:
    with pytest.raises(cv.Invalid):
        host_audio_speaker.CONFIG_SCHEMA(
            {"id": "spk", "host_audio_id": "hub", "device": -1}
        )


@pytest.mark.usefixtures("host")
def test_microphone_pins_stream_limits() -> None:
    config = host_audio_microphone.CONFIG_SCHEMA(
        {
            "id": "mic",
            "host_audio_id": "hub",
            "sample_rate": 48000,
            "bits_per_sample": "24bit",
            "channel": "stereo",
        }
    )
    assert config["bits_per_sample"] == 24
    assert config["num_channels"] == 2
    assert config["min_sample_rate"] == config["max_sample_rate"] == 48000
    assert config["min_bits_per_sample"] == config["max_bits_per_sample"] == 24


@pytest.mark.usefixtures("host")
def test_microphone_rejects_odd_bit_depth() -> None:
    with pytest.raises(cv.Invalid):
        host_audio_microphone.CONFIG_SCHEMA(
            {"id": "mic", "host_audio_id": "hub", "bits_per_sample": "12bit"}
        )
