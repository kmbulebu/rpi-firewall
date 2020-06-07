
## TODO Items:
- Docs
- Automate DNSSEC trust anchors
- Automated security updates and reboots.
- Syslog output
- Enable IPv6
  - Enable IPv6 route advertisements
  - Block IPv6 inbound connections by default
  - Whitelist IPv6 inbound connections
  - NAT 6to4 inbound IPv6 connections for devices without IPv6

Hardening:
- Read Only Root:https://wiki.debian.org/ReadonlyRoot#Enable_readonly_root

Feature Ideas:
- SNMP or Prometheus telemetry
- DMZ VLAN
- Guest VLAN
  - Routes to internet, not to other LAN vlans
- IoT VLAN
  - Enable multicast routing between regular LAN vlan and IoT VLAN
  - Allow traffic from regular LAN to IoT VLAN but not in reverse
- Layer 7 or DNS filtering PiHole?
