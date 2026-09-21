#pragma once

#include "esphome/core/defines.h"
#if defined(USE_HOST) && defined(USE_APPLE_SMC)

#include <cstddef>
#include <cstdint>

namespace esphome::apple_smc {

/// How the bytes of a key are to be read.
///
/// Resolved at setup() from what the controller reports rather than fixed when
/// the C++ is generated, because it differs between machines: the same
/// temperature is sp78 on an Intel Mac and flt on Apple Silicon.
enum class SmcDataType : uint8_t {
  SMC_DATA_TYPE_UNSUPPORTED = 0,
  SMC_DATA_TYPE_FLOAT,     // flt: little-endian IEEE 754
  SMC_DATA_TYPE_UNSIGNED,  // ui8 ... ui64, and fpXY: big-endian
  SMC_DATA_TYPE_SIGNED,    // si8 ... si64, and spXY: big-endian
  SMC_DATA_TYPE_IO_FLOAT,  // ioft: little-endian, always 16 fraction bits
  SMC_DATA_TYPE_FLAG,      // flag: one byte, 0 or 1
  SMC_DATA_TYPE_STRING,    // ch8*: ASCII, not null terminated
};

/// What the controller says about one key.
struct SmcKeyInfo {
  SmcDataType type{SmcDataType::SMC_DATA_TYPE_UNSUPPORTED};
  uint8_t size{0};
  /// Fixed point types carry their fraction bit count in their name: sp78 is
  /// 8, fpe2 is 2. Zero for the plain integer types.
  uint8_t fraction_bits{0};
};

/// Longest value one read can carry.
static constexpr size_t SMC_MAX_VALUE_SIZE = 32;

/// Turn a four character key such as "TCMz" into the number the controller
/// wants. The controller takes it as a plain integer, so on a little-endian
/// machine the four characters sit in memory back to front; getting that wrong
/// makes every single key come back as not found.
inline uint32_t smc_key_code(const char *key) {
  return (static_cast<uint32_t>(static_cast<uint8_t>(key[0])) << 24) |
         (static_cast<uint32_t>(static_cast<uint8_t>(key[1])) << 16) |
         (static_cast<uint32_t>(static_cast<uint8_t>(key[2])) << 8) |
         static_cast<uint32_t>(static_cast<uint8_t>(key[3]));
}

/// Ask the controller about a key. Returns false when the key is missing, or
/// when its type is one this component cannot read. Opens the connection on
/// first use, so it is meant for setup().
bool smc_describe(uint32_t key, SmcKeyInfo &info_out);

/// Read a key's raw bytes into buf, which must hold at least info.size bytes.
bool smc_read(uint32_t key, const SmcKeyInfo &info, uint8_t *buf, size_t buf_len);

/// Turn raw bytes into a number, following info.
bool smc_decode(const SmcKeyInfo &info, const uint8_t *buf, float &value_out);

/// A short label for a type, for logging. Points into static storage.
const char *smc_type_name(SmcDataType type);

}  // namespace esphome::apple_smc

#endif  // defined(USE_HOST) && defined(USE_APPLE_SMC)
