import esphome.codegen as cg
from esphome.components import audio, microphone
import esphome.config_validation as cv
from esphome.const import (
    CONF_BITS_PER_SAMPLE,
    CONF_CHANNEL,
    CONF_ID,
    CONF_NUM_CHANNELS,
    CONF_SAMPLE_RATE,
)
from esphome.types import ConfigType

from .. import (
    HOST_AUDIO_STREAM_SCHEMA,
    HostAudioStream,
    host_audio_ns,
    register_host_audio_stream,
)

AUTO_LOAD = ["host_audio"]
CODEOWNERS = ["@rpardini"]

CONF_MONO = "mono"
CONF_STEREO = "stereo"

HostAudioMicrophone = host_audio_ns.class_(
    "HostAudioMicrophone", HostAudioStream, microphone.Microphone
)


def _set_stream(config: ConfigType) -> ConfigType:
    config[CONF_NUM_CHANNELS] = 1 if config[CONF_CHANNEL] == CONF_MONO else 2
    audio.set_stream_limits(
        min_bits_per_sample=config[CONF_BITS_PER_SAMPLE],
        max_bits_per_sample=config[CONF_BITS_PER_SAMPLE],
        min_channels=config[CONF_NUM_CHANNELS],
        max_channels=config[CONF_NUM_CHANNELS],
        min_sample_rate=config[CONF_SAMPLE_RATE],
        max_sample_rate=config[CONF_SAMPLE_RATE],
    )(config)
    return config


CONFIG_SCHEMA = cv.All(
    microphone.MICROPHONE_SCHEMA.extend(HOST_AUDIO_STREAM_SCHEMA).extend(
        {
            cv.GenerateID(): cv.declare_id(HostAudioMicrophone),
            cv.Optional(CONF_SAMPLE_RATE, default=16000): cv.int_range(
                min=8000, max=192000
            ),
            cv.Optional(CONF_BITS_PER_SAMPLE, default="16bit"): cv.All(
                cv.float_with_unit("bits", "bit"), cv.int_, cv.one_of(8, 16, 24, 32)
            ),
            cv.Optional(CONF_CHANNEL, default=CONF_MONO): cv.one_of(
                CONF_MONO, CONF_STEREO, lower=True
            ),
        }
    ),
    _set_stream,
)


async def to_code(config: ConfigType) -> None:
    var = cg.new_Pvariable(config[CONF_ID])
    await register_host_audio_stream(var, config)
    await microphone.register_microphone(var, config)

    cg.add(
        var.set_stream_info(
            config[CONF_SAMPLE_RATE],
            config[CONF_BITS_PER_SAMPLE],
            config[CONF_NUM_CHANNELS],
        )
    )
