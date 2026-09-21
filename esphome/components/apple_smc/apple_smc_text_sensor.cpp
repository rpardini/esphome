#include "esphome/core/defines.h"
#if defined(USE_HOST) && defined(USE_APPLE_SMC) && defined(USE_TEXT_SENSOR)

#include "apple_smc_text_sensor.h"

#include "esphome/core/log.h"

namespace esphome::apple_smc {

static const char *const TAG = "apple_smc.text_sensor";

void AppleSmcTextSensor::setup() {
  if (!smc_describe(this->code_, this->info_)) {
    ESP_LOGE(TAG, "'%s': the controller has no readable key '%s'", this->get_name().c_str(), this->key_);
    this->mark_failed();
  }
}

void AppleSmcTextSensor::update() {
  uint8_t buf[SMC_MAX_VALUE_SIZE];
  if (!smc_read(this->code_, this->info_, buf, sizeof(buf))) {
    ESP_LOGW(TAG, "'%s': cannot read key '%s'", this->get_name().c_str(), this->key_);
    return;
  }
  // The controller pads a string out to the key's length rather than ending it
  size_t len = this->info_.size;
  while (len > 0 && (buf[len - 1] == '\0' || buf[len - 1] == ' '))
    len--;
  this->publish_state(reinterpret_cast<const char *>(buf), len);
}

void AppleSmcTextSensor::dump_config() {
  LOG_TEXT_SENSOR("", "Apple SMC Text Sensor", this);
  ESP_LOGCONFIG(TAG, "  Key: %s", this->key_);
  if (this->is_failed()) {
    ESP_LOGE(TAG, "  Type: not found");
  } else {
    ESP_LOGCONFIG(TAG, "  Type: %s, %u bytes", smc_type_name(this->info_.type),
                  static_cast<unsigned>(this->info_.size));
  }
  LOG_UPDATE_INTERVAL(this);
}

}  // namespace esphome::apple_smc

#endif  // defined(USE_HOST) && defined(USE_APPLE_SMC) && defined(USE_TEXT_SENSOR)
