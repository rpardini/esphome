"""Tests for the hwmon component's compile-time discovery and scaling."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import sys

import pytest

from esphome.components.hwmon import (
    CONF_ATTRIBUTE,
    CONF_CHIP,
    CONF_DISCOVERY,
    CONF_INPUT,
    DISCOVERY_SCHEMA,
    ENV_HWMON_ROOT,
    _expand_discovery,
    _fill_sensor_defaults,
    hwmon_filename,
    multiplier,
    scan_hwmon,
)
from esphome.const import (
    CONF_ACCURACY_DECIMALS,
    CONF_DEVICE_CLASS,
    CONF_ENTITY_CATEGORY,
    CONF_ID,
    CONF_NAME,
    CONF_SENSORS,
    CONF_STATE_CLASS,
    CONF_TEXT_SENSORS,
    CONF_UNIT_OF_MEASUREMENT,
    CONF_UPDATE_INTERVAL,
    DEVICE_CLASS_TEMPERATURE,
    ENTITY_CATEGORY_DIAGNOSTIC,
    STATE_CLASS_MEASUREMENT,
    STATE_CLASS_NONE,
    UNIT_CELSIUS,
)
from esphome.core import TimePeriod

# chip name -> {attribute file: contents}
FAKE_TREE: dict[str, tuple[str, dict[str, str]]] = {
    "hwmon0": ("pwmfan", {"fan1_input": "234", "pwm1": "46", "pwm1_enable": "1"}),
    "hwmon1": ("npu_thermal", {"temp1_input": "42500", "temp1_max": "120000"}),
    "hwmon2": ("coretemp", {"temp1_input": "45000", "temp1_label": "Package id 0"}),
    "hwmon3": ("nvme", {"temp1_input": "38850"}),
    # Same chip name as hwmon2, and a two digit index to check numeric ordering
    "hwmon10": ("coretemp", {"temp1_input": "46000", "temp1_label": "Package id 0"}),
}


@pytest.fixture
def hwmon_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a fake /sys/class/hwmon tree and point discovery at it."""
    root = tmp_path / "hwmon"
    for directory, (chip, files) in FAKE_TREE.items():
        chip_dir = root / directory
        chip_dir.mkdir(parents=True)
        (chip_dir / "name").write_text(f"{chip}\n")
        for filename, contents in files.items():
            (chip_dir / filename).write_text(f"{contents}\n")
    monkeypatch.setenv(ENV_HWMON_ROOT, str(root))
    return root


def _discover(**discovery: object) -> dict[str, list[dict]]:
    config = {CONF_DISCOVERY: DISCOVERY_SCHEMA(discovery)}
    return _expand_discovery(config)


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({CONF_INPUT: "temp1"}, "temp1_input"),
        ({CONF_INPUT: "fan1"}, "fan1_input"),
        # pwmN is the reading itself, there is no pwmN_input
        ({CONF_INPUT: "pwm1"}, "pwm1"),
        ({CONF_INPUT: "temp1", CONF_ATTRIBUTE: "max"}, "temp1_max"),
        ({CONF_INPUT: "pwm1", CONF_ATTRIBUTE: "enable"}, "pwm1_enable"),
        ({CONF_ATTRIBUTE: "name"}, "name"),
    ],
)
def test_filename_rule(config: dict[str, str], expected: str) -> None:
    assert hwmon_filename(config) == expected


def test_scaling_multipliers() -> None:
    """Raw sysfs integers are milli/micro units and need scaling down."""
    assert 42500 * multiplier({CONF_INPUT: "temp1"}) == pytest.approx(42.5)
    assert 234 * multiplier({CONF_INPUT: "fan1"}) == pytest.approx(234)
    assert 46 * multiplier({CONF_INPUT: "pwm1"}) == pytest.approx(46)
    assert 12000 * multiplier({CONF_INPUT: "in0"}) == pytest.approx(12.0)
    assert 5500000 * multiplier({CONF_INPUT: "power1"}) == pytest.approx(5.5)


def test_type_defaults_for_a_reading() -> None:
    config = _fill_sensor_defaults({CONF_CHIP: "npu_thermal", CONF_INPUT: "temp1"})

    assert config[CONF_UNIT_OF_MEASUREMENT] == UNIT_CELSIUS
    assert config[CONF_DEVICE_CLASS] == DEVICE_CLASS_TEMPERATURE
    assert config[CONF_ACCURACY_DECIMALS] == 1
    assert config[CONF_STATE_CLASS] == STATE_CLASS_MEASUREMENT
    assert CONF_ENTITY_CATEGORY not in config


def test_type_defaults_for_a_threshold() -> None:
    """temp1_max is a static limit, not something to graph."""
    config = _fill_sensor_defaults(
        {CONF_CHIP: "npu_thermal", CONF_INPUT: "temp1", CONF_ATTRIBUTE: "max"}
    )

    assert config[CONF_UNIT_OF_MEASUREMENT] == UNIT_CELSIUS
    assert config[CONF_STATE_CLASS] == STATE_CLASS_NONE
    assert config[CONF_ENTITY_CATEGORY] == ENTITY_CATEGORY_DIAGNOSTIC


