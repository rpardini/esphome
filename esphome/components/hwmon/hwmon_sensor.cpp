#include "esphome/core/defines.h"
#if defined(USE_HOST) && defined(USE_SENSOR)
#if defined(__linux__)

#include "hwmon_sensor.h"

#include "esphome/core/log.h"
#include "hwmon.h"

#include <cmath>
#include <cstdlib>

namespace esphome::hwmon {

static const char *const TAG = "hwmon.sensor";

void HwmonSensor::setup() {
  if (!resolve_path(this->chip_, this->file_, this->path_)) {
    this->mark_failed();
  }
}

void HwmonSensor::update() {
  char buf[32];
  if (read_attribute(this->path_, buf, sizeof(buf)) < 0) {
    ESP_LOGW(TAG, "'%s': cannot read %s", this->get_name().c_str(), this->path_.c_str());
    this->publish_state(NAN);
    return;
  }
  char *end = nullptr;
  double raw = strtod(buf, &end);
  if (end == buf) {
    ESP_LOGW(TAG, "'%s': '%s' is not a number", this->get_name().c_str(), buf);
    this->publish_state(NAN);
    return;
  }
  this->publish_state(static_cast<float>(raw * this->multiplier_));
}

void HwmonSensor::dump_config() {
  LOG_SENSOR("", "hwmon Sensor", this);
  ESP_LOGCONFIG(TAG, "  Chip: %s", this->chip_);
  ESP_LOGCONFIG(TAG, "  Attribute: %s", this->file_);
  if (this->path_.empty()) {
    ESP_LOGE(TAG, "  Path: not found");
  } else {
    ESP_LOGCONFIG(TAG, "  Path: %s", this->path_.c_str());
  }
  LOG_UPDATE_INTERVAL(this);
}

}  // namespace esphome::hwmon

#else  // defined(__linux__)
#error "hwmon is only supported on Linux"
#endif  // defined(__linux__)
#endif  // defined(USE_HOST) && defined(USE_SENSOR)
