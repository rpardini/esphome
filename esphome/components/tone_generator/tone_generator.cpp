#include "tone_generator.h"

#include "esphome/core/log.h"

#include <array>
#include <cmath>

namespace esphome::tone_generator {

static const char *const TAG = "tone_generator";

// Milliseconds of audio generated per loop. Comfortably more than the ~16 ms loop interval, so the
// speaker's buffer stays ahead and the short write it returns is what paces us.
static constexpr uint32_t CHUNK_MS = 20;

static constexpr size_t SINE_TABLE_SIZE = 256;
static constexpr double PI = 3.14159265358979323846;

// std::sin is not constexpr, so the table is built with a Taylor series about zero, after folding the
// argument into the first quadrant. The residual is far below one count of a 16-bit sample.
constexpr double taylor_sin(double x) {
  double term = x;
  double sum = x;
  for (int n = 1; n <= 6; n++) {
    term *= -x * x / ((2 * n) * (2 * n + 1));
    sum += term;
  }
  return sum;
}

constexpr double unit_sin(double x) {
  if (x >= PI) {
    return -unit_sin(x - PI);
  }
  return (x > PI / 2) ? taylor_sin(PI - x) : taylor_sin(x);
}

constexpr std::array<int16_t, SINE_TABLE_SIZE> make_sine_table() {
  std::array<int16_t, SINE_TABLE_SIZE> table{};
  for (size_t i = 0; i < SINE_TABLE_SIZE; i++) {
    table[i] = static_cast<int16_t>(unit_sin(2.0 * PI * i / SINE_TABLE_SIZE) * INT16_MAX);
  }
  return table;
}

// One period of a full-scale sine, in flash. A table with a phase accumulator is used rather than
// calling sinf() per sample, which at 48 kHz would cost a noticeable share of a core, and rather than
// a recursive oscillator, whose error grows too quickly at the very low frequencies this is for.
static constexpr std::array<int16_t, SINE_TABLE_SIZE> SINE_TABLE = make_sine_table();

void ToneGenerator::setup() {
  this->chunk_bytes_ = this->stream_info_.ms_to_bytes(CHUNK_MS);
  this->chunk_ = std::make_unique<uint8_t[]>(this->chunk_bytes_);
  if (this->chunk_ == nullptr) {
    this->mark_failed();
    return;
  }
  this->update_parameters_();
}

void ToneGenerator::loop() {
  if (!this->ensure_speaker_running_()) {
    return;
  }

  this->update_parameters_();
  this->generate_(this->stream_info_.bytes_to_frames(this->chunk_bytes_));

  const size_t bytes_written = this->speaker_->play(this->chunk_.get(), this->chunk_bytes_);

  // Advance only over the frames the speaker took. Carrying on past the ones it dropped would step
  // the waveform once per loop, which is audible as a click even on an otherwise inaudible tone.
  this->phase_ += this->phase_increment_ * this->stream_info_.bytes_to_frames(bytes_written);
}

bool ToneGenerator::ensure_speaker_running_() {
  if (this->speaker_->is_running()) {
    return true;
  }
  if (this->speaker_->is_stopped()) {
    // Speakers latch the format when they start, so it has to be set while stopped.
    this->speaker_->set_audio_stream_info(this->stream_info_);
    this->speaker_->start();
  }
  // start() is asynchronous on every speaker; pick the tone up again once it is running.
  return false;
}

void ToneGenerator::update_parameters_() {
  const float frequency = this->frequency_.value();
  if (frequency != this->last_frequency_) {
    this->last_frequency_ = frequency;
    const double step = static_cast<double>(frequency) / this->stream_info_.get_sample_rate();
    this->phase_increment_ = static_cast<uint32_t>(llround(step * 4294967296.0));
  }

  const float amplitude = clamp(this->amplitude_.value(), 0.0f, 1.0f);
  if (amplitude != this->last_amplitude_) {
    this->last_amplitude_ = amplitude;
    this->amplitude_q15_ = static_cast<int32_t>(lroundf(amplitude * INT16_MAX));
  }
}

void ToneGenerator::generate_(size_t frames) {
  const size_t bytes_per_sample = this->stream_info_.samples_to_bytes(1);
  const uint8_t channels = this->stream_info_.get_channels();
  uint8_t *out = this->chunk_.get();
  uint32_t phase = this->phase_;

  for (size_t frame = 0; frame < frames; frame++) {
    const uint32_t index = phase >> 24;
    const int32_t frac = static_cast<int32_t>((phase >> 8) & 0xFFFF);
    const int32_t first = SINE_TABLE[index];
    const int32_t second = SINE_TABLE[(index + 1) % SINE_TABLE_SIZE];
    const int32_t interpolated = first + (((second - first) * frac) >> 16);
    const int32_t sample = (interpolated * this->amplitude_q15_) >> 15;
    // Left-justify the 16-bit sample into Q31, which is the format pack_q31_as_audio_sample() narrows
    // back down to whatever bit depth the stream uses.
    const int32_t q31_sample = static_cast<int32_t>(static_cast<uint32_t>(sample) << 16);

    for (uint8_t channel = 0; channel < channels; channel++) {
      audio::pack_q31_as_audio_sample(q31_sample, out, bytes_per_sample);
      out += bytes_per_sample;
    }
    phase += this->phase_increment_;
  }
}

void ToneGenerator::dump_config() {
  ESP_LOGCONFIG(TAG,
                "Tone generator:\n"
                "  Frequency: %.1f Hz\n"
                "  Amplitude: %.0f%%\n"
                "  Sample rate: %" PRIu32 "\n"
                "  Bits per sample: %u\n"
                "  Channels: %u",
                this->last_frequency_, this->last_amplitude_ * 100.0f, this->stream_info_.get_sample_rate(),
                static_cast<unsigned>(this->stream_info_.get_bits_per_sample()),
                static_cast<unsigned>(this->stream_info_.get_channels()));
}

}  // namespace esphome::tone_generator
