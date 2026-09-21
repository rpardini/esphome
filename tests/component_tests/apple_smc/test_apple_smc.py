"""Tests for the apple_smc component's compile-time discovery and presentation."""

from __future__ import annotations

from collections.abc import Callable, Generator
from pathlib import Path
import sys

import pytest

from esphome.components.apple_smc import (
    CONF_DISCOVERY,
    CONF_EXCLUDE,
    DISCOVERY_SCHEMA,
    UNKNOWN_ACCURACY_DECIMALS,
    _expand_discovery,
    _fill_sensor_defaults,
    entity_name,
    validate_key,
)
from esphome.components.apple_smc.smc_keys import ENV_SMC_KEYS, classify, scan_keys
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
    STATE_CLASS_MEASUREMENT,
    STATE_CLASS_NONE,
    UNIT_AMPERE,
    UNIT_CELSIUS,
    UNIT_REVOLUTIONS_PER_MINUTE,
    UNIT_VOLT,
    UNIT_WATT,
)
from esphome.core import TimePeriod

# key, type, size. Every type the component handles, plus the shapes it has to
# turn down, written the way a dump taken from a real Mac looks.
FAKE_KEYS = """\
TCMz\tflt \t4
TC0P\tsp78\t2
TR0Z\tioft\t8
F0Ac\tfpe2\t2
F0Tg\tflt \t4
F0Mx\tflt \t4
F0Dc\tflt \t4
F0CR\tui16\t2
F1Ac\tflt \t4
FNum\tui8 \t1
VD0R\tflt \t4
ID0R\tflt \t4
PDTR\tflt \t4
AC-S\tflag\t1
RPlt\tch8*\t8
MSXd\tch8*\t16
MSXD\tch8*\t16
ozzz\tflt \t4
ACBI\thex_\t8
RUID\tch8*\t37
TPDF\tflt \t0
#KEY\tui32\t4
"""


@pytest.fixture(autouse=True)
def on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    """The macOS check is a config-time policy; the rest is portable."""
    monkeypatch.setattr(sys, "platform", "darwin")


@pytest.fixture
def smc_keys_dump(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Point discovery at a key dump instead of the machine's controller."""
    dump = tmp_path / "smc_keys.tsv"
    dump.write_text(FAKE_KEYS)
    monkeypatch.setenv(ENV_SMC_KEYS, str(dump))
    yield dump


def _discover(**discovery: object) -> dict[str, list[dict]]:
    config = {CONF_DISCOVERY: DISCOVERY_SCHEMA(discovery)}
    return _expand_discovery(config)


@pytest.mark.parametrize("key", ["TCMz", "F0Ac", "AC-S", "#KEY"])
def test_key_is_accepted(key: str) -> None:
    assert validate_key(key) == key


@pytest.mark.parametrize("key", ["", "TC0", "TCMzz", "temp\t"])
def test_key_is_rejected(key: str) -> None:
    with pytest.raises(cv.Invalid):
        validate_key(key)


@pytest.mark.parametrize(
    ("data_type", "size", "is_text", "is_integer"),
    [
        ("flt ", 4, False, False),
        ("sp78", 2, False, False),
        ("fpe2", 2, False, False),
        ("ioft", 8, False, False),
        ("ui16", 2, False, True),
        ("si32", 4, False, True),
        ("flag", 1, False, True),
        ("ch8*", 8, True, False),
    ],
)
def test_classify_handles_every_supported_type(
    data_type: str, size: int, is_text: bool, is_integer: bool
) -> None:
    found = classify("TCMz", data_type, size)

    assert found is not None
    assert found.data_type == data_type.rstrip()
    assert found.is_text is is_text
    assert found.is_integer is is_integer


@pytest.mark.parametrize(
    ("key", "data_type", "size"),
    [
        # An opaque type there is no sensible way to publish
        ("ACBI", "hex_", 8),
        ("MSTf", "{jst", 8),
        # Longer than one read can carry, and empty
        ("RUID", "ch8*", 37),
        ("TPDF", "flt ", 0),
        # The controller's own metadata, and a key of the wrong shape
        ("#KEY", "ui32", 4),
        ("TC0", "flt ", 4),
    ],
)
def test_classify_turns_down_what_it_cannot_publish(
    key: str, data_type: str, size: int
) -> None:
    assert classify(key, data_type, size) is None


def test_scan_reads_a_dump(smc_keys_dump: Path) -> None:
    found = {key.key for key in scan_keys()}

    assert "TCMz" in found
    assert "RPlt" in found
    # The unpublishable shapes from the dump are gone
    assert found.isdisjoint({"ACBI", "RUID", "TPDF", "#KEY"})


def test_scan_without_a_dump_or_a_controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_SMC_KEYS, str(tmp_path / "missing.tsv"))

    assert scan_keys() == []


