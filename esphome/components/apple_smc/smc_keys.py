"""Enumerate and classify the keys of the Apple System Management Controller.

The scan runs on the machine doing the build, so it can only see that machine's
controller. ``ESPHOME_APPLE_SMC_KEYS`` points it at a dump taken from another
Mac instead, which is also how the tests drive it.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
import struct
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

_LOGGER = logging.getLogger(__name__)

# Overridable so a build host can describe another machine's controller.
ENV_SMC_KEYS = "ESPHOME_APPLE_SMC_KEYS"

IOKIT_FRAMEWORK = "/System/Library/Frameworks/IOKit.framework/IOKit"

KEY_LENGTH = 4
# One read carries at most this many bytes of payload
MAX_VALUE_SIZE = 32
# Keys starting with this are the controller's own metadata, not readings
METADATA_PREFIX = "#"

# The one text type: ASCII, not null terminated
TEXT_TYPE = "ch8*"
# Types that can only ever be a whole number
_INTEGER_TYPE_RE = re.compile(r"^(?:ui(?:8|16|32|64)|si(?:8|16|32|64)|flag)$")
# Types that carry a fraction. The fixed point family spells its integer and
# fraction bit counts out in hex, e.g. sp78 is 7.8 and fpe2 is 14.2.
_FRACTIONAL_TYPE_RE = re.compile(r"^(?:flt|[fs]p[0-9a-f]{2}|ioft)$")

_KEY_RE = re.compile(r"^[\x20-\x7e]{4}$")

# The user client takes one selector, and the command travels inside the request
_SELECTOR_KERNEL_INDEX = 2
_CMD_READ_BYTES = 5
_CMD_READ_INDEX = 8
_CMD_READ_KEYINFO = 9

# Field offsets inside the 80 byte request/response struct the user client takes
_REQUEST_SIZE = 80
_OFFSET_KEY = 0
_OFFSET_DATA_SIZE = 28
_OFFSET_DATA_TYPE = 32
_OFFSET_RESULT = 40
_OFFSET_DATA8 = 42
_OFFSET_DATA32 = 44
_OFFSET_BYTES = 48

# Reading this key gives the number of keys the controller has
_COUNT_KEY = "#KEY"


@dataclass(frozen=True)
class SmcKey:
    """One controller key that can be turned into an entity."""

    key: str
    data_type: str
    size: int
    is_text: bool
    is_integer: bool


def key_code(key: str) -> int:
    """Turn a four character key into the number the user client expects."""
    return int.from_bytes(key.encode("ascii"), "big")


def _four_cc(value: int) -> str:
    return struct.pack(">I", value).decode("ascii", "replace")


def is_valid_key(key: str) -> bool:
    return _KEY_RE.match(key) is not None


def classify(key: str, data_type: str, size: int) -> SmcKey | None:
    """Decide what a key can be published as, or None when it cannot be.

    Only keys this component knows how to decode are offered. A key that is
    empty, too large for one read, or of an opaque type such as ``hex_`` is
    left out; naming one of those explicitly fails at setup instead.
    """
    if not is_valid_key(key) or key.startswith(METADATA_PREFIX):
        return None
    if size < 1 or size > MAX_VALUE_SIZE:
        return None
    stripped = data_type.rstrip()
    if stripped == TEXT_TYPE:
        return SmcKey(key, stripped, size, True, False)
    if _INTEGER_TYPE_RE.match(stripped) is not None:
        return SmcKey(key, stripped, size, False, True)
    if _FRACTIONAL_TYPE_RE.match(stripped) is not None:
        return SmcKey(key, stripped, size, False, False)
    return None


def scan_keys() -> list[SmcKey]:
    """List every key of the controller this build can reach."""
    if dump := os.environ.get(ENV_SMC_KEYS):
        keys = _read_dump(Path(dump))
    else:
        keys = _scan_controller()
    if not keys:
        _LOGGER.warning("apple_smc: no readable keys found; discovery found nothing")
    return keys


def _read_dump(path: Path) -> list[SmcKey]:
    """Read a tab separated dump of ``key``, ``type`` and ``size``."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as err:
        _LOGGER.warning("apple_smc: cannot read %s (%s)", path, err)
        return []
    keys: list[SmcKey] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) < 3 or not fields[2].strip().isdigit():
            _LOGGER.warning("apple_smc: %s line %d is malformed", path, number)
            continue
        if (found := classify(fields[0], fields[1], int(fields[2]))) is not None:
            keys.append(found)
    return sorted(keys, key=lambda found: found.key)


