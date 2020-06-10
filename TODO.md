## TODO Items:
- Migrate these to GitHub issues
- Docs
- Automate DNSSEC trust anchors
- Automated security updates and reboots.
- Syslog output
- Enable IPv6
  - Whitelist IPv6 inbound connections
  - NAT 6to4 inbound IPv6 connections for devices without IPv6
  - Allow forwarded IPv6 Traffic without target="ACCEPT" on internal zone.
- Setup Router hostname
- Make the LAN VPN conditional
- Make IPv6 Conditional
- Template wg0 and lan_vpn iface names.
- Calculate default start and stop DHCP ranges.
- user-data sets default username/password.

Hardening:
- Read Only Root:https://wiki.debian.org/ReadonlyRoot#Enable_readonly_root
- CIS Benchmark?

Feature Ideas:
- Alternative source for inventory/variables.
- SNMP or Prometheus telemetry
- Configurable ntp
- DMZ VLAN
- Guest VLAN
  - Routes to internet, not to other LAN vlans
- IoT VLAN
  - Enable multicast routing between regular LAN vlan and IoT VLAN
  - Allow traffic from regular LAN to IoT VLAN but not in reverse
- Layer 7 or DNS filtering PiHole?
