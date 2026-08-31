#ifdef USE_HOST
#if defined(__linux__)

#include "hwmon.h"

#include "esphome/core/defines.h"
#include "esphome/core/log.h"

#include <dirent.h>
#include <fcntl.h>
#include <unistd.h>
#include <algorithm>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace esphome::hwmon {

static const char *const TAG = "hwmon";
static const char *const HWMON_ROOT = "/sys/class/hwmon";
static const size_t HWMON_PREFIX_LEN = 5;  // strlen("hwmon")

int read_attribute(const std::string &path, char *buf, size_t buf_len) {
  if (buf_len == 0)
    return -1;
  int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC);
  if (fd == -1)
    return -1;
  ssize_t count = ::read(fd, buf, buf_len - 1);
  ::close(fd);
  if (count < 0)
    return -1;
  while (count > 0 && (buf[count - 1] == '\n' || buf[count - 1] == '\r' || buf[count - 1] == ' '))
    count--;
  buf[count] = '\0';
  return static_cast<int>(count);
}

static bool is_readable(const std::string &path) { return ::access(path.c_str(), R_OK) == 0; }

// Collects the hwmonN indices whose "name" file matches chip, lowest first.
static std::vector<int> find_chips(const char *chip) {
  std::vector<int> matches;
  DIR *dir = ::opendir(HWMON_ROOT);
  if (dir == nullptr) {
    ESP_LOGE(TAG, "Cannot open %s: %s", HWMON_ROOT, strerror(errno));
    return matches;
  }
  char name[64];
  while (const struct dirent *entry = ::readdir(dir)) {
    if (strncmp(entry->d_name, "hwmon", HWMON_PREFIX_LEN) != 0)
      continue;
    const char *digits = entry->d_name + HWMON_PREFIX_LEN;
    char *end = nullptr;
    long index = strtol(digits, &end, 10);
    if (end == digits || *end != '\0')
      continue;
    std::string name_path = std::string(HWMON_ROOT) + "/" + entry->d_name + "/name";
    if (read_attribute(name_path, name, sizeof(name)) < 0)
      continue;
    if (strcmp(name, chip) == 0)
      matches.push_back(static_cast<int>(index));
  }
  ::closedir(dir);
  std::sort(matches.begin(), matches.end());
  return matches;
}

bool resolve_path(const char *chip, const char *file, std::string &path_out) {
  std::vector<int> matches = find_chips(chip);
  if (matches.empty()) {
    ESP_LOGW(TAG, "Chip '%s' not found under %s", chip, HWMON_ROOT);
    return false;
  }
  if (matches.size() > 1) {
    ESP_LOGW(TAG, "Chip '%s' matches %zu devices; using the lowest numbered one", chip, matches.size());
  }
  for (int index : matches) {
    std::string base = std::string(HWMON_ROOT) + "/hwmon" + std::to_string(index);
    std::string candidate = base + "/" + file;
    if (is_readable(candidate)) {
      path_out = candidate;
      return true;
    }
    // Older drivers keep the attributes one level down
    candidate = base + "/device/" + file;
    if (is_readable(candidate)) {
      path_out = candidate;
      return true;
    }
  }
  ESP_LOGW(TAG, "Chip '%s' has no readable attribute '%s'", chip, file);
  return false;
}

}  // namespace esphome::hwmon

#else  // defined(__linux__)
#error "hwmon is only supported on Linux"
#endif  // defined(__linux__)
#endif  // USE_HOST
