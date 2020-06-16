## Introduction

The Raspberry Pi 4B is the first Raspberry Pi device with true gigabit ethernet and USB 3 ports, making it capable of performing as a gigabit ethernet router and firewall. This project transforms a standard installation of Ubuntu Server on a Raspberry Pi into an Internet firewall and router.

## Prerequisites

- A Raspberry Pi 4B or newer
- A USB gigabit ethernet adapter
- Ubuntu Server 20.04 LTS

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

### WAN Interface (Internet Side)

#### WAN Ethernet Device

The ethernet device to use for the wide area network. The built-in ethernet port
of a Raspberry Pi is `eth0`.

```
wan_iface: eth0
```

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

#### VLAN ID

The VLAN ID to use for the LAN privacy network. All outbound traffic on this network will route through a Wireguard VPN.

```
lan_vpn_vlan_id: 2
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
