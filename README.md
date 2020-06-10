
## TODO Items:
- Docs
- Automate DNSSEC trust anchors
- Automated security updates and reboots.
- Syslog output
- Enable IPv6
  - Whitelist IPv6 inbound connections
  - NAT 6to4 inbound IPv6 connections for devices without IPv6
- Setup Router hostname

Hardening:
- Read Only Root:https://wiki.debian.org/ReadonlyRoot#Enable_readonly_root

Feature Ideas:
- SNMP or Prometheus telemetry
- Configurable ntp
- DMZ VLAN
- Guest VLAN
  - Routes to internet, not to other LAN vlans
- IoT VLAN
  - Enable multicast routing between regular LAN vlan and IoT VLAN
  - Allow traffic from regular LAN to IoT VLAN but not in reverse
- Layer 7 or DNS filtering PiHole?

## Configuration Reference

### WAN Interface (Internet Side)

#### WAN Ethernet Device

The ethernet device to use for the wide area network. The built-in ethernet port
of a Raspberry Pi is `eth0`.

```
wan_iface: eth0
```

### LAN Interface

#### LAN Ethernet Device

The ethernet device to use for the local area network. The first attached USB
ethernet device is `eth1`.

```
lan_iface: eth1
```

#### Router LAN IP Address

```
router_ip_address: 192.168.1.1
```

#### Router LAN network

```
lan_network: 192.168.1.0
```

```
lan_network_prefix_length: 24
```

### DNS

#### Domain Name

```
domain: my.home
```

#### Name Servers

```
name_servers:
  - 2606:4700:4700::1113
  - 2606:4700:4700::1003
  - 1.1.1.3
  - 1.0.0.3
```

### DHCP

#### DHCP Lease Length

```
dhcp_lease_seconds: 86400
```

#### DHCP Address Allocation Range

```
dhcp_ip_address_range_start: 192.168.1.2
```
```
dhcp_ip_address_range_stop: 192.168.1.200
```

#### LAN Network Mask
```
dhcp_ip_address_netmask: 255.255.255.0
```

#### DHCP Reservations

```
dhcp_reservations:
  - hostname: nas
    ip_address: 192.168.1.5
    mac_address: A0:B1:C2:D3:E4:F5
  - hostname: printer
    ip_address: 192.168.1.6
    mac_address: F9:E8:D7:C6:B5:A4
```
