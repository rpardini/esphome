import esphome.codegen as cg
from esphome.components import audio, speaker
import esphome.config_validation as cv
from esphome.const import CONF_BUFFER_DURATION, CONF_ID, CONF_NEVER, CONF_TIMEOUT
from esphome.types import ConfigType

from .. import (
    HOST_AUDIO_STREAM_SCHEMA,
    HostAudioStream,
    host_audio_ns,
    register_host_audio_stream,
)

AUTO_LOAD = ["host_audio"]
CODEOWNERS = ["@rpardini"]

HostAudioSpeaker = host_audio_ns.class_(
    "HostAudioSpeaker", HostAudioStream, speaker.Speaker
)


def _set_stream_limits(config: ConfigType) -> ConfigType:
    # PortAudio converts to what the device accepts, so any stream ESPHome produces can be played as is
    audio.set_stream_limits(
        min_bits_per_sample=8,
        max_bits_per_sample=32,
        min_channels=1,
        max_channels=2,
        min_sample_rate=8000,
        max_sample_rate=192000,
    )(config)
    return config


CONFIG_SCHEMA = cv.All(
    speaker.SPEAKER_SCHEMA.extend(HOST_AUDIO_STREAM_SCHEMA).extend(
        {
            cv.GenerateID(): cv.declare_id(HostAudioSpeaker),
            cv.Optional(
                CONF_BUFFER_DURATION, default="500ms"
            ): cv.positive_time_period_milliseconds,
            cv.Optional(CONF_TIMEOUT, default="500ms"): cv.Any(
                cv.positive_time_period_milliseconds,
                cv.one_of(CONF_NEVER, lower=True),
            ),
        }
    ),
    _set_stream_limits,
)


async def to_code(config: ConfigType) -> None:
    var = cg.new_Pvariable(config[CONF_ID])
    await register_host_audio_stream(var, config)
    await speaker.register_speaker(var, config)

    cg.add(var.set_buffer_duration(config[CONF_BUFFER_DURATION]))
    if (timeout := config[CONF_TIMEOUT]) != CONF_NEVER:
        cg.add(var.set_timeout(timeout))
