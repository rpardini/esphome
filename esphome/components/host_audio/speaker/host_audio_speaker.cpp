#include "host_audio_speaker.h"

#ifdef USE_HOST

#include "esphome/core/hal.h"
#include "esphome/core/helpers.h"
#include "esphome/core/log.h"

#include <algorithm>
#include <chrono>
#include <cinttypes>
#include <cmath>
#include <cstring>

namespace esphome::host_audio {

static const char *const TAG = "host_audio.speaker";

// Software volume maps the (0.0, 1.0) range linearly to [-49.0, 0.0] dB, like i2s_audio
static constexpr float SOFTWARE_VOLUME_MIN_DB = -49.0f;
// Largest stream the ring buffer is sized for, so it is allocated once in setup()
static constexpr uint32_t MAX_SAMPLE_RATE = 192000;
static constexpr uint8_t MAX_CHANNELS = 2;
static constexpr uint8_t MAX_BYTES_PER_SAMPLE = 4;
// How often the worker thread checks for commands, the idle timeout and stream changes
static constexpr uint32_t SESSION_POLL_MS = 10;
// How long play() sleeps between attempts to find room in the ring buffer
static constexpr uint32_t WRITE_RETRY_MS = 5;

void HostAudioSpeaker::setup() {
  const size_t bytes_per_frame = MAX_CHANNELS * MAX_BYTES_PER_SAMPLE;
  const size_t capacity = (static_cast<uint64_t>(this->buffer_duration_ms_) * MAX_SAMPLE_RATE / 1000) * bytes_per_frame;
  if (!this->ring_buffer_.allocate(capacity)) {
    ESP_LOGE(TAG, "Failed to allocate a %zu byte buffer", capacity);
    this->mark_failed();
    return;
  }
  this->update_gain_();
  this->start_worker_();
}

void HostAudioSpeaker::dump_config() {
  ESP_LOGCONFIG(TAG, "Host Audio Speaker:\n  Buffer duration: %" PRIu32 " ms", this->buffer_duration_ms_);
  this->dump_device_config_(TAG);
  if (this->timeout_.has_value()) {
    ESP_LOGCONFIG(TAG, "  Timeout: %" PRIu32 " ms", this->timeout_.value());
  }
}

void HostAudioSpeaker::loop() {
  const uint32_t bits = this->take_bits_(TASK_STARTING | TASK_RUNNING | TASK_STOPPING | TASK_STOPPED | ERR_OPEN);

  if (this->has_bits_(COMMAND_START) && this->state_ == speaker::STATE_STOPPED) {
    this->state_ = speaker::STATE_STARTING;
  }
  if (bits & TASK_STARTING) {
    ESP_LOGD(TAG, "Starting");
    this->state_ = speaker::STATE_STARTING;
  }
  if (bits & TASK_RUNNING) {
    ESP_LOGV(TAG, "Started");
    this->state_ = speaker::STATE_RUNNING;
  }
  if (bits & TASK_STOPPING) {
    ESP_LOGV(TAG, "Stopping");
    this->state_ = speaker::STATE_STOPPING;
  }
  if (bits & ERR_OPEN) {
    // The worker thread logs the cause; retrying right away would flood the log
    this->status_momentary_error("stream-open", 1000);
  }
  if (bits & TASK_STOPPED) {
    ESP_LOGD(TAG, "Stopped");
    this->state_ = speaker::STATE_STOPPED;
  }

  if (this->state_ == speaker::STATE_STOPPED && !this->has_bits_(COMMAND_START)) {
    this->disable_loop();
  }
}

void HostAudioSpeaker::start() {
  if (this->is_failed() || this->status_has_error())
    return;
  if (this->state_ == speaker::STATE_STARTING || this->state_ == speaker::STATE_RUNNING)
    return;
  // The latest command wins
  this->take_bits_(COMMAND_STOP | COMMAND_STOP_GRACEFULLY);
  this->set_bits_(COMMAND_START);
  this->enable_loop_soon_any_context();
}

void HostAudioSpeaker::stop() {
  if (this->is_failed())
    return;
  this->take_bits_(COMMAND_START);
  this->set_bits_(COMMAND_STOP);
  this->enable_loop_soon_any_context();
}

void HostAudioSpeaker::finish() {
  if (this->is_failed())
    return;
  this->take_bits_(COMMAND_START);
  this->set_bits_(COMMAND_STOP_GRACEFULLY);
  this->enable_loop_soon_any_context();
}

size_t HostAudioSpeaker::play(const uint8_t *data, size_t length, uint32_t ms_to_wait) {
  if (this->is_failed()) {
    ESP_LOGE(TAG, "Setup failed; cannot play audio");
    return 0;
  }
  if (this->state_ != speaker::STATE_RUNNING && this->state_ != speaker::STATE_STARTING) {
    this->start();
  }
  if (!this->accepting_writes_ || this->audio_stream_info_ != this->current_stream_info_) {
    // Give the stream time to open, or to reopen for a new format, before the caller retries
    delay(ms_to_wait);
    return 0;
  }

  std::lock_guard<std::mutex> lock(this->write_lock_);
  const size_t bytes_per_frame = this->audio_stream_info_.frames_to_bytes(1);
  const uint32_t start_ms = millis();
  size_t written = 0;
  while (this->accepting_writes_) {
    // Whole frames only, so the callback never reads half a frame
    size_t to_write = std::min(length - written, this->ring_buffer_.free());
    to_write -= to_write % bytes_per_frame;
    written += this->ring_buffer_.write(data + written, to_write);
    if (length - written < bytes_per_frame || millis() - start_ms >= ms_to_wait) {
      break;
    }
    delay(std::min(WRITE_RETRY_MS, ms_to_wait));
  }
  if (written > 0) {
    this->last_write_ms_ = millis();
  }
  return written;
}

void HostAudioSpeaker::set_volume(float volume) {
  speaker::Speaker::set_volume(volume);
  this->update_gain_();
}

void HostAudioSpeaker::set_mute_state(bool mute_state) {
  speaker::Speaker::set_mute_state(mute_state);
  this->update_gain_();
}

void HostAudioSpeaker::update_gain_() {
#ifdef USE_AUDIO_DAC
  if (this->audio_dac_ != nullptr) {
    this->target_gain_ = INT32_MAX;  // Hardware volume
    return;
  }
#endif
  if (this->is_silent_()) {
    this->target_gain_ = 0;
  } else if (this->volume_ >= 1.0f) {
    this->target_gain_ = INT32_MAX;
  } else {
    const float db = remap<float, float>(this->volume_, 0.0f, 1.0f, SOFTWARE_VOLUME_MIN_DB, 0.0f);
    this->target_gain_ = static_cast<int32_t>(std::pow(10.0, db / 20.0) * INT32_MAX);
  }
}

void HostAudioSpeaker::apply_gain_(uint8_t *data, uint32_t frames) {
  const int32_t target = this->target_gain_;
  if (this->current_gain_ == target && target == INT32_MAX) {
    return;
  }
  const uint8_t bytes_per_sample = this->current_stream_info_.get_bits_per_sample() / 8;
  const uint8_t channels = this->current_stream_info_.get_channels();
  // Ramp linearly to the new gain over this block, so volume changes don't click
  const int64_t start = this->current_gain_;
  const int64_t delta = static_cast<int64_t>(target) - start;
  for (uint32_t frame = 0; frame < frames; frame++) {
    const int64_t gain = start + delta * (frame + 1) / frames;
    for (uint8_t channel = 0; channel < channels; channel++) {
      const int32_t sample = audio::unpack_audio_sample_to_q31(data, bytes_per_sample);
      audio::pack_q31_as_audio_sample(static_cast<int32_t>((sample * gain) >> 31), data, bytes_per_sample);
      data += bytes_per_sample;
    }
  }
  this->current_gain_ = target;
}

void HostAudioSpeaker::run_session_() {
  this->current_stream_info_ = this->audio_stream_info_;
  const audio::AudioStreamInfo &info = this->current_stream_info_;

  const PaDeviceIndex device = this->find_device_(false);
  const PaDeviceInfo *device_info = device != paNoDevice ? Pa_GetDeviceInfo(device) : nullptr;
  if (device_info == nullptr) {
    ESP_LOGE(TAG, "Output device not found");
    this->set_task_bits_(ERR_OPEN);
    return;
  }

  PaStreamParameters parameters{};
  parameters.device = device;
  parameters.channelCount = info.get_channels();
  parameters.sampleFormat = HostAudioComponent::sample_format(info.get_bits_per_sample());
  parameters.suggestedLatency =
      this->suggested_latency_(device_info->defaultLowOutputLatency, device_info->defaultHighOutputLatency);

  this->current_gain_ = this->target_gain_;
  PaError err;
  {
    std::lock_guard<std::mutex> lock(this->parent_->stream_lock());
    err = Pa_OpenStream(&this->stream_, nullptr, &parameters, info.get_sample_rate(), paFramesPerBufferUnspecified,
                        paClipOff, &HostAudioSpeaker::stream_callback, this);
    if (err == paNoError) {
      const PaStreamInfo *stream_info = Pa_GetStreamInfo(this->stream_);
      this->actual_sample_rate_ = stream_info->sampleRate;
      this->output_latency_ = stream_info->outputLatency;
      err = Pa_StartStream(this->stream_);
      if (err != paNoError) {
        Pa_CloseStream(this->stream_);
        this->stream_ = nullptr;
      }
    }
  }
  if (err != paNoError) {
    ESP_LOGE(TAG, "Opening '%s' for %u Hz, %u channel(s), %u bit failed: %s", device_info->name,
             (unsigned) info.get_sample_rate(), (unsigned) info.get_channels(), (unsigned) info.get_bits_per_sample(),
             error_text(err));
    this->set_task_bits_(ERR_OPEN);
    return;
  }
  ESP_LOGD(TAG, "Playing on '%s': %u Hz, %u channel(s), %u bit, %.0f ms output latency", device_info->name,
           (unsigned) info.get_sample_rate(), (unsigned) info.get_channels(), (unsigned) info.get_bits_per_sample(),
           this->output_latency_ * 1000.0);

  this->last_write_ms_ = millis();
  this->accepting_writes_ = true;
  this->set_task_bits_(TASK_RUNNING);

  bool graceful = false;
  while (!this->shutdown_) {
    this->wait_for_bits_(COMMAND_STOP | COMMAND_STOP_GRACEFULLY, SESSION_POLL_MS);
    if (this->take_bits_(COMMAND_STOP)) {
      ESP_LOGV(TAG, "Exiting: stop requested");
      break;
    }
    if (this->take_bits_(COMMAND_STOP_GRACEFULLY)) {
      graceful = true;
    }
    if (graceful && this->ring_buffer_.available() == 0) {
      ESP_LOGV(TAG, "Exiting: graceful stop complete");
      break;
    }
    if (this->audio_stream_info_ != this->current_stream_info_) {
      ESP_LOGV(TAG, "Exiting: stream info changed");
      break;
    }
    if (!this->pause_state_ && this->timeout_.has_value() && millis() - this->last_write_ms_ > this->timeout_.value()) {
      ESP_LOGV(TAG, "Exiting: no audio for %" PRIu32 " ms", this->timeout_.value());
      break;
    }
  }

  this->set_task_bits_(TASK_STOPPING);
  this->accepting_writes_ = false;
  {
    std::lock_guard<std::mutex> lock(this->parent_->stream_lock());
    // A graceful stop lets PortAudio play what it already holds; otherwise drop it
    err = graceful ? Pa_StopStream(this->stream_) : Pa_AbortStream(this->stream_);
    if (err != paNoError) {
      ESP_LOGW(TAG, "Stopping the stream failed: %s", error_text(err));
    }
    Pa_CloseStream(this->stream_);
    this->stream_ = nullptr;
  }
  std::lock_guard<std::mutex> lock(this->write_lock_);
  this->ring_buffer_.reset();
}

int HostAudioSpeaker::stream_callback(const void *input, void *output, unsigned long frame_count,
                                      const PaStreamCallbackTimeInfo *time_info, PaStreamCallbackFlags status_flags,
                                      void *user_data) {
  // PortAudio's realtime thread: no locks, allocations or logging
  auto *speaker = static_cast<HostAudioSpeaker *>(user_data);
  auto *out = static_cast<uint8_t *>(output);
  const audio::AudioStreamInfo &info = speaker->current_stream_info_;
  const size_t bytes = info.frames_to_bytes(frame_count);

  size_t bytes_read = 0;
  if (!speaker->pause_state_) {
    bytes_read = speaker->ring_buffer_.read(out, bytes);
  }
  std::memset(out + bytes_read, 0, bytes - bytes_read);

  const uint32_t frames = info.bytes_to_frames(bytes_read);
  if (frames == 0) {
    return paContinue;
  }
  speaker->apply_gain_(out, frames);

  // Real audio sits at the start of the block, so its last frame reaches the DAC ``frames`` after the block does.
  // Some backends leave the DAC time at zero; the stream's output latency stands in for it there.
  PaTime dac_delay = speaker->output_latency_;
  if (time_info != nullptr && time_info->outputBufferDacTime > 0.0) {
    dac_delay = time_info->outputBufferDacTime - time_info->currentTime;
  }
  const int64_t now_us =
      std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch())
          .count();
  const int64_t finished_us = now_us + std::llround((dac_delay + frames / speaker->actual_sample_rate_) * 1e6);
  speaker->audio_output_callback_(frames, finished_us);
  return paContinue;
}

}  // namespace esphome::host_audio

#endif  // USE_HOST
