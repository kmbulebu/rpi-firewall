"""Run in a disposable Linux container with NET_ADMIN, Unbound, dig and OpenSSL.

Exercises the rendered DNS configuration; never run on the production router.
Requires Python Jinja2 and PyYAML. No Internet access is used by the DNS tests.
"""
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import threading
import time

from jinja2 import Environment, FileSystemLoader, StrictUndefined
import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/firewall'


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT)


def query(address, name='ready.example.test', source=None, tcp=False):
    args = ['dig', '@' + address, '-p', '15353', name, 'A',
            '+time=4', '+tries=1', '+noall', '+comments', '+answer']
    if source:
        args += ['-b', source]
    if tcp:
        args += ['+tcp']
    return command(*args)


def main():
    values = yaml.safe_load((ROLE / 'defaults/main.yml').read_text())
    values.update(lan_iface='dns-lan', lan_guests_iface='dns-guest',
                  lan_cidr='192.0.2.0/24', lan_guests_cidr='198.51.100.0/24',
                  domain='example.test', ptr_zones=[], dhcp_reservations=[])
    env = Environment(loader=FileSystemLoader(ROLE / 'templates'), undefined=StrictUndefined)
    env.filters['bool'] = bool
    env.filters['ternary'] = lambda value, yes, no: yes if value else no
    rendered = env.get_template('unbound/server.conf').render(**values)
    upstream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    upstream.bind(('127.0.0.1', 15453))
    upstream.settimeout(0.1)
    answering = threading.Event()
    answering.set()
    done = threading.Event()

    def answer():
        while not done.is_set():
            try:
                data, peer = upstream.recvfrom(4096)
            except socket.timeout:
                continue
            if not answering.is_set():
                continue
            end = 12
            while data[end]:
                end += data[end] + 1
            end += 5
            header = data[:2] + struct.pack('!HHHHH', 0x8180, 1, 1, 0, 0)
            rr = b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 1, 4)
            upstream.sendto(header + data[12:end] + rr + socket.inet_aton('203.0.113.42'), peer)

    thread = threading.Thread(target=answer)
    thread.start()
    interfaces = []
    try:
        for iface, ipv4, ipv6 in [('dns-lan', '192.0.2.1/24', '2001:db8:1::1/64'),
                                  ('dns-guest', '198.51.100.1/24', '2001:db8:2::1/64')]:
            command('ip', 'link', 'add', iface, 'type', 'dummy')
            interfaces.append(iface)
            command('ip', 'link', 'set', iface, 'up')
            command('ip', 'addr', 'add', ipv4, 'dev', iface)
            command('ip', '-6', 'addr', 'add', ipv6, 'dev', iface, 'nodad')
        command('ip', '-6', 'addr', 'add', '2001:db8:3::2/128', 'dev', 'lo', 'nodad')
        command('ip', '-6', 'addr', 'add', '2001:db8:3::1/128', 'dev', 'lo', 'nodad')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-keyout', tmp + '/key.pem', '-out', tmp + '/cert.pem',
                    '-days', '1', '-subj', '/CN=dns-test')
            rendered = rendered.replace('/etc/unbound/unbound_server.key', tmp + '/key.pem')
            rendered = rendered.replace('/etc/unbound/unbound_server.pem', tmp + '/cert.pem')
            # Keep production listeners, ACLs and stale settings. Substitute an
            # isolated upstream and runtime paths; disable validation for fake data.
            rendered = rendered[:rendered.index('forward-zone:')]
            rendered += ('  port: 15353\n  username: ""\n  chroot: ""\n'
                         f'  directory: "{tmp}"\n  pidfile: ""\n'
                         '  use-syslog: no\n  module-config: "iterator"\n'
                         # A test-only listener with no interface-action must
                         # still refuse this source, even when LAN is allowed.
                         '  interface: 2001:db8:3::1\n'
                         '  do-not-query-localhost: no\n  tls-upstream: no\n'
                         '  local-data: "ready.example.test. IN A 192.0.2.42"\n'
                         'forward-zone:\n  name: "."\n  forward-tls-upstream: no\n'
                         '  forward-addr: 127.0.0.1@15453\n')
            for fixed in (False, True):
                config = rendered if fixed else '\n'.join(
                    line for line in rendered.splitlines() if 'interface-action:' not in line)
                path = root / 'unbound.conf'
                path.write_text(config)
                print(command('unbound-checkconf', str(path)).strip(), flush=True)
                with (root / 'server.log').open('w') as log:
                    server = subprocess.Popen(['unbound', '-d', '-c', str(path)], stdout=log, stderr=log)
                    try:
                        for _ in range(40):
                            if server.poll() is not None:
                                raise AssertionError((root / 'server.log').read_text())
                            try:
                                if '192.0.2.42' in query('192.0.2.1'):
                                    break
                            except subprocess.CalledProcessError:
                                pass
                            time.sleep(0.1)
                        else:
                            raise AssertionError('DNS did not start')
                        expected = 'NOERROR' if fixed else 'REFUSED'
                        for address in ('2001:db8:1::1', '2001:db8:2::1'):
                            for tcp in (False, True):
                                result = query(address, source='2001:db8:3::2', tcp=tcp)
                                assert 'status: ' + expected in result, result
                        print('IPv6 LAN/guest UDP+TCP:', expected, flush=True)
                        result = query('2001:db8:3::1', source='2001:db8:3::2')
                        assert 'status: REFUSED' in result, result
                        print('Unpermitted listener: REFUSED', flush=True)
                        # Destination sockets are not packet ingress filters.
                        # No WAN or wildcard listener may be introduced.
                        listeners = command('ss', '-lntu')
                        assert '0.0.0.0:15353' not in listeners and '[::]:15353' not in listeners
                        if fixed:
                            result = query('192.0.2.1', 'expired.example.net')
                            assert '203.0.113.42' in result, result
                            answering.clear()
                            time.sleep(2)
                            result = query('192.0.2.1', 'expired.example.net')
                            assert 'status: NOERROR' in result and '203.0.113.42' in result, result
                            print('PASS: expired cached answer survives upstream outage', flush=True)
                    finally:
                        server.terminate()
                        server.wait(timeout=10)
    finally:
        done.set()
        thread.join(timeout=2)
        upstream.close()
        for iface in interfaces:
            command('ip', 'link', 'delete', iface)
        subprocess.run(['ip', '-6', 'addr', 'del', '2001:db8:3::2/128', 'dev', 'lo'], check=False)
        subprocess.run(['ip', '-6', 'addr', 'del', '2001:db8:3::1/128', 'dev', 'lo'], check=False)


if __name__ == '__main__':
    main()
