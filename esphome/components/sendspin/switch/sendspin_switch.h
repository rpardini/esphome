#pragma once

#include "esphome/core/defines.h"

#if defined(USE_ESP32) || defined(USE_HOST)

#include "esphome/components/sendspin/sendspin_hub.h"
#include "esphome/components/switch/switch.h"

namespace esphome::sendspin_ {

/// @brief Switch that starts and stops the Sendspin client through the hub (see SendspinHub::set_enabled()).
class SendspinSwitch final : public switch_::Switch, public SendspinChild {
 public:
  void setup() override;
  void dump_config() override;

 protected:
  void write_state(bool state) override;
};

}  // namespace esphome::sendspin_

#endif  // USE_ESP32 || USE_HOST
