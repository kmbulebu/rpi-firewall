"""Run only in a disposable container with systemd as PID 1 and test dependencies.

Exercises real Ansible tasks, the oneshot/timer, HTTPS downloads and Unbound.
It replaces the container's Unbound configuration; never run on the router.
"""
import contextlib
import http.server
import os
from pathlib import Path
import ssl
import subprocess
import threading
import time

import dns.message
import dns.query
import dns.rcode
import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/firewall'
FIXTURE = Path('/run/rpi-firewall-blocklist-test')
POLICY = Path('/etc/unbound/unbound.conf.d/ad-blocking.conf')
MARKER = FIXTURE / 'core-applied'


def command(*args, **kwargs):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, **kwargs)


def query(name, record='A'):
    return dns.query.udp(dns.message.make_query(name, record), '127.0.0.1', timeout=3)


def ready():
    for _ in range(30):
        try:
            if query('example.test', 'SOA').rcode() == dns.rcode.NOERROR:
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise AssertionError('Test Unbound did not become ready')


def main():
    if os.environ.get('RPI_FIREWALL_DISPOSABLE_TEST') != '1' or not Path('/run/systemd/system').exists():
        raise SystemExit('Requires an explicitly disposable container running systemd')
    FIXTURE.mkdir(exist_ok=True)
    command('systemctl', 'stop', 'unbound.service')
    POLICY.unlink(missing_ok=True)
    Path('/etc/unbound/unbound.conf').write_text('''server:
  interface: 127.0.0.1
  username: ""
  chroot: ""
  directory: /run/rpi-firewall-blocklist-test
  pidfile: ""
  use-syslog: no
  module-config: "iterator"
  local-zone: "example.test." static
  local-data: "example.test. IN SOA localhost. nobody.invalid. 1 3600 1200 604800 10800"
  local-data: "ready.example.test. IN A 192.0.2.1"
include: "/etc/unbound/unbound.conf.d/*.conf"
''')
    # Discard distro snippets in this disposable fixture only.
    for path in Path('/etc/unbound/unbound.conf.d').glob('*.conf'):
        path.unlink()
    # Docker tmpfs mounts may be noexec; keep executable fixtures on the rootfs.
    reload_script = Path('/usr/local/sbin/test-reload-unbound')
    reload_script.write_text('''#!/bin/sh
if test -f /run/rpi-firewall-blocklist-test/fail-reload; then exit 1; fi
if test -f /run/rpi-firewall-blocklist-test/fail-once; then
  rm /run/rpi-firewall-blocklist-test/fail-once
  exit 1
fi
kill -HUP "$1"
''')
    reload_script.chmod(0o755)
    Path('/etc/systemd/system/unbound.service').write_text(f'''[Unit]
Description=Disposable Unbound fixture
[Service]
Type=simple
ExecStart=/usr/sbin/unbound -d -c /etc/unbound/unbound.conf
ExecReload={reload_script} $MAINPID
''')
    command('systemctl', 'daemon-reload')
    command('systemctl', 'start', 'unbound.service')
    ready()
    command('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
            '-keyout', str(FIXTURE / 'key.pem'), '-out', str(FIXTURE / 'ca.pem'),
            '-days', '1', '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1')
    dropin = Path('/etc/systemd/system/ad-blocklists-update.service.d')
    dropin.mkdir(parents=True, exist_ok=True)
    (dropin / '10-fixture.conf').write_text(f'[Service]\nEnvironment=SSL_CERT_FILE={FIXTURE}/ca.pem\n')
    mode = {'status': 503, 'body': 'ads.example.net\n', 'delay': 0, 'requests': 0, 'early': False}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            mode['requests'] += 1
            if not MARKER.exists():
                mode['early'] = True
            time.sleep(mode['delay'])
            with contextlib.suppress(BrokenPipeError, ConnectionResetError, ssl.SSLError):
                self.send_response(mode['status'])
                self.end_headers()
                self.wfile.write(mode['body'].encode())

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(FIXTURE / 'ca.pem', FIXTURE / 'key.pem')
    server.socket = context.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    role = FIXTURE / 'roles/blocklist_test'
    (role / 'tasks').mkdir(parents=True)
    for directory in ('defaults', 'files', 'templates'):
        (role / directory).symlink_to(ROLE / directory)
    (role / 'tasks/ad_blocking.yml').symlink_to(ROLE / 'tasks/ad_blocking.yml')
    main_tasks = yaml.safe_load((ROLE / 'tasks/main.yml').read_text())
    barrier = next(index for index, task in enumerate(main_tasks)
                   if task.get('ansible.builtin.meta') == 'flush_handlers')
    (role / 'tasks/main.yml').write_text(yaml.safe_dump([
        {'name': 'Simulate core configuration', 'ansible.builtin.copy': {
            'content': '{{ test_run }}', 'dest': str(FIXTURE / 'core-config')}, 'notify': 'apply core'},
    ] + main_tasks[barrier:]))
    play = [{'hosts': 'localhost', 'connection': 'local', 'gather_facts': False,
             'roles': ['blocklist_test'], 'handlers': [{'name': 'apply core', 'ansible.builtin.copy': {
                 'content': 'applied', 'dest': str(MARKER)}}]}]
    playfile = FIXTURE / 'play.yml'
    playfile.write_text(yaml.safe_dump(play))
    run = 0
    url = f'https://127.0.0.1:{server.server_port}/list'

    def apply(lists=None, fails=False):
        nonlocal run
        run += 1
        MARKER.unlink(missing_ok=True)
        values = dict(test_run=str(run), ad_blocklists=[url] if lists is None else lists,
                      domain='example.test', lan_address='127.0.0.1', ptr_zones=[], dns_private_domains=[])
        varfile = FIXTURE / 'vars.yml'
        varfile.write_text(yaml.safe_dump(values))
        result = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(playfile), '-e', '@' + str(varfile)],
                                capture_output=True, text=True, env=os.environ | {'ANSIBLE_NOCOLOR': '1'})
        assert (result.returncode != 0) == fails, result.stdout + result.stderr
        assert MARKER.exists(), result.stdout
        assert not mode['early'], 'Subscription requested before core handler completed'
        return result.stdout

    def unit_property(name, prop):
        return command('systemctl', 'show', name, '--property=' + prop, '--value').strip()

    def changed(output):
        return 'changed: [localhost]' in output.split('Report whether the installed policy changed', 1)[1]

    try:
        output = apply()
        assert 'No blocking rules are active' in output
        assert not POLICY.exists()
        assert unit_property('ad-blocklists-update.service', 'ActiveState') == 'failed'
        assert command('systemctl', 'is-enabled', 'ad-blocklists-update.timer').strip() == 'enabled'
        assert not changed(output)
        print('PASS: failed first download preserves core/DNS, reports inactive filtering, leaves retry enabled', flush=True)
        mode['status'] = 200
        output = apply()
        assert changed(output)
        assert query('ads.example.net').rcode() == dns.rcode.NXDOMAIN
        previous = POLICY.read_bytes()
        assert not changed(apply())
        print('PASS: successful policy update, blocking and unchanged repeat', flush=True)
        for status, body in ((503, 'unavailable'), (200, '<html>bad list</html>')):
            mode.update(status=status, body=body)
            output = apply()
            assert 'previous blocking policy is retained' in output
            assert POLICY.read_bytes() == previous and not changed(output)
            assert query('ads.example.net').rcode() == dns.rcode.NXDOMAIN
        print('PASS: download and parse failures retain policy and working DNS', flush=True)
        mode.update(status=200, body='ads.example.net\ntracker.example.net\n')
        command('systemctl', 'start', 'ad-blocklists-update.service')
        assert unit_property('ad-blocklists-update.service', 'ExecMainStatus') == '0'
        assert query('tracker.example.net').rcode() == dns.rcode.NXDOMAIN
        previous = POLICY.read_bytes()
        print('PASS: subsequent service run recovers without another Ansible run', flush=True)
        mode['body'] += 'another.example.net\n'
        (FIXTURE / 'fail-once').touch()
        output = apply()
        assert POLICY.read_bytes() == previous and not changed(output)
        print('PASS: reload failure restores and reloads previous policy', flush=True)
        (FIXTURE / 'fail-reload').touch()
        output = apply(fails=True)
        assert 'normal failure recovery' in output
        assert unit_property('ad-blocklists-update.service', 'ExecMainStatus') == '2'
        assert POLICY.read_bytes() == previous
        (FIXTURE / 'fail-reload').unlink()
        command('systemctl', 'reload', 'unbound.service')
        print('PASS: failed rollback remains a fatal DNS recovery error', flush=True)
        mode.update(status=503, delay=0)
        command('systemctl', 'stop', 'unbound.service')
        apply(fails=True)
        command('systemctl', 'start', 'unbound.service')
        ready()
        print('PASS: unavailable DNS is not hidden as an optional refresh failure', flush=True)
        # An interrupted download must retain policy and preserve the retry timer.
        (dropin / '20-timeout.conf').write_text('[Service]\nTimeoutStartSec=2s\nTimeoutStopSec=5s\n')
        command('systemctl', 'daemon-reload')
        mode.update(status=200, delay=5)
        output = apply()
        assert POLICY.read_bytes() == previous and not changed(output)
        (dropin / '20-timeout.conf').unlink()
        command('systemctl', 'daemon-reload')
        mode.update(status=503, delay=0)
        print('PASS: bounded timeout is nonfatal when DNS and policy remain healthy', flush=True)
        requests = mode['requests']
        output = apply(lists=[])
        assert changed(output)
        assert mode['requests'] == requests
        assert 'always_nxdomain' not in POLICY.read_text()
        disabled = subprocess.run(['systemctl', 'is-enabled', 'ad-blocklists-update.timer'], capture_output=True, text=True)
        assert disabled.stdout.strip() == 'disabled'
        assert not changed(apply(lists=[]))
        print('PASS: local clearing disables retries, needs no download and is idempotent', flush=True)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
