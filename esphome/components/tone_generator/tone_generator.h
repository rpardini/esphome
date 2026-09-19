#pragma once

#include "esphome/components/audio/audio.h"
#include "esphome/components/speaker/speaker.h"

#include "esphome/core/automation.h"
#include "esphome/core/component.h"

#include <memory>

namespace esphome::tone_generator {

/* Writes a continuous sine wave to a speaker.
 *
 * The use this is built for is keeping an external amplifier awake: many powered speakers put
 * themselves to sleep after a few minutes of silence on their line input and need a button press to
 * come back. A sub-audible tone, a few Hz, is enough to stop that happening.
 *
 * The tone is generated at the configured amplitude rather than by setting the speaker's volume, so
 * it does not fight whatever else shares the speaker, and it survives a volume of zero. Point this
 * at a dedicated `mixer` source speaker with `volume_scope: source` on the other sources and the
 * tone stays at a fixed level whatever the user does to the media volume.
 */
class ToneGenerator final : public Component {
 public:
  ToneGenerator(speaker::Speaker *speaker, uint32_t sample_rate, uint8_t bits_per_sample, uint8_t channels)
      : speaker_(speaker), stream_info_(bits_per_sample, channels, sample_rate) {}

  void setup() override;
  void loop() override;
  void dump_config() override;

  template<typename V> void set_frequency(V frequency) { this->frequency_ = frequency; }
  template<typename V> void set_amplitude(V amplitude) { this->amplitude_ = amplitude; }

 protected:
  /// @brief Starts the speaker if it is not running, setting the stream format while it is stopped.
  /// @return True once the speaker is running and will accept audio.
  bool ensure_speaker_running_();

  /// @brief Re-derives the phase step and sample scale when the configured values change.
  void update_parameters_();

  /// @brief Fills the chunk buffer with `frames` frames, leaving this->phase_ untouched.
  void generate_(size_t frames);

  speaker::Speaker *speaker_;
  audio::AudioStreamInfo stream_info_;

  TemplatableStorage<float> frequency_{};
  TemplatableStorage<float> amplitude_{};

  std::unique_ptr<uint8_t[]> chunk_;
  size_t chunk_bytes_{0};

  uint32_t phase_{0};            // Q32 position within one period; wraps on its own
  uint32_t phase_increment_{0};  // Q32 step per frame
  int32_t amplitude_q15_{0};

  float last_frequency_{0.0f};
  float last_amplitude_{-1.0f};
};

}  // namespace esphome::tone_generator
