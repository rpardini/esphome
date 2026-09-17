#include "esphome/core/defines.h"
#if defined(USE_HOST) && defined(USE_MDNS)

#include "esphome/components/network/ip_address.h"
#include "esphome/components/network/util.h"
#include "esphome/core/application.h"
#include "esphome/core/helpers.h"
#include "esphome/core/log.h"
#include "mdns_component.h"

#ifdef USE_MDNS_AVAHI
#include <avahi-client/client.h>
#include <avahi-client/publish.h>
#include <avahi-common/alternative.h>
#include <avahi-common/error.h>
#include <avahi-common/malloc.h>
#include <avahi-common/thread-watch.h>

#include <ifaddrs.h>
#include <net/if.h>
#include <netinet/in.h>
#include <strings.h>
#include <unistd.h>

#include <cinttypes>
#include <cstdio>
#include <cstring>
#endif

namespace esphome::mdns {

#ifdef USE_MDNS_AVAHI

static const char *const TAG = "mdns";

// How often the machine's addresses are checked for changes to republish
static constexpr uint32_t ADDRESS_CHECK_INTERVAL_MS = 30000;

// Avahi's C callbacks, forwarded to the component; friends of MDNSComponent
struct AvahiCallbacks {
  static void client(AvahiClient *client, AvahiClientState state, void *userdata) {
    static_cast<MDNSComponent *>(userdata)->avahi_client_changed_(client, state);
  }

  static void service_group(AvahiEntryGroup *group, AvahiEntryGroupState state, void *userdata) {
    auto *comp = static_cast<MDNSComponent *>(userdata);
    // Each service has its own group
    const MDNSService *service = nullptr;
    for (size_t i = 0; i < comp->services_.size(); i++) {
      if (comp->avahi_groups_[i] == group) {
        service = &comp->services_[i];
        break;
      }
    }
    const char *service_type = service != nullptr ? MDNS_STR_ARG(service->service_type) : "?";
    const char *proto = service != nullptr ? MDNS_STR_ARG(service->proto) : "?";
    switch (state) {
      case AVAHI_ENTRY_GROUP_ESTABLISHED:
        ESP_LOGD(TAG, "Service %s.%s published as '%s'", service_type, proto, comp->avahi_instance_name_);
        break;
      case AVAHI_ENTRY_GROUP_COLLISION:
        comp->avahi_rename_services_(avahi_entry_group_get_client(group));
        break;
      case AVAHI_ENTRY_GROUP_FAILURE:
        ESP_LOGW(TAG, "Publishing service %s.%s failed: %s", service_type, proto,
                 avahi_strerror(avahi_client_errno(avahi_entry_group_get_client(group))));
        break;
      default:
        break;
    }
  }

