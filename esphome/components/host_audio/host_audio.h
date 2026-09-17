#pragma once

#ifdef USE_HOST

#include "esphome/components/audio/audio.h"
#include "esphome/core/component.h"
#include "esphome/core/helpers.h"

#include <portaudio.h>

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <thread>

namespace esphome::host_audio {

enum class LatencyMode : uint8_t {
  LATENCY_MODE_LOW,
  LATENCY_MODE_HIGH,
  LATENCY_MODE_CUSTOM,
};

/// Initializes PortAudio once for every host_audio speaker and microphone, and serializes the stream calls
/// PortAudio does not guarantee to be thread-safe.
class HostAudioComponent : public Component {
 public:
  void setup() override;
  void dump_config() override;
  void on_shutdown() override;
  float get_setup_priority() const override { return setup_priority::BUS; }

  /// Limits device lookups by name to one PortAudio host API, e.g. "ALSA" or "JACK Audio Connection Kit".
  void set_host_api(const char *host_api) { this->host_api_ = host_api; }

  /// Finds a device by index (when ``index`` >= 0), by name (exact first, then case-insensitive substring), or the
  /// default device. Returns paNoDevice when nothing matches.
  PaDeviceIndex find_device(const char *name, int index, bool input) const;

  std::mutex &stream_lock() { return this->stream_lock_; }

  static PaSampleFormat sample_format(uint8_t bits_per_sample);

 protected:
  bool host_api_matches_(const PaDeviceInfo *info) const;

  const char *host_api_{nullptr};
  bool initialized_{false};
  std::mutex stream_lock_;
};

/// Worker thread and command handling shared by the host_audio speaker and microphone.
///
/// The main loop sets command bits and the worker thread opens, runs and closes one PortAudio stream per session.
/// The worker reports its state back with task bits, which loop() turns into the component's state.
class HostAudioStream : public Component, public Parented<HostAudioComponent> {
 public:
  void set_device_name(const char *name) { this->device_name_ = name; }
  void set_device_index(int index) { this->device_index_ = index; }
  void set_latency(LatencyMode mode, uint32_t latency_ms) {
    this->latency_mode_ = mode;
    this->latency_ms_ = latency_ms;
  }

  void on_shutdown() override;

 protected:
  enum Bits : uint32_t {
    COMMAND_START = 1 << 0,
    COMMAND_STOP = 1 << 1,
    COMMAND_STOP_GRACEFULLY = 1 << 2,
    TASK_STARTING = 1 << 3,
    TASK_RUNNING = 1 << 4,
    TASK_STOPPING = 1 << 5,
    TASK_STOPPED = 1 << 6,
    ERR_OPEN = 1 << 7,
  };

  /// Runs one stream session on the worker thread, from opening the stream until it is closed.
  virtual void run_session_() = 0;

  void start_worker_();
  void set_bits_(uint32_t bits);
  /// Called from the worker thread; also wakes the main loop.
  void set_task_bits_(uint32_t bits);
  uint32_t take_bits_(uint32_t bits) { return this->bits_.fetch_and(~bits) & bits; }
  bool has_bits_(uint32_t bits) const { return (this->bits_.load() & bits) != 0; }
  /// Waits on the worker thread until any of ``bits`` is set, shutdown is requested, or the timeout passes.
  void wait_for_bits_(uint32_t bits, uint32_t timeout_ms);

  PaDeviceIndex find_device_(bool input) const;
  PaTime suggested_latency_(PaTime low, PaTime high) const;
  void dump_device_config_(const char *tag) const;
  static const char *error_text(PaError err) { return Pa_GetErrorText(err); }

  const char *device_name_{nullptr};
  int device_index_{-1};
  LatencyMode latency_mode_{LatencyMode::LATENCY_MODE_HIGH};
  uint32_t latency_ms_{0};

  std::atomic<uint32_t> bits_{0};
  std::atomic<bool> shutdown_{false};
  std::mutex wait_lock_;
  std::condition_variable wait_cv_;
  std::thread worker_;

 private:
  void worker_loop_();
};

}  // namespace esphome::host_audio

#endif  // USE_HOST
