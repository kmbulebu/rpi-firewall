"""Real packet tests; ONLY run in an explicitly disposable systemd container.

Production networkd/zone templates + explicit forwarding policy fixtures (#30).
All simulated Internet traffic stays inside namespaces; no external peers.
"""
import contextlib
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import time

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/firewall'
WORK = Path('/run/router-forwarding-test')
PROCESSES = []
NAMESPACES = ('isp', 'internet', 'switch', 'lan', 'guest', 'vpn')


def command(*args, **kwargs):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=30, **kwargs)


def ns(name, *args, **kwargs):
    # Enter only the network namespace; ip netns exec also remounts /sys,
    # which is unavailable inside rootless Podman. No test reads interface sysfs.
    return command('nsenter', '--net=/run/netns/' + name, *args, **kwargs)


def ip(*args):
    return command('ip', *args)


def wait_for(description, predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except (subprocess.SubprocessError, OSError, ValueError):
            pass
        time.sleep(0.2)
    raise AssertionError('Timed out: ' + description)


def start(name, args, namespace=None):
    log = (WORK / (name + '.log')).open('w')
    prefix = ['nsenter', '--net=/run/netns/' + namespace] if namespace else []
    process = subprocess.Popen(prefix + args, stdout=log, stderr=subprocess.STDOUT)
    log.close()
    PROCESSES.append(process)
    return process


def link(left, right, left_ns=None, right_ns=None):
    ip('link', 'add', left, 'type', 'veth', 'peer', 'name', right)
    for name, namespace in ((left, left_ns), (right, right_ns)):
        if namespace:
            ip('link', 'set', name, 'netns', namespace)
        cmd = lambda *args: ns(namespace, 'ip', *args) if namespace else ip(*args)
        cmd('link', 'set', name, 'up')


def address(namespace, iface, value):
    if namespace:
        ns(namespace, 'ip', 'address', 'add', value, 'dev', iface)
    else:
        ip('address', 'add', value, 'dev', iface)


def dhcp_probe(iface):
    """Request an offer from the actual networkd DHCP server, without a lease file."""
    mac = bytes.fromhex(json.loads(command('ip', '-j', 'link', 'show', iface))[0]['address'].replace(':', ''))
    xid = os.urandom(4)
    request = struct.pack('!BBBB4sHH4s4s4s4s16s64s128s', 1, 1, 6, 0, xid, 0, 0x8000,
                          bytes(4), bytes(4), bytes(4), bytes(4), mac.ljust(16, b'\0'), bytes(64), bytes(128))
    request += b'\x63\x82\x53\x63\x35\x01\x01\xff'
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, iface.encode() + b'\0')
        sock.bind(('', 68))
        sock.settimeout(2)
        for _ in range(4):
            sock.sendto(request, ('255.255.255.255', 67))
            try:
                while True:
                    reply = sock.recv(4096)
                    if reply[0] == 2 and reply[4:8] == xid and b'\x35\x01\x02' in reply[240:]:
                        print(socket.inet_ntoa(reply[16:20]))
                        return
            except TimeoutError:
                pass
    raise AssertionError('No DHCP offer on ' + iface)


HTTP_SERVER = r'''
import http.server, socket, threading
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = (self.client_address[0] + '\n').encode() if self.path == '/' else b'x' * 262144
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args): pass
class IPv6(http.server.ThreadingHTTPServer): address_family = socket.AF_INET6
v6 = IPv6(('::', 8081), Handler)
threading.Thread(target=v6.serve_forever, daemon=True).start()
http.server.ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
'''
HTTP_CLIENT = r'''
import sys, urllib.request
with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(sys.argv[1], timeout=3) as r:
    data = r.read()
    print(len(data) if len(data) > 100 else data.decode().strip())
'''


def request(client, destination='198.18.99.2', path='/', ipv6=False):
    host = '[' + destination + ']' if ipv6 else destination
    return ns(client, 'python3', '-c', HTTP_CLIENT,
              f'http://{host}:{8081 if ipv6 else 8080}{path}').strip()


def blocked(client, destination):
    try:
        request(client, destination)
    except subprocess.CalledProcessError:
        return
    raise AssertionError(f'{client} unexpectedly reached {destination}')


