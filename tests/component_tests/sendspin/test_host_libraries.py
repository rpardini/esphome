"""sendspin-cpp's host build: libraries, manifests and role defines."""

from __future__ import annotations

from esphome.components.host.const import KEY_HOST, KEY_LIBRARY_MANIFESTS
from esphome.components.sendspin.host_libraries import add_host_libraries
from esphome.const import PlatformFramework
from esphome.core import CORE
from tests.component_tests.types import SetCoreConfigCallable


def _setup(set_core_config: SetCoreConfigCallable) -> None:
    set_core_config(PlatformFramework.HOST_NATIVE)
    CORE.data[KEY_HOST] = {}


def _manifests() -> dict[str, dict]:
    return CORE.data[KEY_HOST][KEY_LIBRARY_MANIFESTS]


def test_player_role_adds_codec_libraries(
    set_core_config: SetCoreConfigCallable,
) -> None:
    _setup(set_core_config)
    add_host_libraries("0.8.0", ["COLOR", "ARTWORK", "VISUALIZER"])

    assert set(_manifests()) == {
        "sendspin/sendspin-cpp",
        "machinezone/IXWebSocket",
        "esphome-libs/micro-flac",
        "esphome-libs/micro-opus",
    }
    assert set(CORE.platformio_libraries) == {
        "sendspin-cpp",
        "ArduinoJson",
        "IXWebSocket",
        "micro-flac",
        "micro-opus",
    }
    assert CORE.platformio_libraries["sendspin-cpp"].repository.endswith("#v0.8.0")
    assert {
        "-DSENDSPIN_ENABLE_CONTROLLER",
        "-DSENDSPIN_ENABLE_METADATA",
        "-DSENDSPIN_ENABLE_PLAYER",
    } <= CORE.build_flags
    assert "-DSENDSPIN_ENABLE_COLOR" not in CORE.build_flags

    sendspin = _manifests()["sendspin/sendspin-cpp"]
    assert sendspin["ESPHOME"]["PRIVATE_INCLUDE_DIRS"] == ["src", "src/host"]
    src_filter = sendspin["build"]["srcFilter"]
    assert "-<artwork_role.cpp>" in src_filter
    assert "-<player_role.cpp>" not in src_filter


def test_without_player_role_skips_codecs(
    set_core_config: SetCoreConfigCallable,
) -> None:
    _setup(set_core_config)
    add_host_libraries("0.8.0", ["COLOR", "PLAYER"])

    assert set(_manifests()) == {"sendspin/sendspin-cpp", "machinezone/IXWebSocket"}
    src_filter = _manifests()["sendspin/sendspin-cpp"]["build"]["srcFilter"]
    assert {"-<decoder.cpp>", "-<sync_task.cpp>"} <= set(src_filter)
    assert "-DSENDSPIN_ENABLE_PLAYER" not in CORE.build_flags
