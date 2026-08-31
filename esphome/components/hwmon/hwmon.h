#pragma once

#ifdef USE_HOST

#include <cstddef>
#include <string>

namespace esphome::hwmon {

/// Resolve a chip name (as found in /sys/class/hwmon/<*>/name) and an attribute file
/// name into a full path. Returns false and leaves path_out untouched when either the
/// chip or the attribute is missing. Meant for setup() only; it allocates.
bool resolve_path(const char *chip, const char *file, std::string &path_out);

/// Read a sysfs attribute into buf, stripping trailing whitespace and null terminating.
/// Returns the number of bytes stored, or -1 on error.
int read_attribute(const std::string &path, char *buf, size_t buf_len);

}  // namespace esphome::hwmon

#endif  // USE_HOST
