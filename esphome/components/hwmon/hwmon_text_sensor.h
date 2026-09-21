#pragma once

#include "esphome/core/defines.h"
#if defined(USE_HOST) && defined(USE_TEXT_SENSOR)

#include "esphome/components/text_sensor/text_sensor.h"
#include "esphome/core/component.h"

#include <string>

namespace esphome::hwmon {

class HwmonTextSensor : public text_sensor::TextSensor, public PollingComponent {
 public:
  HwmonTextSensor(const char *chip, const char *file) : chip_(chip), file_(file) {}

  void setup() override;
  void update() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::DATA; }

 protected:
  const char *chip_;
  const char *file_;
  std::string path_;
};

}  // namespace esphome::hwmon

#endif  // defined(USE_HOST) && defined(USE_TEXT_SENSOR)
