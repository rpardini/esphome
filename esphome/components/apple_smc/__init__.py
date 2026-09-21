"""Read Apple System Management Controller sensors on the host platform."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
import logging
import re
import sys

import esphome.codegen as cg

# Aliased: importing apple_smc.sensor / apple_smc.text_sensor would otherwise
# shadow these
from esphome.components import (
    sensor as sensor_component,
    text_sensor as text_sensor_component,
)
from esphome.config_helpers import filter_source_files_from_platform
import esphome.config_validation as cv
from esphome.const import (
    CONF_ACCURACY_DECIMALS,
    CONF_DEVICE_CLASS,
    CONF_DISABLED_BY_DEFAULT,
    CONF_ENTITY_CATEGORY,
    CONF_ICON,
    CONF_ID,
    CONF_KEY,
    CONF_NAME,
    CONF_SENSORS,
    CONF_STATE_CLASS,
    CONF_TEXT_SENSORS,
    CONF_UNIT_OF_MEASUREMENT,
    CONF_UPDATE_INTERVAL,
    DEVICE_CLASS_CURRENT,
    DEVICE_CLASS_POWER,
    DEVICE_CLASS_TEMPERATURE,
    DEVICE_CLASS_VOLTAGE,
    ENTITY_CATEGORY_DIAGNOSTIC,
    ICON_FAN,
    PLATFORM_HOST,
    STATE_CLASS_MEASUREMENT,
    STATE_CLASS_NONE,
    UNIT_AMPERE,
    UNIT_CELSIUS,
    UNIT_REVOLUTIONS_PER_MINUTE,
    UNIT_VOLT,
    UNIT_WATT,
    PlatformFramework,
)
from esphome.types import ConfigType

from . import smc_keys

CODEOWNERS = ["@rpardini"]
AUTO_LOAD = ["sensor", "text_sensor"]

_LOGGER = logging.getLogger(__name__)

apple_smc_ns = cg.esphome_ns.namespace("apple_smc")
AppleSmcSensor = apple_smc_ns.class_(
    "AppleSmcSensor", sensor_component.Sensor, cg.PollingComponent
)
AppleSmcTextSensor = apple_smc_ns.class_(
    "AppleSmcTextSensor", text_sensor_component.TextSensor, cg.PollingComponent
)

CONF_DISCOVERY = "discovery"
CONF_EXCLUDE = "exclude"
CONF_INCLUDE = "include"


@dataclass(frozen=True)
class KeyClass:
    """How one family of keys is presented."""

    unit: str | None = None
    device_class: str | None = None
    accuracy_decimals: int = 0
    icon: str | None = None
    # A static limit rather than a reading, so not something to graph
    is_limit: bool = False


# Values come out of the controller already in SI units, so there is nothing to
# scale. The first letter of a key says what it measures, following the
# controller's own naming.
PREFIX_CLASSES: dict[str, KeyClass] = {
    "T": KeyClass(UNIT_CELSIUS, DEVICE_CLASS_TEMPERATURE, 1),
    "V": KeyClass(UNIT_VOLT, DEVICE_CLASS_VOLTAGE, 3),
    "I": KeyClass(UNIT_AMPERE, DEVICE_CLASS_CURRENT, 3),
    "P": KeyClass(UNIT_WATT, DEVICE_CLASS_POWER, 2),
}

_FAN_SPEED_CLASS = KeyClass(UNIT_REVOLUTIONS_PER_MINUTE, None, 0, ICON_FAN)
_FAN_LIMIT_CLASS = KeyClass(UNIT_REVOLUTIONS_PER_MINUTE, None, 0, ICON_FAN, True)

# F0Ac and friends are fan speeds; the rest of the F keys (F0Dc, FNum, ...)
# measure other things and are left without a unit
_FAN_SPEED_ATTRIBUTES = {"Ac": "Speed", "Tg": "Target Speed"}
_FAN_LIMIT_ATTRIBUTES = {"Mn": "Minimum Speed", "Mx": "Maximum Speed"}
_FAN_KEY_RE = re.compile(r"^F(\d)(Ac|Tg|Mn|Mx)$")

# Names Apple does not document but that every Mac tool agrees on. Anything
# missing here is named after its key, which is honest and still searchable.
WELL_KNOWN_NAMES: dict[str, str] = {
    "FNum": "Fan Count",
    "ID0R": "DC In Current",
    "PD0R": "DC In Power",
    "PDTR": "DC In Total Power",
    "PSTR": "System Total Power",
    "RPlt": "SMC Platform",
    "TA0P": "Ambient Temperature",
    "TB0T": "Battery Temperature",
    "TC0D": "CPU Die Temperature",
    "TC0P": "CPU Proximity Temperature",
    "TG0D": "GPU Die Temperature",
    "TG0P": "GPU Proximity Temperature",
    "TW0P": "Wireless Temperature",
    "VD0R": "DC In Voltage",
}

# Nothing is known about most of the controller's keys beyond their number.
# They are still worth offering, but they belong out of the way, and most of
# them are floating point so they need room for a fraction.
UNKNOWN_ACCURACY_DECIMALS = 2

_NON_ID_RE = re.compile(r"[^a-z0-9]+")

_INCLUDE_HINT = (
    "'discovery' needs an 'include' list of key patterns. The controller has "
    "well over a thousand keys, so there is no useful default; try "
    'include: ["T*", "F0*", "P*"], or ["*"] for every key it offers.'
)


def key_class(key: str) -> KeyClass | None:
    """Return how a key is presented, or None when nothing is known about it."""
    if (match := _FAN_KEY_RE.match(key)) is not None:
        attribute = match.group(2)
        if attribute in _FAN_SPEED_ATTRIBUTES:
            return _FAN_SPEED_CLASS
        return _FAN_LIMIT_CLASS
    return PREFIX_CLASSES.get(key[0]) if key else None


def entity_name(key: str) -> str:
    """A readable name for a key, for discovery to use."""
    if (name := WELL_KNOWN_NAMES.get(key)) is not None:
        return name
    if (match := _FAN_KEY_RE.match(key)) is not None:
        attribute = match.group(2)
        title = _FAN_SPEED_ATTRIBUTES.get(attribute) or _FAN_LIMIT_ATTRIBUTES[attribute]
        # The keys count fans from zero, everything a user sees counts from one
        return f"Fan {int(match.group(1)) + 1} {title}"
    return f"SMC {key}"


def validate_key(value: str) -> str:
    value = cv.string_strict(value)
    if not smc_keys.is_valid_key(value):
        raise cv.Invalid(
            f"'{value}' is not a controller key; expected exactly four printable "
            "ASCII characters, as found in the key list, e.g. 'TCMz' or 'F0Ac'"
        )
    return value


def validate_macos_host(config: ConfigType) -> ConfigType:
    """Reject other hosts: the controller is reached through macOS only."""
    if sys.platform != "darwin":
        raise cv.Invalid(
            "apple_smc is only supported on macOS for the host platform. "
            f"Current platform: {sys.platform}"
        )
    return config


def _empty_if_none(value: ConfigType | None) -> ConfigType:
    return {} if value is None else value


def _fill_sensor_defaults(config: ConfigType) -> ConfigType:
    """Derive unit, device class and friends from the key.

    Runs before schema validation so the injected values go through the normal
    validators, and so anything the user set explicitly wins.
    """
    if not isinstance(config, dict):
        return config
    raw_key = config.get(CONF_KEY)
    if not isinstance(raw_key, str):
        return config
    if (info := key_class(raw_key)) is None:
        config.setdefault(CONF_ACCURACY_DECIMALS, UNKNOWN_ACCURACY_DECIMALS)
        config.setdefault(CONF_STATE_CLASS, STATE_CLASS_MEASUREMENT)
        config.setdefault(CONF_ENTITY_CATEGORY, ENTITY_CATEGORY_DIAGNOSTIC)
        return config
    if info.unit is not None:
        config.setdefault(CONF_UNIT_OF_MEASUREMENT, info.unit)
    if info.device_class is not None:
        config.setdefault(CONF_DEVICE_CLASS, info.device_class)
    if info.icon is not None:
        config.setdefault(CONF_ICON, info.icon)
    config.setdefault(CONF_ACCURACY_DECIMALS, info.accuracy_decimals)
    if info.is_limit:
        config.setdefault(CONF_STATE_CLASS, STATE_CLASS_NONE)
        config.setdefault(CONF_ENTITY_CATEGORY, ENTITY_CATEGORY_DIAGNOSTIC)
    else:
        config.setdefault(CONF_STATE_CLASS, STATE_CLASS_MEASUREMENT)
    return config


APPLE_SMC_SENSOR_SCHEMA = cv.All(
    cv.only_on(PLATFORM_HOST),
    validate_macos_host,
    _fill_sensor_defaults,
    sensor_component.sensor_schema(AppleSmcSensor)
    .extend({cv.Required(CONF_KEY): validate_key})
    .extend(cv.polling_component_schema("60s")),
)

APPLE_SMC_TEXT_SENSOR_SCHEMA = cv.All(
    cv.only_on(PLATFORM_HOST),
    validate_macos_host,
    text_sensor_component.text_sensor_schema(
        AppleSmcTextSensor, entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    )
    .extend({cv.Required(CONF_KEY): validate_key})
    .extend(cv.polling_component_schema("60s")),
)


def _require_include(config: ConfigType) -> ConfigType:
    if not isinstance(config, dict) or CONF_INCLUDE not in config:
        raise cv.Invalid(_INCLUDE_HINT)
    return config


DISCOVERY_SCHEMA = cv.All(
    _require_include,
    cv.Schema(
        {
            cv.Required(CONF_INCLUDE): cv.ensure_list(cv.string_strict),
            cv.Optional(CONF_EXCLUDE, default=[]): cv.ensure_list(cv.string_strict),
            cv.Optional(CONF_UPDATE_INTERVAL, default="60s"): cv.update_interval,
            cv.Optional(CONF_DISABLED_BY_DEFAULT, default=False): cv.boolean,
        }
    ),
)


def _matches(key: str, include: list[str], exclude: list[str]) -> bool:
    if not any(fnmatch(key, pattern) for pattern in include):
        return False
    return not any(fnmatch(key, pattern) for pattern in exclude)


def _slug(value: str) -> str:
    return _NON_ID_RE.sub("_", value.lower()).strip("_")


def _unique(value: str, used: set[str], separator: str) -> str:
    """Add a suffix until value is unused, ignoring case.

    Keys are case sensitive and MSXd and MSXD are different readings, but an
    entity name has to stay distinct once it is folded into an object ID, so
    the two cannot both be called after their own key.
    """
    candidate = value
    suffix = 1
    while candidate.casefold() in used:
        suffix += 1
        candidate = f"{value}{separator}{suffix}"
    used.add(candidate.casefold())
    return candidate


def _expand_discovery(config: ConfigType) -> ConfigType:
    if not isinstance(config, dict):
        return config
    if (discovery := config.get(CONF_DISCOVERY)) is None:
        return config

    include = discovery[CONF_INCLUDE]
    exclude = discovery[CONF_EXCLUDE]
    sensors: list[ConfigType] = []
    text_sensors: list[ConfigType] = []
    used_ids: set[str] = set()
    used_names: set[str] = set()
    for found in smc_keys.scan_keys():
        if not _matches(found.key, include, exclude):
            continue
        item: ConfigType = {
            CONF_ID: _unique(f"apple_smc_{_slug(found.key)}", used_ids, "_"),
            CONF_NAME: _unique(entity_name(found.key), used_names, " "),
            CONF_KEY: found.key,
            CONF_UPDATE_INTERVAL: discovery[CONF_UPDATE_INTERVAL],
            CONF_DISABLED_BY_DEFAULT: discovery[CONF_DISABLED_BY_DEFAULT],
        }
        # Discovery knows what the controller reports a key as, so an
        # unrecognised whole number does not have to leave room for a fraction
        if found.is_integer and key_class(found.key) is None:
            item[CONF_ACCURACY_DECIMALS] = 0
        (text_sensors if found.is_text else sensors).append(item)
        _LOGGER.info(
            "apple_smc: discovered %s (%s) as '%s'",
            found.key,
            found.data_type,
            item[CONF_NAME],
        )

    config[CONF_SENSORS] = [*config.get(CONF_SENSORS, []), *sensors]
    config[CONF_TEXT_SENSORS] = [*config.get(CONF_TEXT_SENSORS, []), *text_sensors]
    return config


CONFIG_SCHEMA = cv.All(
    _empty_if_none,
    cv.only_on(PLATFORM_HOST),
    validate_macos_host,
    cv.Schema({cv.Optional(CONF_DISCOVERY): DISCOVERY_SCHEMA}, extra=cv.ALLOW_EXTRA),
    _expand_discovery,
    cv.Schema(
        {
            cv.Optional(CONF_DISCOVERY): DISCOVERY_SCHEMA,
            cv.Optional(CONF_SENSORS, default=[]): cv.ensure_list(
                APPLE_SMC_SENSOR_SCHEMA
            ),
            cv.Optional(CONF_TEXT_SENSORS, default=[]): cv.ensure_list(
                APPLE_SMC_TEXT_SENSOR_SCHEMA
            ),
        }
    ),
)


def _request_build() -> None:
    """Switch the C++ on and link the frameworks it calls into.

    Called for every entity rather than from to_code, because the entity
    platforms can be used without an apple_smc block of their own.
    """
    cg.add_define("USE_APPLE_SMC")
    cg.add_build_flag("-Wl,-framework,IOKit")
    cg.add_build_flag("-Wl,-framework,CoreFoundation")


async def new_apple_smc_sensor(config: ConfigType) -> cg.Pvariable:
    _request_build()
    var = await sensor_component.new_sensor(config, config[CONF_KEY])
    await cg.register_component(var, config)
    return var


async def new_apple_smc_text_sensor(config: ConfigType) -> cg.Pvariable:
    _request_build()
    var = await text_sensor_component.new_text_sensor(config, config[CONF_KEY])
    await cg.register_component(var, config)
    return var


async def to_code(config: ConfigType) -> None:
    for conf in config[CONF_SENSORS]:
        await new_apple_smc_sensor(conf)
    for conf in config[CONF_TEXT_SENSORS]:
        await new_apple_smc_text_sensor(conf)


FILTER_SOURCE_FILES = filter_source_files_from_platform(
    {
        "apple_smc.cpp": {PlatformFramework.HOST_NATIVE},
        "apple_smc_sensor.cpp": {PlatformFramework.HOST_NATIVE},
        "apple_smc_text_sensor.cpp": {PlatformFramework.HOST_NATIVE},
    }
)
