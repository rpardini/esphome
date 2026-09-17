#pragma once

#ifdef USE_HOST

#include "../host_audio.h"
#include "../host_audio_ring_buffer.h"

#include "esphome/components/audio/audio.h"
#include "esphome/components/speaker/speaker.h"
#include "esphome/core/component.h"
#include "esphome/core/optional.h"

#include <atomic>
#include <cstdint>
#include <mutex>

namespace esphome::host_audio {

/// Plays audio through a PortAudio output device.
///
/// play() writes into a lock-free ring buffer that PortAudio's realtime callback drains. The callback reports every
/// block of real audio frames with the time it reaches the DAC, which Sendspin uses to keep playback in sync.
class HostAudioSpeaker : public HostAudioStream, public speaker::Speaker {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::PROCESSOR; }

  void set_buffer_duration(uint32_t buffer_duration_ms) { this->buffer_duration_ms_ = buffer_duration_ms; }
  void set_timeout(uint32_t ms) { this->timeout_ = ms; }

  using speaker::Speaker::play;
  size_t play(const uint8_t *data, size_t length, uint32_t ms_to_wait) override;
  size_t play(const uint8_t *data, size_t length) override { return this->play(data, length, 0); }

  void start() override;
  void stop() override;
  void finish() override;

  void set_pause_state(bool pause_state) override { this->pause_state_ = pause_state; }
  bool get_pause_state() const override { return this->pause_state_; }

  bool has_buffered_data() const override { return this->ring_buffer_.available() > 0; }

  void set_volume(float volume) override;
  void set_mute_state(bool mute_state) override;

 protected:
  void run_session_() override;
  void update_gain_();
  void apply_gain_(uint8_t *data, uint32_t frames);

  static int stream_callback(const void *input, void *output, unsigned long frame_count,
                             const PaStreamCallbackTimeInfo *time_info, PaStreamCallbackFlags status_flags,
                             void *user_data);

  uint32_t buffer_duration_ms_{500};
  optional<uint32_t> timeout_;

  SpscRingBuffer ring_buffer_;
  // Serializes writers; the ring buffer allows only one at a time
  std::mutex write_lock_;
  std::atomic<bool> accepting_writes_{false};
  std::atomic<uint32_t> last_write_ms_{0};
  std::atomic<bool> pause_state_{false};

  // Set by the worker thread before the stream starts; read by the callback while it runs
  audio::AudioStreamInfo current_stream_info_;
  PaStream *stream_{nullptr};
  double actual_sample_rate_{0};
  PaTime output_latency_{0};

  // Software volume in Q31; INT32_MAX is unity gain
  std::atomic<int32_t> target_gain_{INT32_MAX};
  // Only used by the callback, which ramps it toward target_gain_
  int32_t current_gain_{INT32_MAX};
};

}  // namespace esphome::host_audio

#endif  // USE_HOST
