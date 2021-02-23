## TODO Items:

Hardening:
- Read Only Root:https://wiki.debian.org/ReadonlyRoot#Enable_readonly_root
- CIS Benchmark?

Possible Sysctl Tweaks:

```
kernel.sem = 250 256000 100 1024
```

Feature Ideas:
- Alternative source for inventory/variables.
- DMZ VLAN
- Guest VLAN
  - Routes to internet, not to other LAN vlans
- IoT VLAN
  - Enable multicast routing between regular LAN vlan and IoT VLAN
  - Allow traffic from regular LAN to IoT VLAN but not in reverse
- Layer 7 or DNS filtering PiHole?
