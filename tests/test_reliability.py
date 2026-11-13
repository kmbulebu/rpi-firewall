"""Exercise configurable lease durations across all downstream networks."""
import configparser
from pathlib import Path
import unittest

from jinja2 import Environment, FileSystemLoader, StrictUndefined
import yaml

ROLE = Path(__file__).resolve().parents[1] / 'roles/firewall'


class LeaseTests(unittest.TestCase):
    def test_each_network_uses_its_own_duration(self):
        values = yaml.safe_load((ROLE / 'defaults/main.yml').read_text())
        values.update(lan_address='192.168.1.1', lan_cidr='192.168.1.0/24',
                      lan_guests_address='192.168.200.254',
                      lan_guests_cidr='192.168.200.0/24',
                      lan_vpn_cidr='192.168.254.0/24',
                      ip6_ula_cidr='fd12:3456::/48', lan_ip6_ula_cidr='fd12:3456::/64')
        env = Environment(loader=FileSystemLoader(ROLE / 'templates'), undefined=StrictUndefined)
        cases = [('lan', '10-lan', 'lan_dhcp_lease_seconds', 86400),
                 ('lan_guests', '30-lan-guests', 'lan_guests_dhcp_lease_seconds', 3600),
                 ('lan_vpn', '20-lan-vpn', 'lan_vpn_dhcp_lease_seconds', 86400)]
        for overrides in (False, True):
            for index, (directory, filename, variable, default) in enumerate(cases):
                expected = 7200 + index * 3600 if overrides else default
                if overrides:
                    values[variable] = expected
                with self.subTest(network=directory, overrides=overrides):
                    rendered = env.get_template(f'networkd/{directory}/{filename}.network').render(**values)
                    config = configparser.ConfigParser(strict=False, interpolation=None)
                    config.read_string(rendered)
                    self.assertEqual(config.getint('DHCPServer', 'DefaultLeaseTimeSec'), expected)
                    self.assertEqual(config.getint('DHCPServer', 'MaxLeaseTimeSec'), expected)