def configure():
    """Apply a focused role slice, not the full router/bootstrap playbook."""
    role = WORK / 'roles/packet_test'
    (role / 'tasks').mkdir(parents=True)
    for directory in ('defaults', 'templates', 'files'):
        (role / directory).symlink_to(ROLE / directory)
    templates = [
        'wan/10-wan.network', 'wan-backup/10-wan-backup.network', 'lan/10-lan.network',
        'lan_guests/30-lan-guests.netdev', 'lan_guests/30-lan-guests.network',
    ]
    tasks = [
        {'name': 'Render network configuration', 'ansible.builtin.template': {
            'src': 'networkd/{{ item }}', 'dest': '/etc/systemd/network/{{ item | basename }}'},
         'loop': templates, 'notify': 'reload test networkd'},
        {'ansible.builtin.include_tasks': str(ROLE / 'tasks/validate_vpn.yml')},
        {'ansible.builtin.include_tasks': str(ROLE / 'tasks/wireguard_client.yml')},
        {'name': 'Render firewall zones', 'ansible.builtin.template': {
            'src': 'firewalld/zones/{{ item }}.xml', 'dest': '/etc/firewalld/zones/{{ item }}.xml'},
         'loop': ['external', 'internal', 'guest'], 'notify': 'reload test firewall'},
        {'name': 'Render firewall settings', 'ansible.builtin.template': {
            'src': 'firewalld/firewalld.conf', 'dest': '/etc/firewalld/firewalld.conf'},
         'notify': 'reload test firewall'},
    ]
    (role / 'tasks/main.yml').write_text(yaml.safe_dump(tasks))
    private = command('wg', 'genkey').strip()
    peer_private = command('wg', 'genkey').strip()
    (WORK / 'peer.key').write_text(peer_private + '\n')
    (WORK / 'router.pub').write_text(command('wg', 'pubkey', input=private))
    values = dict(enable_lan_vpn=True, wan_backup_enabled=True, lan_guests_vlan_id=5,
                  router_ip_address='192.0.2.1/24', lan_address='192.0.2.1', lan_cidr='192.0.2.0/24',
                  lan_guests_router_ip_address='198.51.100.1/24', lan_guests_address='198.51.100.1',
                  lan_guests_cidr='198.51.100.0/24', lan_vpn_router_ip_address='192.0.3.1/24',
                  lan_vpn_cidr='192.0.3.0/24', ip6_ula_cidr='fd12:3456::/48',
                  lan_ip6_ula_cidr='2001:db8:10::/64', domain='example.test',
                  enable_prometheus_node_exporter_bool=False, lan_vpn_wg_private_key=private,
                  lan_vpn_wg_address='10.200.0.2/24', lan_vpn_wg_peer_persistent_keep_alive=1,
                  lan_vpn_wg_peer_public_key=command('wg', 'pubkey', input=peer_private).strip(),
                  lan_vpn_wg_peer_endpoint='198.18.1.1:51820')
    varfile = WORK / 'vars.yml'
    varfile.write_text(yaml.safe_dump(values))
    varfile.chmod(0o600)
    play = [dict(hosts='localhost', gather_facts=False, connection='local', roles=['packet_test'], handlers=[
        {'name': 'reload test networkd', 'listen': 'restart systemd-networkd',
         'ansible.builtin.command': 'networkctl reload'},
        {'name': 'reload test firewall', 'listen': 'reload firewalld',
         'ansible.builtin.command': 'firewall-cmd --reload',
         'when': 'firewall_running | default(false) | bool'},
    ])]
    (WORK / 'play.yml').write_text(yaml.safe_dump(play))

    def apply(running=False):
        return command('ansible-playbook', '-i', 'localhost,', str(WORK / 'play.yml'),
                       '-e', '@' + str(varfile), '-e', 'firewall_running=' + str(running).lower())
    return apply


def policy_fixtures():
    # Missing managed policies are tracked separately in #30. These ACCEPT
    # policies are explicit scenario inputs, not a claim of deployment parity.
    for name, source, destination in [('test-lan-wan', 'internal', 'external'),
                                      ('test-guest-wan', 'guest', 'external'),
                                      ('test-vpn', 'internal_vpn', 'external_vpn')]:
        Path('/etc/firewalld/policies', name + '.xml').write_text(
            f'<policy target="ACCEPT"><ingress-zone name="{source}"/>'
            f'<egress-zone name="{destination}"/></policy>\n')
    Path('/etc/firewalld/policies/ipv6-icmp-ingress.xml').write_text(
        (ROLE / 'files/firewalld/policies/ipv6-icmp-ingress.xml').read_text())


