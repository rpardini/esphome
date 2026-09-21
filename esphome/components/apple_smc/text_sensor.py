from esphome.types import ConfigType

from . import (  # noqa: F401  pylint: disable=unused-import
    APPLE_SMC_TEXT_SENSOR_SCHEMA,
    FILTER_SOURCE_FILES,
    new_apple_smc_text_sensor,
)

CONFIG_SCHEMA = APPLE_SMC_TEXT_SENSOR_SCHEMA


async def to_code(config: ConfigType) -> None:
    await new_apple_smc_text_sensor(config)
