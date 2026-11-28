"""Run in a disposable systemd container with udev, iproute2 and repo Ansible dependencies."""

import json
import os
from pathlib import Path
import socketserver
import struct
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
LAN_ADDRESS = '192.0.2.1'
LAN_IPV6_ADDRESS = '2001:db8:0:0::1'
SOCKET_UNIT = 'apcupsd-lan.socket'
DAEMON_UNIT = 'apcupsd.service'


def command(*args):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert result.returncode == 0, result.stdout
    return result.stdout


class UpsStatus(socketserver.StreamRequestHandler):
    def handle(self):
        length = struct.unpack('!H', self.rfile.read(2))[0]
        if self.rfile.read(length) == b'status':
            for line in (
                b'APC      : 001,007,0125\n',
                b'STATUS   : ONLINE\n',
                b'UPSNAME  : test-ups\n',
                b'MODEL    : Simulated UPS\n',
                b'UPSMODE  : Stand Alone\n',
                b'STATFLAG : 0x05000008\n',
                b'LINEV    : 120.0 Volts\n',
                b'BCHARGE  : 100.0 Percent\n',
                b'TIMELEFT : 45.0 Minutes\n',
                b'END APC  : 2026-01-01 00:00:00 +0000\n',
            ):
                self.wfile.write(struct.pack('!H', len(line)) + line)
        self.wfile.write(b'\0\0')


def on_peer(interface, *args):
    return command('nsenter', f'--net=/run/netns/{interface}', *args)


def configure_peer(interface, index):
    gateway = f'192.0.2.{index * 16 + 1}'
    peer = f'192.0.2.{index * 16 + 2}'
    gateway_ipv6 = f'2001:db8:0:{index}::1'
    peer_ipv6 = f'2001:db8:0:{index}::2'
    command('ip', 'netns', 'add', interface)
    command('ip', 'link', 'add', interface, 'type', 'veth', 'peer', 'name', 'peer0', 'netns', interface)
    command('ip', 'addr', 'add', f'{gateway}/28', 'dev', interface)
    command('ip', 'addr', 'add', f'{gateway_ipv6}/64', 'dev', interface, 'nodad')
    command('ip', 'link', 'set', interface, 'up')
    on_peer(interface, 'ip', 'addr', 'add', f'{peer}/28', 'dev', 'peer0')
    on_peer(interface, 'ip', 'addr', 'add', f'{peer_ipv6}/64', 'dev', 'peer0', 'nodad')
    on_peer(interface, 'ip', 'link', 'set', 'peer0', 'up')
    if interface != 'lan0':
        # A WAN/guest/VPN host may explicitly route to the router's LAN IP.
        on_peer(interface, 'ip', 'route', 'add', f'{LAN_ADDRESS}/32', 'via', gateway)
        on_peer(interface, 'ip', '-6', 'route', 'add', f'{LAN_IPV6_ADDRESS}/128', 'via', gateway_ipv6)


def can_connect(interface, address):
    return on_peer(
        interface, 'python3', '-c',
        'import socket, sys\n'
        'try:\n'
        '    with socket.create_connection((sys.argv[1], 3551), timeout=1):\n'
        '        print("yes")\n'
        'except OSError:\n'
        '    print("no")\n',
        address,
    ).strip() == 'yes'


def local_status():
    for _ in range(30):
        result = subprocess.run(
            ['apcaccess', 'status'], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        if result.returncode == 0 and '45.0 Minutes' in result.stdout:
            return result.stdout
        time.sleep(1)
    raise AssertionError(result.stdout)


def main():
    if os.environ.get('RPI_FIREWALL_DISPOSABLE_TEST') != '1' or not Path('/run/systemd/system').exists():
        raise SystemExit('Requires an explicitly disposable Ubuntu container running systemd')

    play = Path('/tmp/apcupsd-role-test.yml')
    play.write_text(f'''---
- hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - ansible.builtin.include_role:
        name: {ROOT}/roles/firewall
        tasks_from: apcupsd
''')
    with socketserver.ThreadingTCPServer(('127.0.0.1', 0), UpsStatus) as ups:
        worker = threading.Thread(target=ups.serve_forever, daemon=True)
        worker.start()

        def apply(enabled, lan_access=False):
            variables = {
                'apcupsd_enabled': enabled,
                'apcupsd_lan_access': lan_access,
                'apcupsd_ups_type': 'net',
                'apcupsd_ups_cable': 'ether',
                'apcupsd_device': f'127.0.0.1:{ups.server_address[1]}',
                'router_ip_address': f'{LAN_ADDRESS}/28',
            }
            return command('ansible-playbook', '-i', 'localhost,', str(play), '-e', json.dumps(variables))

        peers = ['lan0', 'wan0', 'wan-backup', 'lan_guests', 'tailscale0', 'lan_vpn']
        try:
            for index, interface in enumerate(peers):
                configure_peer(interface, index)

            output = apply(False)
            assert 'changed=0' in output, output
            assert not Path('/usr/lib/systemd/system/apcupsd.service').exists()

            apply(True)
            assert '45.0 Minutes' in local_status()
            assert not can_connect('lan0', LAN_ADDRESS)

            apply(True, lan_access=True)
            lan_status = on_peer('lan0', 'apcaccess', 'status', f'{LAN_ADDRESS}:3551')
            assert '45.0 Minutes' in lan_status, lan_status
            assert not can_connect('lan0', LAN_IPV6_ADDRESS)
            for index, interface in enumerate(peers[1:], start=1):
                assert not can_connect(interface, LAN_ADDRESS), interface
                assert not can_connect(interface, f'192.0.2.{index * 16 + 1}'), interface
                assert not can_connect(interface, LAN_IPV6_ADDRESS), interface
                assert not can_connect(interface, f'2001:db8:0:{index}::1'), interface
            assert 'enabled' == command('systemctl', 'is-enabled', SOCKET_UNIT).strip()

            command('systemctl', 'restart', SOCKET_UNIT)
            assert '45.0 Minutes' in on_peer('lan0', 'apcaccess', 'status', f'{LAN_ADDRESS}:3551')
            output = apply(True, lan_access=True)
            assert 'changed=0' in output, output

            apply(True)
            assert '45.0 Minutes' in local_status()
            assert not can_connect('lan0', LAN_ADDRESS)
            assert not Path(f'/etc/systemd/system/{SOCKET_UNIT}').exists()

            apply(False, lan_access=True)
            assert 'inactive' == command('systemctl', 'show', DAEMON_UNIT, '-p', 'ActiveState', '--value').strip()
            assert 'disabled' == command('systemctl', 'show', DAEMON_UNIT, '-p', 'UnitFileState', '--value').strip()
            output = apply(False)
            assert 'changed=0' in output, output
            print('PASS: packaged daemon, local status, LAN-only ingress, restart, and both disable paths')
        finally:
            subprocess.run(['systemctl', 'stop', SOCKET_UNIT, DAEMON_UNIT], check=False, capture_output=True)
            for interface in peers:
                subprocess.run(['ip', 'netns', 'del', interface], check=False, capture_output=True)
            ups.shutdown()


if __name__ == '__main__':
    main()
