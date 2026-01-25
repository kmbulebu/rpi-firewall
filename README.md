## Introduction

rpi-firewall turns an Ubuntu Server installation on a Raspberry Pi into a configurable router and firewall. It uses Ansible-based configuration-as-code to provision networking (systemd-networkd), DHCP/DNS, firewall rules, optional WireGuard VPN routing, Tailscale, and monitoring services such as Prometheus Node Exporter.

## Getting started

### Prerequisites
- A Raspberry Pi 4B or newer (or other device supported by Ubuntu Server).
- A USB gigabit ethernet adapter (recommended for true gigabit WAN + LAN split).
- Ubuntu Server 24.04 (or similar supported release) installed on the Pi's SD card.
- Basic familiarity with Ansible and editing YAML inventory files.

### Installation steps (quick)
1. Flash Ubuntu Server to an SD card following the Ubuntu Pi instructions.
2. Copy or create an `inventory.yml` for your device (see `rpi/inventory.yml` as an example).
3. Replace the `network-config` and `user-data` files on the SD card with the files from `rpi/` in this repo.
4. Boot the Pi connected to your ISP (WAN) and your LAN switch (LAN) using the USB ethernet adapter.
5. Once booted, cloud-init on the device will start `ansible-pull` automatically (first boot). Wait for that process to finish before attempting further changes.

## Configuring

On first boot cloud-init runs `ansible-pull` automatically to apply the configured playbook. Modifying (or `touch`ing) the `inventory.yml` on the device triggers `ansible-pull` to re-run; wait for that run to complete before making other changes.

**Note about first-boot playbook URL/ref**

The playbook Git URL and ref used during the initial (first boot) `ansible-pull` are taken from the cloud-init `user-data` you place on the SD card (see the `rpi/` files). If you need to change which repository or ref is used for first-boot provisioning, edit the `user-data` on the SD card before booting the device. After first boot, `ansible-pull` runs use the `inventory.yml` on the device to control subsequent pulls.

## Why I wrote this (motivation)

### Declarative configuration

I wanted a declarative approach to configuring and maintaining my router and firewall. I previously ran OpenWRT and similar firmwares and had to configure it through a web UI. I could backup NVRAM, but those backups were often not transferable between firmware versions. With dozens of DHCP leases and firewall rules, manual reconfiguration was too tedious.

### Just use Linux

I wanted a fully featured home router and firewall with Linux and available packages and wanted to prove I could.

### Systemd is good enough

Other solutions and tutorials often ignore that systemd and popular Linux distributions already ship most router features. Additional software is not always necessary. For example, systemd-networkd has a DHCP server and IPv6 router advertisements. My aim is to minimize software and leverage systemd where possible.

### Raspberry Pi line rate

I also wanted to prove a Raspberry Pi 4 could achieve gigabit ethernet line rate.

## Features

- DHCP server with reservations for LAN devices.
- Local DNS resolution for LAN clients.
- Encrypted DNS (DNS-over-TLS) optional for LAN clients.
- IPv6 support (requires ISP prefix delegation; includes ULA addressing).
- Guest network VLAN with isolated DHCP/DNS.
- VPN VLAN where all traffic is routed through WireGuard.
- WireGuard VPN client support.
- Tailscale support (optional).
- Port forwarding from WAN to LAN services.
- Automated updates with scheduled reboots.
- Monitoring via Prometheus Node Exporter (optional).
- Remote syslog output (optional).
- Startup diagnostics (DHCP lease viewer / quick status).

## How do I?

### Set admin password or SSH authorized key

To set the admin password, provide an encrypted shadow-format password string in your inventory as `firewall_admin_user_password_hash`.

Example:

```yaml
firewall_admin_user_password_hash: '$6$examplehash...'
```

You can generate a suitable hash locally using tools such as `mkpasswd --method=SHA-512` (from `whois`) or a `python` snippet that uses `crypt`.

To set an SSH authorized key, set `firewall_admin_user_authorized_key` to the public key string in the inventory.

### Match WAN/LAN NICs on systems with multiple adapters

Use `firewall_wan_iface_networkd_link_match` and `firewall_lan_iface_networkd_link_match` to match the physical NICs that should be assigned WAN and LAN roles. This is useful when `eth0/eth1` ordering is not stable across reboots or hardware changes. The values are passed to systemd-networkd link match rules.

Example:

```yaml
firewall_wan_iface_networkd_link_match: "Property=ID_BUS=usb"
firewall_lan_iface_networkd_link_match: "Driver=bcmgenet"
```

### Configure the DHCP server

DHCP is enabled for the LAN interface. Configure the pool size and lease times with:

```yaml
firewall_lan_dhcp_pool_offset: 10
firewall_lan_dhcp_pool_size: 200
firewall_lan_dhcp_default_lease_time_sec: 7200
firewall_lan_dhcp_max_lease_time_sec: 21600
```

### Set static DHCP reservations

