#!/usr/bin/python3
"""Compile subscribed DNS lists into a validated, atomic Unbound policy."""

import argparse
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.request


MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024


def domain(value):
    value = value.lower().rstrip('.')
    labels = value.split('.')
    if len(value) > 253 or len(labels) < 2 or any(
        not re.fullmatch(r'[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?', label)
        for label in labels
    ):
        raise ValueError(f'Invalid DNS domain: {value!r}')
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    raise ValueError(f'IP address is not a DNS domain: {value!r}')


def parse_list(text):
    """Accept DNS-only ABP rules, hosts files, and plain domain lists.

    Reject unsupported rules instead of silently claiming complete coverage.
    Hosts mappings for localhost and non-blocking addresses are not policies.
    """
    blocked, allowed = set(), set()
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith(('!', '#', '[')):
            continue
        target = blocked
        if line.startswith('@@'):
            target = allowed
            line = line[2:]
        if line.startswith('||') and line.endswith('^'):
            target.add(domain(line[2:-1]))
            continue
        fields = line.split('#', 1)[0].split()
        if not fields:
            continue
        if len(fields) > 1:
            if fields[0] not in ('0.0.0.0', '127.0.0.1', '::', '::1'):
                try:
                    ipaddress.ip_address(fields[0])
                except ValueError:
                    raise ValueError(f'Unsupported hosts mapping at line {number}') from None
                continue
            for host in fields[1:]:
                if host in ('localhost', 'localhost.localdomain', 'local',
                            'broadcasthost', 'ip6-localhost', 'ip6-loopback',
                            'ip6-localnet', 'ip6-mcastprefix', 'ip6-allnodes',
                            'ip6-allrouters', 'ip6-allhosts'):
                    continue
                target.add(domain(host))
        else:
            target.add(domain(fields[0]))
    if not blocked:
        raise ValueError('Subscription contained no blocking domains')
    return blocked, allowed


def ancestors(name):
    labels = name.split('.')
    return {'.'.join(labels[index:]) for index in range(len(labels))}


def compile_policy(blocked, allowed, protected):
    # Never let a subscription shadow local forward/reverse/private zones.
    protected_parents = set().union(*(ancestors(x) for x in protected))
    blocked = {
        name for name in blocked
        if not ancestors(name) & (allowed | protected)
        and name not in protected_parents
    }
    # A transparent child zone permits an allowed subtree under a blocked parent.
    exceptions = {name for name in allowed if ancestors(name) & blocked}
    lines = ['# Managed by update-ad-blocklists; edit inventory, not this file.', 'server:']
    lines += [f'  local-zone: "{name}." always_nxdomain' for name in sorted(blocked)]
    lines += [f'  local-zone: "{name}." transparent' for name in sorted(exceptions)]
    return '\n'.join(lines) + '\n', len(blocked)


def download(url):
    if not isinstance(url, str) or not url.startswith('https://'):
        raise ValueError('Blocklist subscriptions must use HTTPS')
    request = urllib.request.Request(url, headers={'User-Agent': 'rpi-firewall-blocklists/1'})
    with urllib.request.urlopen(request, timeout=60) as response:
        if not response.url.startswith('https://'):
            raise ValueError('Refusing subscription redirected away from HTTPS')
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError('Subscription exceeds 64 MiB limit')
    return data.decode('utf-8-sig')


def atomic_write(path, data):
    descriptor, temporary = tempfile.mkstemp(prefix='.ad-blocking-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply_policy(path, content, config):
    previous = path.read_bytes() if path.exists() else None
    data = content.encode()
    if previous == data:
        print('Policy unchanged')
        return
    # Validate candidate syntax before installing it, then validate the entire
    # configuration before asking the running server to load it.
    with tempfile.TemporaryDirectory(prefix='ad-blocking-') as temporary:
        candidate = Path(temporary) / 'candidate.conf'
        candidate.write_bytes(data)
        subprocess.run(['unbound-checkconf', str(candidate)], check=True)
    atomic_write(path, data)
    try:
        subprocess.run(['unbound-checkconf', str(config)], check=True)
        subprocess.run(['systemctl', 'reload', 'unbound.service'], check=True)
    except BaseException:
        if previous is None:
            path.unlink()
        else:
            atomic_write(path, previous)
        # A failed reload may have partially applied the candidate. Restore the
        # old on-disk configuration and attempt to load it again.
        subprocess.run(['systemctl', 'reload', 'unbound.service'], check=False)
        raise
    print('Policy updated:', path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/etc/rpi-firewall/ad-blocking.json'))
    parser.add_argument('--output', type=Path, default=Path('/etc/unbound/unbound.conf.d/ad-blocking.conf'))
    parser.add_argument('--unbound-config', type=Path, default=Path('/etc/unbound/unbound.conf'))
    parser.add_argument('--lock', type=Path, default=Path('/run/lock/ad-blocklists-update.lock'))
    parser.add_argument('--compile-only', action='store_true', help='Compile to --output without loading it')
    args = parser.parse_args()
    with args.lock.open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        config = json.loads(args.config.read_text())
        blocked, allowed = set(), {domain(x) for x in config['allow_domains']}
        protected = {domain(x) for x in config['protected_domains']}
        for url in config['blocklists']:
            entries, exceptions = parse_list(download(url))
            blocked.update(entries)
            allowed.update(exceptions)
            print(f'Fetched {len(entries)} blocking domains from {url}')
        content, count = compile_policy(blocked, allowed, protected)
        if args.compile_only:
            atomic_write(args.output, content.encode())
        elif not config['blocklists'] and not args.output.exists():
            print('Policy unchanged (no subscriptions)')
        else:
            apply_policy(args.output, content, args.unbound_config)
        print(f'Compiled {count} blocking domains; {len(allowed)} exceptions')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'Blocklist update failed; previous policy retained: {error}', file=sys.stderr)
        sys.exit(1)
