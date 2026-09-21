#pragma once

#include "esphome/core/defines.h"
#if defined(USE_HOST) && defined(USE_APPLE_SMC) && defined(USE_TEXT_SENSOR)

#include "apple_smc.h"

#include "esphome/components/text_sensor/text_sensor.h"
#include "esphome/core/component.h"

namespace esphome::apple_smc {

class AppleSmcTextSensor : public text_sensor::TextSensor, public PollingComponent {
 public:
  explicit AppleSmcTextSensor(const char *key) : key_(key), code_(smc_key_code(key)) {}

  void setup() override;
  void update() override;
  void dump_config() override;

 protected:
  const char *key_;
  uint32_t code_;
  SmcKeyInfo info_;
};

}  // namespace esphome::apple_smc

#endif  // defined(USE_HOST) && defined(USE_APPLE_SMC) && defined(USE_TEXT_SENSOR)
