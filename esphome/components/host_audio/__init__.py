import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.const import CONF_DEVICE, CONF_ID, PLATFORM_HOST
from esphome.host import pkg_config
from esphome.types import ConfigType

CODEOWNERS = ["@rpardini"]

CONF_HOST_API = "host_api"
CONF_HOST_AUDIO_ID = "host_audio_id"
CONF_LATENCY = "latency"

PORTAUDIO_PACKAGE = "portaudio-2.0"
PORTAUDIO_INSTALL_HINT = "'apt install portaudio19-dev' or 'brew install portaudio'"

host_audio_ns = cg.esphome_ns.namespace("host_audio")
HostAudioComponent = host_audio_ns.class_("HostAudioComponent", cg.Component)
HostAudioStream = host_audio_ns.class_("HostAudioStream", cg.Component)
LatencyMode = host_audio_ns.enum("LatencyMode", is_class=True)

LATENCY_LOW = "low"
LATENCY_HIGH = "high"


def _require_portaudio(config: ConfigType) -> ConfigType:
    pkg_config.require_package(PORTAUDIO_PACKAGE, "host_audio", PORTAUDIO_INSTALL_HINT)
    return config


CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(HostAudioComponent),
            cv.Optional(CONF_HOST_API): cv.string_strict,
        }
    ).extend(cv.COMPONENT_SCHEMA),
    cv.only_on(PLATFORM_HOST),
    _require_portaudio,
)

HOST_AUDIO_STREAM_SCHEMA = cv.Schema(
    {
        cv.GenerateID(CONF_HOST_AUDIO_ID): cv.use_id(HostAudioComponent),
        # A PortAudio device index, or a name to look for; the default device when omitted
        cv.Optional(CONF_DEVICE): cv.Any(cv.int_range(min=0), cv.string_strict),
        cv.Optional(CONF_LATENCY, default=LATENCY_HIGH): cv.Any(
            cv.one_of(LATENCY_LOW, LATENCY_HIGH, lower=True),
            cv.positive_time_period_milliseconds,
        ),
    }
).extend(cv.COMPONENT_SCHEMA)


async def register_host_audio_stream(var: cg.MockObj, config: ConfigType) -> None:
    await cg.register_component(var, config)
    await cg.register_parented(var, config[CONF_HOST_AUDIO_ID])
    if isinstance(device := config.get(CONF_DEVICE), int):
        cg.add(var.set_device_index(device))
    elif device is not None:
        cg.add(var.set_device_name(device))
    latency = config[CONF_LATENCY]
    if latency == LATENCY_LOW:
        cg.add(var.set_latency(LatencyMode.LATENCY_MODE_LOW, 0))
    elif latency == LATENCY_HIGH:
        cg.add(var.set_latency(LatencyMode.LATENCY_MODE_HIGH, 0))
    else:
        cg.add(
            var.set_latency(LatencyMode.LATENCY_MODE_CUSTOM, latency.total_milliseconds)
        )


async def to_code(config: ConfigType) -> None:
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    if (host_api := config.get(CONF_HOST_API)) is not None:
        cg.add(var.set_host_api(host_api))

    pkg_config.add_package_build_flags(
        pkg_config.require_package(
            PORTAUDIO_PACKAGE, "host_audio", PORTAUDIO_INSTALL_HINT
        )
    )
    cg.add_build_flag("-pthread")
