"""Run only in a disposable Linux container with networkd, Ansible and NET_ADMIN.

The test modifies /etc/systemd/network and /etc/firewalld inside that container.
Start a system D-Bus and networkd first. It checks configuration/device lifecycle,
not VPN peer connectivity or production firewall packet handling.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/firewall'


def command(*args, **kwargs):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, **kwargs)


def main():
    if os.environ.get('RPI_FIREWALL_DISPOSABLE_TEST') != '1':
        raise SystemExit('Set RPI_FIREWALL_DISPOSABLE_TEST=1 only in a disposable container')
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        role = root / 'roles/vpn_test'
        (role / 'tasks').mkdir(parents=True)
        for directory in ('defaults', 'files', 'templates'):
            (role / directory).symlink_to(ROLE / directory)
        # Exercise the real firewalld template and upgrade cleanup tasks without
        # the host package/module management tasks in the full firewall role.
        firewall_tasks = yaml.safe_load((ROLE / 'tasks/firewalld.yml').read_text())
        firewall_tasks = [task for task in firewall_tasks if task.get('name') in (
            'Remove obsolete lockdown whitelist', 'Template firewalld configuration')]
        assert len(firewall_tasks) == 2
        Path('/etc/firewalld/lockdown-whitelist.xml').write_text('<whitelist/>')
        tasks = firewall_tasks + [
            {'name': 'Parse additional network templates', 'ansible.builtin.template': {
                'src': 'networkd/{{ item }}', 'dest': '/etc/systemd/network/{{ item | basename }}'},
             'loop': ['wan/10-wan.network', 'wan-backup/10-wan-backup.network',
                      'lan_guests/30-lan-guests.network'],
             'notify': 'restart systemd-networkd'},
            {'ansible.builtin.include_tasks': str(ROLE / 'tasks/validate_vpn.yml')},
            {'name': 'Render LAN attachment', 'ansible.builtin.template': {
                'src': 'networkd/lan/10-lan.network', 'dest': '/etc/systemd/network/10-lan.network'},
             'notify': 'restart systemd-networkd'},
            {'ansible.builtin.include_tasks': str(ROLE / 'tasks/wireguard_client.yml')},
            {'name': 'Render WireGuard MOTD', 'ansible.builtin.template': {
                'src': 'motd/65-wireguard-status', 'dest': '/run/test-wg-motd', 'mode': '0755'}},
        ]
        (role / 'tasks/main.yml').write_text(yaml.safe_dump(tasks))
        play = [{'hosts': 'localhost', 'gather_facts': False, 'connection': 'local',
                 'roles': ['vpn_test'], 'handlers': [
                     {'name': 'restart systemd-networkd', 'ansible.builtin.command': 'networkctl reload'},
                     {'name': 'reload firewalld', 'ansible.builtin.debug': {'msg': 'Firewall reload deferred in lifecycle test'}},
                 ]}]
        (root / 'play.yml').write_text(yaml.safe_dump(play))
        values = dict(lan_iface='lan-test', router_ip_address='192.0.2.1/24', lan_address='192.0.2.1',
                      lan_cidr='192.0.2.0/24', lan_guests_iface='guest-test', lan_guests_cidr='198.51.100.0/24',
                      lan_guests_address='198.51.100.1',
                      lan_vpn_cidr='192.168.254.0/24', ip6_ula_cidr='fd12:3456::/48',
                      lan_ip6_ula_cidr='fd12:3456::/64')
        private = command('wg', 'genkey').strip()
        peer = command('wg', 'pubkey', input=command('wg', 'genkey')).strip()
        credentials = dict(lan_vpn_wg_address='10.0.0.2/24', lan_vpn_wg_private_key=private,
                           lan_vpn_wg_peer_public_key=peer, lan_vpn_wg_peer_endpoint='192.0.2.254:51820')
        varfile = root / 'vars.yml'

        def apply(extra, expect_failure=False):
            varfile.write_text(yaml.safe_dump(values | extra))
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(root / 'play.yml'),
                                     '-e', '@' + str(varfile)], text=True, capture_output=True)
            if (result.returncode != 0) != expect_failure:
                raise AssertionError(result.stdout + result.stderr)
            return result.stdout

        def links():
            return {item['ifname']: item for item in json.loads(command('ip', '-j', '-d', 'link', 'show'))}

        def wait_for(names, absent=()):
            for _ in range(60):
                state = links()
                if set(names) <= state.keys() and not set(absent) & state.keys():
                    return state
                time.sleep(0.1)
            log = Path('/tmp/networkd.log')
            details = log.read_text()[-12000:] if log.exists() else 'No networkd log available'
            raise AssertionError(f'Unexpected links: {list(links())}\n{details}')

        apply({'enable_lan_vpn': False})
        assert not Path('/etc/firewalld/lockdown-whitelist.xml').exists()
        assert 'changed=0 ' in apply({'enable_lan_vpn': False})
        print('PASS: disabled needs no credentials; second run unchanged', flush=True)
        apply(credentials | {'enable_lan_vpn': True, 'lan_vpn_mtu': 1279}, expect_failure=True)
        assert not Path('/etc/systemd/network/30-wireguard-client.netdev').exists()
        print('PASS: invalid MTU rejected before deployment', flush=True)
        apply(credentials | {'enable_lan_vpn': True})
        wait_for(['lan_vpn', 'wg0', 'vpn_vrf0'])
        motd = command('bash', '/run/test-wg-motd', env=os.environ | {'WG_HIDE_KEYS': 'never'})
        assert 'interface: wg0' in motd and 'private key: (hidden)' in motd
        assert private not in motd
        assert 'changed=0 ' in apply(credentials | {'enable_lan_vpn': True})
        print('PASS: enable creates VPN devices; second run unchanged', flush=True)
        renamed = credentials | dict(enable_lan_vpn=True, lan_vpn_iface='privacy',
                                      vpn_client_iface='tunnel1', vpn_vrf_iface='privacy_vrf', lan_vpn_mtu=1400)
        apply(renamed)
        state = wait_for(['privacy', 'tunnel1', 'privacy_vrf'], ['lan_vpn', 'wg0', 'vpn_vrf0'])
        for iface in ('privacy', 'tunnel1', 'privacy_vrf'):
            assert state[iface]['mtu'] == 1400, state[iface]
        motd = command('bash', '/run/test-wg-motd', env=os.environ | {'WG_HIDE_KEYS': 'never'})
        assert 'interface: tunnel1' in motd and private not in motd
        assert 'BindCarrier=tunnel1' in Path('/etc/systemd/network/20-lan-vpn.network').read_text()
        assert 'SendOption=26:uint16:1400' in Path('/etc/systemd/network/20-lan-vpn.network').read_text()
        print('PASS: rename retires previous devices; custom MTU applied', flush=True)
        # Reset inventory names to defaults: cleanup must use previous file names.
        apply({'enable_lan_vpn': False})
        wait_for([], ['privacy', 'tunnel1', 'privacy_vrf', 'lan_vpn', 'wg0', 'vpn_vrf0'])
        assert 'VPN disabled' in command('bash', '/run/test-wg-motd')
        assert not Path('/etc/systemd/network/30-wireguard-client.netdev').exists()
        assert not Path('/var/lib/rpi-firewall/retired-vpn-links.json').exists()
        for zone in ('internal_vpn', 'external_vpn'):
            assert '<interface ' not in Path(f'/etc/firewalld/zones/{zone}.xml').read_text()
        assert 'changed=0 ' in apply({'enable_lan_vpn': False})
        print('PASS: disable removes old custom devices without credentials; second run unchanged', flush=True)
        command('firewall-offline-cmd', '--check-config')
        networkd_log = Path('/tmp/networkd.log').read_text()
        assert 'IPForward=' not in networkd_log, networkd_log
        assert 'Unknown key' not in networkd_log, networkd_log
        assert 'Unknown section' not in networkd_log, networkd_log
        print('PASS: networkd accepts forwarding settings; firewalld config validates without lockdown', flush=True)


if __name__ == '__main__':
    main()
