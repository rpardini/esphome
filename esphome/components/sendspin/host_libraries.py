"""sendspin-cpp and its dependencies, built from source for the host platform.

On ESP32 these come in as ESP-IDF components. Their host builds are CMake
projects without a manifest the host build can use, so the build layout is
described here instead, following each library's own CMake files.
"""

from __future__ import annotations

from typing import Any

import esphome.codegen as cg
from esphome.components import host

ARDUINOJSON_VERSION = "7.4.3"
IXWEBSOCKET_VERSION = "v11.4.5"
MICRO_FLAC_VERSION = "v0.2.0"
MICRO_OPUS_VERSION = "v0.4.1"

# Role sources in sendspin-cpp's cmake/sources.cmake; every other top-level
# source is always built
_SENDSPIN_ROLE_SOURCES = {
    "ARTWORK": ["artwork_role.cpp"],
    "COLOR": ["color_role.cpp"],
    "CONTROLLER": ["controller_role.cpp"],
    "METADATA": ["metadata_role.cpp"],
    "PLAYER": [
        "audio_ring_buffer.cpp",
        "decoder.cpp",
        "player_role.cpp",
        "sync_task.cpp",
    ],
    "VISUALIZER": ["visualizer_role.cpp"],
}

# The unpatched lib/opus submodule of micro-opus, fixed point and without the
# float API (as micro-opus configures it). Tools with a main() are left out.
_OPUS_SRC_FILTER = [
    "+<src/*.c>",
    "-<src/analysis.c>",
    "-<src/mlp.c>",
    "-<src/mlp_data.c>",
    "-<src/opus_compare.c>",
    "-<src/opus_demo.c>",
    "-<src/qext_compare.c>",
    "-<src/repacketizer_demo.c>",
    "+<celt/*.c>",
    "-<celt/opus_custom_demo.c>",
    "+<silk/*.c>",
    "+<silk/fixed/*.c>",
]
_OPUS_FLAGS = [
    "-DOPUS_BUILD",
    "-DFIXED_POINT=1",
    "-DDISABLE_FLOAT_API",
    "-DVAR_ARRAYS",
    "-DHAVE_LRINT",
    "-DHAVE_LRINTF",
]


def _manifest(build: dict[str, Any], private_include_dirs: list[str]) -> dict[str, Any]:
    return {
        "platforms": "*",
        "build": build,
        "ESPHOME": {"PRIVATE_INCLUDE_DIRS": private_include_dirs},
    }


def add_host_libraries(sendspin_version: str, disabled_roles: list[str]) -> None:
    """Add sendspin-cpp without ``disabled_roles`` and the libraries it needs."""
    enabled_roles = [role for role in _SENDSPIN_ROLE_SOURCES if role not in disabled_roles]
    for role in enabled_roles:
        cg.add_build_flag(f"-DSENDSPIN_ENABLE_{role}")

    host.add_library(
        "sendspin-cpp",
        f"https://github.com/sendspin/sendspin-cpp.git#v{sendspin_version}",
        _manifest(
            {
                "srcDir": "src",
                "includeDir": "include",
                "srcFilter": [
                    "+<*.cpp>",
                    "+<host/*.cpp>",
                    *(
                        f"-<{source}>"
                        for role in disabled_roles
                        for source in _SENDSPIN_ROLE_SOURCES.get(role, [])
                    ),
                ],
            },
            ["src", "src/host"],
        ),
    )

    # Same configuration as the json component, which also provides the library
    cg.add_library("bblanchon/ArduinoJson", ARDUINOJSON_VERSION)
    cg.add_build_flag("-DARDUINOJSON_ENABLE_STD_STRING=1")
    cg.add_build_flag("-DARDUINOJSON_USE_LONG_LONG=1")

    # Built without TLS and zlib, as sendspin-cpp's host build does
    host.add_library(
        "IXWebSocket",
        f"https://github.com/machinezone/IXWebSocket.git#{IXWEBSOCKET_VERSION}",
        _manifest(
            {
                "srcDir": "ixwebsocket",
                "includeDir": ".",
                "srcFilter": [
                    "+<*.cpp>",
                    "-<IXSocketAppleSSL.cpp>",
                    "-<IXSocketMbedTLS.cpp>",
                    "-<IXSocketOpenSSL.cpp>",
                ],
            },
            [],
        ),
    )
    cg.add_build_flag("-pthread")

    if "PLAYER" in disabled_roles:
        return

    # micro-flac and micro-opus both bundle micro-ogg-demuxer; build it once
    host.add_library(
        "micro-flac",
        f"https://github.com/esphome-libs/micro-flac.git#{MICRO_FLAC_VERSION}",
        _manifest(
            {
                "srcDir": ".",
                "includeDir": "include",
                "srcFilter": [
                    "+<src/*.cpp>",
                    "+<lib/micro-ogg-demuxer/src/ogg_demuxer.cpp>",
                ],
                "flags": ["-O2", "-DNDEBUG"],
            },
            ["src", "lib/micro-ogg-demuxer/include"],
        ),
    )
    # sendspin-cpp decodes Opus with libopus directly, so only that is built
    host.add_library(
        "micro-opus",
        f"https://github.com/esphome-libs/micro-opus.git#{MICRO_OPUS_VERSION}",
        _manifest(
            {
                "srcDir": "lib/opus",
                "includeDir": "lib/opus/include",
                "srcFilter": _OPUS_SRC_FILTER,
                "flags": ["-O2", *_OPUS_FLAGS],
            },
            ["lib/opus", "lib/opus/celt", "lib/opus/silk", "lib/opus/silk/fixed"],
        ),
    )
