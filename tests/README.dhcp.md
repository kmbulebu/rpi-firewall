# DHCP option packet tests

Run only in a disposable Linux container. The test changes system networkd
configuration and creates three network namespaces and veth pairs. It has no
external network and never connects to the router.

From the repository root (Docker; Podman can use `--systemd=always` instead of
`--cgroupns=private`):

```sh
docker build -f tests/Dockerfile.forwarding -t rpi-dhcp-test .
docker run -d --name rpi-dhcp-test --privileged --network=none --cgroupns=private \
  --tmpfs /run --tmpfs /run/lock --tmpfs /tmp \
  -v "$PWD:/repo:ro" -w /repo rpi-dhcp-test
# Wait until systemd is ready before running the test.
docker exec rpi-dhcp-test systemctl show --property=Version --value
docker exec -e RPI_FIREWALL_DISPOSABLE_TEST=1 rpi-dhcp-test \
  python3 tests/integration_dhcp_options.py
docker rm -f rpi-dhcp-test
```

The test renders the production templates through Ansible and inspects actual
DHCP DISCOVER/OFFER/REQUEST/ACK exchanges with systemd 259 on Ubuntu 26.04.
It verifies:

- Domain option 15 and RFC 3397 search encoding on the trusted LAN only,
  including an 11-character DNS label (a hexadecimal length prefix).
- Each subnet's broadcast address, including a guest `/25`.
- Default, shorter requested, longer requested and capped leases on each network.
- Independent maximum limits and idempotent configuration.
- No duplicate options or static T1/T2 options; RFC 2131 client defaults give
  `0 < T1 < T2 < granted lease`. The test does not wait hours for renewals.
- Existing subnet mask, DNS, router and VPN MTU options remain present.
- Invalid domain names and lease limits fail before templates are written.

`dhcp_client_fixture.py` uses standard-library sockets, receiving raw IPv4
packets so the client does not need a configured address. It does not apply the
lease to an interface or test a desktop resolver's search behavior. Firewalld is
stopped in this focused lab; production firewall behavior has separate tests.
