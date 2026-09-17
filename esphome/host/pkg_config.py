"""Find system libraries for host builds with pkg-config.

Components that link a library installed on the build machine (PortAudio,
Avahi, ...) look it up here during validation, so a missing development
package fails with an install hint instead of a compiler error.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import shutil
import subprocess

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.core import CORE

_LOGGER = logging.getLogger(__name__)

DOMAIN = "host_pkg_config"
_TIMEOUT_S = 10


@dataclass(frozen=True)
class PkgConfigPackage:
    name: str
    version: str
    cflags: str
    libs: str


def _run(binary: str, *args: str) -> str | None:
    try:
        result = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        _LOGGER.debug("Running %s %s failed: %s", binary, " ".join(args), err)
        return None
    if result.returncode != 0:
        _LOGGER.debug(
            "%s %s exited with %d: %s",
            binary,
            " ".join(args),
            result.returncode,
            result.stderr.strip(),
        )
        return None
    return result.stdout.strip()


def find_package(name: str) -> PkgConfigPackage | None:
    """Look up ``name`` with pkg-config; None when it or the package is missing.

    Results are cached in ``CORE.data``, so they are refreshed on every run
    (a package installed while the dashboard runs is found next time).
    """
    cache: dict[str, PkgConfigPackage | None] = CORE.data.setdefault(DOMAIN, {})
    if name in cache:
        return cache[name]
    package = None
    binary = os.environ.get("PKG_CONFIG") or shutil.which("pkg-config")
    if binary is None:
        _LOGGER.debug("pkg-config not found while looking for %s", name)
    elif (version := _run(binary, "--modversion", name)) is not None:
        cflags = _run(binary, "--cflags", name)
        libs = _run(binary, "--libs", name)
        if cflags is not None and libs is not None:
            package = PkgConfigPackage(name, version, cflags, libs)
    cache[name] = package
    return package


def require_package(name: str, component: str, install_hint: str) -> PkgConfigPackage:
    """Like :func:`find_package`, but raise a validation error when missing."""
    if (package := find_package(name)) is None:
        raise cv.Invalid(
            f"{component} needs the '{name}' development package, found with "
            f"pkg-config. Install it (e.g. {install_hint}) and make sure "
            "pkg-config is on PATH."
        )
    return package


def add_package_build_flags(package: PkgConfigPackage) -> None:
    """Add the package's compile and link flags to the build.

    Added as one entry: build flags are a sorted set, so separate entries
    would split two-token flags such as ``-framework CoreAudio``. The host
    build lexes the entry again and routes each token.
    """
    if flags := " ".join(f for f in (package.cflags, package.libs) if f):
        cg.add_build_flag(flags)
