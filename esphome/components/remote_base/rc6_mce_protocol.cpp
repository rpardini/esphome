#include "rc6_mce_protocol.h"
#include "esphome/core/log.h"

namespace esphome::remote_base {

static const char *const RC6_MCE_TAG = "remote.rc6_mce";

// Timings and the Manchester walk are RC6's and match the built-in rc6 protocol; only the mode
// accepted and the payload length differ.
static constexpr uint16_t RC6_MCE_FREQ = 36000;
static constexpr uint16_t RC6_MCE_UNIT = 444;
static constexpr uint16_t RC6_MCE_HEADER_MARK = (6 * RC6_MCE_UNIT);
static constexpr uint16_t RC6_MCE_HEADER_SPACE = (2 * RC6_MCE_UNIT);

static constexpr uint8_t RC6_MCE_START_BIT = 0x08;
static constexpr uint8_t RC6_MCE_MODE_MASK = 0x07;
static constexpr uint8_t RC6_MCE_MODE = 6;

static constexpr uint8_t RC6_MCE_DATA_BITS = 32;
// Set in the customer code when the long, 15-bit form is used; the short form makes for a 24-bit
// frame that this protocol does not read.
static constexpr uint16_t RC6_MCE_LONG_CUSTOMER_FLAG = 0x8000;
static constexpr uint16_t RC6_MCE_TOGGLE_MASK = 0x8000;

void RC6MCEProtocol::encode(RemoteTransmitData *dst, const RC6MCEData &data) {
  dst->reserve(76);
  dst->set_carrier_frequency(RC6_MCE_FREQ);

  dst->item(RC6_MCE_HEADER_MARK, RC6_MCE_HEADER_SPACE);

  int32_t next{0};

  // Manchester encoder: `next` carries the pending half-symbol, positive for a mark and negative
  // for a space, so that two like halves in a row are emitted as one double-length pulse.
  auto encode_bit = [&](bool bit, uint16_t unit) {
    if (bit) {
      if (next < 0) {
        dst->space(-next);
        next = 0;
      }
      next = next + unit;
      dst->mark(next);
      next = -unit;
    } else {
      if (next > 0) {
        dst->mark(next);
        next = 0;
      }
      next = next - unit;
      dst->space(-next);
      next = unit;
    }
  };

  // Start bit and mode
  const uint8_t header = RC6_MCE_START_BIT | RC6_MCE_MODE;
  for (uint8_t mask = 0x8; mask; mask >>= 1) {
    encode_bit(header & mask, RC6_MCE_UNIT);
  }

  // Trailer bit, double width. MCE ignores it on receive; send it as zero.
  encode_bit(false, RC6_MCE_UNIT * 2);

  const uint32_t raw =
      (static_cast<uint32_t>(data.address) << 16) | (data.toggle ? RC6_MCE_TOGGLE_MASK : 0) | data.command;

  for (uint32_t mask = 0x80000000; mask; mask >>= 1) {
    encode_bit(raw & mask, RC6_MCE_UNIT);
  }

  if (next > 0) {
    dst->mark(next);
  } else {
    dst->space(-next);
  }
}

optional<RC6MCEData> RC6MCEProtocol::decode(RemoteReceiveData src) {
  RC6MCEData data{
      .address = 0,
      .command = 0,
      .toggle = 0,
  };

  if (!src.expect_item(RC6_MCE_HEADER_MARK, RC6_MCE_HEADER_SPACE)) {
    return {};
  }

  uint8_t bit{1};
  uint8_t offset{0};
  uint8_t header{0};
  uint32_t buffer{0};

  // Start bit and mode
  while (offset < 4) {
    bit = src.peek() > 0;
    header = header + (bit << (3 - offset++));
    src.advance();

    if (src.peek_mark(RC6_MCE_UNIT) || src.peek_space(RC6_MCE_UNIT)) {
      src.advance();
    } else if (offset == 4) {
      break;
    } else if (!src.peek_mark(RC6_MCE_UNIT * 2) && !src.peek_space(RC6_MCE_UNIT * 2)) {
      return {};
    }
  }

  if (!(header & RC6_MCE_START_BIT) || ((header & RC6_MCE_MODE_MASK) != RC6_MCE_MODE)) {
    return {};  // The built-in rc6 protocol handles mode 0
  }

  // Trailer bit, double width. Consumed but not used: MCE's toggle is in the payload.
  src.advance();
  if (src.peek_mark(RC6_MCE_UNIT * 2) || src.peek_space(RC6_MCE_UNIT * 2)) {
    src.advance();
  }

  offset = 0;
  while (offset < RC6_MCE_DATA_BITS) {
    bit = src.peek() > 0;
    buffer |= static_cast<uint32_t>(bit) << (RC6_MCE_DATA_BITS - 1 - offset++);
    src.advance();

    if (offset == RC6_MCE_DATA_BITS) {
      break;
    } else if (src.peek_mark(RC6_MCE_UNIT) || src.peek_space(RC6_MCE_UNIT)) {
      src.advance();
    } else if (!src.peek_mark(RC6_MCE_UNIT * 2) && !src.peek_space(RC6_MCE_UNIT * 2)) {
      return {};
    }
  }

  const uint16_t customer_code = buffer >> 16;
  if (!(customer_code & RC6_MCE_LONG_CUSTOMER_FLAG)) {
    return {};  // Short-form mode 6A: a 7-bit customer code and a shorter frame than was read
  }

  const uint16_t payload = buffer & 0xFFFF;
  data.address = customer_code;
  data.toggle = (payload & RC6_MCE_TOGGLE_MASK) ? 1 : 0;
  data.command = payload & ~RC6_MCE_TOGGLE_MASK;
  return data;
}

void RC6MCEProtocol::dump(const RC6MCEData &data) {
  ESP_LOGI(RC6_MCE_TAG, "Received RC6 MCE: address=0x%04X, command=0x%04X, toggle=%u", data.address, data.command,
           data.toggle);
}

}  // namespace esphome::remote_base
