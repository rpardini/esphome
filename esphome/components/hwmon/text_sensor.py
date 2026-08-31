from esphome.types import ConfigType

from . import (  # noqa: F401  pylint: disable=unused-import
    FILTER_SOURCE_FILES,
    HWMON_TEXT_SENSOR_SCHEMA,
    new_hwmon_text_sensor,
)

CONFIG_SCHEMA = HWMON_TEXT_SENSOR_SCHEMA


async def to_code(config: ConfigType) -> None:
    await new_hwmon_text_sensor(config)
