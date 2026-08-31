"""Read Linux hwmon (lm-sensors) readings on the host platform."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
import logging
import os
from pathlib import Path
import re
import sys

import esphome.codegen as cg

# Aliased: importing hwmon.sensor / hwmon.text_sensor would otherwise shadow these
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
    CONF_NAME,
    CONF_SENSORS,
    CONF_STATE_CLASS,
    CONF_TEXT_SENSORS,
    CONF_UNIT_OF_MEASUREMENT,
    CONF_UPDATE_INTERVAL,
    DEVICE_CLASS_CURRENT,
    DEVICE_CLASS_ENERGY,
    DEVICE_CLASS_FREQUENCY,
    DEVICE_CLASS_HUMIDITY,
    DEVICE_CLASS_POWER,
    DEVICE_CLASS_TEMPERATURE,
    DEVICE_CLASS_VOLTAGE,
    ENTITY_CATEGORY_DIAGNOSTIC,
    ICON_FAN,
    PLATFORM_HOST,
    STATE_CLASS_MEASUREMENT,
    STATE_CLASS_NONE,
    STATE_CLASS_TOTAL_INCREASING,
    UNIT_AMPERE,
    UNIT_CELSIUS,
    UNIT_HERTZ,
    UNIT_PERCENT,
    UNIT_REVOLUTIONS_PER_MINUTE,
    UNIT_VOLT,
    UNIT_WATT,
    UNIT_WATT_HOURS,
    PlatformFramework,
)
from esphome.types import ConfigType

CODEOWNERS = ["@rpardini"]
AUTO_LOAD = ["sensor", "text_sensor"]

_LOGGER = logging.getLogger(__name__)

hwmon_ns = cg.esphome_ns.namespace("hwmon")
HwmonSensor = hwmon_ns.class_(
    "HwmonSensor", sensor_component.Sensor, cg.PollingComponent
)
HwmonTextSensor = hwmon_ns.class_(
    "HwmonTextSensor", text_sensor_component.TextSensor, cg.PollingComponent
)

CONF_ATTRIBUTE = "attribute"
CONF_CHIP = "chip"
CONF_DISCOVERY = "discovery"
CONF_EXCLUDE = "exclude"
CONF_INCLUDE = "include"
CONF_INCLUDE_THRESHOLDS = "include_thresholds"
CONF_INPUT = "input"

# Overridable so a build host can point at a copy of another machine's sysfs tree.
ENV_HWMON_ROOT = "ESPHOME_HWMON_ROOT"
DEFAULT_HWMON_ROOT = "/sys/class/hwmon"

ATTRIBUTE_READING = "input"
THRESHOLD_ATTRIBUTES = ("min", "max", "crit")


@dataclass(frozen=True)
class InputType:
    """How one hwmon reading type is scaled and presented."""

    divisor: float
    unit: str | None = None
    device_class: str | None = None
    accuracy_decimals: int = 0
    icon: str | None = None
    state_class: str = STATE_CLASS_MEASUREMENT


# Divisors follow the kernel hwmon sysfs interface: temperatures and voltages are
# in milli-units, power and energy in micro-units, fan speeds already in RPM.
INPUT_TYPES: dict[str, InputType] = {
    "temp": InputType(1000, UNIT_CELSIUS, DEVICE_CLASS_TEMPERATURE, 1),
    "fan": InputType(1, UNIT_REVOLUTIONS_PER_MINUTE, None, 0, ICON_FAN),
    "pwm": InputType(1, None, None, 0, ICON_FAN),
    "in": InputType(1000, UNIT_VOLT, DEVICE_CLASS_VOLTAGE, 3),
    "curr": InputType(1000, UNIT_AMPERE, DEVICE_CLASS_CURRENT, 3),
    "power": InputType(1000000, UNIT_WATT, DEVICE_CLASS_POWER, 2),
    "energy": InputType(
        3.6e9,
        UNIT_WATT_HOURS,
        DEVICE_CLASS_ENERGY,
        3,
        state_class=STATE_CLASS_TOTAL_INCREASING,
    ),
    "humidity": InputType(1000, UNIT_PERCENT, DEVICE_CLASS_HUMIDITY, 1),
    "freq": InputType(1, UNIT_HERTZ, DEVICE_CLASS_FREQUENCY, 0),
}

_TYPES = "|".join(INPUT_TYPES)
_INPUT_RE = re.compile(rf"^({_TYPES})(\d+)$")
_ATTRIBUTE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_CHIP_DIR_RE = re.compile(r"^hwmon(\d+)$")
_NON_ID_RE = re.compile(r"[^a-z0-9]+")

# Discovery only offers files it knows how to present. Anything else (and any
# threshold, unless asked for) has to be named explicitly in the YAML.
_READING_FILE_RE = re.compile(rf"^({_TYPES})(\d+)_({ATTRIBUTE_READING})$")
_THRESHOLD_FILE_RE = re.compile(
    rf"^({_TYPES})(\d+)_({'|'.join(THRESHOLD_ATTRIBUTES)})$"
)
_PWM_FILE_RE = re.compile(r"^(pwm)(\d+)$")
_TEXT_FILE_RE = re.compile(rf"^({_TYPES})(\d+)_(label|enable)$")

_ATTRIBUTE_TITLES = {
    "min": "Min",
    "max": "Max",
    "crit": "Critical",
    "label": "Label",
    "enable": "Mode",
}


def validate_chip(value: str) -> str:
    value = cv.string_strict(value)
    if not value or "/" in value:
        raise cv.Invalid(
            "Chip must be a hwmon chip name without '/', as found in "
            "/sys/class/hwmon/*/name (e.g. 'coretemp')"
        )
    return value


def validate_input(value: str) -> str:
    value = cv.string_strict(value)
    if _INPUT_RE.match(value) is None:
        raise cv.Invalid(
            f"'{value}' is not a hwmon input; expected one of "
            f"{', '.join(INPUT_TYPES)} followed by a number, e.g. 'temp1' or 'fan1'"
        )
    return value


def validate_attribute(value: str) -> str:
    value = cv.string_strict(value)
    if _ATTRIBUTE_RE.match(value) is None:
        raise cv.Invalid(
            f"'{value}' is not a hwmon attribute name; expected a plain sysfs file "
            "name such as 'input', 'max', 'label' or 'enable'"
        )
    return value


def validate_linux_host(config: ConfigType) -> ConfigType:
    """Reject non-Linux hosts: hwmon is a Linux kernel interface."""
    if not sys.platform.lower().startswith("linux"):
        raise cv.Invalid(
            "hwmon is only supported on Linux for the host platform. "
            f"Current platform: {sys.platform}"
        )
    return config


def input_type(input_name: str) -> InputType:
    """Return the scaling and presentation rules for an input such as 'temp1'."""
    return INPUT_TYPES[_INPUT_RE.match(input_name).group(1)]


def multiplier(config: ConfigType) -> float:
    """Factor turning the raw sysfs integer into the published value."""
    return 1.0 / input_type(config[CONF_INPUT]).divisor


def hwmon_filename(config: ConfigType) -> str:
    """Build the sysfs file name an entity reads from."""
    input_name = config.get(CONF_INPUT)
    attribute = config.get(CONF_ATTRIBUTE)
    if input_name is None:
        return attribute
    if attribute is None:
        # pwmN is the reading itself; every other type reads <input>_input
        return input_name if input_name.startswith("pwm") else f"{input_name}_input"
    return f"{input_name}_{attribute}"


def _empty_if_none(value: ConfigType | None) -> ConfigType:
    return {} if value is None else value


def _fill_sensor_defaults(config: ConfigType) -> ConfigType:
    """Derive unit, device class and friends from the input type.

    Runs before schema validation so the injected values go through the normal
    validators, and so anything the user set explicitly wins.
    """
    if not isinstance(config, dict):
        return config
    raw_input = config.get(CONF_INPUT)
    if not isinstance(raw_input, str) or (match := _INPUT_RE.match(raw_input)) is None:
        return config
    info = INPUT_TYPES[match.group(1)]
    if info.unit is not None:
        config.setdefault(CONF_UNIT_OF_MEASUREMENT, info.unit)
    if info.device_class is not None:
        config.setdefault(CONF_DEVICE_CLASS, info.device_class)
    if info.icon is not None:
        config.setdefault(CONF_ICON, info.icon)
    config.setdefault(CONF_ACCURACY_DECIMALS, info.accuracy_decimals)
    attribute = config.get(CONF_ATTRIBUTE)
    if attribute in (None, ATTRIBUTE_READING):
        config.setdefault(CONF_STATE_CLASS, info.state_class)
    else:
        # Thresholds such as temp1_crit are static limits, not measurements
        config.setdefault(CONF_STATE_CLASS, STATE_CLASS_NONE)
        config.setdefault(CONF_ENTITY_CATEGORY, ENTITY_CATEGORY_DIAGNOSTIC)
    return config


HWMON_SENSOR_SCHEMA = cv.All(
    cv.only_on(PLATFORM_HOST),
    validate_linux_host,
    _fill_sensor_defaults,
    sensor_component.sensor_schema(HwmonSensor)
    .extend(
        {
            cv.Required(CONF_CHIP): validate_chip,
            cv.Required(CONF_INPUT): validate_input,
            cv.Optional(CONF_ATTRIBUTE): validate_attribute,
        }
    )
    .extend(cv.polling_component_schema("60s")),
)

HWMON_TEXT_SENSOR_SCHEMA = cv.All(
    cv.only_on(PLATFORM_HOST),
    validate_linux_host,
    text_sensor_component.text_sensor_schema(
        HwmonTextSensor, entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    )
    .extend(
        {
            cv.Required(CONF_CHIP): validate_chip,
            cv.Optional(CONF_INPUT): validate_input,
            cv.Required(CONF_ATTRIBUTE): validate_attribute,
        }
    )
    .extend(cv.polling_component_schema("60s")),
)

DISCOVERY_SCHEMA = cv.All(
    _empty_if_none,
    cv.Schema(
        {
            cv.Optional(CONF_INCLUDE, default=["*"]): cv.ensure_list(cv.string_strict),
            cv.Optional(CONF_EXCLUDE, default=[]): cv.ensure_list(cv.string_strict),
            cv.Optional(CONF_INCLUDE_THRESHOLDS, default=False): cv.boolean,
            cv.Optional(CONF_UPDATE_INTERVAL, default="60s"): cv.update_interval,
            cv.Optional(CONF_DISABLED_BY_DEFAULT, default=False): cv.boolean,
        }
    ),
)


@dataclass(frozen=True)
class DiscoveredEntry:
    """One hwmon sysfs file that discovery decided to turn into an entity."""

    chip: str
    filename: str
    input_name: str | None
    attribute: str | None
    label: str | None
    is_text: bool


def hwmon_root() -> Path:
    return Path(os.environ.get(ENV_HWMON_ROOT) or DEFAULT_HWMON_ROOT)


def _read_sysfs_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _list_files(directory: Path) -> list[str]:
    try:
        return sorted(entry.name for entry in directory.iterdir() if entry.is_file())
    except OSError:
        return []


def _classify(
    filename: str, include_thresholds: bool
) -> tuple[str, str | None, bool] | None:
    """Split a candidate file name into (input, attribute, is_text).

    Returns None instead when discovery does not handle the file.
    """
    if (match := _PWM_FILE_RE.match(filename)) is not None:
        return f"{match.group(1)}{match.group(2)}", None, False
    if (match := _READING_FILE_RE.match(filename)) is not None:
        return f"{match.group(1)}{match.group(2)}", match.group(3), False
    if (match := _TEXT_FILE_RE.match(filename)) is not None:
        return f"{match.group(1)}{match.group(2)}", match.group(3), True
    if include_thresholds and (match := _THRESHOLD_FILE_RE.match(filename)) is not None:
        return f"{match.group(1)}{match.group(2)}", match.group(3), False
    return None


def scan_hwmon(include_thresholds: bool = False) -> list[DiscoveredEntry]:
    """Walk the hwmon tree of the machine running this build."""
    root = hwmon_root()
    try:
        chip_dirs = [
            (int(match.group(1)), path)
            for path in root.iterdir()
            if (match := _CHIP_DIR_RE.match(path.name)) is not None
        ]
    except OSError as err:
        _LOGGER.warning(
            "hwmon: cannot read %s (%s); discovery found no sensors", root, err
        )
        return []

    entries: list[DiscoveredEntry] = []
    for _, chip_dir in sorted(chip_dirs):
        chip = _read_sysfs_text(chip_dir / "name")
        if not chip:
            continue
        attr_dir = chip_dir
        filenames = _list_files(attr_dir)
        if not any(_classify(name, True) for name in filenames):
            # Older drivers keep the attributes one level down
            legacy_dir = chip_dir / "device"
            if legacy_dir.is_dir():
                attr_dir = legacy_dir
                filenames = _list_files(attr_dir)
        labels: dict[str, str | None] = {}
        for filename in filenames:
            if (parsed := _classify(filename, include_thresholds)) is None:
                continue
            input_name, attribute, is_text = parsed
            if input_name not in labels:
                labels[input_name] = _read_sysfs_text(attr_dir / f"{input_name}_label")
            entries.append(
                DiscoveredEntry(
                    chip, filename, input_name, attribute, labels[input_name], is_text
                )
            )
    if not entries:
        _LOGGER.warning("hwmon: discovery found no sensors under %s", root)
    return entries


def _matches(key: str, include: list[str], exclude: list[str]) -> bool:
    if not any(fnmatch(key, pattern) for pattern in include):
        return False
    return not any(fnmatch(key, pattern) for pattern in exclude)


def _entity_name(entry: DiscoveredEntry) -> str:
    # A *_label file names its own reading, so it reads better keyed by input
    base = entry.input_name if entry.attribute == "label" else entry.label
    base = base or entry.input_name or entry.filename
    if entry.attribute in (None, ATTRIBUTE_READING):
        return f"{entry.chip} {base}"
    title = _ATTRIBUTE_TITLES.get(entry.attribute, entry.attribute)
    return f"{entry.chip} {base} {title}"


def _slug(value: str) -> str:
    return _NON_ID_RE.sub("_", value.lower()).strip("_")


def _unique(value: str, used: set[str], separator: str) -> str:
    candidate = value
    suffix = 1
    while candidate in used:
        suffix += 1
        candidate = f"{value}{separator}{suffix}"
    used.add(candidate)
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
    for entry in scan_hwmon(discovery[CONF_INCLUDE_THRESHOLDS]):
        key = f"{entry.chip}/{entry.filename}"
        if not _matches(key, include, exclude):
            continue
        item: ConfigType = {
            CONF_ID: _unique(
                f"hwmon_{_slug(entry.chip)}_{_slug(entry.filename)}", used_ids, "_"
            ),
            CONF_NAME: _unique(_entity_name(entry), used_names, " "),
            CONF_CHIP: entry.chip,
            CONF_UPDATE_INTERVAL: discovery[CONF_UPDATE_INTERVAL],
            CONF_DISABLED_BY_DEFAULT: discovery[CONF_DISABLED_BY_DEFAULT],
        }
        if entry.input_name is not None:
            item[CONF_INPUT] = entry.input_name
        if entry.attribute is not None:
            item[CONF_ATTRIBUTE] = entry.attribute
        (text_sensors if entry.is_text else sensors).append(item)
        _LOGGER.info("hwmon: discovered %s as '%s'", key, item[CONF_NAME])

    config[CONF_SENSORS] = [*config.get(CONF_SENSORS, []), *sensors]
    config[CONF_TEXT_SENSORS] = [*config.get(CONF_TEXT_SENSORS, []), *text_sensors]
    return config


CONFIG_SCHEMA = cv.All(
    _empty_if_none,
    cv.only_on(PLATFORM_HOST),
    validate_linux_host,
    cv.Schema({cv.Optional(CONF_DISCOVERY): DISCOVERY_SCHEMA}, extra=cv.ALLOW_EXTRA),
    _expand_discovery,
    cv.Schema(
        {
            cv.Optional(CONF_DISCOVERY): DISCOVERY_SCHEMA,
            cv.Optional(CONF_SENSORS, default=[]): cv.ensure_list(HWMON_SENSOR_SCHEMA),
            cv.Optional(CONF_TEXT_SENSORS, default=[]): cv.ensure_list(
                HWMON_TEXT_SENSOR_SCHEMA
            ),
        }
    ),
)


async def new_hwmon_sensor(config: ConfigType) -> cg.Pvariable:
    var = await sensor_component.new_sensor(
        config, config[CONF_CHIP], hwmon_filename(config), multiplier(config)
    )
    await cg.register_component(var, config)
    return var


async def new_hwmon_text_sensor(config: ConfigType) -> cg.Pvariable:
    var = await text_sensor_component.new_text_sensor(
        config, config[CONF_CHIP], hwmon_filename(config)
    )
    await cg.register_component(var, config)
    return var


async def to_code(config: ConfigType) -> None:
    for conf in config[CONF_SENSORS]:
        await new_hwmon_sensor(conf)
    for conf in config[CONF_TEXT_SENSORS]:
        await new_hwmon_text_sensor(conf)


FILTER_SOURCE_FILES = filter_source_files_from_platform(
    {
        "hwmon.cpp": {PlatformFramework.HOST_NATIVE},
        "hwmon_sensor.cpp": {PlatformFramework.HOST_NATIVE},
        "hwmon_text_sensor.cpp": {PlatformFramework.HOST_NATIVE},
    }
)