@pytest.mark.parametrize(
    ("key", "unit", "device_class", "decimals"),
    [
        ("TCMz", UNIT_CELSIUS, DEVICE_CLASS_TEMPERATURE, 1),
        ("VD0R", UNIT_VOLT, DEVICE_CLASS_VOLTAGE, 3),
        ("ID0R", UNIT_AMPERE, DEVICE_CLASS_CURRENT, 3),
        ("PDTR", UNIT_WATT, DEVICE_CLASS_POWER, 2),
    ],
)
def test_the_first_letter_drives_the_presentation(
    key: str, unit: str, device_class: str, decimals: int
) -> None:
    config = _fill_sensor_defaults({CONF_KEY: key})

    assert config[CONF_UNIT_OF_MEASUREMENT] == unit
    assert config[CONF_DEVICE_CLASS] == device_class
    assert config[CONF_ACCURACY_DECIMALS] == decimals
    assert config[CONF_STATE_CLASS] == STATE_CLASS_MEASUREMENT
    assert CONF_ENTITY_CATEGORY not in config


@pytest.mark.parametrize("key", ["F0Ac", "F0Tg"])
def test_fan_speeds_are_rpm(key: str) -> None:
    config = _fill_sensor_defaults({CONF_KEY: key})

    assert config[CONF_UNIT_OF_MEASUREMENT] == UNIT_REVOLUTIONS_PER_MINUTE
    assert config[CONF_ICON] == ICON_FAN
    assert config[CONF_STATE_CLASS] == STATE_CLASS_MEASUREMENT
    assert CONF_DEVICE_CLASS not in config


@pytest.mark.parametrize("key", ["F0Mn", "F0Mx"])
def test_fan_limits_are_not_readings(key: str) -> None:
    """F0Mx is what the fan can do, not what it is doing."""
    config = _fill_sensor_defaults({CONF_KEY: key})

    assert config[CONF_UNIT_OF_MEASUREMENT] == UNIT_REVOLUTIONS_PER_MINUTE
    assert config[CONF_STATE_CLASS] == STATE_CLASS_NONE
    assert config[CONF_ENTITY_CATEGORY] == ENTITY_CATEGORY_DIAGNOSTIC


@pytest.mark.parametrize("key", ["F0Dc", "FNum", "ozzz"])
def test_an_unrecognised_key_is_left_bare(key: str) -> None:
    config = _fill_sensor_defaults({CONF_KEY: key})

    assert CONF_UNIT_OF_MEASUREMENT not in config
    assert CONF_DEVICE_CLASS not in config
    assert config[CONF_ACCURACY_DECIMALS] == UNKNOWN_ACCURACY_DECIMALS
    assert config[CONF_ENTITY_CATEGORY] == ENTITY_CATEGORY_DIAGNOSTIC


