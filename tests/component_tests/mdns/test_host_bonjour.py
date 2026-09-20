"""Choosing and validating the Bonjour backend for mDNS on the host platform."""

from __future__ import annotations

import pytest

from esphome.components import mdns
import esphome.config_validation as cv
from esphome.const import CONF_DISABLED, PlatformFramework
from esphome.core import CORE
from tests.component_tests.types import SetCoreConfigCallable


def _setup(
    set_core_config: SetCoreConfigCallable,
    platform_framework: PlatformFramework,
    mdns_config: dict | None,
) -> None:
    set_core_config(platform_framework)
    CORE.config = {} if mdns_config is None else {"mdns": mdns_config}


@pytest.mark.parametrize(
    ("platform_framework", "sys_platform", "mdns_config", "expected"),
    [
        pytest.param(
            PlatformFramework.HOST_NATIVE,
            "darwin",
            {CONF_DISABLED: False},
            True,
            id="auto_on_macos",
        ),
        pytest.param(
            PlatformFramework.HOST_NATIVE,
            "linux",
            {CONF_DISABLED: False},
            False,
            id="not_macos",
        ),
        pytest.param(
            PlatformFramework.HOST_NATIVE,
            "darwin",
            {CONF_DISABLED: False, "bonjour": False},
            False,
            id="opted_out",
        ),
        pytest.param(
            PlatformFramework.HOST_NATIVE,
            "darwin",
            {CONF_DISABLED: True},
            False,
            id="mdns_disabled",
        ),
        pytest.param(
            PlatformFramework.HOST_NATIVE, "darwin", None, False, id="no_mdns"
        ),
        pytest.param(
            PlatformFramework.ESP32_IDF,
            "darwin",
            {CONF_DISABLED: False},
            False,
            id="esp32",
        ),
    ],
)
def test_host_bonjour_enabled(
    set_core_config: SetCoreConfigCallable,
    monkeypatch: pytest.MonkeyPatch,
    platform_framework: PlatformFramework,
    sys_platform: str,
    mdns_config: dict | None,
    expected: bool,
) -> None:
    _setup(set_core_config, platform_framework, mdns_config)
    monkeypatch.setattr(mdns.sys, "platform", sys_platform)
    assert mdns.host_bonjour_enabled() is expected


def test_bonjour_required_on_linux(
    set_core_config: SetCoreConfigCallable, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_core_config(PlatformFramework.HOST_NATIVE)
    monkeypatch.setattr(mdns.sys, "platform", "linux")
    with pytest.raises(cv.Invalid, match="macOS build machine"):
        mdns.CONFIG_SCHEMA({"bonjour": True})


def test_bonjour_off_needs_no_macos(
    set_core_config: SetCoreConfigCallable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configuration shared between machines can turn the backend off anywhere."""
    set_core_config(PlatformFramework.HOST_NATIVE)
    monkeypatch.setattr(mdns.sys, "platform", "linux")
    assert mdns.CONFIG_SCHEMA({"bonjour": False})["bonjour"] is False


def test_bonjour_option_only_on_host(set_core_config: SetCoreConfigCallable) -> None:
    set_core_config(PlatformFramework.ESP32_IDF)
    with pytest.raises(cv.Invalid, match="only available on the host platform"):
        mdns.CONFIG_SCHEMA({"bonjour": False})
