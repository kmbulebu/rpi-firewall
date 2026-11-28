"""Exercise packaged NUT with an emulated UPS in a disposable Ubuntu systemd container."""

import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
LAN_IP = '192.0.2.1'
UPS = 'router-ups'
PEERS = ('lan0', 'wan0', 'wan-backup', 'lan_guests', 'tailscale0', 'lan_vpn')


def command(*args):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert result.returncode == 0, result.stdout
    return result.stdout


def peer_command(interface, *args):
    return command('nsenter', f'--net=/run/netns/{interface}', *args)


def configure_peer(interface, index):
    router = f'192.0.2.{16 * index + 1}'
    peer = f'192.0.2.{16 * index + 2}'
    router_v6 = f'2001:db8:{index}::1'
    peer_v6 = f'2001:db8:{index}::2'
    command('ip', 'netns', 'add', interface)
    command('ip', 'link', 'add', interface, 'type', 'veth', 'peer', 'name', 'peer0', 'netns', interface)
    command('ip', 'addr', 'add', f'{router}/28', 'dev', interface)
    command('ip', 'addr', 'add', f'{router_v6}/64', 'dev', interface, 'nodad')
    command('ip', 'link', 'set', interface, 'up')
    peer_command(interface, 'ip', 'addr', 'add', f'{peer}/28', 'dev', 'peer0')
    peer_command(interface, 'ip', 'addr', 'add', f'{peer_v6}/64', 'dev', 'peer0', 'nodad')
    peer_command(interface, 'ip', 'link', 'set', 'peer0', 'up')
    if index:
        peer_command(interface, 'ip', 'route', 'add', f'{LAN_IP}/32', 'via', router)
        peer_command(interface, 'ip', '-6', 'route', 'add', '2001:db8::1/128', 'via', router_v6)


def can_connect(interface, address):
    return peer_command(
        interface, 'python3', '-c',
        'import socket, sys\n'
        'try:\n'
        '    with socket.create_connection((sys.argv[1], 3493), timeout=1):\n'
        '        print("yes")\n'
        'except OSError:\n'
        '    print("no")\n',
        address,
    ).strip() == 'yes'


def wait_for(probe):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if probe():
            return
        time.sleep(.25)
    raise AssertionError(command('journalctl', '-u', 'nut-monitor', '-u', 'nut-server', '--no-pager', '-n', '60'))


def main():
    if os.environ.get('RPI_FIREWALL_DISPOSABLE_TEST') != '1' or not Path('/run/systemd/system').exists():
        raise SystemExit('Requires an explicitly disposable systemd container with udev and iproute2')

    play = Path('/tmp/nut-role-test.yml')
    play.write_text(f'''---
- hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - ansible.builtin.include_role:
        name: {ROOT}/roles/firewall
        tasks_from: nut
''')
    simulation = Path('/tmp/router-ups.dev')
    simulation.write_text('ups.status: OL\nbattery.charge: 100\n')

    def apply(enabled, lan_access=False, options=None):
        variables = {
            'nut_enabled': enabled,
            'nut_lan_access': lan_access,
            'nut_ups_driver': 'dummy-ups',
            'nut_ups_port': str(simulation),
            'nut_ups_options': options or {},
            'router_ip_address': f'{LAN_IP}/28',
        }
        return command('ansible-playbook', '-i', 'localhost,', str(play), '-e', json.dumps(variables))

    def local_status():
        status = subprocess.run(['upsc', f'{UPS}@127.0.0.1', 'ups.status'], capture_output=True, text=True)
        return status.returncode == 0 and status.stdout.strip() == 'OL'

    try:
        for index, interface in enumerate(PEERS):
            configure_peer(interface, index)
        command('udevadm', 'settle')

        output = apply(False)
        assert 'changed=0' in output, output
        assert not Path('/usr/lib/systemd/system/nut.target').exists()

        apply(True)
        wait_for(local_status)
        assert not can_connect('lan0', LAN_IP)

        apply(True, lan_access=True)
        status = peer_command('lan0', 'upsc', f'{UPS}@{LAN_IP}', 'ups.status')
        assert status.splitlines()[-1] == 'OL', status
        assert not can_connect('lan0', '2001:db8::1')
        for index, interface in enumerate(PEERS[1:], start=1):
            for address in (LAN_IP, f'192.0.2.{16 * index + 1}', '2001:db8::1', f'2001:db8:{index}::1'):
                assert not can_connect(interface, address), (interface, address)
        command('systemctl', 'restart', 'nut-lan.socket')
        assert peer_command('lan0', 'upsc', f'{UPS}@{LAN_IP}', 'ups.status').splitlines()[-1] == 'OL'
        output = apply(True, lan_access=True)
        assert 'changed=0' in output, output

        apply(True, options={'mode': 'dummy-once'})
        wait_for(local_status)
        assert not can_connect('lan0', LAN_IP)
        assert not Path('/etc/systemd/system/nut-lan.socket').exists()

        # Keep the real monitor and low-battery logic while substituting an inert shutdown action.
        monitor_config = Path('/etc/nut/upsmon.conf')
        config = monitor_config.read_text()
        command_line = 'SHUTDOWNCMD "/sbin/shutdown -h +0"'
        assert command_line in config
        marker = Path('/run/nut-test-shutdown')
        marker.unlink(missing_ok=True)
        monitor_config.write_text(config.replace(command_line, f'SHUTDOWNCMD "/usr/bin/touch {marker}"'))
        command('systemctl', 'restart', 'nut-monitor.service')
        simulation.write_text('ups.status: OB LB\nbattery.charge: 5\n')
        wait_for(marker.exists)
        assert Path('/run/nut-router-killpower').exists()

        apply(False, lan_access=True)
        assert 'MODE=none' in Path('/etc/nut/nut.conf').read_text()
        for unit in ('nut.target', 'nut-server.service', 'nut-monitor.service'):
            assert command('systemctl', 'show', unit, '-P', 'ActiveState').strip() == 'inactive'
        assert command('systemctl', 'show', 'nut.target', '-P', 'UnitFileState').strip() == 'disabled'
        output = apply(False)
        assert 'changed=0' in output, output
        print('PASS: NUT monitoring, LAN-only status, reapply, disable and simulated low-battery shutdown')
    finally:
        Path('/run/nut-router-killpower').unlink(missing_ok=True)
        subprocess.run(['systemctl', 'stop', 'nut-lan.socket', 'nut.target'], check=False, capture_output=True)
        for interface in PEERS:
            subprocess.run(['ip', 'netns', 'del', interface], check=False, capture_output=True)


if __name__ == '__main__':
    main()
