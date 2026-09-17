#pragma once

#ifdef USE_HOST

#include "../host_audio.h"

#include "esphome/components/microphone/microphone.h"
#include "esphome/core/component.h"

#include <atomic>
#include <cstdint>
#include <vector>

namespace esphome::host_audio {

/// Captures audio from a PortAudio input device.
///
/// A worker thread reads fixed size chunks with PortAudio's blocking API and passes each one to the data callbacks,
/// on that thread, like the i2s_audio microphone does from its task.
class HostAudioMicrophone final : public HostAudioStream, public microphone::Microphone {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::PROCESSOR; }

  void set_stream_info(uint32_t sample_rate, uint8_t bits_per_sample, uint8_t channels) {
    this->audio_stream_info_ = audio::AudioStreamInfo(bits_per_sample, channels, sample_rate);
  }

  void start() override;
  void stop() override;

 protected:
  void run_session_() override;

  // Microphone sources that currently want audio
  std::atomic<uint32_t> listeners_{0};
  std::atomic<uint32_t> overflows_{0};
  std::vector<uint8_t> buffer_;
};

}  // namespace esphome::host_audio

#endif  // USE_HOST
