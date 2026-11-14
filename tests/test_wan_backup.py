"""Check rendered uplink configuration with backup enabled and disabled."""
import configparser
from pathlib import Path
import subprocess
import unittest
import xml.etree.ElementTree as ET

from jinja2 import Environment, FileSystemLoader, StrictUndefined
import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/firewall'


class WanBackupTests(unittest.TestCase):
    def setUp(self):
        self.values = yaml.safe_load((ROLE / 'defaults/main.yml').read_text())
        self.values.update(
            wan_iface_networkd_link_match='PermanentMACAddress=70:88:6b:8d:9f:e0',
            wan_backup_iface_networkd_link_match='PermanentMACAddress=96:0b:9e:b5:54:6d',
            ip6_ula_cidr='fd12:3456::/48',
        )
        self.env = Environment(loader=FileSystemLoader(ROLE / 'templates'),
                               undefined=StrictUndefined)
        # These templates use Ansible's boolean filter.
        self.env.filters['bool'] = lambda value: value in (True, 1, 'true', 'yes', 'on', '1')

    def render(self, name):
        return self.env.get_template(name).render(**self.values)

    def test_uplink_metrics_and_tables(self):
        for name, expected in [('wan', 1024), ('wan-backup', 100)]:
            config = configparser.ConfigParser(strict=False, interpolation=None)
            config.read_string(self.render(f'networkd/{name}/10-{name}.network'))
            self.assertEqual(config.getint('DHCPv4', 'RouteMetric'), expected)
            self.assertEqual(config.getint('IPv6AcceptRA', 'RouteMetric'), expected)
            self.assertFalse(config.has_option('Network', 'VRF'))
            self.assertFalse(config.has_option('DHCPv4', 'RouteTable'))
            self.assertEqual(config.get('DHCPv4', 'UseDNS'), 'false')
        primary = self.render('networkd/wan/10-wan.network')
        self.assertIn('Gateway=_ipv6ra\nMetric=1024', primary)
        backup = self.render('networkd/wan-backup/10-wan-backup.network')
        self.assertIn('IgnoreCarrierLoss=no', backup)
        self.assertNotIn('DHCPPrefixDelegation=yes', backup)

    def test_optional_primary_dhcpv6_identity(self):
        primary = 'networkd/wan/10-wan.network'
        self.assertNotIn('DUIDType=', self.render(primary))
        self.assertNotIn('DUIDRawData=', self.render(primary))
        self.values.update(wan_dhcpv6_duid_type='vendor',
                           wan_dhcpv6_duid_raw_data='00:00:0d:e9:45:58:41:4d:50:4c:45')
        rendered = self.render(primary)
        self.assertIn('DUIDType=vendor', rendered)
        self.assertIn('DUIDRawData=00:00:0d:e9:45:58:41:4d:50:4c:45', rendered)
        self.assertNotIn('DUIDType=', self.render('networkd/wan-backup/10-wan-backup.network'))

    def test_optional_primary_dhcpv4_identity(self):
        def config():
            result = configparser.ConfigParser(strict=False, interpolation=None)
            result.read_string(self.render('networkd/wan/10-wan.network'))
            return result
        self.assertEqual(config()['DHCPv4']['ClientIdentifier'], 'mac')
        self.assertNotIn('DUIDType', config()['DHCPv4'])
        self.assertNotIn('IAID', config()['DHCPv4'])
        self.values.update(wan_dhcpv4_client_identifier='duid',
                           wan_dhcpv4_duid_type='vendor',
                           wan_dhcpv4_duid_raw_data='00:00:0d:e9:45',
                           wan_dhcpv4_iaid=1)
        self.assertEqual(config()['DHCPv4']['ClientIdentifier'], 'duid')
        self.assertEqual(config()['DHCPv4']['DUIDType'], 'vendor')
        self.assertEqual(config()['DHCPv4']['DUIDRawData'], '00:00:0d:e9:45')
        self.assertEqual(config()['DHCPv4']['IAID'], '1')
        self.assertNotIn('DUIDType', config()['DHCPv6'])
        self.assertNotIn('DUIDType=', self.render('networkd/wan-backup/10-wan-backup.network'))
        self.values['wan_dhcpv4_iaid'] = 0
        self.assertEqual(config()['DHCPv4']['IAID'], '0')
        self.values.update(wan_dhcpv4_client_identifier='duid-only', wan_dhcpv4_iaid='')
        self.assertEqual(config()['DHCPv4']['ClientIdentifier'], 'duid-only')
        self.assertNotIn('IAID', config()['DHCPv4'])

    def test_backup_identity_does_not_spoof_primary_mac(self):
        link = self.render('networkd/wan-backup/00-wan-backup.link')
        self.assertIn('PermanentMACAddress=96:0b:9e:b5:54:6d', link)
        self.assertIn('Name=wan-backup', link)
        self.assertNotIn('\nMACAddress=', link)

    def test_firewall_with_optional_hotspot_absent(self):
        for enabled in (False, True):
            self.values['wan_backup_enabled'] = enabled
            zone = ET.fromstring(self.render('firewalld/zones/external.xml'))
            interfaces = [item.attrib['name'] for item in zone.findall('interface')]
            self.assertEqual(interfaces, ['wan0', 'wan-backup'] if enabled else ['wan0'])
            self.assertIsNotNone(zone.find('masquerade'))
            conf = self.render('firewalld/firewalld.conf')
            self.assertIn('IPv6_rpfilter=yes', conf)
            self.assertIn('NftablesFlowtable=wan0 lan0', conf)

    def test_status_script_parses_with_both_settings(self):
        for enabled in (False, True):
            self.values['wan_backup_enabled'] = enabled
            script = self.render('motd/60-wan-status')
            result = subprocess.run(['bash', '-n'], input=script, text=True,
                                    capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('for IFACE in wan0' + (' wan-backup' if enabled else '') + '; do', script)


if __name__ == '__main__':
    unittest.main()
