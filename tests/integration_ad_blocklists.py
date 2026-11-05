"""Run explicitly in a disposable environment with unbound, dig and HTTPS access."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import time

spec = importlib.util.spec_from_file_location(
    'blocklists', Path(__file__).resolve().parents[1] /
    'roles/firewall/files/unbound/update-ad-blocklists.py')
blocklists = importlib.util.module_from_spec(spec)
spec.loader.exec_module(blocklists)


def check_query(name, qtype, tcp, expected):
    args = ['dig', '@127.0.0.1', '-p', '15353', name, qtype,
            '+time=10', '+tries=1', '+noall', '+comments', '+answer']
    if tcp:
        args.append('+tcp')
    result = subprocess.check_output(args, text=True)
    assert 'status: ' + expected in result, result
    if expected == 'NOERROR':
        assert 'IN\tA\t' in result, result
    print(name, qtype, 'TCP' if tcp else 'UDP', expected, 'PASS', flush=True)


def run_queries(root, queries):
    config = root / 'unbound.conf'
    subprocess.run(['unbound-checkconf', str(config)], check=True)
    with (root / 'unbound.log').open('w') as log:
        server = subprocess.Popen(['unbound', '-d', '-c', str(config)], stdout=log, stderr=log)
        try:
            for _ in range(50):
                if server.poll() is not None:
                    raise AssertionError((root / 'unbound.log').read_text())
                ready = subprocess.run(
                    ['dig', '@127.0.0.1', '-p', '15353', 'ready.example.test',
                     '+time=1', '+tries=1', '+short'], capture_output=True, text=True)
                if '192.0.2.1' in ready.stdout:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError('Unbound did not become ready')
            for query in queries:
                check_query(*query)
        finally:
            server.terminate()
            server.wait(timeout=10)


def main():
    blocked, exceptions = blocklists.parse_list(blocklists.download('https://small.oisd.nl'))
    print(f'Downloaded {len(blocked)} OISD rules', flush=True)
    with tempfile.TemporaryDirectory(prefix='unbound-blocklist-integration-') as tmp:
        root = Path(tmp)
        policy = root / 'policy.conf'
        (root / 'unbound.conf').write_text(
            'server:\n  interface: 127.0.0.1\n  port: 15353\n  username: ""\n'
            '  chroot: ""\n  directory: "' + tmp + '"\n  pidfile: ""\n'
            '  use-syslog: no\n  do-daemonize: no\n'
            '  tls-cert-bundle: /etc/ssl/certs/ca-certificates.crt\n'
            '  local-zone: "example.test." static\n'
            '  local-data: "ready.example.test. IN A 192.0.2.1"\n'
            'include: "' + str(policy) + '"\nforward-zone:\n  name: "."\n'
            '  forward-tls-upstream: yes\n'
            '  forward-addr: 1.1.1.3@853#family.cloudflare-dns.com\n')
        def install(allowed, entries=blocked):
            content, count = blocklists.compile_policy(entries, exceptions | allowed, {'example.test'})
            blocklists.atomic_write(policy, content.encode())
            print(f'Testing {count} blocks, {len(allowed)} inventory exceptions', flush=True)
        install(set())
        run_queries(root, [
            ('pagead2.googlesyndication.com', 'A', False, 'NXDOMAIN'),
            ('pagead2.googlesyndication.com', 'AAAA', True, 'NXDOMAIN'),
            ('sub.pagead2.googlesyndication.com', 'A', False, 'NXDOMAIN'),
            ('ready.example.test', 'A', False, 'NOERROR'),
            ('example.com', 'A', False, 'NOERROR'),
        ])
        install({'pagead2.googlesyndication.com'})
        run_queries(root, [
            ('pagead2.googlesyndication.com', 'A', False, 'NOERROR'),
            ('googlesyndication.com', 'A', False, 'NXDOMAIN'),
        ])
        install(set(), entries=set())
        run_queries(root, [('pagead2.googlesyndication.com', 'A', False, 'NOERROR')])
    print('PASS: blocking, allowlist, local names, TLS forwarding and disabling', flush=True)


if __name__ == '__main__':
    main()
