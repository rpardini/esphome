#include "esphome/core/defines.h"
#if defined(USE_HOST) && defined(USE_MDNS) && defined(USE_MDNS_BONJOUR)

#include "esphome/core/application.h"
#include "esphome/core/helpers.h"
#include "esphome/core/log.h"
#include "mdns_component.h"

#include <dns_sd.h>

#include <arpa/inet.h>
#include <strings.h>
#include <sys/select.h>
#include <unistd.h>

#include <cstdio>
#include <cstring>

namespace esphome::mdns {

static const char *const TAG = "mdns";

// How often the machine's addresses are checked for changes to republish
static constexpr uint32_t ADDRESS_CHECK_INTERVAL_MS = 30000;
// How often the shared connection is read; mDNSResponder's replies are not time critical
static constexpr uint32_t POLL_INTERVAL_MS = 100;
// Replies read in one pass, so a stuck connection cannot hold the loop
static constexpr int MAX_REPLIES_PER_POLL = 8;
// Largest TXT record published for one service; built on the stack, never on the heap
static constexpr size_t TXT_BUFFER_SIZE = 512;
// Scheduler id of the address re-check, so it can be cancelled when the hostname is withdrawn
static constexpr uint32_t ADDRESS_CHECK_ID = 0;

// Bonjour's C callbacks, forwarded to the component; friends of MDNSComponent
struct BonjourCallbacks {
  static void service(DNSServiceRef ref, DNSServiceFlags flags, DNSServiceErrorType error, const char *name,
                      const char *regtype, const char *domain, void *context) {
    auto *comp = static_cast<MDNSComponent *>(context);
    if (error != kDNSServiceErr_NoError) {
      ESP_LOGW(TAG, "Publishing service %s failed: error %d", regtype, (int) error);
      return;
    }
    // mDNSResponder renames on a collision by itself and reports the name it settled on; later
    // registrations reuse it so the services stay together under one name
    if (comp->bonjour_instance_name_ != name) {
      ESP_LOGW(TAG, "Service name '%s' is already in use; renamed to '%s'", comp->bonjour_instance_name_.c_str(), name);
      comp->bonjour_instance_name_ = name;
    }
    ESP_LOGD(TAG, "Service %s published as '%s'", regtype, name);
  }

