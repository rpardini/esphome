#ifdef USE_HOST
#if defined(__linux__)

#include "hwmon_text_sensor.h"

#include "esphome/core/log.h"
#include "hwmon.h"

namespace esphome::hwmon {

ESPHOME_LOG_TAG(TAG, "hwmon.text_sensor");

void HwmonTextSensor::setup() {
  if (!resolve_path(this->chip_, this->file_, this->path_)) {
    this->mark_failed();
  }
}

void HwmonTextSensor::update() {
  char buf[128];
  int len = read_attribute(this->path_, buf, sizeof(buf));
  if (len < 0) {
    ESP_LOGW(TAG, "'%s': cannot read %s", LOG_STR_ARG(this->get_log_name()), this->path_.c_str());
    return;
  }
  this->publish_state(buf, static_cast<size_t>(len));
}

void HwmonTextSensor::dump_config() {
  LOG_TEXT_SENSOR("", "hwmon Text Sensor", this);
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
#endif  // USE_HOST
