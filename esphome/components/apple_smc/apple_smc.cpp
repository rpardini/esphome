#include "esphome/core/defines.h"
#if defined(USE_HOST) && defined(USE_APPLE_SMC)

#include "apple_smc.h"

#include "esphome/core/log.h"

#include <IOKit/IOKitLib.h>

#include <cstring>

namespace esphome::apple_smc {

static const char *const TAG = "apple_smc";

// The user client offers a single method; what to do travels inside the request
static const uint32_t SELECTOR_KERNEL_INDEX = 2;
static const uint8_t CMD_READ_BYTES = 5;
static const uint8_t CMD_READ_KEYINFO = 9;

// The request and the response share this layout. It is the controller's, not
// ours: the field order and the padding are what the user client expects.
struct SMCVersion {
  uint8_t major;
  uint8_t minor;
  uint8_t build;
  uint8_t reserved[1];
  uint16_t release;
};

struct SMCPLimitData {
  uint16_t version;
  uint16_t length;
  uint32_t cpu_plimit;
  uint32_t gpu_plimit;
  uint32_t mem_plimit;
};

struct SMCKeyInfoData {
  uint32_t data_size;
  uint32_t data_type;
  uint8_t data_attributes;
};

struct SMCKeyData {
  uint32_t key;
  SMCVersion version;
  SMCPLimitData plimit_data;
  SMCKeyInfoData key_info;
  uint8_t result;
  uint8_t status;
  uint8_t data8;
  uint32_t data32;
  uint8_t bytes[32];
};

static_assert(sizeof(SMCKeyData) == 80, "the controller expects an 80 byte request");

static io_connect_t connection = IO_OBJECT_NULL;
static bool connection_tried = false;

static io_connect_t get_connection() {
  if (connection_tried)
    return connection;
  connection_tried = true;
  io_service_t service = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("AppleSMC"));
  if (service == IO_OBJECT_NULL) {
    ESP_LOGE(TAG, "The AppleSMC service is not present");
    return connection;
  }
  kern_return_t err = IOServiceOpen(service, mach_task_self(), 0, &connection);
  IOObjectRelease(service);
  if (err != KERN_SUCCESS) {
    ESP_LOGE(TAG, "Cannot open the AppleSMC user client: 0x%x", err);
    connection = IO_OBJECT_NULL;
  }
  return connection;
}

static bool smc_call(uint32_t key, uint8_t command, uint32_t size, SMCKeyData &response) {
  io_connect_t conn = get_connection();
  if (conn == IO_OBJECT_NULL)
    return false;
  SMCKeyData request{};
  request.key = key;
  request.key_info.data_size = size;
  request.data8 = command;
  size_t response_size = sizeof(response);
  if (IOConnectCallStructMethod(conn, SELECTOR_KERNEL_INDEX, &request, sizeof(request), &response, &response_size) !=
      KERN_SUCCESS)
    return false;
  // A non-zero result means the controller refused: a missing key, or one that
  // exists but will not be read
  return response.result == 0;
}

static int hex_digit(char digit) {
  if (digit >= '0' && digit <= '9')
    return digit - '0';
  if (digit >= 'a' && digit <= 'f')
    return digit - 'a' + 10;
  return -1;
}

static SmcDataType classify_type(uint32_t type_code, uint8_t &fraction_bits) {
  char name[5];
  name[0] = static_cast<char>(type_code >> 24);
  name[1] = static_cast<char>(type_code >> 16);
  name[2] = static_cast<char>(type_code >> 8);
  name[3] = static_cast<char>(type_code);
  name[4] = '\0';
  size_t len = 4;
  while (len > 0 && name[len - 1] == ' ')
    name[--len] = '\0';

  fraction_bits = 0;
  if (strcmp(name, "flt") == 0)
    return SmcDataType::SMC_DATA_TYPE_FLOAT;
  if (strcmp(name, "ioft") == 0)
    return SmcDataType::SMC_DATA_TYPE_IO_FLOAT;
  if (strcmp(name, "flag") == 0)
    return SmcDataType::SMC_DATA_TYPE_FLAG;
  if (strcmp(name, "ch8*") == 0)
    return SmcDataType::SMC_DATA_TYPE_STRING;
  if (name[1] == 'i' && (name[0] == 'u' || name[0] == 's'))
    return name[0] == 'u' ? SmcDataType::SMC_DATA_TYPE_UNSIGNED : SmcDataType::SMC_DATA_TYPE_SIGNED;
  // The fixed point family spells its integer and fraction bit counts out in
  // hex: sp78 is 7.8 signed, fpe2 is 14.2 unsigned
  if (len == 4 && name[1] == 'p' && (name[0] == 'f' || name[0] == 's')) {
    int bits = hex_digit(name[3]);
    if (bits < 0)
      return SmcDataType::SMC_DATA_TYPE_UNSUPPORTED;
    fraction_bits = static_cast<uint8_t>(bits);
    return name[0] == 'f' ? SmcDataType::SMC_DATA_TYPE_UNSIGNED : SmcDataType::SMC_DATA_TYPE_SIGNED;
  }
  return SmcDataType::SMC_DATA_TYPE_UNSUPPORTED;
}