def topology():
    for name in NAMESPACES:
        ip('netns', 'add', name)
        ns(name, 'ip', 'link', 'set', 'lo', 'up')
    link('wan0', 'primary', right_ns='isp')
    link('lan0', 'trunk', right_ns='switch')
    ns('switch', 'ip', 'link', 'add', 'br0', 'type', 'bridge', 'vlan_filtering', '1')
    ns('switch', 'ip', 'link', 'set', 'br0', 'up')
    ns('switch', 'ip', 'link', 'set', 'trunk', 'master', 'br0')
    for vlan in (2, 5):
        ns('switch', 'bridge', 'vlan', 'add', 'dev', 'trunk', 'vid', str(vlan))
    for client, vlan in [('lan', 1), ('guest', 5), ('vpn', 2)]:
        port = client + '-port'
        link(port, 'eth0', left_ns='switch', right_ns=client)
        ns('switch', 'ip', 'link', 'set', port, 'master', 'br0')
        if vlan != 1:
            ns('switch', 'bridge', 'vlan', 'del', 'dev', port, 'vid', '1')
            ns('switch', 'bridge', 'vlan', 'add', 'dev', port, 'vid', str(vlan), 'pvid', 'untagged')
    link('world', 'eth0', left_ns='isp', right_ns='internet')
    address('isp', 'primary', '198.18.1.1/24')
    address('isp', 'primary', '2001:db8:1::1/64')
    address('isp', 'world', '198.18.99.1/24')
    address('isp', 'world', '2001:db8:99::1/64')
    address('internet', 'eth0', '198.18.99.2/24')
    address('internet', 'eth0', '2001:db8:99::2/64')
    ns('internet', 'ip', 'route', 'add', 'default', 'via', '198.18.99.1')
    ns('internet', 'ip', '-6', 'route', 'add', 'default', 'via', '2001:db8:99::1')
    ns('isp', 'sysctl', '-qw', 'net.ipv4.ip_forward=1', 'net.ipv6.conf.all.forwarding=1')
    ns('isp', 'ip', '-6', 'route', 'add', '2001:db8:10::/64', 'via', '2001:db8:1::2')
    start('internet-http', ['python3', '-u', '-c', HTTP_SERVER], 'internet')
    start('lan-http', ['python3', '-u', '-c', HTTP_SERVER], 'lan')
    start('router-http', ['python3', '-u', '-c', HTTP_SERVER])
    # Only fixture addresses/routes missing from the DHCP-only simulated ISP.
    # Actual WAN DHCP, metrics and router-side client networks use role templates.
    dropin = Path('/etc/systemd/network/10-wan.network.d')
    dropin.mkdir()
    (dropin / 'test-ipv6.conf').write_text(
        '[Network]\nAddress=2001:db8:1::2/64\n[Route]\nGateway=2001:db8:1::1\n')
    for client, addr, gateway in [('lan', '192.0.2.20/24', '192.0.2.1'),
                                  ('guest', '198.51.100.20/24', '198.51.100.1'),
                                  ('vpn', '192.0.3.20/24', '192.0.3.1')]:
        address(client, 'eth0', addr)
        ns(client, 'ip', 'route', 'add', 'default', 'via', gateway)
    # Static client IPv6 isolates forwarding/PMTU tests from SLAAC timing and PD.
    ns('lan', 'ip', '-6', 'address', 'add', '2001:db8:10::20/64', 'dev', 'eth0', 'nodad')
    ns('lan', 'ip', '-6', 'route', 'add', 'default', 'via', '2001:db8:10::1')
    # Networkd's prefix token defaults to EUI64; a fixed test gateway is explicit.
    dropin = Path('/etc/systemd/network/10-lan.network.d')
    dropin.mkdir()
    (dropin / 'test-ipv6.conf').write_text('[Network]\nAddress=2001:db8:10::1/64\n')


def upstream_dhcp(iface, subnet):
    return start('dhcp-' + iface, ['dnsmasq', '--no-daemon', '--port=0', '--conf-file=/dev/null',
                 '--interface=' + iface, '--bind-interfaces', '--dhcp-authoritative',
                 f'--dhcp-range=198.18.{subnet}.10,198.18.{subnet}.20,255.255.255.0,1h',
                 f'--dhcp-option=3,198.18.{subnet}.1',
                 '--dhcp-leasefile=' + str(WORK / (iface + '.leases'))], 'isp')


