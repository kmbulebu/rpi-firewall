## TODO Items:

Hardening:
- Read Only Root:https://wiki.debian.org/ReadonlyRoot#Enable_readonly_root
- CIS Benchmark?

Possible Sysctl Tweaks:

```
kernel.sem = 250 256000 100 1024
# If we need software to interact with vrfs
net.ipv4.udp_l3mdev_accept=1
net.ipv4.tcp_l3mdev_accept=1
```

Improvements:
 - CPU affinity for node exporter and blackbox exporter
 - Realtime kernel?

Feature Ideas:
- Blackbox exporter
- VRFs for all
- Alternative source for inventory/variables.
- DMZ VLAN
- Guest VLAN
  - Routes to internet, not to other LAN vlans
- IoT VLAN
  - Enable multicast routing between regular LAN vlan and IoT VLAN
  - Allow traffic from regular LAN to IoT VLAN but not in reverse
- Layer 7 or DNS filtering PiHole?
- ipfix/netflow or packetbeat
- Surricata 