  static void record(DNSServiceRef ref, DNSRecordRef record, DNSServiceFlags flags, DNSServiceErrorType error,
                     void *context) {
    if (error == kDNSServiceErr_NoError) {
      return;
    }
    auto *comp = static_cast<MDNSComponent *>(context);
    if (error == kDNSServiceErr_NameConflict) {
      // Another machine answers for this name; handled after the reply, not inside it
      comp->bonjour_host_conflict_ = true;
      return;
    }
    ESP_LOGW(TAG, "Publishing an address for %s failed: error %d", comp->bonjour_host_name_.c_str(), (int) error);
  }
};

void MDNSComponent::bonjour_register_service_(size_t index) {
  const MDNSService &service = this->services_[index];
  char type[64];
  snprintf(type, sizeof(type), "%s.%s", MDNS_STR_ARG(service.service_type), MDNS_STR_ARG(service.proto));

  char txt_buffer[TXT_BUFFER_SIZE];
  TXTRecordRef txt;
  TXTRecordCreate(&txt, sizeof(txt_buffer), txt_buffer);
  for (const auto &entry : service.txt_records) {
    const char *key = MDNS_STR_ARG(entry.key);
    const char *value = MDNS_STR_ARG(entry.value);
    DNSServiceErrorType err = TXTRecordSetValue(&txt, key, (uint8_t) strlen(value), value);
    if (err != kDNSServiceErr_NoError) {
      ESP_LOGW(TAG, "Dropping TXT record %s of service %s: error %d", key, type, (int) err);
    }
  }

  const char *host = this->bonjour_host_name_.empty() ? nullptr : this->bonjour_host_name_.c_str();
  // A subordinate ref starts as a copy of the shared connection
  DNSServiceRef ref = this->bonjour_connection_;
  DNSServiceErrorType err =
      DNSServiceRegister(&ref, kDNSServiceFlagsShareConnection, kDNSServiceInterfaceIndexAny,
                         this->bonjour_instance_name_.c_str(), type, nullptr, host, htons(service.port.value()),
                         TXTRecordGetLength(&txt), TXTRecordGetBytesPtr(&txt), BonjourCallbacks::service, this);
  TXTRecordDeallocate(&txt);
  if (err != kDNSServiceErr_NoError) {
    ESP_LOGW(TAG, "Failed to publish service %s: error %d", type, (int) err);
    return;
  }
  this->bonjour_services_[index] = ref;
}

void MDNSComponent::bonjour_register_services_() {
  for (size_t i = 0; i < this->services_.size(); i++) {
    if (this->bonjour_services_[i] != nullptr) {
      DNSServiceRefDeallocate(this->bonjour_services_[i]);
      this->bonjour_services_[i] = nullptr;
    }
#ifdef USE_MDNS_SUPPORTS_ENABLE_DISABLE
    if (!this->services_[i].enabled)
      continue;
#endif
    this->bonjour_register_service_(i);
  }
}

void MDNSComponent::bonjour_publish_addresses_() {
  size_t published = 0;
  for (const auto &entry : this->bonjour_addresses_) {
    const bool is_ipv4 = entry.family == AF_INET;
    DNSRecordRef record = nullptr;
    DNSServiceErrorType err = DNSServiceRegisterRecord(
        this->bonjour_connection_, &record, kDNSServiceFlagsUnique, (uint32_t) entry.interface,
        this->bonjour_host_name_.c_str(), is_ipv4 ? kDNSServiceType_A : kDNSServiceType_AAAA, kDNSServiceClass_IN,
        is_ipv4 ? 4 : 16, entry.address, 0, BonjourCallbacks::record, this);
    if (err != kDNSServiceErr_NoError) {
      // Usually an interface mDNSResponder doesn't serve
      ESP_LOGV(TAG, "Skipping an address on interface %d: error %d", entry.interface, (int) err);
      continue;
    }
    this->bonjour_records_.push_back(record);
    published++;
  }
  ESP_LOGD(TAG, "Publishing hostname %s with %zu addresses", this->bonjour_host_name_.c_str(), published);
}

void MDNSComponent::bonjour_remove_addresses_() {
  for (auto *record : this->bonjour_records_) {
    DNSServiceRemoveRecord(this->bonjour_connection_, record, 0);
  }
  this->bonjour_records_.clear();
}

void MDNSComponent::bonjour_drop_host_name_() {
  ESP_LOGW(TAG, "Cannot publish hostname %s (already in use); services use the machine's hostname",
           this->bonjour_host_name_.c_str());
  this->bonjour_host_conflict_ = false;
  this->bonjour_remove_addresses_();
  this->bonjour_addresses_.clear();
  this->bonjour_host_name_.clear();
  this->cancel_interval(ADDRESS_CHECK_ID);
  this->bonjour_register_services_();
}

void MDNSComponent::bonjour_check_addresses_() {
  HostAddresses current;
  collect_host_addresses(current);
  bool changed = current.size() != this->bonjour_addresses_.size();
  for (size_t i = 0; !changed && i < current.size(); i++) {
    changed = !(current[i] == this->bonjour_addresses_[i]);
  }
  if (!changed) {
    return;
  }
  ESP_LOGD(TAG, "Addresses changed; publishing %s again", this->bonjour_host_name_.c_str());
  this->bonjour_remove_addresses_();
  this->bonjour_addresses_ = current;
  this->bonjour_publish_addresses_();
}

void MDNSComponent::setup() {
  this->setup_buffers_and_register_([](MDNSComponent *comp, StaticVector<MDNSService, MDNS_SERVICE_COUNT> &services) {
    DNSServiceErrorType err = DNSServiceCreateConnection(&comp->bonjour_connection_);
    if (err != kDNSServiceErr_NoError) {
      ESP_LOGW(TAG, "Cannot reach mDNSResponder (error %d); services are not published", (int) err);
      comp->bonjour_connection_ = nullptr;
      return;
    }
    const std::string &name = App.get_name();
    comp->bonjour_instance_name_ = name;

    // mDNSResponder already publishes the machine's own hostname, which it reports with a
    // trailing ".local" that the node name does not carry
    static constexpr char LOCAL_SUFFIX[] = ".local";
    static constexpr size_t LOCAL_SUFFIX_LEN = sizeof(LOCAL_SUFFIX) - 1;
    char machine_hostname[256] = {0};
    gethostname(machine_hostname, sizeof(machine_hostname) - 1);
    const size_t hostname_len = strlen(machine_hostname);
    if (hostname_len > LOCAL_SUFFIX_LEN &&
        strcasecmp(machine_hostname + hostname_len - LOCAL_SUFFIX_LEN, LOCAL_SUFFIX) == 0) {
      machine_hostname[hostname_len - LOCAL_SUFFIX_LEN] = '\0';
    }
    if (strcasecmp(machine_hostname, name.c_str()) != 0) {
      // The trailing dot makes this the fully qualified name DNSServiceRegisterRecord expects
      comp->bonjour_host_name_ = name + ".local.";
      collect_host_addresses(comp->bonjour_addresses_);
      comp->bonjour_publish_addresses_();
      comp->set_interval(ADDRESS_CHECK_ID, ADDRESS_CHECK_INTERVAL_MS, [comp]() { comp->bonjour_check_addresses_(); });
    }

    comp->bonjour_register_services_();
  });
}

void MDNSComponent::loop() {
  if (this->bonjour_connection_ == nullptr) {
    return;
  }
  const uint32_t now = App.get_loop_component_start_time();
  if (now - this->bonjour_last_poll_ < POLL_INTERVAL_MS) {
    return;
  }
  this->bonjour_last_poll_ = now;

  const int fd = DNSServiceRefSockFD(this->bonjour_connection_);
  if (fd < 0) {
    return;
  }
  for (int i = 0; i < MAX_REPLIES_PER_POLL; i++) {
    fd_set readers;
    FD_ZERO(&readers);
    FD_SET(fd, &readers);
    // DNSServiceProcessResult() blocks, so it is only called on a readable connection
    struct timeval timeout = {0, 0};
    if (select(fd + 1, &readers, nullptr, nullptr, &timeout) <= 0) {
      break;
    }
    DNSServiceErrorType err = DNSServiceProcessResult(this->bonjour_connection_);
    if (err != kDNSServiceErr_NoError) {
      // Deallocating the connection cancels every operation registered on it
      ESP_LOGW(TAG, "Lost the connection to mDNSResponder (error %d); services are no longer published", (int) err);
      DNSServiceRefDeallocate(this->bonjour_connection_);
      this->bonjour_connection_ = nullptr;
      this->bonjour_services_.fill(nullptr);
      this->bonjour_records_.clear();
      this->cancel_interval(ADDRESS_CHECK_ID);
      return;
    }
  }
  if (this->bonjour_host_conflict_) {
    this->bonjour_drop_host_name_();
  }
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
    service.enabled = enabled;
    if (this->bonjour_connection_ == nullptr) {
      return true;
    }
    if (enabled) {
      this->bonjour_register_service_(i);
    } else if (this->bonjour_services_[i] != nullptr) {
      // Deallocating the subordinate ref withdraws the service
      DNSServiceRefDeallocate(this->bonjour_services_[i]);
      this->bonjour_services_[i] = nullptr;
    }
    return true;
  }
  ESP_LOGW(TAG, "Service %s not found", service_type);
  return false;
}
#endif  // USE_MDNS_SUPPORTS_ENABLE_DISABLE

void MDNSComponent::on_shutdown() {
  if (this->bonjour_connection_ == nullptr) {
    return;
  }
  // This cancels every operation and withdraws every record registered on the connection, so the
  // subordinate refs must not be deallocated as well
  DNSServiceRefDeallocate(this->bonjour_connection_);
  this->bonjour_connection_ = nullptr;
  this->bonjour_services_.fill(nullptr);
  this->bonjour_records_.clear();
}

}  // namespace esphome::mdns

#endif