def default_wan(iface):
    routes = json.loads(ip('-j', 'route', 'show', 'default'))
    candidates = [r for r in routes if r.get('dev') in ('wan0', 'wan-backup')]
    return bool(candidates) and min(candidates, key=lambda r: r.get('metric', 0))['dev'] == iface


def vpn_peer():
    ns('isp', 'ip', 'link', 'add', 'wg-peer', 'type', 'wireguard')
    ns('isp', 'wg', 'set', 'wg-peer', 'private-key', str(WORK / 'peer.key'), 'listen-port', '51820',
       'peer', (WORK / 'router.pub').read_text().strip(), 'allowed-ips', '10.200.0.2/32')
    address('isp', 'wg-peer', '10.200.0.1/24')
    ns('isp', 'ip', 'link', 'set', 'wg-peer', 'up')


def no_vpn_leak(label):
    # Observe plaintext destination traffic at the primary WAN peer. A failed
    # HTTP request alone cannot prove it wasn't sent via the ordinary WAN.
    capture = WORK / (label + '.pcap')
    proc = start(label, ['tcpdump', '-Z', 'root', '-U', '-n', '-i', 'primary', '-w', str(capture),
                       'dst host 198.18.99.2 and tcp dst port 8080'], 'isp')
    wait_for('packet capture ready', lambda: proc.poll() is None and capture.stat().st_size >= 24)
    try:
        blocked('vpn', '198.18.99.2')
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    packets = command('tcpdump', '-nn', '-r', str(capture))
    # tcpdump writes its file header diagnostic to stderr, merged by command().
    assert not re.search(r' IP .* > .*8080:', packets), packets
    print('PASS: no VPN plaintext leak: ' + label, flush=True)


def scenarios(apply):
    command('systemctl', 'start', 'systemd-networkd')
    wait_for('guest DHCP without WAN', lambda: ns('guest', 'python3', __file__, '--dhcp-probe', 'eth0'))
    assert not default_wan('wan0')
    print('PASS: networkd serves guest DHCP with no upstream lease', flush=True)
    command('firewall-offline-cmd', '--check-config')
    command('systemctl', 'start', 'firewalld')
    assert 'changed=0 ' in apply(True)
    print('PASS: second Ansible network/firewall/VPN slice run unchanged', flush=True)
    upstream_dhcp('primary', 1)
    wait_for('primary DHCP default route', lambda: default_wan('wan0'))
    wait_for('LAN Internet', lambda: request('lan').startswith('198.18.1.'))
    wait_for('guest Internet', lambda: request('guest').startswith('198.18.1.'))
    assert request('lan', '192.0.2.20') == '192.0.2.20'
    assert request('lan', '192.0.2.1') == '192.0.2.20'
    blocked('guest', '192.0.2.20')
    blocked('guest', '192.0.2.1')
    print('PASS: LAN/guest Internet uses WAN NAT; guest cannot reach LAN HTTP', flush=True)
    # Real WireGuard encryption, VRF table 200 and tunnel masquerading.
    vpn_peer()
    wait_for('VPN Internet through peer', lambda: request('vpn') == '10.200.0.2')
    ns('isp', 'ip', 'link', 'set', 'wg-peer', 'down')
    no_vpn_leak('peer-failure')
    ns('isp', 'ip', 'link', 'set', 'wg-peer', 'up')
    wait_for('VPN recovery', lambda: request('vpn') == '10.200.0.2')
    command('networkctl', 'down', 'wg0')
    no_vpn_leak('tunnel-down')
    command('networkctl', 'up', 'wg0')
    wait_for('VPN device recovery', lambda: request('vpn') == '10.200.0.2')
    # Lower MTU beyond the router, then require an actual Packet Too Big and a
    # successful large IPv6 TCP transfer. Fresh destination avoids cached PMTU.
    ns('isp', 'ip', 'link', 'set', 'world', 'mtu', '1280')
    ns('internet', 'ip', 'link', 'set', 'eth0', 'mtu', '1280')
    result = subprocess.run(['nsenter', '--net=/run/netns/lan', 'ping', '-6', '-n', '-c', '1', '-W', '2',
                             '-M', 'do', '-s', '1400', '2001:db8:99::2'], text=True, capture_output=True)
    assert 'Packet too big' in result.stdout and '1280' in result.stdout, result.stdout + result.stderr
    assert request('lan', '2001:db8:99::2', '/large', ipv6=True) == '262144'
    print('PASS: IPv6 Packet Too Big reaches client; large TCP transfer succeeds', flush=True)
    # Fail the provider's forwarding, while the router still sees Ethernet
    # carrier and retains its primary lease/default. Existing default metrics
    # deliberately prefer a plugged-in backup even when primary retains carrier.
    ns('isp', 'nft', 'add', 'table', 'inet', 'outage')
    ns('isp', 'nft', 'add', 'chain', 'inet', 'outage', 'forward',
       '{ type filter hook forward priority -10; policy accept; }')
    ns('isp', 'nft', 'add', 'rule', 'inet', 'outage', 'forward', 'iifname', 'primary', 'drop')
    blocked('guest', '198.18.99.2')
    assert json.loads(ip('-j', 'link', 'show', 'wan0'))[0]['operstate'] == 'UP'
    assert default_wan('wan0')
    ns('guest', 'python3', __file__, '--dhcp-probe', 'eth0')
    link('wan-backup', 'backup', right_ns='isp')
    address('isp', 'backup', '198.18.2.1/24')
    upstream_dhcp('backup', 2)
    wait_for('backup DHCP default', lambda: default_wan('wan-backup'))
    wait_for('guest backup Internet', lambda: request('guest').startswith('198.18.2.'))
    assert request('lan').startswith('198.18.2.')
    blocked('guest', '192.0.2.20')
    print('PASS: upstream failure retains carrier; hot-plug backup restores LAN/guest Internet', flush=True)
    ip('link', 'del', 'wan-backup')
    wait_for('primary route after unplug', lambda: default_wan('wan0'))
    blocked('guest', '198.18.99.2')
    ns('guest', 'python3', __file__, '--dhcp-probe', 'eth0')
    ns('isp', 'nft', 'delete', 'table', 'inet', 'outage')
    wait_for('primary Internet recovery', lambda: request('guest').startswith('198.18.1.'))
    assert request('lan').startswith('198.18.1.')
    assert command('systemctl', 'is-active', 'firewalld', 'systemd-networkd').split() == ['active', 'active']
    log = command('journalctl', '-b', '-u', 'systemd-networkd', '--no-pager')
    assert 'Unknown key' not in log and 'Unknown section' not in log, log
    print('PASS: unplug removes backup route; DHCP survives outage; primary recovers', flush=True)


