"""request_service_enable_disable() only opts in on platforms whose mDNS stack
can add and remove services after setup, and tells the caller so."""

from unittest.mock import patch

import pytest

from esphome.components import mdns
from esphome.const import CONF_DISABLED, PlatformFramework
from esphome.core import CORE
from esphome.host.pkg_config import PkgConfigPackage
from tests.component_tests.types import SetCoreConfigCallable

DEFINE = "USE_MDNS_SUPPORTS_ENABLE_DISABLE"
AVAHI = PkgConfigPackage("avahi-client", "0.8", "", "-lavahi-client -lavahi-common")


def _defines() -> set[str]:
    return {define.name for define in CORE.defines}


def _set_config(
    set_core_config: SetCoreConfigCallable,
    platform_framework: PlatformFramework,
    config: dict,
) -> None:
    set_core_config(platform_framework)
    CORE.config = config


@pytest.mark.parametrize(
    "platform_framework",
    [PlatformFramework.ESP32_IDF, PlatformFramework.ESP32_ARDUINO],
)
def test_esp32_adds_define_and_keeps_services_stored(
    set_core_config: SetCoreConfigCallable, platform_framework: PlatformFramework
) -> None:
    _set_config(set_core_config, platform_framework, {"mdns": {CONF_DISABLED: False}})

    assert mdns.request_service_enable_disable() is True
    # Disabled services must stay stored so they can be re-registered later.
    assert {DEFINE, "USE_MDNS_STORE_SERVICES"} <= _defines()


@pytest.mark.parametrize(
    "platform_framework",
    [PlatformFramework.ESP8266_ARDUINO, PlatformFramework.RP2_ARDUINO],
)
def test_other_platforms_return_false(
    set_core_config: SetCoreConfigCallable, platform_framework: PlatformFramework
) -> None:
    _set_config(set_core_config, platform_framework, {"mdns": {CONF_DISABLED: False}})

    assert mdns.request_service_enable_disable() is False
    assert DEFINE not in _defines()


@pytest.mark.parametrize(
    "config",
    [
        pytest.param({}, id="no_mdns"),
        pytest.param({"mdns": {CONF_DISABLED: True}}, id="mdns_disabled"),
        pytest.param(
            {"mdns": {CONF_DISABLED: False}, "openthread": {}}, id="openthread"
        ),
    ],
)
def test_esp32_returns_false_when_services_cannot_be_toggled(
    set_core_config: SetCoreConfigCallable, config: dict
) -> None:
    _set_config(set_core_config, PlatformFramework.ESP32_IDF, config)

    assert mdns.request_service_enable_disable() is False
    assert DEFINE not in _defines()


def _host_config(set_core_config: SetCoreConfigCallable, mdns_config: dict) -> None:
    _set_config(set_core_config, PlatformFramework.HOST_NATIVE, {"mdns": mdns_config})


def test_host_with_avahi_adds_define(
    set_core_config: SetCoreConfigCallable, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host_config(set_core_config, {CONF_DISABLED: False})
    monkeypatch.setattr(mdns.sys, "platform", "linux")
    with patch.object(mdns.pkg_config, "find_package", return_value=AVAHI):
        assert mdns.request_service_enable_disable() is True
    assert {DEFINE, "USE_MDNS_STORE_SERVICES"} <= _defines()


def test_host_without_avahi_returns_false(
    set_core_config: SetCoreConfigCallable, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host_config(set_core_config, {CONF_DISABLED: False})
    monkeypatch.setattr(mdns.sys, "platform", "linux")
    with patch.object(mdns.pkg_config, "find_package", return_value=None):
        assert mdns.request_service_enable_disable() is False
    assert DEFINE not in _defines()


def test_host_with_bonjour_adds_define(
    set_core_config: SetCoreConfigCallable, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host_config(set_core_config, {CONF_DISABLED: False})
    monkeypatch.setattr(mdns.sys, "platform", "darwin")
    assert mdns.request_service_enable_disable() is True
    assert {DEFINE, "USE_MDNS_STORE_SERVICES"} <= _defines()


def test_host_without_bonjour_returns_false(
    set_core_config: SetCoreConfigCallable, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host_config(set_core_config, {CONF_DISABLED: False, "bonjour": False})
    monkeypatch.setattr(mdns.sys, "platform", "darwin")
    assert mdns.request_service_enable_disable() is False
    assert DEFINE not in _defines()
