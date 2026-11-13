#!/usr/bin/python3
"""Retire only virtual links identified by the role's previous netdev files."""
import argparse
import configparser
import json
from pathlib import Path
import re
import subprocess

MANAGED = (
    ('00-lan-vpn.netdev', 'vlan'),
    ('30-wireguard-client.netdev', 'wireguard'),
    ('00-vpn-vrf.netdev', 'vrf'),
)


def valid_name(name):
    return bool(re.fullmatch(r'[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,14}', name)) and name != 'lo'


def plan(directory, desired, pending=None):
    retired = []
    if pending and Path(pending).exists():
        retired = json.loads(Path(pending).read_text())
        if not isinstance(retired, list) or any(
            not isinstance(item, dict) or not valid_name(item.get('name', ''))
            or item.get('kind') not in {entry[1] for entry in MANAGED}
            for item in retired
        ):
            raise ValueError('Invalid pending VPN cleanup record')
    for filename, kind in MANAGED:
        path = Path(directory) / filename
        if not path.exists():
            continue
        config = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            config.read_string(path.read_text())
            name = config.get('NetDev', 'Name')
            recorded_kind = config.get('NetDev', 'Kind')
        except (configparser.Error, UnicodeError):
            # Never include file contents: WireGuard netdevs contain private keys.
            raise ValueError(f'Cannot inspect managed netdev {filename}') from None
        if not valid_name(name) or recorded_kind != kind:
            raise ValueError(f'Unexpected device identity in {filename}')
        if name != desired.get(kind):
            item = {'name': name, 'kind': kind}
            if item not in retired:
                retired.append(item)
    retired = [item for item in retired if item['name'] != desired.get(item['kind'])]
    if any(item['name'] in desired.values() for item in retired):
        raise ValueError('Disable VPN before reusing an existing device name for another device kind')
    return retired


def delete_link(name, kind):
    if not valid_name(name) or kind not in {item[1] for item in MANAGED}:
        raise ValueError('Invalid VPN device identity')
    links = json.loads(subprocess.check_output(['ip', '-j', '-d', 'link', 'show'], text=True))
    link = next((item for item in links if item['ifname'] == name), None)
    if link is None:
        return False
    if link.get('linkinfo', {}).get('info_kind') != kind:
        raise ValueError(f'Refusing to delete {name}: kernel device kind does not match {kind}')
    subprocess.run(['ip', 'link', 'delete', 'dev', name], check=True)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    inspect = commands.add_parser('plan')
    inspect.add_argument('--directory', default='/etc/systemd/network')
    inspect.add_argument('--pending', default='/var/lib/rpi-firewall/retired-vpn-links.json')
    for kind in ('vlan', 'wireguard', 'vrf'):
        inspect.add_argument('--' + kind)
    delete = commands.add_parser('delete')
    delete.add_argument('kind', choices=['vlan', 'wireguard', 'vrf'])
    delete.add_argument('name')
    args = parser.parse_args()
    if args.command == 'plan':
        desired = {kind: getattr(args, kind) for kind in ('vlan', 'wireguard', 'vrf')}
        print(json.dumps(plan(args.directory, desired, args.pending)))
    else:
        print(json.dumps({'changed': delete_link(args.name, args.kind)}))


if __name__ == '__main__':
    main()