const char *smc_type_name(SmcDataType type) {
  switch (type) {
    case SmcDataType::SMC_DATA_TYPE_FLOAT:
      return "float";
    case SmcDataType::SMC_DATA_TYPE_UNSIGNED:
      return "unsigned";
    case SmcDataType::SMC_DATA_TYPE_SIGNED:
      return "signed";
    case SmcDataType::SMC_DATA_TYPE_IO_FLOAT:
      return "io float";
    case SmcDataType::SMC_DATA_TYPE_FLAG:
      return "flag";
    case SmcDataType::SMC_DATA_TYPE_STRING:
      return "string";
    default:
      return "unsupported";
  }
}

bool smc_describe(uint32_t key, SmcKeyInfo &info_out) {
  SMCKeyData response{};
  if (!smc_call(key, CMD_READ_KEYINFO, 0, response))
    return false;
  uint32_t size = response.key_info.data_size;
  if (size == 0 || size > SMC_MAX_VALUE_SIZE)
    return false;
  uint8_t fraction_bits = 0;
  SmcDataType type = classify_type(response.key_info.data_type, fraction_bits);
  if (type == SmcDataType::SMC_DATA_TYPE_UNSUPPORTED)
    return false;
  info_out.type = type;
  info_out.size = static_cast<uint8_t>(size);
  info_out.fraction_bits = fraction_bits;
  return true;
}

bool smc_read(uint32_t key, const SmcKeyInfo &info, uint8_t *buf, size_t buf_len) {
  if (info.size == 0 || info.size > buf_len)
    return false;
  SMCKeyData response{};
  if (!smc_call(key, CMD_READ_BYTES, info.size, response))
    return false;
  memcpy(buf, response.bytes, info.size);
  return true;
}

bool smc_decode(const SmcKeyInfo &info, const uint8_t *buf, float &value_out) {
  switch (info.type) {
    case SmcDataType::SMC_DATA_TYPE_FLOAT: {
      if (info.size != sizeof(float))
        return false;
      // Every Mac this runs on is little-endian, which is the order the
      // controller uses for floats. Its integers go the other way round.
      float value;
      memcpy(&value, buf, sizeof(value));
      value_out = value;
      return true;
    }
    case SmcDataType::SMC_DATA_TYPE_IO_FLOAT: {
      if (info.size != sizeof(uint64_t))
        return false;
      uint64_t raw;
      memcpy(&raw, buf, sizeof(raw));
      value_out = static_cast<float>(static_cast<double>(raw) / 65536.0);
      return true;
    }
    case SmcDataType::SMC_DATA_TYPE_FLAG:
      value_out = buf[0] != 0 ? 1.0f : 0.0f;
      return true;
    case SmcDataType::SMC_DATA_TYPE_UNSIGNED:
    case SmcDataType::SMC_DATA_TYPE_SIGNED: {
      if (info.size > sizeof(uint64_t))
        return false;
      uint64_t raw = 0;
      for (uint8_t i = 0; i < info.size; i++)
        raw = (raw << 8) | buf[i];
      double value = static_cast<double>(raw);
      if (info.type == SmcDataType::SMC_DATA_TYPE_SIGNED && info.size < sizeof(uint64_t) && (buf[0] & 0x80) != 0)
        value -= static_cast<double>(uint64_t{1} << (8 * info.size));
      value_out = static_cast<float>(value / static_cast<double>(uint32_t{1} << info.fraction_bits));
      return true;
    }
    default:
      return false;
  }
}

}  // namespace esphome::apple_smc

#endif  // defined(USE_HOST) && defined(USE_APPLE_SMC)