def test_explicit_values_win_over_the_derived_ones() -> None:
    config = _fill_sensor_defaults(
        {
            CONF_KEY: "TCMz",
            CONF_UNIT_OF_MEASUREMENT: "K",
            CONF_ACCURACY_DECIMALS: 4,
        }
    )

    assert config[CONF_UNIT_OF_MEASUREMENT] == "K"
    assert config[CONF_ACCURACY_DECIMALS] == 4


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        # The keys count fans from zero, the names count from one
        ("F0Ac", "Fan 1 Speed"),
        ("F1Ac", "Fan 2 Speed"),
        ("F0Mx", "Fan 1 Maximum Speed"),
        ("VD0R", "DC In Voltage"),
        ("TCMz", "SMC TCMz"),
    ],
)
def test_discovered_entity_names(key: str, expected: str) -> None:
    assert entity_name(key) == expected


def test_discovery_needs_an_include_list() -> None:
    with pytest.raises(cv.Invalid, match="include"):
        DISCOVERY_SCHEMA(None)
    with pytest.raises(cv.Invalid, match="include"):
        DISCOVERY_SCHEMA({CONF_EXCLUDE: ["T*"]})


def test_discovery_splits_sensors_and_text_sensors(smc_keys_dump: Path) -> None:
    config = _discover(include=["*"])

    assert {conf[CONF_KEY] for conf in config[CONF_TEXT_SENSORS]} == {
        "RPlt",
        "MSXd",
        "MSXD",
    }
    assert "TCMz" in {conf[CONF_KEY] for conf in config[CONF_SENSORS]}


def test_discovery_include_and_exclude(smc_keys_dump: Path) -> None:
    config = _discover(include=["F0*", "V*"], exclude=["F0C*"])

    assert {conf[CONF_KEY] for conf in config[CONF_SENSORS]} == {
        "F0Ac",
        "F0Dc",
        "F0Mx",
        "F0Tg",
        "VD0R",
    }


def test_discovery_exclude_beats_include(smc_keys_dump: Path) -> None:
    config = _discover(include=["*"], exclude=["*"])

    assert config[CONF_SENSORS] == []
    assert config[CONF_TEXT_SENSORS] == []


def test_discovery_gives_colliding_keys_distinct_ids(smc_keys_dump: Path) -> None:
    """Keys MSXd and MSXD differ only in case, which an ID cannot keep."""
    config = _discover(include=["MSX*"])

    assert [conf[CONF_ID] for conf in config[CONF_TEXT_SENSORS]] == [
        "apple_smc_msxd",
        "apple_smc_msxd_2",
    ]
    assert [conf[CONF_NAME] for conf in config[CONF_TEXT_SENSORS]] == [
        "SMC MSXD",
        "SMC MSXd 2",
    ]


def test_discovery_passes_on_the_update_interval(smc_keys_dump: Path) -> None:
    config = _discover(
        include=["TCMz"], update_interval="15s", disabled_by_default=True
    )

    assert config[CONF_SENSORS][0][CONF_UPDATE_INTERVAL] == TimePeriod(
        milliseconds=15000
    )
    assert config[CONF_SENSORS][0][CONF_DISABLED_BY_DEFAULT] is True


def test_discovery_keeps_whole_numbers_whole(smc_keys_dump: Path) -> None:
    """F0CR is an integer per the controller, so it needs no decimals."""
    config = _discover(include=["F0CR", "F0Dc"])

    decimals = {
        conf[CONF_KEY]: conf.get(CONF_ACCURACY_DECIMALS)
        for conf in config[CONF_SENSORS]
    }
    assert decimals == {"F0CR": 0, "F0Dc": None}


def test_generated_code(
    generate_main: Callable[[str | Path], str], smc_keys_dump: Path
) -> None:
    """Hand-picked and discovered entities both reach the generated C++."""
    main_cpp = generate_main("tests/component_tests/apple_smc/test_apple_smc.yaml")

    # Hand-picked
    assert 'AppleSmcSensor("TC0P")' in main_cpp
    assert 'AppleSmcSensor("VD0R")' in main_cpp
    assert 'AppleSmcTextSensor("zSEm")' in main_cpp
    # Discovered from the dump
    assert 'AppleSmcSensor("TCMz")' in main_cpp
    assert 'AppleSmcSensor("F0Ac")' in main_cpp
    assert 'AppleSmcTextSensor("RPlt")' in main_cpp
