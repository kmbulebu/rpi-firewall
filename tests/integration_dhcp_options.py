"""Exercise real networkd DHCP packets; run only in a disposable systemd container."""
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/firewall'
NETWORKS = [('lan', '10-lan', '192.0.2.1/24', 86400),
            ('lan_guests', '30-lan-guests', '198.51.100.129/25', 3600),
            ('lan_vpn', '20-lan-vpn', '203.0.113.1/24', 86400)]


def command(*args):
    result = subprocess.run(args, text=True, capture_output=True, timeout=90)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return result.stdout


def main():
    if os.environ.get('RPI_FIREWALL_DISPOSABLE_TEST') != '1':
        raise SystemExit('Set RPI_FIREWALL_DISPOSABLE_TEST=1 only in a disposable container')
    print(command('/usr/lib/systemd/systemd-networkd', '--version'), flush=True)
    command('systemctl', 'stop', 'systemd-networkd', 'firewalld')
    # No udev in this lab: networkd requires read-only sysfs to skip udev waiting.
    if not os.statvfs('/sys').f_flag & os.ST_RDONLY:
        command('mount', '-o', 'remount,bind,ro', '/sys')
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        role = root / 'roles/dhcp_test'
        (role / 'tasks').mkdir(parents=True)
        for directory in ('defaults', 'templates'):
            (role / directory).symlink_to(ROLE / directory)
        tasks = [{'ansible.builtin.import_tasks': str(ROLE / 'tasks/validate_dhcp.yml')}]
        tasks += [{'name': 'Render downstream network', 'ansible.builtin.template': {
            'src': f'networkd/{prefix}/{filename}.network',
            'dest': f'/etc/systemd/network/{filename}.network'}}
            for prefix, filename, _, _ in NETWORKS]
        (role / 'tasks/main.yml').write_text(yaml.safe_dump(tasks))
        play = [{'hosts': 'localhost', 'connection': 'local', 'gather_facts': False, 'roles': ['dhcp_test']}]
        (root / 'play.yml').write_text(yaml.safe_dump(play))
        values = dict(domain='engineering.example.test', lan_iface='srv0', lan_guests_iface='srv1',
                      lan_vpn_iface='srv2', router_ip_address=NETWORKS[0][2],
                      lan_guests_router_ip_address=NETWORKS[1][2], lan_vpn_router_ip_address=NETWORKS[2][2],
                      lan_cidr='192.0.2.0/24', lan_guests_cidr='198.51.100.128/25',
                      lan_vpn_cidr='203.0.113.0/24', ip6_ula_cidr='fd12:3456::/48',
                      lan_ip6_ula_cidr='fd12:3456::/64', lan_guests_dhcp_pool_size=100)
        for prefix, _, address, _ in NETWORKS:
            interface = ipaddress.ip_interface(address)
            values[prefix + '_address'] = str(interface.ip)
            values[prefix + '_broadcast'] = str(interface.network.broadcast_address)
        varfile = root / 'vars.yml'

        def apply(extra=None, failure=False):
            varfile.write_text(yaml.safe_dump(values | (extra or {})))
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(root / 'play.yml'),
                                     '-e', '@' + str(varfile)], text=True, capture_output=True, timeout=90)
            if (result.returncode != 0) != failure:
                raise AssertionError(result.stdout + result.stderr)
            return result.stdout

        command('ip', 'link', 'add', 'vpn_vrf0', 'type', 'vrf', 'table', '100')
        command('ip', 'link', 'set', 'vpn_vrf0', 'up')
        command('ip', 'link', 'add', 'wg0', 'type', 'dummy')
        command('ip', 'link', 'set', 'wg0', 'up')
        for index in range(3):
            command('ip', 'netns', 'add', f'client{index}')
            command('ip', 'link', 'add', f'srv{index}', 'type', 'veth', 'peer', 'name', f'cli{index}')
            command('ip', 'link', 'set', f'cli{index}', 'netns', f'client{index}')
            command('ip', 'link', 'set', f'srv{index}', 'up')
            command('nsenter', f'--net=/run/netns/client{index}', 'ip', 'link', 'set', f'cli{index}', 'up')
        apply()
        assert 'changed=0 ' in apply()
        command('systemctl', 'start', 'systemd-networkd')
        command('networkctl', 'reload')

        def check_packets(index, expected, requested=None):
            args = ['nsenter', f'--net=/run/netns/client{index}', 'python3',
                    str(ROOT / 'tests/dhcp_client_fixture.py'), f'cli{index}']
            if requested is not None:
                args += [str(requested)]
            exchange = json.loads(command(*args))
            prefix, _, _, _ = NETWORKS[index]
            for phase, options in exchange.items():
                assert all(len(items) == 1 for items in options.values()), (phase, options)
                lease = int.from_bytes(bytes.fromhex(options['51'][0]), 'big')
                assert lease == expected, (prefix, phase, lease, expected)
                assert bytes.fromhex(options['28'][0]) == ipaddress.ip_address(values[prefix + '_broadcast']).packed
                assert bytes.fromhex(options['1'][0]) == ipaddress.ip_interface(NETWORKS[index][2]).netmask.packed
                assert bytes.fromhex(options['3'][0]) == ipaddress.ip_interface(NETWORKS[index][2]).ip.packed
                expected_dns = b'\x01\x01\x01\x01\x01\x00\x00\x01' if index == 2 else ipaddress.ip_interface(NETWORKS[index][2]).ip.packed
                assert bytes.fromhex(options['6'][0]) == expected_dns
                if index == 0:
                    assert bytes.fromhex(options['15'][0]) == b'engineering.example.test'
                    assert bytes.fromhex(options['119'][0]) == b'\x0bengineering\x07example\x04test\0'
                else:
                    assert '15' not in options and '119' not in options, options
                # No static T1/T2 overrides: RFC 2131 client defaults track the negotiated lease.
                assert '58' not in options and '59' not in options, options
                assert 0 < lease / 2 < lease * 7 / 8 < lease
                if index == 2:
                    assert int.from_bytes(bytes.fromhex(options['26'][0]), 'big') == 1292
            print(f'PASS: {prefix} OFFER/ACK lease={expected}, requested={requested}, options and timer defaults', flush=True)

        for index, (_, _, _, default) in enumerate(NETWORKS):
            check_packets(index, default)
            check_packets(index, 600, 600)
            check_packets(index, default, default * 4)
        overrides = {prefix + '_dhcp_lease_seconds': 1800 + index * 600
                     for index, (prefix, *_) in enumerate(NETWORKS)}
        overrides.update({prefix + '_dhcp_max_lease_seconds': 7200 + index * 3600
                          for index, (prefix, *_) in enumerate(NETWORKS)})
        apply(overrides)
        assert 'changed=0 ' in apply(overrides)
        command('networkctl', 'reload')
        command('networkctl', 'reconfigure', 'srv0', 'srv1', 'srv2')
        for index in range(3):
            check_packets(index, 1800 + index * 600)
            check_packets(index, 3600, 3600)
            check_packets(index, 7200 + index * 3600, 28800)
        deployed = {Path('/etc/systemd/network') / (filename + '.network'): None
                    for _, filename, _, _ in NETWORKS}
        deployed = {path: path.read_bytes() for path in deployed}
        for invalid in (0, -1, True, 1.5, '2h', 4294967295):
            apply({'lan_dhcp_lease_seconds': invalid}, failure=True)
        for invalid in (0, True, 100, 4294967295):
            apply({'lan_dhcp_max_lease_seconds': invalid}, failure=True)
        for invalid in ('bad..test', 'a' * 64 + '.test', 'bad.test\nEmitDomain=no', 'test.', 'tést.test'):
            apply({'domain': invalid}, failure=True)
        assert all(path.read_bytes() == content for path, content in deployed.items())
        journal = command('journalctl', '-u', 'systemd-networkd', '--no-pager')
        assert 'Failed to decode DHCP option' not in journal, journal
        assert 'Failed to parse' not in journal, journal
        assert 'Unknown key' not in journal, journal
        print('PASS: independent limits, idempotence, invalid input rejected, networkd parses options', flush=True)


if __name__ == '__main__':
    main()
