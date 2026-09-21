#include "esphome/core/defines.h"
#if defined(USE_HOST) && defined(USE_APPLE_SMC) && defined(USE_SENSOR)

#include "apple_smc_sensor.h"

#include "esphome/core/log.h"

#include <cmath>

namespace esphome::apple_smc {

static const char *const TAG = "apple_smc.sensor";

void AppleSmcSensor::setup() {
  if (!smc_describe(this->code_, this->info_)) {
    ESP_LOGE(TAG, "'%s': the controller has no readable key '%s'", this->get_name().c_str(), this->key_);
    this->mark_failed();
  }
}

void AppleSmcSensor::update() {
  uint8_t buf[SMC_MAX_VALUE_SIZE];
  float value;
  if (!smc_read(this->code_, this->info_, buf, sizeof(buf)) || !smc_decode(this->info_, buf, value)) {
    ESP_LOGW(TAG, "'%s': cannot read key '%s'", this->get_name().c_str(), this->key_);
    this->publish_state(NAN);
    return;
  }
  this->publish_state(value);
}

void AppleSmcSensor::dump_config() {
  LOG_SENSOR("", "Apple SMC Sensor", this);
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

#endif  // defined(USE_HOST) && defined(USE_APPLE_SMC) && defined(USE_SENSOR)
