"""Choosing and validating the Avahi backend for mDNS on the host platform."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from esphome.components import mdns
import esphome.config_validation as cv
from esphome.const import CONF_DISABLED, PlatformFramework
from esphome.core import CORE
from esphome.host.pkg_config import PkgConfigPackage
from tests.component_tests.types import SetCoreConfigCallable

AVAHI = PkgConfigPackage("avahi-client", "0.8", "", "-lavahi-client -lavahi-common")


def _setup(
    set_core_config: SetCoreConfigCallable,
    platform_framework: PlatformFramework,
    mdns_config: dict | None,
) -> None:
    set_core_config(platform_framework)
    CORE.config = {} if mdns_config is None else {"mdns": mdns_config}


@pytest.mark.parametrize(
    ("platform_framework", "sys_platform", "mdns_config", "package", "expected"),
    [
        pytest.param(
            PlatformFramework.HOST_NATIVE,
            "linux",
            {CONF_DISABLED: False},
            AVAHI,
            True,
            id="auto_found",
        ),
        pytest.param(
            PlatformFramework.HOST_NATIVE,
            "linux",
            {CONF_DISABLED: False},
            None,
            False,
            id="auto_missing",
        ),
        pytest.param(
            PlatformFramework.HOST_NATIVE,
            "linux",
            {CONF_DISABLED: False, "avahi": False},
            AVAHI,
            False,
            id="opted_out",
        ),
        pytest.param(
            PlatformFramework.HOST_NATIVE,
            "darwin",
            {CONF_DISABLED: False},
            AVAHI,
            False,
            id="not_linux",
        ),
        pytest.param(
            PlatformFramework.HOST_NATIVE,
            "linux",
            {CONF_DISABLED: True},
            AVAHI,
            False,
            id="mdns_disabled",
        ),
        pytest.param(
            PlatformFramework.HOST_NATIVE, "linux", None, AVAHI, False, id="no_mdns"
        ),
        pytest.param(
            PlatformFramework.ESP32_IDF,
            "linux",
            {CONF_DISABLED: False},
            AVAHI,
            False,
            id="esp32",
        ),
    ],
)
def test_host_avahi_enabled(
    set_core_config: SetCoreConfigCallable,
    monkeypatch: pytest.MonkeyPatch,
    platform_framework: PlatformFramework,
    sys_platform: str,
    mdns_config: dict | None,
    package: PkgConfigPackage | None,
    expected: bool,
) -> None:
    _setup(set_core_config, platform_framework, mdns_config)
    monkeypatch.setattr(mdns.sys, "platform", sys_platform)
    with patch.object(mdns.pkg_config, "find_package", return_value=package):
        assert mdns.host_avahi_enabled() is expected


def test_avahi_required_but_missing(
    set_core_config: SetCoreConfigCallable, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_core_config(PlatformFramework.HOST_NATIVE)
    monkeypatch.setattr(mdns.sys, "platform", "linux")
    with (
        patch.object(mdns.pkg_config, "find_package", return_value=None),
        pytest.raises(cv.Invalid, match="libavahi-client-dev"),
    ):
        mdns.CONFIG_SCHEMA({"avahi": True})


def test_avahi_required_on_macos(
    set_core_config: SetCoreConfigCallable, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_core_config(PlatformFramework.HOST_NATIVE)
    monkeypatch.setattr(mdns.sys, "platform", "darwin")
    with pytest.raises(cv.Invalid, match="Linux build machine"):
        mdns.CONFIG_SCHEMA({"avahi": True})


def test_avahi_option_only_on_host(set_core_config: SetCoreConfigCallable) -> None:
    set_core_config(PlatformFramework.ESP32_IDF)
    with pytest.raises(cv.Invalid, match="only available on the host platform"):
        mdns.CONFIG_SCHEMA({"avahi": False})
