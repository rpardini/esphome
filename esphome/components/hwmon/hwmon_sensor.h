#pragma once

#include "esphome/core/defines.h"
#if defined(USE_HOST) && defined(USE_SENSOR)

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

#endif  // defined(USE_HOST) && defined(USE_SENSOR)
