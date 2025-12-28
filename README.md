## Introduction

The Raspberry Pi 4B is the first Raspberry Pi device with true gigabit ethernet and USB 3 ports, making it capable of performing as a gigabit ethernet router and firewall. This project transforms a standard installation of Ubuntu Server on a Raspberry Pi into an Internet firewall and router.

## Prerequisites

- A Raspberry Pi 4B or newer
- A USB gigabit ethernet adapter
- Ubuntu Server 24.04 LTS (Noble)

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
the destination server and UDP port. `192.168.1.10:514`

```
rsyslog_udp_server:
```

### Automatic Updates

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

The length of time devices may use an IP addressed assigned by the router.

```
dhcp_lease_seconds: 86400
```

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

The VPN client and associated LAN is disabled by default. Set to true to enable.

```
enable_lan_vpn: false
```

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
| `ansible_playbook_filename` | `playbook.yml` | Playbook filename in the repository. |
| `ansible_playbook_git_ref` | `master` | Git ref/branch to check out. |
| `ansible_playbook_git_url` | `https://github.com/kmbulebu/rpi-firewall.git` | Git repository URL for the playbook. |
| `domain` | `my.home` | Local domain name for LAN devices. |
| `dns_private_domains` | `[]` | List of private domains to allow DNS resolution to local IP addresses. |
| `enable_prometheus_node_exporter` | `no` | Enable Prometheus node exporter and listen on LAN interface. |
| `lan_dhcp_pool_offset` | `10` | Offset from router IP to start DHCP pool. |
| `lan_dhcp_pool_size` | `200` | Number of DHCP addresses in the LAN pool. |
| `lan_iface` | `lan0` | Desired LAN interface name. |
| `lan_iface_networkd_link_match` | `"Driver=bcmgenet"` | `systemd-networkd` match string for LAN link. |
| `lan_guests_dhcp_pool_offset` | `10` | DHCP pool offset for guests network. |
| `lan_guests_dhcp_pool_size` | `200` | DHCP pool size for guests network. |
| `lan_guests_iface` | `lan_guests` | Interface name for LAN guests network. |
| `lan_guests_router_ip_address` | `192.168.200.254/24` | Router IP/prefix for the guests network. |
| `lan_guests_vlan_id` | `6` | VLAN id for the guests network. |
| `lan_vpn_iface` | `lan_vpn` | Desired interface name for the LAN VPN VLAN. |
| `lan_vpn_router_ip_address` | `192.168.254.1/24` | Router IP/prefix for the LAN VPN network. |
| `lan_vpn_vlan_id` | `2` | VLAN id for the LAN VPN network. |
| `lan_vpn_wg_listen_port` | `51820` | WireGuard listen port for the VPN interface. |
| `lan_vpn_wg_peer_allowed_ips` | `0.0.0.0/0` | Allowed IPs for the WireGuard peer (routes through VPN). |
| `lan_vpn_wg_peer_persistent_keep_alive` | `15` | WireGuard persistent keepalive interval in seconds. |
| `motd_dhcp_leases_limit` | `5` | Number of DHCP leases shown in the MOTD per interface. |
| `name_servers` | `- 1.1.1.3#family.cloudflare-dns.com\n- 1.0.0.3#family.cloudflare-dns.com` | List of upstream name servers (default uses Cloudflare Family DNS). |
| `ntp_servers` | `- ntp.ubuntu.com` | List of NTP servers used to sync system time. |
| `router_hostname` | `router` | Hostname for the router. |
| `router_ip_address` | `192.168.1.1/24` | Router LAN IP and prefix. |
| `tailscale_iface` | `tailscale0` | Interface name for Tailscale. |
| `tailscaled_listen_port` | `0` | Listening port for tailscaled (0 = default/no specific port). |
| `upgrade_automatic_reboot` | `yes` | Whether to automatically reboot after upgrades when needed. |
| `upgrade_reboot_time` | `'04:55'` | Time (UTC) to schedule automatic reboots after upgrades. |
| `vpn_client_iface` | `wg0` | WireGuard client interface name. |
| `vpn_vrf_iface` | `vpn_vrf0` | VRF interface name for VPN routing. |
| `wan_device_set_mac_address` | `` | Optional MAC address to set on WAN device (empty by default). |
| `wan_iface` | `wan0` | Desired WAN interface name. |
| `wan_iface_networkd_link_match` | `"Property=ID_BUS=usb"` | `systemd-networkd` match string for WAN link. |

