#pragma once

#ifdef USE_HOST

#include "esphome/components/sensor/sensor.h"
#include "esphome/core/component.h"

#include <string>

namespace esphome::hwmon {

class HwmonSensor : public sensor::Sensor, public PollingComponent {
 public:
  HwmonSensor(const char *chip, const char *file, float multiplier)
      : chip_(chip), file_(file), multiplier_(multiplier) {}

  void setup() override;
  void update() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::DATA; }

 protected:
  const char *chip_;
  const char *file_;
  float multiplier_;
  std::string path_;
};

}  // namespace esphome::hwmon

#endif  // USE_HOST