  static void address_group(AvahiEntryGroup *group, AvahiEntryGroupState state, void *userdata) {
    auto *comp = static_cast<MDNSComponent *>(userdata);
    if (state == AVAHI_ENTRY_GROUP_ESTABLISHED) {
      ESP_LOGD(TAG, "Hostname %s published", comp->avahi_host_name_.c_str());
    } else if (state == AVAHI_ENTRY_GROUP_COLLISION || state == AVAHI_ENTRY_GROUP_FAILURE) {
      // Another machine answers for this name: point the services at this machine's own hostname instead
      ESP_LOGW(TAG, "Cannot publish hostname %s (%s); services use the machine's hostname",
               comp->avahi_host_name_.c_str(),
               state == AVAHI_ENTRY_GROUP_COLLISION ? LOG_STR_LITERAL("already in use") : LOG_STR_LITERAL("failed"));
      // Groups are reset rather than freed, since this runs inside a group's own callback
      comp->avahi_host_name_.clear();
      comp->avahi_reset_groups_();
      comp->avahi_publish_all_(avahi_entry_group_get_client(group));
    }
  }
};

bool MDNSComponent::AvahiHostAddress::operator==(const AvahiHostAddress &other) const {
  return this->interface == other.interface && this->protocol == other.protocol &&
         memcmp(this->address, other.address, sizeof(this->address)) == 0;
}

void MDNSComponent::avahi_collect_addresses_(StaticVector<AvahiHostAddress, AVAHI_MAX_ADDRESSES> &addresses) {
  addresses.clear();
  struct ifaddrs *interfaces = nullptr;
  if (getifaddrs(&interfaces) != 0) {
    return;
  }
  for (struct ifaddrs *ifa = interfaces; ifa != nullptr && addresses.size() < AVAHI_MAX_ADDRESSES;
       ifa = ifa->ifa_next) {
    if (ifa->ifa_addr == nullptr || !(ifa->ifa_flags & IFF_UP) || (ifa->ifa_flags & IFF_LOOPBACK)) {
      continue;
    }
    AvahiHostAddress entry{};
    entry.interface = static_cast<int>(if_nametoindex(ifa->ifa_name));
    if (ifa->ifa_addr->sa_family == AF_INET) {
      entry.protocol = AVAHI_PROTO_INET;
      const auto *sin = reinterpret_cast<struct sockaddr_in *>(ifa->ifa_addr);
      memcpy(entry.address, &sin->sin_addr, sizeof(sin->sin_addr));
    } else if (ifa->ifa_addr->sa_family == AF_INET6) {
      entry.protocol = AVAHI_PROTO_INET6;
      const auto *sin6 = reinterpret_cast<struct sockaddr_in6 *>(ifa->ifa_addr);
      memcpy(entry.address, &sin6->sin6_addr, sizeof(sin6->sin6_addr));
    } else {
      continue;
    }
    addresses.push_back(entry);
  }
  freeifaddrs(interfaces);
}

void MDNSComponent::avahi_reset_groups_() {
  for (auto *group : this->avahi_groups_) {
    if (group != nullptr) {
      avahi_entry_group_reset(group);
    }
  }
  if (this->avahi_address_group_ != nullptr) {
    avahi_entry_group_reset(this->avahi_address_group_);
  }
}

void MDNSComponent::avahi_publish_all_(AvahiClient *client) {
  if (!this->avahi_host_name_.empty()) {
    this->avahi_publish_addresses_(client);
  }
  for (size_t i = 0; i < this->services_.size(); i++) {
#ifdef USE_MDNS_SUPPORTS_ENABLE_DISABLE
    if (!this->services_[i].enabled)
      continue;
#endif
    this->avahi_publish_service_(client, i);
  }
}

void MDNSComponent::avahi_publish_addresses_(AvahiClient *client) {
  if (this->avahi_address_group_ == nullptr) {
    this->avahi_address_group_ = avahi_entry_group_new(client, AvahiCallbacks::address_group, this);
    if (this->avahi_address_group_ == nullptr) {
      ESP_LOGW(TAG, "Cannot publish hostname: %s", avahi_strerror(avahi_client_errno(client)));
      return;
    }
  }
  if (!avahi_entry_group_is_empty(this->avahi_address_group_)) {
    return;
  }
  size_t published = 0;
  for (const auto &entry : this->avahi_addresses_) {
    AvahiAddress address{};
    address.proto = entry.protocol;
    memcpy(address.data.data, entry.address, entry.protocol == AVAHI_PROTO_INET ? 4 : 16);
    // No reverse records: Avahi already answers those for the machine's own hostname
    int err = avahi_entry_group_add_address(this->avahi_address_group_, entry.interface, entry.protocol,
                                            AVAHI_PUBLISH_NO_REVERSE, this->avahi_host_name_.c_str(), &address);
    if (err < 0) {
      // Usually an interface Avahi doesn't serve
      ESP_LOGV(TAG, "Skipping an address on interface %d: %s", entry.interface, avahi_strerror(err));
      continue;
    }
    published++;
  }
  if (published > 0) {
    avahi_entry_group_commit(this->avahi_address_group_);
  }
}

void MDNSComponent::avahi_publish_service_(AvahiClient *client, size_t index) {
  AvahiEntryGroup *&group = this->avahi_groups_[index];
  if (group == nullptr) {
    group = avahi_entry_group_new(client, AvahiCallbacks::service_group, this);
    if (group == nullptr) {
      ESP_LOGW(TAG, "Cannot create an Avahi entry group: %s", avahi_strerror(avahi_client_errno(client)));
      return;
    }
  }
  if (!avahi_entry_group_is_empty(group)) {
    return;
  }

  const MDNSService &service = this->services_[index];
  char type[64];
  snprintf(type, sizeof(type), "%s.%s", MDNS_STR_ARG(service.service_type), MDNS_STR_ARG(service.proto));
  AvahiStringList *txt = nullptr;
  for (const auto &record : service.txt_records) {
    txt = avahi_string_list_add_pair(txt, MDNS_STR_ARG(record.key), MDNS_STR_ARG(record.value));
  }
  const char *host = this->avahi_host_name_.empty() ? nullptr : this->avahi_host_name_.c_str();
  int err = avahi_entry_group_add_service_strlst(group, AVAHI_IF_UNSPEC, AVAHI_PROTO_UNSPEC, (AvahiPublishFlags) 0,
                                                 this->avahi_instance_name_, type, nullptr, host,
                                                 this->avahi_ports_[index], txt);
  avahi_string_list_free(txt);
  if (err == AVAHI_ERR_COLLISION) {
    this->avahi_rename_services_(client);
    return;
  }
  if (err < 0) {
    ESP_LOGW(TAG, "Failed to publish service %s: %s", type, avahi_strerror(err));
    return;
  }
  err = avahi_entry_group_commit(group);
  if (err < 0) {
    ESP_LOGW(TAG, "Failed to commit service %s: %s", type, avahi_strerror(err));
  }
}

void MDNSComponent::avahi_rename_services_(AvahiClient *client) {
  char *name = avahi_alternative_service_name(this->avahi_instance_name_);
  ESP_LOGW(TAG, "Service name '%s' is already in use; renaming to '%s'", this->avahi_instance_name_, name);
  avahi_free(this->avahi_instance_name_);
  this->avahi_instance_name_ = name;
  for (auto *group : this->avahi_groups_) {
    if (group != nullptr) {
      avahi_entry_group_reset(group);
    }
  }
  for (size_t i = 0; i < this->services_.size(); i++) {
#ifdef USE_MDNS_SUPPORTS_ENABLE_DISABLE
    if (!this->services_[i].enabled)
      continue;
#endif
    this->avahi_publish_service_(client, i);
  }
}

void MDNSComponent::avahi_client_changed_(AvahiClient *client, int state) {
  switch (state) {
    case AVAHI_CLIENT_S_RUNNING:
      this->avahi_publish_all_(client);
      break;
    case AVAHI_CLIENT_S_COLLISION:
    case AVAHI_CLIENT_S_REGISTERING:
      // The daemon's own hostname is changing; publish again once it is running
      this->avahi_reset_groups_();
      break;
    case AVAHI_CLIENT_CONNECTING:
      ESP_LOGI(TAG, "Waiting for avahi-daemon");
      break;
    case AVAHI_CLIENT_FAILURE: {
      int err = avahi_client_errno(client);
      if (err != AVAHI_ERR_DISCONNECTED) {
        ESP_LOGW(TAG, "Avahi client failed: %s", avahi_strerror(err));
        break;
      }
      ESP_LOGW(TAG, "Lost the connection to avahi-daemon; reconnecting");
      // Freeing the client frees its groups too
      this->avahi_groups_.fill(nullptr);
      this->avahi_address_group_ = nullptr;
      avahi_client_free(client);
      this->avahi_client_ = avahi_client_new(avahi_threaded_poll_get(this->avahi_poll_), AVAHI_CLIENT_NO_FAIL,
                                             AvahiCallbacks::client, this, &err);
      if (this->avahi_client_ == nullptr) {
        ESP_LOGW(TAG, "Reconnecting to avahi-daemon failed: %s", avahi_strerror(err));
      }
      break;
    }
    default:
      break;
  }
}

void MDNSComponent::avahi_check_addresses_() {
  StaticVector<AvahiHostAddress, AVAHI_MAX_ADDRESSES> current;
  avahi_collect_addresses_(current);
  bool changed = current.size() != this->avahi_addresses_.size();
  for (size_t i = 0; !changed && i < current.size(); i++) {
    changed = !(current[i] == this->avahi_addresses_[i]);
  }
  if (!changed) {
    return;
  }
  ESP_LOGD(TAG, "Addresses changed; publishing %s again", this->avahi_host_name_.c_str());
  avahi_threaded_poll_lock(this->avahi_poll_);
  this->avahi_addresses_ = current;
  if (this->avahi_address_group_ != nullptr && !this->avahi_host_name_.empty()) {
    avahi_entry_group_reset(this->avahi_address_group_);
    if (this->avahi_client_ != nullptr && avahi_client_get_state(this->avahi_client_) == AVAHI_CLIENT_S_RUNNING) {
      this->avahi_publish_addresses_(this->avahi_client_);
    }
  }
  avahi_threaded_poll_unlock(this->avahi_poll_);
}

void MDNSComponent::setup() {
  this->setup_buffers_and_register_([](MDNSComponent *comp, StaticVector<MDNSService, MDNS_SERVICE_COUNT> &services) {
    for (size_t i = 0; i < services.size(); i++) {
      comp->avahi_ports_[i] = services[i].port.value();
    }
    const std::string &name = App.get_name();
    comp->avahi_instance_name_ = avahi_strdup(name.c_str());

    // Avahi already publishes the machine's own hostname
    char machine_hostname[256] = {0};
    gethostname(machine_hostname, sizeof(machine_hostname) - 1);
    if (strcasecmp(machine_hostname, name.c_str()) != 0) {
      comp->avahi_host_name_ = name + ".local";
      avahi_collect_addresses_(comp->avahi_addresses_);
      comp->set_interval(ADDRESS_CHECK_INTERVAL_MS, [comp]() { comp->avahi_check_addresses_(); });
    }

    comp->avahi_poll_ = avahi_threaded_poll_new();
    if (comp->avahi_poll_ == nullptr) {
      ESP_LOGW(TAG, "Cannot start the Avahi client; services are not published");
      return;
    }
    int err;
    // The client callback can run inside avahi_client_new(), before the poll thread starts
    comp->avahi_client_ = avahi_client_new(avahi_threaded_poll_get(comp->avahi_poll_), AVAHI_CLIENT_NO_FAIL,
                                           AvahiCallbacks::client, comp, &err);
    if (comp->avahi_client_ == nullptr) {
      ESP_LOGW(TAG, "Cannot start the Avahi client (%s); services are not published", avahi_strerror(err));
      avahi_threaded_poll_free(comp->avahi_poll_);
      comp->avahi_poll_ = nullptr;
      return;
    }
    avahi_threaded_poll_start(comp->avahi_poll_);
  });
}

#ifdef USE_MDNS_SUPPORTS_ENABLE_DISABLE
bool MDNSComponent::set_service_enabled(const char *service_type, const char *proto, bool enabled) {
  if (!this->is_ready()) {
    ESP_LOGW(TAG, "Cannot %s service %s before setup", enabled ? "enable" : "disable", service_type);
    return false;
  }
  for (size_t i = 0; i < this->services_.size(); i++) {
    auto &service = this->services_[i];
    if (strcmp(MDNS_STR_ARG(service.service_type), service_type) != 0 ||
        strcmp(MDNS_STR_ARG(service.proto), proto) != 0) {
      continue;
    }
    if (service.enabled == enabled)
      return true;
    if (this->avahi_poll_ == nullptr) {
      service.enabled = enabled;
      return true;
    }
    const uint16_t port = service.port.value();
    avahi_threaded_poll_lock(this->avahi_poll_);
    service.enabled = enabled;
    if (enabled) {
      this->avahi_ports_[i] = port;
      if (this->avahi_client_ != nullptr && avahi_client_get_state(this->avahi_client_) == AVAHI_CLIENT_S_RUNNING) {
        this->avahi_publish_service_(this->avahi_client_, i);
      }
    } else if (this->avahi_groups_[i] != nullptr) {
      // Freeing the group withdraws the service
      avahi_entry_group_free(this->avahi_groups_[i]);
      this->avahi_groups_[i] = nullptr;
    }
    avahi_threaded_poll_unlock(this->avahi_poll_);
    return true;
  }
  ESP_LOGW(TAG, "Service %s not found", service_type);
  return false;
}
#endif  // USE_MDNS_SUPPORTS_ENABLE_DISABLE

void MDNSComponent::on_shutdown() {
  if (this->avahi_poll_ != nullptr) {
    avahi_threaded_poll_stop(this->avahi_poll_);
  }
  if (this->avahi_client_ != nullptr) {
    // Withdraws every published record
    avahi_client_free(this->avahi_client_);
    this->avahi_client_ = nullptr;
  }
  if (this->avahi_poll_ != nullptr) {
    avahi_threaded_poll_free(this->avahi_poll_);
    this->avahi_poll_ = nullptr;
  }
  avahi_free(this->avahi_instance_name_);
  this->avahi_instance_name_ = nullptr;
}

#else  // USE_MDNS_AVAHI

void MDNSComponent::setup() {
#ifdef USE_MDNS_STORE_SERVICES
#ifdef USE_MDNS_DEVICE_INFO_TXT
  get_mac_address_into_buffer(this->mac_address_);
  char *mac_ptr = this->mac_address_;
  format_hex_to(this->config_hash_str_, App.get_config_hash());
  char *cfg_ptr = this->config_hash_str_;
#else
  char *mac_ptr = nullptr;
  char *cfg_ptr = nullptr;
#endif
  this->compile_records_(this->services_, mac_ptr, cfg_ptr);
#endif
  // Without Avahi the host platform doesn't publish anything
}

void MDNSComponent::on_shutdown() {}

#endif  // USE_MDNS_AVAHI

}  // namespace esphome::mdns

#endif