Use `firewall_dhcp_reservations` to assign fixed IPs by MAC address:

```yaml
firewall_dhcp_reservations:
  - hostname: nas
    ip_address: 192.168.1.5
    mac_address: A0:B1:C2:D3:E4:F5
  - hostname: printer
    ip_address: 192.168.1.6
    mac_address: F9:E8:D7:C6:B5:A4
```

### Configure the guest VLAN

Enable and configure the guest VLAN for isolated DHCP/DNS:

```yaml
firewall_lan_guests_vlan_id: 6
firewall_lan_guests_router_ip_address: 192.168.200.254/24
firewall_lan_guests_dhcp_pool_offset: 10
firewall_lan_guests_dhcp_pool_size: 200
```

### Configure the VPN VLAN (all traffic through WireGuard)

Enable the VPN VLAN and provide WireGuard peer settings. All traffic from this VLAN is routed through the WireGuard interface.

```yaml
firewall_enable_lan_vpn: true
firewall_lan_vpn_vlan_id: 2
firewall_lan_vpn_router_ip_address: 192.168.254.1/24
firewall_lan_vpn_wg_listen_port: 51820
firewall_lan_vpn_wg_peer_allowed_ips: 0.0.0.0/0
firewall_lan_vpn_wg_private_key: "<private-key>"
firewall_lan_vpn_wg_peer_public_key: "<peer-public-key>"
firewall_lan_vpn_wg_peer_endpoint: "vpn.example.com:51820"
```

### Add a port forward (expose a service)

Port forwarding opens a port on the WAN interface and forwards traffic to a LAN host:

```yaml
firewall_port_forwards:
  - description: "Web App"
    address_from: 0.0.0.0/0
    ports_from: 443
    ports_to: 8443
    proto: tcp
    address_to: 192.168.1.10
```

`ports_from` is the WAN port to open on the router. `ports_to` is the destination port on the LAN host at `address_to`.

### Configure IPv6 (prefix delegation)

IPv6 routing depends on your ISP providing prefix delegation. If your ISP does not provide PD, global IPv6 routing will not work. ULA addressing is still configured for internal use.

### Enable Tailscale

Provide an auth key in inventory to enable Tailscale provisioning:

```yaml
firewall_tailscale_auth_key: "tskey-..."
```

### Configure local DNS private domains

Add private domains that should resolve to local IPs:

```yaml
firewall_dns_private_domains:
  - my.home
  - internal.my.home
```

### Enable encrypted DNS (DNS-over-TLS)

Unbound can listen on TLS ports (853/443) when TLS is enabled. Set:

```yaml
firewall_unbound_enable_tls: true
```

Provide TLS credentials at:

- `/etc/unbound/unbound_server.key`
- `/etc/unbound/unbound_server.pem`

Ubuntu ships a self-signed Unbound certificate at these paths. DNS-over-HTTPS is not implemented.

### Enable Prometheus Node Exporter

```yaml
firewall_enable_prometheus_node_exporter: true
```

The exporter listens on the LAN interface on port 9100.

### Configure remote syslog

```yaml
firewall_rsyslog_udp_server: 192.168.1.10:514
firewall_rsyslog_tcp_server: 192.168.1.10:514
```

### Configure automatic updates

```yaml
firewall_upgrade_automatic_reboot: true
firewall_upgrade_reboot_time: '04:55'
```

### Disable Raspberry Pi tunings on non-Pi hardware

```yaml
firewall_enable_rpi_tunings: false
```

### Update the playbook ref after first boot (stable version)

To pin to a stable tag or branch after the initial boot, set:

```yaml
firewall_ansible_playbook_git_ref: 'v1.2.3'
firewall_ansible_playbook_git_url: 'https://github.com/kmbulebu/rpi-firewall.git'
```

First-boot uses the `user-data` on the SD card; later runs use `inventory.yml`.

### View DHCP server leases

A helper script is included in the repo at `roles/firewall/files/tools/show_dhcp_leases.py` to present DHCP leases neatly.

Example usage (from repo root):

```bash
sudo python3 roles/firewall/files/tools/show_dhcp_leases.py
```

## Variables

The table below lists role variables defined in `roles/firewall/defaults/main.yml` and their default values. Variables that are derived from other variables (found in `roles/firewall/vars/main.yml`) are omitted.