def main():
    if os.environ.get('RPI_FIREWALL_DISPOSABLE_TEST') != '1' or not Path('/run/systemd/system').exists():
        raise SystemExit('Requires an explicitly disposable container running systemd')
    WORK.mkdir(mode=0o700)
    for directory in ('/etc/systemd/network', '/etc/firewalld/zones', '/etc/firewalld/policies',
                      '/etc/systemd/networkd.conf.d'):
        Path(directory).mkdir(parents=True, exist_ok=True)
    command('systemctl', 'stop', 'systemd-networkd', 'firewalld')
    if not os.statvfs('/sys').f_flag & os.ST_RDONLY:
        raise SystemExit('Launch the test container with read-only /sys (see tests/README.forwarding.md)')
    Path('/etc/systemd/networkd.conf.d/20-router.conf').write_text(
        (ROLE / 'files/networkd/20-router.conf').read_text())
    # Only namespace-local sysctls; never resize host-global conntrack buckets.
    command('sysctl', '-qw', 'net.ipv4.conf.all.rp_filter=2', 'net.ipv4.conf.default.rp_filter=2')
    try:
        topology()
        command('systemctl', 'start', 'systemd-networkd')
        apply = configure()
        apply()
        policy_fixtures()
        scenarios(apply)
    except BaseException as error:
        if isinstance(error, subprocess.CalledProcessError):
            print(error.output, flush=True)
        for args in [('ip', '-4', 'route', 'show', 'table', 'all'),
                     ('ip', '-6', 'route', 'show', 'table', 'all'),
                     ('journalctl', '-b', '--no-pager', '-n', '100', '-u', 'systemd-networkd', '-u', 'firewalld')]:
            with contextlib.suppress(Exception):
                print(command(*args), flush=True)
        for log in WORK.glob('*.log'):
            print(log.name, log.read_text()[-4000:], flush=True)
        raise
    finally:
        for process in reversed(PROCESSES):
            if process.poll() is None:
                process.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=3)
                if process.poll() is None:
                    process.kill()
                    process.wait()
        for name in reversed(NAMESPACES):
            with contextlib.suppress(subprocess.SubprocessError):
                ip('netns', 'del', name)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--dhcp-probe':
        dhcp_probe(sys.argv[2])
    else:
        main()
