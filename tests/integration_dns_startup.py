"""Exercise DNS provisioning in an explicitly disposable systemd container.

Replaces container DNS configuration. Never run on a production host or router.
The image supplies packages/collections; the runtime test needs no WAN access.
"""
import os
from pathlib import Path
import re
import subprocess
import time

import dns.message
import dns.query
import dns.rcode
import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path('/run/rpi-firewall-dns-startup-test')
ANCHOR = Path('/var/lib/unbound/root.key')
RESOLV = Path('/etc/resolv.conf')
STUB = Path('/run/systemd/resolve/stub-resolv.conf')


def command(*args, **kwargs):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, **kwargs)


def check_dns():
    for address, name, kind in [('127.0.0.53', 'startup-fixture.example.test', 'A'),
                                ('192.0.2.1', 'example.test', 'SOA')]:
        for attempt in range(30):
            try:
                response = dns.query.udp(dns.message.make_query(name, kind), address, timeout=1)
                assert response.rcode() == dns.rcode.NOERROR and response.answer, response
                if kind == 'A':
                    assert any('192.0.2.42' in record.to_text() for record in response.answer)
                break
            except Exception:
                if attempt == 29:
                    raise
                time.sleep(0.1)
    assert RESOLV.is_symlink() and RESOLV.resolve() == STUB
    assert 'nameserver 127.0.0.53' in RESOLV.read_text()
    command('unbound-checkconf')


def apply(expect_failure=False):
    result = subprocess.run(
        ['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(FIXTURE / 'play.yml')],
        env={**os.environ, 'ANSIBLE_ROLES_PATH': str(ROOT / 'roles'), 'ANSIBLE_NOCOLOR': '1'},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(result.stdout, flush=True)
    assert (result.returncode != 0) == expect_failure, result.stdout
    return result.stdout


def main():
    if os.environ.get('RPI_FIREWALL_DISPOSABLE_TEST') != '1' or not Path('/run/systemd/system').exists():
        raise SystemExit('Requires an explicitly disposable container running systemd')
    FIXTURE.mkdir(exist_ok=True)
    command('systemctl', 'stop', 'unbound', 'systemd-resolved')
    # Rootless containers cannot request realtime I/O priority, and CI CPUs may
    # not include the Pi's hardcoded CPU 2. Keep the real distro service/helper
    # while overriding only those unrelated hardware tuning settings.
    overrides = Path('/etc/systemd/system/unbound.service.d')
    overrides.mkdir(exist_ok=True)
    (overrides / '99-container.conf').write_text(
        '[Service]\nNice=0\nCPUAffinity=\nIOSchedulingClass=best-effort\n')
    command('systemctl', 'daemon-reload')
    # Container engines mount this file. Detach only the disposable container's
    # mount so Ansible can replace it, as it does on a real Ubuntu installation.
    mounted = subprocess.run(['mountpoint', '-q', str(RESOLV)]).returncode == 0
    if mounted:
        command('umount', str(RESOLV))
    RESOLV.unlink(missing_ok=True)
    RESOLV.write_text('nameserver 127.0.0.53\n')
    with Path('/etc/hosts').open('a') as hosts:
        hosts.write('\n192.0.2.42 startup-fixture.example.test\n')
    for iface, address in [('dns-lan', '192.0.2.1/24'), ('dns-guest', '198.51.100.1/24')]:
        command('ip', 'link', 'add', iface, 'type', 'dummy')
        command('ip', 'link', 'set', iface, 'up')
        command('ip', 'addr', 'add', address, 'dev', iface)
    command('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
            '-keyout', '/etc/unbound/unbound_server.key',
            '-out', '/etc/unbound/unbound_server.pem',
            '-days', '1', '-subj', '/CN=dns-startup-test')
    command('chown', 'unbound:unbound', '/etc/unbound/unbound_server.key')
    ANCHOR.unlink(missing_ok=True)
    # An accidental invocation would fail this test, including on the first run.
    trap = Path('/usr/sbin/unbound-anchor')
    trap.write_text('#!/bin/sh\necho Unexpected network anchor initialization >&2\nexit 99\n')
    trap.chmod(0o755)
    play = [{'hosts': 'all', 'gather_facts': False,
             'vars': {'router_ip_address': '192.0.2.1/24',
                      'lan_guests_router_ip_address': '198.51.100.1/24',
                      'lan_iface': 'dns-lan', 'lan_guests_iface': 'dns-guest',
                      'domain': 'example.test'},
             'tasks': [{'name': 'Configure DNS', 'ansible.builtin.include_role':
                        {'name': 'firewall', 'tasks_from': 'dns'}}]}]
    (FIXTURE / 'play.yml').write_text(yaml.safe_dump(play))
    command('systemctl', 'reset-failed', 'unbound', 'systemd-resolved')
    apply()
    check_dns()
    assert ANCHOR.stat().st_size > 0
    # Model RFC 5011 tracking state that Ansible must not replace on later pulls.
    with ANCHOR.open('a') as anchor:
        anchor.write('\n; preserved tracking state fixture\n')
    before = ANCHOR.read_bytes()
    output = apply()
    assert re.search(r'changed=0\s', output), output
    assert ANCHOR.read_bytes() == before
    check_dns()
    # Recreate the boot lifecycle: persistent symlink/anchor, fresh runtime stub.
    command('systemctl', 'stop', 'unbound', 'systemd-resolved')
    STUB.unlink(missing_ok=True)
    assert RESOLV.is_symlink() and not RESOLV.exists()
    command('systemctl', 'start', 'systemd-resolved', 'unbound')
    check_dns()
    assert ANCHOR.read_bytes() == before
    # An already configured symlink must also be repairable when the runtime
    # target is missing and resolved needs to be started by the playbook.
    command('systemctl', 'stop', 'systemd-resolved')
    STUB.unlink(missing_ok=True)
    apply()
    check_dns()
    # Verify that a failed resolved start cannot be ignored or replace a working
    # resolver file with a dangling stub link.
    command('systemctl', 'stop', 'systemd-resolved')
    RESOLV.unlink()
    RESOLV.write_text('nameserver 192.0.2.53\n')
    dropin = Path('/etc/systemd/system/systemd-resolved.service.d')
    dropin.mkdir(exist_ok=True)
    (dropin / '99-fail.conf').write_text('[Service]\nExecStart=\nExecStart=/bin/false\nRestart=no\n')
    command('systemctl', 'daemon-reload')
    output = apply(expect_failure=True)
    assert 'Enable systemd-resolved' in output and re.search(r'ignored=0\s', output)
    assert not RESOLV.is_symlink() and RESOLV.read_text() == 'nameserver 192.0.2.53\n'
    (dropin / '99-fail.conf').unlink()
    command('systemctl', 'daemon-reload')
    command('systemctl', 'reset-failed', 'systemd-resolved')
    apply()
    check_dns()
    print('PASS: offline anchor seed, idempotence, restart, dangling link repair, startup failure/recovery')


if __name__ == '__main__':
    main()
