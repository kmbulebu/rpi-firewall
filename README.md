## Introduction

The Raspberry Pi 4B is the first Raspberry Pi device with true gigabit ethernet and USB 3 ports, making it capable of performing as a gigabit ethernet router and firewall. This project transforms a standard installation of Ubuntu Server on a Raspberry Pi into an Internet firewall and router.

## Prerequisites

- A Raspberry Pi 4B or newer
- A USB gigabit ethernet adapter
- Ubuntu Server 26.04 LTS (Resolute)

## Features

- Configuration as code. No Web Interface
- DHCP and DNS server
- IPv6 Support
- Wireguard VPN client

## Installation

1. Prepare an SD card following the [Ubuntu tutorial.](https://ubuntu.com/tutorials/how-to-install-ubuntu-on-your-raspberry-pi#2-prepare-the-sd-card)

2. Create an [inventory.yml](./rpi/inventory.yml) file, using the [./rpi/inventory.yml](./rpi/inventory.yml) as a start and this README as a guide.

3. Copy the `inventory.yml` file to the root directory of the SD card.

4. Replace the `network-config` and `user-data` files in the root of the SD card with those found [here](./rpi/).

5. Plug the Raspberry Pi ethernet port into your ISP connection. Plug the USB ethernet adapter into the Raspberry PI and your LAN switch.

6. Boot the Raspberry Pi with the SD card.

## Sample `inventory.yml` configuration

Coming Soon...

## Configuration Reference

### Admin user

Username: `firewall`
Password: `MyVoiceIsMyPassword`

### Password

To change the `firewall` user password, provide a valid shadow password hash. The password is required for SSH authentication and sudo.

See: https://docs.ansible.com/ansible/latest/reference_appendices/faq.html#how-do-i-generate-encrypted-passwords-for-the-user-module

```
admin_user_password_hash: '$6$.PKuLy7JxcwGR8$GeHpj./OiBfpMgWkEaI0yLkZ9jLHoTrwlMgbLRV2rf81FAk5CKeQRcoZLg4Z70YvII7MkFDv6BlgcfgAWlYsA/'
```

### SSH Authorized Key



```
admin_user_authorized_key=
```

### WAN Interface (Internet Side)

#### Optional USB hotspot backup

The router uses systemd-networkd 259 on Ubuntu 26.04. A hotspot providing USB
Ethernet tethering can be plugged in when primary Internet service fails. Enable
USB tethering on the hotspot if required. No modem dialer or health monitor is
needed: plugging it in selects backup for each address family for which it
supplies a default route; unplugging it returns that family to primary WAN.
Primary WAN can retain Ethernet carrier throughout the outage.

Add device-specific matchers to the router inventory, using each adapter's
permanent MAC (obtain it with `ethtool -P INTERFACE`):

```yaml
wan_backup_enabled: true
wan_backup_iface_networkd_link_match: "PermanentMACAddress=96:0b:9e:b5:54:6d"
wan_iface_networkd_link_match: "PermanentMACAddress=70:88:6b:8d:9f:e0"
```

The addresses above are examples from this installation. Matchers must identify
separate devices. The original `Property=ID_BUS=usb` primary matcher would also
claim a USB hotspot, so it must be replaced before enabling backup. Backup MAC
spoofing is not used. Some hotspots change their MAC on reboot; if that happens,
update the matcher or use stable USB identity properties instead.

The role defaults are:

```yaml
wan_backup_enabled: false
wan_backup_iface: wan-backup
wan_route_metric: 1024
wan_backup_route_metric: 100
```

Lower route metrics win. The metrics apply to DHCPv4 and IPv6 RA routes on the
two uplinks, including primary WAN's explicit IPv6 default route. They do not
change LAN RA acceptance (including Thread routes), advertised LAN router
preference, or the VPN VRF's table 200. WireGuard's outer connection can move to
backup; existing connections may need to reconnect after either switch.

Both uplinks share the external firewalld zone, including masquerading, inbound
exceptions and configured port forwards. Cellular carrier NAT may prevent inbound
connections regardless of those rules. IPv6 reverse-path filtering remains strict.
Software flow offload remains configured for primary WAN and LAN; the optional
hotspot is not added to the flowtable. Verify cached-flow behavior during switching
as part of live failover testing. Disabling backup removes its networkd files. Unplug the hotspot when disabling it so an existing configured interface
cannot retain old state.

Prefix delegation to LAN and guests remains tied to primary WAN. Backup does not
request PD. IPv4-only tethering is supported; it does not replace a failed primary
IPv6 route. Previously advertised primary IPv6 prefixes may remain on clients
until their lifetimes expire. Do not assume that cellular IPv6 can route primary
WAN's delegated addresses. The hotspot's subnet must not overlap a LAN/VPN/guest
subnet.

After the first deployment, unplug and reconnect an already attached hotspot:
`.link` naming is applied when the device appears, not by restarting networkd.
The role reloads udev rules for future plug-ins without retriggering existing
primary/LAN devices. Use the MOTD or `networkctl status wan-backup` to inspect
addresses, and `ip -4 route show default` / `ip -6 route show default` to inspect
uplink metrics.

Before relying on backup, test plugging it in while primary retains carrier,
DNS and Internet access from LAN/guests, LAN Thread routes, VPN reconnection,
unplugging to return to primary, and booting with the hotspot absent. Test IPv6
separately if the hotspot offers it.

### LAN Interface

#### Router hostname

Set the router hostname.

```
router_hostname: router
```

#### LAN Ethernet Device

The ethernet device to use for the local area network. The first attached USB
ethernet device is `eth1`.

```
lan_iface: eth1
```

#### Router LAN IP Address

The IP address and prefix to assign to the LAN side of the router. This address and prefix will determine the IP address range for the local area network. For example, a setting of `192.168.1.1/24` will assign the router an IP address of `192.168.1.1`, and the network will have an IP address range of `192.168.1.1` to `192.168.1.255` with `192.168.1.255` as the broadcast address.

```
router_ip_address: 192.168.1.1/24
```

### DNS

#### Domain Name

The domain name to assign to local network devices.

```
domain: my.home
```

#### Name Servers

A list of name servers to use for DNS resolution.

```
name_servers:
  - 2606:4700:4700::1113
  - 2606:4700:4700::1003
  - 1.1.1.3
  - 1.0.0.3
```

### DNS resilience and IPv6 clients

Unbound first attempts an upstream lookup, then can use an expired cached answer
if upstream fails or takes longer than the configured wait. Defaults are:

```yaml
dns_serve_expired: true
dns_serve_expired_ttl: 86400             # Maximum age after expiry, seconds
dns_serve_expired_client_timeout: 1800  # Upstream wait, milliseconds
```

Expired replies have a 30-second TTL. Failed refreshes do not extend the age
limit. This uses the in-memory cache and adds no persistent cache or disk writes;
it cannot supply uncached names or survive an Unbound restart.

DNS listeners remain restricted to LAN and guest addresses. Unbound's
`interface-action` permits queries to those listening sockets from clients with
delegated global IPv6 addresses, without hardcoding the changing delegated
prefix. Existing source ACLs remain in place. These rules select destination
sockets, not ingress devices: firewalld must continue to block DNS access from WAN.
IPv6 RA advertises the router's stable link-local DNS address. Interface names are
expanded to addresses when Unbound starts; a newly assigned global listener address
requires a restart, but the advertised link-local listener does not depend on PD.

See [Unbound's configuration reference](https://unbound.docs.nlnetlabs.nl/en/latest/manpages/unbound.conf.html)
for interface ACLs and stale response behavior.

### DNS ad blocking

Set `ad_blocklists` under `all.hosts.localhost` in the live
`/boot/firmware/inventory.yml` to enable filtering. The sample
[inventory](./rpi/inventory.yml) includes OISD Small and commented alternatives
with their scope and aggressiveness. Existing installations default to no lists.

```yaml
ad_blocklists:
  - https://small.oisd.nl
ad_allow_domains:
  - needed.example.com
```

Ansible installs a Python standard-library updater and a daily systemd timer.
It downloads the subscriptions immediately during the play and compiles them into
`/etc/unbound/unbound.conf.d/ad-blocking.conf`. Unbound remains the LAN and guest
resolver, with the existing local records, IPv6 listeners, DNSSEC validation and
TLS upstreams. Ubuntu's own `systemd-resolved` configuration is unchanged. This is
native Unbound filtering, without a Pi-hole container or dashboard. The VPN VLAN
currently advertises external DNS servers and is not covered by this policy.

Supported subscriptions are HTTPS DNS-only Adblock lists (`||domain^`, including
`@@||domain^` exceptions), hosts files such as StevenBlack, and plain domain lists.
Browser rules with URL paths or modifiers are unsupported and fail the update.
Blocked domains **and their subdomains** return NXDOMAIN. Allowlisted domains and
their subdomains override blocking, including more specific entries in the lists.
Local forward/reverse zones and `dns_private_domains` are protected automatically.
Permanent exceptions belong in inventory; prefer narrow exceptions.

Downloads must all succeed and contain blocking entries before a policy is
installed. The updater validates the candidate and complete Unbound configuration
before reloading. Failed downloads or validation retain the previous policy; a
reload failure restores it and attempts to reload the previous configuration.
A failed initial update fails the Ansible play instead of reporting working
filtering. Set `ad_blocklists: []` and rerun Ansible to clear the generated policy
and disable scheduled updates. Keep the variable explicitly empty to override any
inherited subscriptions.

To inspect updates and verify both blocking and ordinary resolution (substitute
your router's LAN address):

```bash
systemctl status ad-blocklists-update.timer
journalctl -u ad-blocklists-update.service
sudo /usr/local/sbin/update-ad-blocklists
dig @192.168.1.1 pagead2.googlesyndication.com A  # NXDOMAIN when listed
dig @192.168.1.1 example.com A                 # Ordinary public answer
```

### NTP Servers

To customize the automatic setting of the system clock, provide a list of NTP servers.

> Note: The Raspbery Pi does not have a real time clock. The clock must be set on
every boot from an NTP server.

```
ntp_servers:
  - ntp.ubuntu.com
```

### Remote Syslog

To send router and firewall logs to a remote syslog server, provide
the destination server and port. `192.168.1.10:514`

```
rsyslog_udp_server:
```

```
rsyslog_tcp_server:
```

### Established TCP connections

```yaml
nf_conntrack_tcp_timeout_established: 432000  # Five days, kernel default
```

This is the idle timeout for established connections in connection tracking,
not a maximum session length. Shorter values can break quiet connections through
NAT. Reduce it only if measured connection-table pressure warrants it.

### Automatic Updates

The configuration update timer first runs ten minutes after boot, then hourly.

To ensure the router remains patched and current, packages updates are installed
automatically. Specify a time when reboots, if needed, are scheduled. The time
zone is in Coordinated Universal Time (UTC).

```
upgrade_reboot_time: '07:00'
```

### Prometheus Node Exporter

Enable the prometheus node exporter on the LAN interface, TCP port 9100.

```
enable_prometheus_node_exporter: no
```

### DHCP

#### DHCP Lease Length

Configure lease durations separately for each network, in seconds. Both the
default and maximum lease time use the configured value. Existing leases adopt
the new duration when renewed. Networkd persists leases to disk on systemd 259.

```yaml
lan_dhcp_lease_seconds: 86400         # 24 hours
lan_vpn_dhcp_lease_seconds: 86400     # 24 hours
lan_guests_dhcp_lease_seconds: 3600   # 1 hour
```

A guest DHCP lease is independent of Wi-Fi portal authorization or session expiry.

#### DHCP Address Allocation Range

The start and stop of an IP address range from which to assign IP addresses to devices on the network. This is useful for setting aside IP addresses for devices that do not use DHCP.

```
dhcp_ip_address_range_start: 192.168.1.2
```
```
dhcp_ip_address_range_stop: 192.168.1.254
```

#### DHCP Reservations

A list of DHCP reservations to ensure devices have a static IP address assignment. For each, specify the desired hostname and IP address to assign to the device with the specified MAC address.

```
dhcp_reservations:
  - hostname: nas
    ip_address: 192.168.1.5
    mac_address: A0:B1:C2:D3:E4:F5
  - hostname: printer
    ip_address: 192.168.1.6
    mac_address: F9:E8:D7:C6:B5:A4
```

### Port Forwarding

Port forwarding opens a port on the WAN interface, and forwards incoming traffic to that port to a device on the LAN.

#### Port Forwards

A list of ports to forward to network devices.

`description`: A human readable description of what the port forward enables.

`address_from`: The source IP address(es) in CIDR notation that will be allowed to connect to the open port.

`proto`: The type of layer 4 port, `tcp` or `udp`.

`ports_from`: The port number the WAN interface should open for connections.

`address_to`: The IP address of the LAN device to receive the forwarded traffic.

`ports_to`: The tcp or udp port number on the LAN device to receive the forwarded traffic.

Example:
```
port_forwards:
  - description: "Web Site"
    address_from: 0.0.0.0/0
    ports_from: 80
    ports_to: 8080
    proto: tcp
    address_to: 192.168.1.10
```

### LAN VPN Interface

#### Enabling

The VPN client and associated LAN are disabled by default. Set to true to enable.
Existing VPN installations must explicitly set this flag before upgrading; older
versions configured VPN regardless of the flag. WireGuard credentials are required
only when enabled.

```
enable_lan_vpn: false
```

Disabling removes the managed VPN network files and running VLAN, WireGuard and
VRF devices. LAN attachment, the VPN subnet prohibition route, and VPN-specific
WAN exception are removed as well. Firewall zones remain without interface
bindings so administrator-managed policy references remain valid. Packages and
administrator-managed firewall policies are retained.

Cleanup uses the names recorded in the previous managed netdev files and checks
kernel device kinds before deletion. Pending cleanup identities are retained for
retry if a run fails. Changing interface names retires the old devices; disable
first before reusing an old name for a different device kind. Existing VPN
connections are interrupted when devices are retired.

#### MTU

```yaml
lan_vpn_mtu: 1292
```

This sets the WireGuard, VPN VLAN and VRF MTUs, and the DHCP option 26 advertised
to VPN clients. The default preserves existing behavior. The supported range is
1280–1500 bytes; choose a value that fits the upstream path after WireGuard
overhead. No automatic path measurement or MTU increase is performed. Clients
receive a changed advertised MTU when they renew their DHCP leases, and client
support for this DHCP option varies.

#### VLAN ID

The VLAN ID to use for the LAN privacy network. All outbound traffic on this network will route through a Wireguard VPN.

```
lan_vpn_vlan_id: 2
```

#### LAN VPN Interface Name

The desired name of the VLAN network interface.

```
lan_vpn_iface: lan_vpn
```

#### LAN VPN IP Address

The IP address and prefix to assign to the VPN LAN side of the router. This address and prefix will determine the IP address range for the plocal area network attached to the VPN. For example, a setting of `192.168.254.1/24` will assign the router an IP address of `192.168.254.1`, and the network will have an IP address range of `192.168.254.1` to `192.168.254.255` with `192.168.254.255` as the broadcast address.

```
lan_vpn_router_ip_address: 192.168.254.1/24
```

#### DHCP Address Allocation Range

The start and stop of an IP address range from which to assign IP addresses to devices on the VPN LAN network. This is useful for setting aside IP addresses for devices that do not use DHCP.

```
lan_vpn_dhcp_ip_address_range_start: 192.168.254.2
```
```
lan_vpn_dhcp_ip_address_range_stop: 192.168.254.254
```

#### Wireguard Interface Name

The desired name of the wireguard network interface.

```
vpn_client_iface: wg0
```

#### Wireguard Private Key

Set to the Wireguard private key. In a Wireguard configuration file, this is found under the `[Interface]` section, named `PrivateKey`.

```
lan_vpn_wg_private_key:
```

#### Wireguard Listen Port

Set to the Wireguard listening port. In a Wireguard configuration file, this is found under the `[Interface]` section, named `ListenPort`.

```
lan_vpn_wg_listen_port: 51820
```

#### Wireguard Address

Set to the Wireguard IP address and prefix. In a Wireguard configuration file, this is found under the `[Interface]` section, named `Address`.

```
lan_vpn_wg_address:
```

#### Wireguard Peer Public Key

Set to the Wireguard peer public key. In a Wireguard configuration file, this is found under the `[Peer]` section, named `PublicKey`. Typically, this is the public key of a VPN service provider.

```
lan_vpn_wg_peer_public_key:
```

#### Wireguard Peer Endpoint

Set to the Wireguard Internet accessible peer IP address and network port. In a Wireguard configuration file, this found under the `[Peer]` section, named `Endpoint`. Typically, this is the IP address and port, separated by a colon, of a VPN service provider.

```
lan_vpn_wg_peer_endpoint:
```

#### Wireguard Peer Allowed IPs

All traffic matching this IP address range will be sent through the VPN. All other traffic will be dropped. In a Wireguard configuration file, this found under the `[Peer]` section, named `AllowedIPs`. In most cases, this should be `0.0.0.0/0` to route all of the LAN VPN traffic through the Wireguard VPN.

```
lan_vpn_wg_peer_allowed_ips: 0.0.0.0/0
```

#### Wireguard Peer Persistent Keepalive

How often, in seconds, to send a keepalive packet to the peer to ensure a network address translated (NAT) connection remains alive. In a Wireguard configuration file, this found under the `[Peer]` section, named `PersistentKeepalive`.

```
lan_vpn_wg_peer_persistent_keep_alive: 15
```

**Variables**

The table below lists role variables defined in `roles/firewall/defaults/main.yml` and their default values. Variables that are derived from other variables (found in `roles/firewall/vars/main.yml`) are omitted.

| Variable | Default | Description |
|---|---|---|
| `admin_user_password_hash` | `'$6$mPBFViTIy1dObC2$mYr5HlI2uiZ9DsPvvLFz8CePmCgcyyddlQ.R9tN6vibTMTZJ4XiNtADYv4cwx9Ocxqb9ZFzwvziOPPIfC9I5K0'` | Shadow-format password hash for the `firewall` admin user. |
| `ansible_inventory` | `/boot/firmware/inventory.yml` | Path to inventory file on the target system. |
| `ansible_bootstrap_directory` | `/root/.ansible/pull/bootstrap` | Separate checkout used to bootstrap Ansible collection dependencies. |
| `ansible_playbook_filename` | `playbook.yml` | Playbook filename in the repository. |
| `ansible_playbook_git_ref` | `master` | Git ref/branch to check out. |
| `ansible_playbook_git_url` | `https://github.com/kmbulebu/rpi-firewall.git` | Git repository URL for the playbook. |
| `domain` | `my.home` | Local domain name for LAN devices. |
| `dns_private_domains` | `[]` | List of private domains to allow DNS resolution to local IP addresses. |
| `dns_serve_expired` | `true` | Use expired cached answers when upstream resolution fails or times out. |
| `dns_serve_expired_ttl` | `86400` | Maximum age in seconds after a cached answer expires. |
| `dns_serve_expired_client_timeout` | `1800` | Upstream wait in milliseconds before using an expired answer. |
| `enable_prometheus_node_exporter` | `no` | Enable Prometheus node exporter and listen on LAN interface. |
| `lan_dhcp_pool_offset` | `10` | Offset from router IP to start DHCP pool. |
| `lan_dhcp_pool_size` | `200` | Number of DHCP addresses in the LAN pool. |
| `lan_dhcp_lease_seconds` | `86400` | Default and maximum trusted LAN lease duration in seconds. |
| `lan_iface` | `lan0` | Desired LAN interface name. |
| `lan_iface_networkd_link_match` | `"Driver=bcmgenet"` | `systemd-networkd` match string for LAN link. |
| `lan_guests_dhcp_pool_offset` | `10` | DHCP pool offset for guests network. |
| `lan_guests_dhcp_pool_size` | `200` | DHCP pool size for guests network. |
| `lan_guests_dhcp_lease_seconds` | `3600` | Default and maximum guest lease duration in seconds. |
| `lan_guests_iface` | `lan_guests` | Interface name for LAN guests network. |
| `lan_guests_router_ip_address` | `192.168.200.254/24` | Router IP/prefix for the guests network. |
| `lan_guests_vlan_id` | `6` | VLAN id for the guests network. |
| `enable_lan_vpn` | `false` | Enable the VPN client and its VLAN/VRF; disabled configurations require no VPN credentials. |
| `lan_vpn_mtu` | `1292` | Shared VPN tunnel/VLAN/VRF MTU and DHCP MTU advertisement, in bytes. |
| `lan_vpn_iface` | `lan_vpn` | Desired interface name for the LAN VPN VLAN. |
| `lan_vpn_router_ip_address` | `192.168.254.1/24` | Router IP/prefix for the LAN VPN network. |
| `lan_vpn_dhcp_lease_seconds` | `86400` | Default and maximum VPN LAN lease duration in seconds. |
| `lan_vpn_vlan_id` | `2` | VLAN id for the LAN VPN network. |
| `lan_vpn_wg_listen_port` | `51820` | WireGuard listen port for the VPN interface. |
| `lan_vpn_wg_peer_allowed_ips` | `0.0.0.0/0` | Allowed IPs for the WireGuard peer (routes through VPN). |
| `lan_vpn_wg_peer_persistent_keep_alive` | `15` | WireGuard persistent keepalive interval in seconds. |
| `motd_dhcp_leases_limit` | `5` | Number of DHCP leases shown in the MOTD per interface. |
| `nf_conntrack_tcp_timeout_established` | `432000` | Idle established TCP connection tracking timeout in seconds. |
| `name_servers` | `- 1.1.1.3#family.cloudflare-dns.com\n- 1.0.0.3#family.cloudflare-dns.com` | List of upstream name servers (default uses Cloudflare Family DNS). |
| `ntp_servers` | `- ntp.ubuntu.com` | List of NTP servers used to sync system time. |
| `router_hostname` | `router` | Hostname for the router. |
| `router_ip_address` | `192.168.1.1/24` | Router LAN IP and prefix. |
| `tailscale_iface` | `tailscale0` | Interface name for Tailscale. |
| `tailscaled_listen_port` | `0` | Listening port for tailscaled (0 = default/no specific port). |
| `upgrade_automatic_reboot` | `yes` | Whether to automatically reboot after upgrades when needed. |
| `upgrade_reboot_time` | `'04:55'` | Time (UTC) to schedule automatic reboots after upgrades. |
| `vpn_client_iface` | `wg0` | Desired WireGuard client interface name. |
| `vpn_vrf_iface` | `vpn_vrf0` | VRF interface name for VPN routing. |
| `wan_device_set_mac_address` | `` | Optional MAC address to set on WAN device (empty by default). |
| `wan_iface` | `wan0` | Desired WAN interface name. |
| `wan_iface_networkd_link_match` | `"Property=ID_BUS=usb"` | `systemd-networkd` match string for WAN link. |
| `wan_backup_enabled` | `false` | Configure optional USB hotspot backup. |
| `wan_backup_iface` | `wan-backup` | Desired backup WAN interface name. |
| `wan_backup_iface_networkd_link_match` | unset | Explicit device matcher; required when backup is enabled. |
| `wan_route_metric` | `1024` | Primary DHCPv4 and IPv6 RA route metric. |
| `wan_backup_route_metric` | `100` | Backup metric; must be positive and lower than primary. |
