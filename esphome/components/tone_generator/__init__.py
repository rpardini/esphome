import esphome.codegen as cg
from esphome.components import audio, speaker
import esphome.config_validation as cv
from esphome.const import (
    CONF_BITS_PER_SAMPLE,
    CONF_FREQUENCY,
    CONF_ID,
    CONF_NUM_CHANNELS,
    CONF_SAMPLE_RATE,
    CONF_SPEAKER,
)
from esphome.core import Lambda
from esphome.types import ConfigType

AUTO_LOAD = ["audio"]
CODEOWNERS = ["@rpardini"]
MULTI_CONF = True

CONF_AMPLITUDE = "amplitude"

tone_generator_ns = cg.esphome_ns.namespace("tone_generator")
ToneGenerator = tone_generator_ns.class_("ToneGenerator", cg.Component)


def _validate_frequency(config: ConfigType) -> ConfigType:
    frequency = config[CONF_FREQUENCY]
    if isinstance(frequency, Lambda):
        return config
    nyquist = config[CONF_SAMPLE_RATE] / 2
    if frequency >= nyquist:
        raise cv.Invalid(
            f"Frequency {frequency} Hz must be below half the sample rate ({nyquist} Hz)",
            path=[CONF_FREQUENCY],
        )
    return config


CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(ToneGenerator),
            cv.Required(CONF_SPEAKER): cv.use_id(speaker.Speaker),
            # Required rather than inherited from the speaker: a mixer source declares no stream
            # limits to inherit from, and in non-queue mode the mixer rejects a source whose rate
            # differs from the others, so guessing here would fail at runtime instead of at config
            # time.
            cv.Required(CONF_SAMPLE_RATE): cv.int_range(min=8000, max=96000),
            cv.Optional(CONF_FREQUENCY, default="1000Hz"): cv.templatable(
                cv.All(cv.frequency, cv.Range(min=0.1, max=20000.0))
            ),
            cv.Optional(CONF_AMPLITUDE, default="50%"): cv.templatable(cv.percentage),
            cv.Optional(CONF_BITS_PER_SAMPLE, default=16): cv.one_of(
                8, 16, 24, 32, int=True
            ),
            cv.Optional(CONF_NUM_CHANNELS, default=1): cv.int_range(min=1, max=2),
        }
    ).extend(cv.COMPONENT_SCHEMA),
    _validate_frequency,
)


def _final_validate(config: ConfigType) -> None:
    audio.final_validate_audio_schema(
        "tone_generator",
        audio_device=CONF_SPEAKER,
        bits_per_sample=config[CONF_BITS_PER_SAMPLE],
        channels=config[CONF_NUM_CHANNELS],
        sample_rate=config[CONF_SAMPLE_RATE],
    )(config)


FINAL_VALIDATE_SCHEMA = _final_validate


async def to_code(config: ConfigType) -> None:
    spkr = await cg.get_variable(config[CONF_SPEAKER])
    var = cg.new_Pvariable(
        config[CONF_ID],
        spkr,
        config[CONF_SAMPLE_RATE],
        config[CONF_BITS_PER_SAMPLE],
        config[CONF_NUM_CHANNELS],
    )
    await cg.register_component(var, config)

    frequency = await cg.templatable(config[CONF_FREQUENCY], [], cg.float_)
    cg.add(var.set_frequency(frequency))
    amplitude = await cg.templatable(config[CONF_AMPLITUDE], [], cg.float_)
    cg.add(var.set_amplitude(amplitude))
