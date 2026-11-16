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
                      lan_broadcast='192.168.1.255', lan_guests_broadcast='192.168.200.255',
                      lan_vpn_broadcast='192.168.254.255',
                      lan_guests_address='192.168.200.254',
                      lan_guests_cidr='192.168.200.0/24',
                      lan_vpn_cidr='192.168.254.0/24',
                      ip6_ula_cidr='fd12:3456::/48', lan_ip6_ula_cidr='fd12:3456::/64')
        env = Environment(loader=FileSystemLoader(ROLE / 'templates'), undefined=StrictUndefined)
        env.filters['bool'] = bool
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

    def test_independent_maximums_and_encoded_search_domain(self):
        values = yaml.safe_load((ROLE / 'defaults/main.yml').read_text())
        values.update(domain='engineering.example.test', lan_address='192.168.1.1',
                      lan_broadcast='192.168.1.255', lan_guests_broadcast='192.168.200.127',
                      lan_vpn_broadcast='192.168.254.255', lan_cidr='192.168.1.0/24',
                      lan_guests_address='192.168.200.1', lan_guests_cidr='192.168.200.0/25',
                      lan_vpn_cidr='192.168.254.0/24', ip6_ula_cidr='fd12:3456::/48',
                      lan_ip6_ula_cidr='fd12:3456::/64')
        env = Environment(loader=FileSystemLoader(ROLE / 'templates'), undefined=StrictUndefined)
        env.filters['bool'] = bool
        cases = [('lan', '10-lan'), ('lan_guests', '30-lan-guests'), ('lan_vpn', '20-lan-vpn')]
        for index, (prefix, filename) in enumerate(cases):
            maximum = 172800 + index * 3600
            values[prefix + '_dhcp_max_lease_seconds'] = maximum
        for index, (prefix, filename) in enumerate(cases):
            with self.subTest(network=prefix):
                rendered = env.get_template(f'networkd/{prefix}/{filename}.network').render(**values)
                config = configparser.ConfigParser(strict=False, interpolation=None)
                config.read_string(rendered)
                self.assertEqual(config.getint('DHCPServer', 'DefaultLeaseTimeSec'),
                                 values[prefix + '_dhcp_lease_seconds'])
                self.assertEqual(config.getint('DHCPServer', 'MaxLeaseTimeSec'), 172800 + index * 3600)
                self.assertIn('SendOption=28:ipv4address:' + values[prefix + '_broadcast'], rendered)
                self.assertNotIn('SendOption=58:', rendered)
                self.assertNotIn('SendOption=59:', rendered)
                if prefix == 'lan':
                    self.assertEqual(config.get('DHCPServer', 'Domain'), 'engineering.example.test')
                    self.assertIn(r'SendOption=119:string:\x0bengineering\x07example\x04test\x00', rendered)
                    self.assertNotIn('SendOption=15:', rendered)
                else:
                    self.assertFalse(config.has_option('DHCPServer', 'Domain'))
                    self.assertNotIn('SendOption=119:', rendered)
