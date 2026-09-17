"""Tests for esphome.host.pkg_config."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

import esphome.config_validation as cv
from esphome.core import CORE
from esphome.host import pkg_config

_OUTPUTS = {
    "--modversion": "19.7.0",
    "--cflags": "-I/opt/pa/include",
    "--libs": "-L/opt/pa/lib -lportaudio -framework CoreAudio",
}


def _fake_run(missing: bool = False):
    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        if missing:
            return subprocess.CompletedProcess(args, 1, "", "not found")
        return subprocess.CompletedProcess(args, 0, _OUTPUTS[args[1]] + "\n", "")

    return run


def test_find_package() -> None:
    with (
        patch.dict("os.environ", {"PKG_CONFIG": "/usr/bin/pkg-config"}),
        patch.object(subprocess, "run", side_effect=_fake_run()) as run,
    ):
        package = pkg_config.find_package("portaudio-2.0")
        # Cached for the rest of the run
        assert pkg_config.find_package("portaudio-2.0") is package
    assert package == pkg_config.PkgConfigPackage(
        "portaudio-2.0",
        "19.7.0",
        "-I/opt/pa/include",
        "-L/opt/pa/lib -lportaudio -framework CoreAudio",
    )
    assert run.call_count == 3


def test_find_package_missing_package() -> None:
    with (
        patch.dict("os.environ", {"PKG_CONFIG": "/usr/bin/pkg-config"}),
        patch.object(subprocess, "run", side_effect=_fake_run(missing=True)),
    ):
        assert pkg_config.find_package("nope") is None


def test_find_package_without_pkg_config() -> None:
    with (
        patch.dict("os.environ", {"PKG_CONFIG": ""}),
        patch.object(pkg_config.shutil, "which", return_value=None),
        patch.object(subprocess, "run") as run,
    ):
        assert pkg_config.find_package("portaudio-2.0") is None
    run.assert_not_called()


def test_find_package_binary_fails_to_run() -> None:
    with (
        patch.dict("os.environ", {"PKG_CONFIG": "/nonexistent/pkg-config"}),
        patch.object(subprocess, "run", side_effect=FileNotFoundError),
    ):
        assert pkg_config.find_package("portaudio-2.0") is None


def test_require_package_raises_with_hint() -> None:
    with (
        patch.object(pkg_config, "find_package", return_value=None),
        pytest.raises(cv.Invalid, match="apt install portaudio19-dev"),
    ):
        pkg_config.require_package(
            "portaudio-2.0", "host_audio", "apt install portaudio19-dev"
        )


def test_add_package_build_flags_adds_one_entry() -> None:
    package = pkg_config.PkgConfigPackage(
        "x", "1", "-I/inc", "-lx -framework CoreAudio"
    )
    pkg_config.add_package_build_flags(package)
    assert CORE.build_flags == {"-I/inc -lx -framework CoreAudio"}
