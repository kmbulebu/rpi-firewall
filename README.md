
## TODO Items:
- Docs
- Systemd.timer for ansible-pull
- Enable IPv6
  - Enable IPv6 route advertisements
  - Block IPv6 inbound connections by default
  - Whitelist IPv6 inbound connections
  - NAT 6to4 or 6to6 inbound IPv6 connections to enable stable and private IPv6 IP
- Create a DMZ VLAN
- Create a Guest VLAN
  - Routes to internet, not to other LAN vlans
- Create a IoT VLAN
  - Enable multicast routing between regular LAN vlan and IoT VLAN
  - Do not otherwise allow traffic between.
- Automate DNSSEC trust anchors
