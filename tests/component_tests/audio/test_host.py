"""The audio component on the host platform: stream helpers only, no codecs."""

from __future__ import annotations

import pytest

from esphome.components import audio
import esphome.config_validation as cv
from esphome.const import PlatformFramework
from tests.component_tests.types import SetCoreConfigCallable


def test_auto_load_ring_buffer_only_on_esp32(
    set_core_config: SetCoreConfigCallable,
) -> None:
    set_core_config(PlatformFramework.ESP32_IDF)
    assert audio.AUTO_LOAD() == ["ring_buffer"]
    set_core_config(PlatformFramework.HOST_NATIVE)
    assert audio.AUTO_LOAD() == []


def test_host_accepts_audio_without_codecs(
    set_core_config: SetCoreConfigCallable,
) -> None:
    set_core_config(PlatformFramework.HOST_NATIVE)
    assert audio.CONFIG_SCHEMA({}) == {}


def test_host_rejects_codecs(set_core_config: SetCoreConfigCallable) -> None:
    set_core_config(PlatformFramework.HOST_NATIVE)
    with pytest.raises(cv.Invalid, match="'codecs' is only supported on ESP32"):
        audio.CONFIG_SCHEMA({"codecs": {"flac": {}}})


def test_other_platforms_rejected(set_core_config: SetCoreConfigCallable) -> None:
    set_core_config(PlatformFramework.ESP8266_ARDUINO)
    with pytest.raises(cv.Invalid, match="only available on"):
        audio.CONFIG_SCHEMA({})
