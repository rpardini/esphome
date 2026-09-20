#include "mdns_host_addresses.h"
#if defined(USE_HOST) && (defined(USE_MDNS_AVAHI) || defined(USE_MDNS_BONJOUR))

#include <ifaddrs.h>
#include <net/if.h>
#include <netinet/in.h>

#include <cstring>

namespace esphome::mdns {

bool HostAddress::operator==(const HostAddress &other) const {
  return this->interface == other.interface && this->family == other.family &&
         memcmp(this->address, other.address, sizeof(this->address)) == 0;
}

void collect_host_addresses(HostAddresses &addresses) {
  addresses.clear();
  struct ifaddrs *interfaces = nullptr;
  if (getifaddrs(&interfaces) != 0) {
    return;
  }
  for (struct ifaddrs *ifa = interfaces; ifa != nullptr && addresses.size() < MDNS_HOST_MAX_ADDRESSES;
       ifa = ifa->ifa_next) {
    // Point-to-point interfaces are VPN tunnels; mDNS is link-local multicast and does not
    // reach over them, so their addresses would only crowd out the ones that are reachable
    if (ifa->ifa_addr == nullptr || !(ifa->ifa_flags & IFF_UP) || (ifa->ifa_flags & (IFF_LOOPBACK | IFF_POINTOPOINT))) {
      continue;
    }
    HostAddress entry{};
    entry.interface = static_cast<int>(if_nametoindex(ifa->ifa_name));
    entry.family = ifa->ifa_addr->sa_family;
    if (entry.family == AF_INET) {
      const auto *sin = reinterpret_cast<struct sockaddr_in *>(ifa->ifa_addr);
      memcpy(entry.address, &sin->sin_addr, sizeof(sin->sin_addr));
    } else if (entry.family == AF_INET6) {
      const auto *sin6 = reinterpret_cast<struct sockaddr_in6 *>(ifa->ifa_addr);
      memcpy(entry.address, &sin6->sin6_addr, sizeof(sin6->sin6_addr));
    } else {
      continue;
    }
    addresses.push_back(entry);
  }
  freeifaddrs(interfaces);
}

}  // namespace esphome::mdns

#endif