def test_explicit_values_win_over_type_defaults() -> None:
    config = _fill_sensor_defaults(
        {
            CONF_CHIP: "pwmfan",
            CONF_INPUT: "fan1",
            CONF_UNIT_OF_MEASUREMENT: "rpm",
            CONF_ACCURACY_DECIMALS: 2,
        }
    )

    assert config[CONF_UNIT_OF_MEASUREMENT] == "rpm"
    assert config[CONF_ACCURACY_DECIMALS] == 2


def test_type_defaults_ignore_an_unparseable_input() -> None:
    """A bad input is left for the schema to report, not crashed on here."""
    config = _fill_sensor_defaults({CONF_CHIP: "pwmfan", CONF_INPUT: "nonsense"})

    assert CONF_UNIT_OF_MEASUREMENT not in config


def test_scan_finds_every_reading(hwmon_root: Path) -> None:
    found = {f"{entry.chip}/{entry.filename}" for entry in scan_hwmon()}

    assert found == {
        "pwmfan/fan1_input",
        "pwmfan/pwm1",
        "pwmfan/pwm1_enable",
        "npu_thermal/temp1_input",
        "coretemp/temp1_input",
        "coretemp/temp1_label",
        "nvme/temp1_input",
    }


def test_scan_skips_thresholds_unless_asked(hwmon_root: Path) -> None:
    assert not any(entry.attribute == "max" for entry in scan_hwmon())

    with_thresholds = scan_hwmon(include_thresholds=True)
    assert any(entry.filename == "temp1_max" for entry in with_thresholds)


def test_scan_without_a_hwmon_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_HWMON_ROOT, str(tmp_path / "missing"))

    assert scan_hwmon() == []


def test_discovery_splits_sensors_and_text_sensors(hwmon_root: Path) -> None:
    config = _discover()

    sensor_names = {conf[CONF_NAME] for conf in config[CONF_SENSORS]}
    text_names = {conf[CONF_NAME] for conf in config[CONF_TEXT_SENSORS]}

    assert "pwmfan fan1" in sensor_names
    assert "pwmfan pwm1" in sensor_names
    assert "pwmfan pwm1 Mode" in text_names
    assert "coretemp temp1 Label" in text_names


def test_discovery_names_readings_after_their_label(hwmon_root: Path) -> None:
    config = _discover(include=["coretemp/temp1_input"])

    assert [conf[CONF_NAME] for conf in config[CONF_SENSORS]] == [
        "coretemp Package id 0",
        "coretemp Package id 0 2",
    ]
    assert [conf[CONF_ID] for conf in config[CONF_SENSORS]] == [
        "hwmon_coretemp_temp1_input",
        "hwmon_coretemp_temp1_input_2",
    ]


def test_discovery_include_and_exclude(hwmon_root: Path) -> None:
    config = _discover(include=["*/temp*"], exclude=["nvme/*"])

    chips = {conf[CONF_CHIP] for conf in config[CONF_SENSORS]}
    assert chips == {"npu_thermal", "coretemp"}


def test_discovery_passes_on_the_update_interval(hwmon_root: Path) -> None:
    config = _discover(include=["pwmfan/fan1_input"], update_interval="15s")

    assert config[CONF_SENSORS][0][CONF_UPDATE_INTERVAL] == TimePeriod(
        milliseconds=15000
    )


def test_discovery_exclude_beats_include(hwmon_root: Path) -> None:
    config = _discover(include=["*"], exclude=["*"])

    assert config[CONF_SENSORS] == []
    assert config[CONF_TEXT_SENSORS] == []


def test_discovery_thresholds_are_opt_in(hwmon_root: Path) -> None:
    without = _discover(include=["npu_thermal/*"])
    assert {conf.get(CONF_ATTRIBUTE) for conf in without[CONF_SENSORS]} == {"input"}

    with_thresholds = _discover(include=["npu_thermal/*"], include_thresholds=True)
    assert {conf.get(CONF_ATTRIBUTE) for conf in with_thresholds[CONF_SENSORS]} == {
        "input",
        "max",
    }


def test_generated_code(
    generate_main: Callable[[str | Path], str],
    hwmon_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hand-picked and discovered entities both reach the generated C++."""
    # The Linux check is a config-time policy; codegen itself is portable
    monkeypatch.setattr(sys, "platform", "linux")

    main_cpp = generate_main("tests/component_tests/hwmon/test_hwmon.yaml")

    # Hand-picked: temperatures are milli-degrees, so the multiplier is 1/1000
    assert 'HwmonSensor("npu_thermal", "temp1_input", 0.001' in main_cpp
    # Hand-picked: fan speeds are already RPM
    assert 'HwmonSensor("pwmfan", "fan1_input", 1.0' in main_cpp
    assert 'HwmonTextSensor("pwmfan", "pwm1_enable")' in main_cpp
    # Discovered from the fake tree
    assert 'HwmonSensor("coretemp", "temp1_input", 0.001' in main_cpp
