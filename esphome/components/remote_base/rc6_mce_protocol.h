#pragma once

/* RC6 mode 6A, as sent by Windows Media Center (eHome/MCE) remotes. The built-in rc6 protocol cannot
 * decode these: it only handles mode 0, and its data struct holds an 8-bit address where mode 6A
 * carries a 16-bit customer code.
 *
 * Frame layout, all Manchester coded in units of 444us:
 *
 *   leader (6 units mark, 2 units space)
 *   start bit (1)
 *   3 mode bits (110b = 6)
 *   trailer bit (double width; carries RC6's own toggle, unused by MCE)
 *   32 data bits: 16-bit customer code, then toggle (bit 15) and the command
 *
 * MCE puts its toggle in the payload rather than the trailer bit, which is why its codes are
 * universally written as 0x800F04xx with the toggle masked out: customer code 0x800F, command 0x04xx.
 */

#include "remote_base.h"

namespace esphome::remote_base {

struct RC6MCEData {
  uint16_t address;  // customer code; 0x800F on Media Center remotes
  uint16_t command;  // toggle masked out -- the 0x04xx of 0x800F04xx
  uint8_t toggle;

  // Deliberately ignores the toggle: it flips on every keypress and is what tells a repeat apart
  // from a new press, not part of the button's identity.
  bool operator==(const RC6MCEData &rhs) const { return address == rhs.address && command == rhs.command; }
};

class RC6MCEProtocol : public RemoteProtocol<RC6MCEData> {
 public:
  void encode(RemoteTransmitData *dst, const RC6MCEData &data);
  optional<RC6MCEData> decode(RemoteReceiveData src);
  void dump(const RC6MCEData &data);
};

DECLARE_REMOTE_PROTOCOL(RC6MCE)

template<typename... Ts> class RC6MCEAction : public RemoteTransmitterActionBase<Ts...> {
 public:
  TEMPLATABLE_VALUE(uint16_t, address)
  TEMPLATABLE_VALUE(uint16_t, command)

  void encode(RemoteTransmitData *dst, Ts... x) {
    RC6MCEData data{};
    data.address = this->address_.value(x...);
    data.command = this->command_.value(x...);
    data.toggle = this->toggle_;
    RC6MCEProtocol().encode(dst, data);
    this->toggle_ = !this->toggle_;
  }

 protected:
  uint8_t toggle_{0};
};

}  // namespace esphome::remote_base
