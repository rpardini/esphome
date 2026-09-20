#pragma once
#include "esphome/core/defines.h"
#if defined(USE_HOST) && (defined(USE_MDNS_AVAHI) || defined(USE_MDNS_BONJOUR))

// AF_INET / AF_INET6, which the family field below is written in terms of
#include <sys/socket.h>

#include <cstdint>

#include "esphome/core/helpers.h"

namespace esphome::mdns {

/// Most addresses published for the node's own hostname
static constexpr size_t MDNS_HOST_MAX_ADDRESSES = 16;

/// One of the machine's addresses, in the form both host backends publish it
struct HostAddress {
  /// Interface index, as if_nametoindex() reports it
  int interface;
  /// AF_INET or AF_INET6; decides how many of the bytes below are used
  int family;
  uint8_t address[16];

  bool operator==(const HostAddress &other) const;
};

using HostAddresses = StaticVector<HostAddress, MDNS_HOST_MAX_ADDRESSES>;

/// Collects the machine's current addresses, skipping interfaces that are down or loopback.
void collect_host_addresses(HostAddresses &addresses);

}  // namespace esphome::mdns

#endif
