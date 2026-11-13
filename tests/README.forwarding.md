# Forwarding and recovery lab

Run **only in a disposable container**. The script replaces networkd and
firewalld configuration inside it. No router inventory or credentials are used.

```sh
docker build -f tests/Dockerfile.forwarding -t rpi-forwarding-test .
docker run -d --name rpi-forwarding-test --privileged --network=none --cgroupns=private \
  --tmpfs /run --tmpfs /run/lock --tmpfs /tmp \
  -v "$PWD:/repo:ro" -w /repo rpi-forwarding-test
# Wait for systemctl is-system-running to report running or degraded.
docker exec rpi-forwarding-test systemctl is-system-running --wait
docker exec -e RPI_FIREWALL_DISPOSABLE_TEST=1 rpi-forwarding-test \
  python3 tests/integration_forwarding.py
docker rm -f rpi-forwarding-test
```

A degraded container boot can be normal; inspect failed units if the test fails.
The CI job waits for boot, prints service diagnostics on failure and always
removes the container. Rootless Podman is also supported: substitute `podman` for `docker` and add
`--mount type=bind,src=/sys,dst=/sys,readonly` to `podman run`. In rootless mode,
the runtime does not permit remounting sysfs from inside the container, so it
must already be read-only. Do not add this host sysfs bind to the Docker command: its recursive
submounts can interfere with the private cgroup hierarchy and prevent boot.

After boot the lab makes only the container's `/sys` mount read-only with
`mount -o remount,bind,ro /sys`, leaving the private cgroup submount writable.
It verifies networkd's mount view before running scenarios. This prevents
networkd waiting for an absent udev daemon. The script uses `nsenter` to enter client network namespaces without
remounting `/sys`.

## What it exercises

The container is the router. Six network namespaces represent an ISP, an
Internet server, a VLAN switch, and LAN/guest/VPN clients. Veth links carry actual
packets; bridge VLAN filtering separates the three clients. The ISP provides
DHCP on primary and backup uplinks and hosts a real WireGuard peer. The simulated
Internet is another routed hop beyond the ISP, so forwarding, masquerading and
path MTU behavior are exercised. The container has no external network connection.

The test applies the repository's networkd and firewall zone/configuration
templates, VPN validation and VPN lifecycle tasks through Ansible. It runs the
actual systemd-networkd and firewalld services and checks:

- DHCP service on the guest VLAN before an upstream DHCP lease exists.
- A second run of that focused Ansible slice reports `changed=0`.
- LAN and guest TCP connections reach the Internet server with WAN NAT.
- Guest connections cannot reach a listening HTTP server on the LAN or router.
- VPN client traffic reaches the Internet through the real WireGuard peer and
  tunnel masquerading, in the production VRF layout.
- Both peer failure and local tunnel shutdown prevent VPN access, with packet
  captures checking that plaintext traffic does not escape via ordinary WAN.
  Restoring the peer/device restores access.
- An IPv6 Packet Too Big reaches the LAN client after an intermediate link's
  MTU drops to 1280, and a 256 KiB TCP response still transfers successfully.
- Upstream forwarding fails while primary Ethernet carrier, DHCP lease and
  default route remain. Inserting a backup veth obtains DHCP and restores LAN
  and guest access using the configured route metrics. Removing it withdraws
  its route; restoring the provider restores primary access. Guest DHCP stays
  available during the outages.

## Explicit limits and fixtures

Forwarding policy ownership is tracked in
[issue #30](https://github.com/kmbulebu/rpi-firewall/issues/30). This lab supplies
three **test-only** ACCEPT policies: internal → external, guest → external, and
internal_vpn → external_vpn. It also installs the repository's IPv6 ICMP ingress
policy. The fixtures deliberately do not reproduce every live-router custom
policy. A pass is not proof that a fresh full deployment reproduces those policies.

Idempotence covers the tested networking/firewall/VPN slice, not bootstrap,
package upgrades, timers, DNS or the entire playbook. Existing DNS and blocklist
jobs cover their separate service behavior. Clients use static addresses for
traffic assertions; a small DHCP DISCOVER/OFFER probe checks DHCP availability,
not lease renewal or persistence. IPv6 uses static documentation prefixes on the
simulated ISP and LAN, not DHCPv6 prefix delegation. The production flowtable
setting stays enabled, but the switching assertions use fresh connections;
long-lived cached-flow continuity remains a hardware check.

The suite uses Ubuntu 26.04 userspace and the CI runner's native kernel. It does
not emulate ARM, the Pi kernel, USB devices, Wi-Fi or the captive portal. It does
not resize conntrack buckets or alter any host-global sysctls.

## Physical router checks

Use a maintenance window and keep local access available. Confirm the expected
configuration has been applied first, then check:

1. Boot with primary Ethernet present but upstream Internet unavailable and the
   hotspot absent. LAN management, DHCP and local DNS should remain usable.
2. Fail primary Internet beyond its Ethernet link, leaving carrier up. Insert the
   actual USB hotspot; verify `wan-backup` naming, DHCP, route preference and
   Internet/DNS access from LAN and guests.
3. Check guest isolation and VPN reconnection on backup. Check IPv6 separately:
   primary delegated addresses need not work through an IPv4-only hotspot.
4. Restore primary, unplug the hotspot and verify primary Internet/DNS/VPN
   recovery. Repeat insertion/removal while a long-lived TCP/UDP transfer is
   running to assess cached flow behavior; reconnection may be required.
5. Verify LAN Thread route advertisements and the Omada portal independently.

Record Pi kernel/networkd versions and outcomes. These tests validate recovery
and access boundaries, not throughput or resistance to connection-table exhaustion.
