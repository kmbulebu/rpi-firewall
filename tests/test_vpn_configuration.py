"""VPN configuration and retirement regression tests."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from jinja2 import Environment, FileSystemLoader, StrictUndefined
import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/firewall'
spec = importlib.util.spec_from_file_location('vpn_links', ROLE / 'files/tools/vpn_links.py')
vpn_links = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vpn_links)


class VpnConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.values = yaml.safe_load((ROLE / 'defaults/main.yml').read_text())
        self.values.update(lan_address='192.168.1.1', lan_cidr='192.168.1.0/24',
                           lan_guests_cidr='192.168.200.0/24',
                           lan_vpn_cidr='192.168.254.0/24',
                           ip6_ula_cidr='fd12:3456::/48', lan_ip6_ula_cidr='fd12:3456::/64')
        self.env = Environment(loader=FileSystemLoader(ROLE / 'templates'), undefined=StrictUndefined)
        self.env.filters['bool'] = bool

    def render(self, path):
        return self.env.get_template(path).render(**self.values)

    def test_disabled_has_no_lan_attachment_route_or_firewall_binding(self):
        lan = self.render('networkd/lan/10-lan.network')
        self.assertNotIn('VLAN=lan_vpn', lan)
        self.assertNotIn('Destination=192.168.254.0/24', lan)
        for zone in ('internal_vpn', 'external_vpn'):
            xml = ET.fromstring(self.render(f'firewalld/zones/{zone}.xml'))
            self.assertEqual(xml.findall('interface'), [])
            self.assertIsNone(xml.find('masquerade'))
        external = ET.fromstring(self.render('firewalld/zones/external.xml'))
        self.assertFalse(any(rule.attrib.get('port') == '51820' for rule in external.iter('source-port')))
        self.assertNotIn('"lan_vpn"', self.render('motd/75-dhcp-leases'))

    def test_custom_names_and_mtu_stay_consistent(self):
        self.values.update(enable_lan_vpn=True, lan_vpn_iface='privacy',
                           vpn_client_iface='tunnel1', vpn_vrf_iface='privacy_vrf',
                           lan_vpn_wg_address='10.0.0.2/24', lan_vpn_wg_private_key='test',
                           lan_vpn_wg_peer_public_key='test', lan_vpn_wg_peer_endpoint='192.0.2.1:51820')
        for mtu in (1292, 1400):
            self.values['lan_vpn_mtu'] = mtu
            for directory in ('lan_vpn', 'wireguard'):
                for path in (ROLE / 'templates/networkd' / directory).iterdir():
                    rendered = self.render(f'networkd/{directory}/{path.name}')
                    self.assertIn(f'MTUBytes={mtu}', rendered)
            lan = self.render('networkd/lan_vpn/20-lan-vpn.network')
            self.assertIn('BindCarrier=tunnel1', lan)
            self.assertIn(f'SendOption=26:uint16:{mtu}', lan)
            self.assertIn('VRF=privacy_vrf', lan)
            self.assertIn('VLAN=privacy', self.render('networkd/lan/10-lan.network'))
            zone = ET.fromstring(self.render('firewalld/zones/external_vpn.xml'))
            self.assertEqual(zone.find('interface').attrib['name'], 'tunnel1')


class VpnRetirementTests(unittest.TestCase):
    def write_netdev(self, root, filename, name, kind):
        (root / filename).write_text(f'[NetDev]\nName={name}\nKind={kind}\n[WireGuard]\nPrivateKey=secret\n')

    def test_disable_and_rename_use_previous_managed_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for filename, kind in vpn_links.MANAGED:
                self.write_netdev(root, filename, 'old-' + kind, kind)
            disabled = vpn_links.plan(root, {})
            self.assertEqual([item['kind'] for item in disabled], ['vlan', 'wireguard', 'vrf'])
            self.assertNotIn('secret', json.dumps(disabled))
            self.assertEqual(vpn_links.plan(root, {'vlan': 'old-vlan'}), disabled[1:])
            self.assertEqual(vpn_links.plan(root, {item['kind']: item['name'] for item in disabled}), [])

    def test_pending_cleanup_survives_removed_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = root / 'pending.json'
            pending.write_text('[{"name":"old-wg","kind":"wireguard"}]')
            self.assertEqual(vpn_links.plan(root, {}, pending), [{'name': 'old-wg', 'kind': 'wireguard'}])
            self.assertEqual(vpn_links.plan(root, {'wireguard': 'old-wg'}, pending), [])
            with self.assertRaises(ValueError):
                vpn_links.plan(root, {'vlan': 'old-wg'}, pending)

    def test_malformed_netdev_errors_do_not_expose_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '30-wireguard-client.netdev'
            path.write_text('PrivateKey=secret')
            with self.assertRaises(ValueError) as raised:
                vpn_links.plan(tmp, {})
            self.assertNotIn('secret', str(raised.exception))

    @patch.object(vpn_links.subprocess, 'run')
    @patch.object(vpn_links.subprocess, 'check_output')
    def test_cleanup_checks_kind_and_is_idempotent(self, inspect, run):
        inspect.return_value = '[{"ifname":"old-wg","linkinfo":{"info_kind":"wireguard"}}]'
        self.assertTrue(vpn_links.delete_link('old-wg', 'wireguard'))
        run.assert_called_once_with(['ip', 'link', 'delete', 'dev', 'old-wg'], check=True)
        run.reset_mock()
        inspect.return_value = '[]'
        self.assertFalse(vpn_links.delete_link('old-wg', 'wireguard'))
        inspect.return_value = '[{"ifname":"lan0","linkinfo":{"info_kind":"dummy"}}]'
        with self.assertRaises(ValueError):
            vpn_links.delete_link('lan0', 'wireguard')
        run.assert_not_called()
