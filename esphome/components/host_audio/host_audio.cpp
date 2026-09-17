#include "host_audio.h"

#ifdef USE_HOST

#include "esphome/core/log.h"

#include <cinttypes>
#include <cstring>
#include <strings.h>

namespace esphome::host_audio {

static const char *const TAG = "host_audio";

void HostAudioComponent::setup() {
  PaError err = Pa_Initialize();
  if (err != paNoError) {
    ESP_LOGE(TAG, "PortAudio initialization failed: %s", Pa_GetErrorText(err));
    this->mark_failed();
    return;
  }
  this->initialized_ = true;
}

void HostAudioComponent::dump_config() {
  ESP_LOGCONFIG(TAG, "Host Audio:\n  PortAudio: %s", Pa_GetVersionInfo()->versionText);
  if (this->host_api_ != nullptr) {
    ESP_LOGCONFIG(TAG, "  Host API: %s", this->host_api_);
  }
  if (!this->initialized_) {
    return;
  }
  PaDeviceIndex default_input = Pa_GetDefaultInputDevice();
  PaDeviceIndex default_output = Pa_GetDefaultOutputDevice();
  PaDeviceIndex count = Pa_GetDeviceCount();
  for (PaDeviceIndex i = 0; i < count; i++) {
    const PaDeviceInfo *info = Pa_GetDeviceInfo(i);
    if (info == nullptr) {
      continue;
    }
    const PaHostApiInfo *api = Pa_GetHostApiInfo(info->hostApi);
    ESP_LOGCONFIG(TAG, "  Device %d: '%s' (%s), inputs: %d%s, outputs: %d%s, default rate: %.0f Hz", i, info->name,
                  api != nullptr ? api->name : "?", info->maxInputChannels,
                  i == default_input ? LOG_STR_LITERAL(" [default]") : LOG_STR_LITERAL(""), info->maxOutputChannels,
                  i == default_output ? LOG_STR_LITERAL(" [default]") : LOG_STR_LITERAL(""), info->defaultSampleRate);
  }
}

void HostAudioComponent::on_shutdown() {
  if (this->initialized_) {
    Pa_Terminate();
    this->initialized_ = false;
  }
}

bool HostAudioComponent::host_api_matches_(const PaDeviceInfo *info) const {
  if (this->host_api_ == nullptr) {
    return true;
  }
  const PaHostApiInfo *api = Pa_GetHostApiInfo(info->hostApi);
  return api != nullptr && strcasecmp(api->name, this->host_api_) == 0;
}

PaDeviceIndex HostAudioComponent::find_device(const char *name, int index, bool input) const {
  if (!this->initialized_) {
    return paNoDevice;
  }
  auto usable = [input](const PaDeviceInfo *info) {
    return info != nullptr && (input ? info->maxInputChannels : info->maxOutputChannels) > 0;
  };
  PaDeviceIndex count = Pa_GetDeviceCount();

  if (index >= 0) {
    return index < count && usable(Pa_GetDeviceInfo(index)) ? index : paNoDevice;
  }

  if (name == nullptr) {
    if (this->host_api_ != nullptr) {
      for (PaHostApiIndex api = 0; api < Pa_GetHostApiCount(); api++) {
        const PaHostApiInfo *api_info = Pa_GetHostApiInfo(api);
        if (api_info != nullptr && strcasecmp(api_info->name, this->host_api_) == 0) {
          return input ? api_info->defaultInputDevice : api_info->defaultOutputDevice;
        }
      }
      return paNoDevice;
    }
    return input ? Pa_GetDefaultInputDevice() : Pa_GetDefaultOutputDevice();
  }

  PaDeviceIndex substring_match = paNoDevice;
  for (PaDeviceIndex i = 0; i < count; i++) {
    const PaDeviceInfo *info = Pa_GetDeviceInfo(i);
    if (!usable(info) || !this->host_api_matches_(info)) {
      continue;
    }
    if (strcmp(info->name, name) == 0) {
      return i;
    }
    if (substring_match == paNoDevice && strcasestr(info->name, name) != nullptr) {
      substring_match = i;
    }
  }
  return substring_match;
}

PaSampleFormat HostAudioComponent::sample_format(uint8_t bits_per_sample) {
  // ESPHome audio is signed, little endian and packed, which is PortAudio's native layout on the hosts we build for
  switch (bits_per_sample) {
    case 8:
      return paInt8;
    case 16:
      return paInt16;
    case 24:
      return paInt24;
    case 32:
      return paInt32;
    default:
      return 0;
  }
}

void HostAudioStream::on_shutdown() {
  this->shutdown_ = true;
  this->set_bits_(COMMAND_STOP);
  if (this->worker_.joinable()) {
    this->worker_.join();
  }
}

void HostAudioStream::start_worker_() { this->worker_ = std::thread(&HostAudioStream::worker_loop_, this); }

void HostAudioStream::set_bits_(uint32_t bits) {
  {
    std::lock_guard<std::mutex> lock(this->wait_lock_);
    this->bits_.fetch_or(bits);
  }
  this->wait_cv_.notify_all();
}

void HostAudioStream::set_task_bits_(uint32_t bits) {
  this->bits_.fetch_or(bits);
  this->enable_loop_soon_any_context();
}

void HostAudioStream::wait_for_bits_(uint32_t bits, uint32_t timeout_ms) {
  std::unique_lock<std::mutex> lock(this->wait_lock_);
  this->wait_cv_.wait_for(lock, std::chrono::milliseconds(timeout_ms),
                          [this, bits]() { return this->shutdown_ || this->has_bits_(bits); });
}

void HostAudioStream::worker_loop_() {
  while (!this->shutdown_) {
    this->wait_for_bits_(COMMAND_START, 1000);
    if (this->shutdown_ || !this->has_bits_(COMMAND_START)) {
      continue;
    }
    this->take_bits_(COMMAND_START);
    this->set_task_bits_(TASK_STARTING);
    this->run_session_();
    this->set_task_bits_(TASK_STOPPED);
  }
}

PaDeviceIndex HostAudioStream::find_device_(bool input) const {
  return this->parent_->find_device(this->device_name_, this->device_index_, input);
}

PaTime HostAudioStream::suggested_latency_(PaTime low, PaTime high) const {
  switch (this->latency_mode_) {
    case LatencyMode::LATENCY_MODE_LOW:
      return low;
    case LatencyMode::LATENCY_MODE_CUSTOM:
      return this->latency_ms_ / 1000.0;
    case LatencyMode::LATENCY_MODE_HIGH:
    default:
      return high;
  }
}

void HostAudioStream::dump_device_config_(const char *tag) const {
  if (this->device_index_ >= 0) {
    ESP_LOGCONFIG(tag, "  Device index: %d", this->device_index_);
  } else {
    ESP_LOGCONFIG(tag, "  Device: %s", this->device_name_ != nullptr ? this->device_name_ : "default");
  }
  switch (this->latency_mode_) {
    case LatencyMode::LATENCY_MODE_LOW:
      ESP_LOGCONFIG(tag, "  Latency: low");
      break;
    case LatencyMode::LATENCY_MODE_HIGH:
      ESP_LOGCONFIG(tag, "  Latency: high");
      break;
    case LatencyMode::LATENCY_MODE_CUSTOM:
      ESP_LOGCONFIG(tag, "  Latency: %" PRIu32 " ms", this->latency_ms_);
      break;
  }
}

}  // namespace esphome::host_audio

#endif  // USE_HOST