class _UserClient:
    """The one call the SMC user client offers, wrapped up."""

    def __init__(self, iokit: Any, connection: int) -> None:
        self._iokit = iokit
        self._connection = connection

    def call(
        self, key: int = 0, command: int = 0, index: int = 0, size: int = 0
    ) -> bytes | None:
        import ctypes

        request = bytearray(_REQUEST_SIZE)
        struct.pack_into("<I", request, _OFFSET_KEY, key)
        struct.pack_into("<I", request, _OFFSET_DATA_SIZE, size)
        request[_OFFSET_DATA8] = command
        struct.pack_into("<I", request, _OFFSET_DATA32, index)

        buffer = (ctypes.c_char * _REQUEST_SIZE).from_buffer_copy(bytes(request))
        response = ctypes.create_string_buffer(_REQUEST_SIZE)
        length = ctypes.c_size_t(_REQUEST_SIZE)
        result = self._iokit.IOConnectCallStructMethod(
            self._connection,
            _SELECTOR_KERNEL_INDEX,
            ctypes.byref(buffer),
            _REQUEST_SIZE,
            response,
            ctypes.byref(length),
        )
        if result != 0:
            return None
        raw = response.raw
        # A non-zero result means the controller refused: missing key, or one
        # that exists but cannot be read
        return None if raw[_OFFSET_RESULT] != 0 else raw


@contextmanager
def _smc_connection() -> Iterator[_UserClient]:
    # ctypes and the framework are loaded here rather than at import time, so
    # that nothing pays for them on a platform that has no controller
    import ctypes
    import ctypes.util

    iokit = ctypes.CDLL(IOKIT_FRAMEWORK)
    libc = ctypes.CDLL(ctypes.util.find_library("c"))
    iokit.IOServiceMatching.restype = ctypes.c_void_p
    iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]
    iokit.IOServiceGetMatchingService.restype = ctypes.c_uint
    iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    iokit.IOServiceOpen.restype = ctypes.c_int
    iokit.IOServiceOpen.argtypes = [
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.POINTER(ctypes.c_uint),
    ]
    iokit.IOServiceClose.argtypes = [ctypes.c_uint]
    iokit.IOObjectRelease.argtypes = [ctypes.c_uint]
    iokit.IOConnectCallStructMethod.restype = ctypes.c_int
    iokit.IOConnectCallStructMethod.argtypes = [
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    libc.mach_task_self.restype = ctypes.c_uint

    # IOServiceGetMatchingService consumes the matching dictionary
    service = iokit.IOServiceGetMatchingService(
        None, iokit.IOServiceMatching(b"AppleSMC")
    )
    if service == 0:
        raise OSError("the AppleSMC service is not present")
    connection = ctypes.c_uint(0)
    if (
        iokit.IOServiceOpen(service, libc.mach_task_self(), 0, ctypes.byref(connection))
        != 0
    ):
        iokit.IOObjectRelease(service)
        raise OSError("cannot open the AppleSMC user client")
    try:
        yield _UserClient(iokit, connection.value)
    finally:
        iokit.IOServiceClose(connection.value)
        iokit.IOObjectRelease(service)


def _key_info(client: _UserClient, key: int) -> tuple[int, str] | None:
    if (raw := client.call(key=key, command=_CMD_READ_KEYINFO)) is None:
        return None
    size, data_type = struct.unpack_from("<II", raw, _OFFSET_DATA_SIZE)
    return size, _four_cc(data_type)


def _scan_controller() -> list[SmcKey]:
    try:
        with _smc_connection() as client:
            return _walk(client)
    except OSError as err:
        _LOGGER.warning("apple_smc: cannot reach the controller (%s)", err)
        return []


def _walk(client: _UserClient) -> list[SmcKey]:
    count_key = key_code(_COUNT_KEY)
    if (info := _key_info(client, count_key)) is None:
        _LOGGER.warning("apple_smc: the controller does not report a key count")
        return []
    raw = client.call(key=count_key, command=_CMD_READ_BYTES, size=info[0])
    if raw is None:
        return []
    total = int.from_bytes(raw[_OFFSET_BYTES : _OFFSET_BYTES + info[0]], "big")

    keys: list[SmcKey] = []
    for index in range(total):
        if (raw := client.call(command=_CMD_READ_INDEX, index=index)) is None:
            continue
        key = _four_cc(struct.unpack_from("<I", raw, _OFFSET_KEY)[0])
        if (info := _key_info(client, key_code(key))) is None:
            continue
        size, data_type = info
        if (found := classify(key, data_type, size)) is None:
            continue
        # Plenty of keys are described but refuse to be read; leave those out
        if client.call(key=key_code(key), command=_CMD_READ_BYTES, size=size) is None:
            continue
        keys.append(found)
    _LOGGER.debug("apple_smc: %d of %d keys are readable", len(keys), total)
    return sorted(keys, key=lambda found: found.key)
