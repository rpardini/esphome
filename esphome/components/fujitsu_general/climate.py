import esphome.codegen as cg
from esphome.components import climate_ir
import esphome.config_validation as cv

AUTO_LOAD = ["climate_ir"]

CONF_POWER_ON_MODE_TRANSITION = "power_on_mode_transition"

fujitsu_general_ns = cg.esphome_ns.namespace("fujitsu_general")
FujitsuGeneralClimate = fujitsu_general_ns.class_(
    "FujitsuGeneralClimate", climate_ir.ClimateIR
)

CONFIG_SCHEMA = climate_ir.climate_ir_with_receiver_schema(FujitsuGeneralClimate).extend(
    {
        cv.Optional(CONF_POWER_ON_MODE_TRANSITION, default=False): cv.boolean,
    }
)


async def to_code(config):
    var = await climate_ir.new_climate_ir(config)
    cg.add(var.set_power_on_mode_transition(config[CONF_POWER_ON_MODE_TRANSITION]))
