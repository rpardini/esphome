from esphome.types import ConfigType

from . import APPLE_SMC_SENSOR_SCHEMA, FILTER_SOURCE_FILES, new_apple_smc_sensor  # noqa: F401  pylint: disable=unused-import

CONFIG_SCHEMA = APPLE_SMC_SENSOR_SCHEMA


async def to_code(config: ConfigType) -> None:
    await new_apple_smc_sensor(config)
