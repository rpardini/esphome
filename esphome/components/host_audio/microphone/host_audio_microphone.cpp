#include "host_audio_microphone.h"

#ifdef USE_HOST

#include "esphome/core/log.h"

#include <cinttypes>

namespace esphome::host_audio {

static const char *const TAG = "host_audio.microphone";

// Chunk size handed to the data callbacks, the same as the i2s_audio microphone
static constexpr uint32_t READ_DURATION_MS = 16;

void HostAudioMicrophone::setup() {
  this->buffer_.resize(
      this->audio_stream_info_.frames_to_bytes(this->audio_stream_info_.ms_to_frames(READ_DURATION_MS)));
  this->start_worker_();
}

void HostAudioMicrophone::dump_config() {
  ESP_LOGCONFIG(TAG,
                "Host Audio Microphone:\n"
                "  Sample rate: %" PRIu32 " Hz\n"
                "  Bits per sample: %u\n"
                "  Channels: %u",
                this->audio_stream_info_.get_sample_rate(), this->audio_stream_info_.get_bits_per_sample(),
                this->audio_stream_info_.get_channels());
  this->dump_device_config_(TAG);
}

void HostAudioMicrophone::loop() {
  const uint32_t bits = this->take_bits_(TASK_STARTING | TASK_RUNNING | TASK_STOPPING | TASK_STOPPED | ERR_OPEN);

  if (bits & TASK_STARTING) {
    ESP_LOGD(TAG, "Starting");
    this->state_ = microphone::STATE_STARTING;
  }
  if (bits & TASK_RUNNING) {
    ESP_LOGV(TAG, "Started");
    this->state_ = microphone::STATE_RUNNING;
  }
  if (bits & TASK_STOPPING) {
    ESP_LOGV(TAG, "Stopping");
    this->state_ = microphone::STATE_STOPPING;
  }
  if (bits & ERR_OPEN) {
    this->status_momentary_error("stream-open", 1000);
  }
  if (bits & TASK_STOPPED) {
    ESP_LOGD(TAG, "Stopped");
    this->state_ = microphone::STATE_STOPPED;
    if (this->listeners_ > 0) {
      // Still wanted, e.g. after a failed open; try again
      this->set_bits_(COMMAND_START);
    }
  }
  if (const uint32_t overflows = this->overflows_.exchange(0)) {
    ESP_LOGV(TAG, "Input overflowed %" PRIu32 " time(s); audio was lost", overflows);
  }

  if (this->state_ == microphone::STATE_STOPPED && this->listeners_ == 0) {
    this->disable_loop();
  }
}

void HostAudioMicrophone::start() {
  if (this->is_failed())
    return;
  if (this->listeners_++ == 0) {
    this->take_bits_(COMMAND_STOP);
    this->set_bits_(COMMAND_START);
    this->enable_loop_soon_any_context();
  }
}

void HostAudioMicrophone::stop() {
  if (this->is_failed())
    return;
  uint32_t listeners = this->listeners_;
  while (listeners > 0 && !this->listeners_.compare_exchange_weak(listeners, listeners - 1)) {
  }
  if (listeners == 1) {
    this->take_bits_(COMMAND_START);
    this->set_bits_(COMMAND_STOP);
    this->enable_loop_soon_any_context();
  }
}

void HostAudioMicrophone::run_session_() {
  const audio::AudioStreamInfo &info = this->audio_stream_info_;
  const PaDeviceIndex device = this->find_device_(true);
  const PaDeviceInfo *device_info = device != paNoDevice ? Pa_GetDeviceInfo(device) : nullptr;
  if (device_info == nullptr) {
    ESP_LOGE(TAG, "Input device not found");
    this->set_task_bits_(ERR_OPEN);
    // Avoids a busy retry loop while the device is missing
    this->wait_for_bits_(COMMAND_STOP, 1000);
    return;
  }

  PaStreamParameters parameters{};
  parameters.device = device;
  parameters.channelCount = info.get_channels();
  parameters.sampleFormat = HostAudioComponent::sample_format(info.get_bits_per_sample());
  parameters.suggestedLatency =
      this->suggested_latency_(device_info->defaultLowInputLatency, device_info->defaultHighInputLatency);

  const uint32_t frames_per_chunk = info.bytes_to_frames(this->buffer_.size());
  PaStream *stream = nullptr;
  PaError err;
  {
    std::lock_guard<std::mutex> lock(this->parent_->stream_lock());
    err = Pa_OpenStream(&stream, &parameters, nullptr, info.get_sample_rate(), frames_per_chunk, paClipOff, nullptr,
                        nullptr);
    if (err == paNoError) {
      err = Pa_StartStream(stream);
      if (err != paNoError) {
        Pa_CloseStream(stream);
      }
    }
  }
  if (err != paNoError) {
    ESP_LOGE(TAG, "Opening '%s' for %" PRIu32 " Hz, %u channel(s), %u bit failed: %s", device_info->name,
             info.get_sample_rate(), info.get_channels(), info.get_bits_per_sample(), error_text(err));
    this->set_task_bits_(ERR_OPEN);
    this->wait_for_bits_(COMMAND_STOP, 1000);
    return;
  }
  ESP_LOGD(TAG, "Capturing from '%s'", device_info->name);
  this->set_task_bits_(TASK_RUNNING);

  // Blocking reads of one chunk pace the loop, so a stop takes effect within one chunk
  while (!this->shutdown_ && !this->has_bits_(COMMAND_STOP)) {
    err = Pa_ReadStream(stream, this->buffer_.data(), frames_per_chunk);
    if (err == paInputOverflowed) {
      this->overflows_++;
      this->enable_loop_soon_any_context();
    } else if (err != paNoError) {
      ESP_LOGE(TAG, "Reading failed: %s", error_text(err));
      break;
    }
    if (this->data_callbacks_.size() > 0) {
      this->data_callbacks_.call(this->buffer_);
    }
  }
  this->take_bits_(COMMAND_STOP);

  this->set_task_bits_(TASK_STOPPING);
  std::lock_guard<std::mutex> lock(this->parent_->stream_lock());
  Pa_AbortStream(stream);
  Pa_CloseStream(stream);
}

}  // namespace esphome::host_audio

#endif  // USE_HOST