| Variable | Default | Description |
|---|---|---|
| `firewall_ansible_playbook_git_url` | `https://github.com/kmbulebu/rpi-firewall.git` | Repository URL used by ansible-pull for updates. |
| `firewall_ansible_playbook_git_ref` | `master` | Branch, tag, or ref to check out on updates. |
| `firewall_ansible_playbook_filename` | `playbook.yml` | Playbook filename executed by ansible-pull. |
| `firewall_ansible_inventory` | `/boot/firmware/inventory.yml` | Inventory path on the target device. |
| `firewall_admin_user_password_hash` | `'$6$mPBFViTIy1dObC2$mYr5HlI2uiZ9DsPvvLFz8CePmCgcyyddlQ.R9tN6vibTMTZJ4XiNtADYv4cwx9Ocxqb9ZFzwvziOPPIfC9I5K0'` | Shadow password hash for the `firewall` admin user. |
| `firewall_domain` | `my.home` | Base DNS domain advertised to LAN clients. |
| `firewall_dns_private_domains` | `[]` | Private DNS zones that should resolve to local IPs. |
| `firewall_enable_prometheus_node_exporter` | `false` | Enable Prometheus node exporter on the router. |
| `firewall_wan_iface_networkd_link_match` | `Property=ID_BUS=usb` | Match rule for binding the WAN interface. |
| `firewall_wan_iface` | `wan0` | Logical name assigned to the WAN interface. |
| `firewall_lan_iface_networkd_link_match` | `Driver=bcmgenet` | Match rule for binding the LAN interface. |
| `firewall_lan_iface` | `lan0` | Logical name assigned to the LAN interface. |
| `firewall_lan_dhcp_pool_offset` | `10` | Offset from router IP for the first LAN DHCP lease. |
| `firewall_lan_dhcp_pool_size` | `200` | Number of DHCP leases available on the LAN. |
| `firewall_lan_dhcp_default_lease_time_sec` | `7200` | Default DHCP lease time in seconds. |
| `firewall_lan_dhcp_max_lease_time_sec` | `21600` | Maximum DHCP lease time in seconds. |
| `firewall_wan_device_set_mac_address` | `` | Optional MAC address override for the WAN NIC. |
| `firewall_lan_guests_iface` | `lan_guests` | Interface name for the guest VLAN. |
| `firewall_lan_guests_router_ip_address` | `192.168.200.254/24` | Router IP/prefix for the guest VLAN. |
| `firewall_lan_guests_vlan_id` | `6` | VLAN ID used for the guest network. |
| `firewall_lan_guests_dhcp_pool_offset` | `10` | Offset from router IP for guest DHCP leases. |
| `firewall_lan_guests_dhcp_pool_size` | `200` | Number of DHCP leases in the guest VLAN pool. |
| `firewall_vpn_client_iface` | `wg0` | WireGuard client interface name. |
| `firewall_vpn_vrf_iface` | `vpn_vrf0` | VRF interface name used for VPN routing. |
| `firewall_lan_vpn_iface` | `lan_vpn` | Interface name for the VPN VLAN. |
| `firewall_lan_vpn_router_ip_address` | `192.168.254.1/24` | Router IP/prefix for the VPN VLAN. |
| `firewall_lan_vpn_vlan_id` | `2` | VLAN ID used for the VPN network. |
| `firewall_lan_vpn_wg_listen_port` | `51820` | WireGuard listen port on the router. |
| `firewall_lan_vpn_wg_peer_allowed_ips` | `0.0.0.0/0` | Allowed IPs routed through the WireGuard peer. |
| `firewall_lan_vpn_wg_peer_persistent_keep_alive` | `15` | WireGuard keepalive interval in seconds. |
| `firewall_name_servers` | `- 1.1.1.3#family.cloudflare-dns.com<br>- 1.0.0.3#family.cloudflare-dns.com` | Upstream DNS resolvers for Unbound. |
| `firewall_ntp_servers` | `- ntp.ubuntu.com` | NTP servers used to synchronize system time. |
| `firewall_router_hostname` | `router` | Hostname applied to the router. |
| `firewall_router_ip_address` | `192.168.1.1/24` | Router LAN IP address and prefix. |
| `firewall_motd_dhcp_leases_limit` | `5` | Max DHCP leases shown in the MOTD display. |
| `firewall_upgrade_reboot_time` | `'04:55'` | UTC time for unattended upgrade reboots. |
| `firewall_upgrade_automatic_reboot` | `true` | Allow automatic reboot after unattended upgrades. |
| `firewall_tailscaled_listen_port` | `0` | Tailscale daemon listen port (0 for default). |
| `firewall_tailscale_iface` | `tailscale0` | Interface name for Tailscale traffic. |
| `firewall_port_forwards` | `[]` | List of WAN-to-LAN port forwarding rules. |
| `firewall_dhcp_reservations` | `[]` | Static DHCP reservation entries for LAN clients. |
| `firewall_skip_mounts` | `false` | Skip fstab and mount management (testbench). |
| `firewall_unbound_cpu_affinity` | `""` | CPU affinity for the Unbound service. |
| `firewall_force_networkd_restart` | `false` | Force a systemd-networkd restart when true. |
| `firewall_unbound_enable_tls` | `true` | Enable Unbound TLS listeners for DoT. |
| `firewall_force_resolved_restart` | `false` | Force a systemd-resolved restart when true. |
| `firewall_enable_lan_vpn` | `false` | Enable the VPN VLAN and WireGuard routing. |
| `firewall_enable_rpi_tunings` | `true` | Enable Raspberry Pi-specific tuning tasks. |
