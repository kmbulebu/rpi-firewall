"""Check MOTD rendering, secret suppression and lightweight conntrack counters."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from jinja2 import Environment, FileSystemLoader, StrictUndefined
import yaml

ROLE = Path(__file__).resolve().parents[1] / 'roles/firewall'
spec = importlib.util.spec_from_file_location('motd_conntrack', ROLE / 'files/motd/68-conntrack-status.py')
conntrack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(conntrack)


class MotdTests(unittest.TestCase):
    def setUp(self):
        self.values = yaml.safe_load((ROLE / 'defaults/main.yml').read_text())
        self.env = Environment(loader=FileSystemLoader(ROLE / 'templates'), undefined=StrictUndefined)
        self.env.filters['bool'] = bool
        self.env.filters['quote'] = shlex.quote

    def render(self, path):
        return self.env.get_template(path).render(**self.values)

    def test_per_interface_leases_use_reverse_expiration_order(self):
        for enabled in (False, True):
            self.values['enable_lan_vpn'] = enabled
            script = self.render('motd/75-dhcp-leases')
            self.assertIn('LIMIT=2', script)
            self.assertIn('--sort exp --reverse --iface', script)
            self.assertIn('"lan0"', script)
            self.assertIn('"lan_guests"', script)
            self.assertEqual('"lan_vpn"' in script, enabled)
            subprocess.run(['bash', '-n'], input=script, text=True, check=True)
        self.values['motd_dhcp_leases_limit'] = 3
        self.assertIn('LIMIT=3', self.render('motd/75-dhcp-leases'))
        self.assertNotIn('route show', self.render('motd/60-wan-status'))

    def test_wireguard_forces_hidden_keys_and_uses_configured_interface(self):
        self.values.update(enable_lan_vpn=True, vpn_client_iface='tunnel-test')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'ip').write_text('#!/bin/sh\nexit 0\n')
            (root / 'wg').write_text('''#!/bin/sh
[ "$WG_HIDE_KEYS" = always ] || exit 1
[ "$WG_COLOR_MODE" = never ] || exit 1
[ "$1 $2" = 'show tunnel-test' ] || exit 1
printf 'latest handshake: 5 seconds ago\ntransfer: 1 MiB received, 2 MiB sent\n'
''')
            for name in ('ip', 'wg'):
                (root / name).chmod(0o755)
            result = subprocess.run(['bash'], input=self.render('motd/65-wireguard-status'),
                                    text=True, capture_output=True, check=True,
                                    env=os.environ | {'PATH': tmp + ':' + os.defpath, 'WG_HIDE_KEYS': 'never'})
            self.assertIn('latest handshake: 5 seconds ago', result.stdout)
            self.assertIn('transfer: 1 MiB', result.stdout)
        self.values['enable_lan_vpn'] = False
        result = subprocess.run(['bash'], input=self.render('motd/65-wireguard-status'),
                                text=True, capture_output=True, check=True)
        self.assertIn('VPN disabled', result.stdout)

    def test_lease_utility_limits_to_the_two_highest_known_remaining_times(self):
        now = time.clock_gettime(time.CLOCK_BOOTTIME)
        entries = []
        for last_octet, remaining in ((1, 60), (2, 1200), (3, 600), (4, None)):
            expires = int((now + remaining) * 1_000_000) if remaining is not None else 'unknown'
            entries.append([2, [], [192, 0, 2, last_octet], [], [0, 1, 2, 3, 4, last_octet], expires])
        result = subprocess.run([sys.executable, str(ROLE / 'files/tools/show_dhcp_leases.py'),
                                 '--sort', 'exp', '--reverse', '--limit', '2'],
                                input=json.dumps({'data': entries}), text=True, capture_output=True, check=True)
        rows = result.stdout.splitlines()
        self.assertEqual(len(rows), 2)
        self.assertIn('192.0.2.2 ', rows[0])
        self.assertIn('192.0.2.3 ', rows[1])

    def test_proc_counters_sum_cpu_values_by_name(self):
        text = 'entries insert_failed drop early_drop\n000a 0001 0002 0003\n000a 0004 0008 0010\n'
        self.assertEqual(conntrack.proc_counters(text), {'insert_failed': 5, 'drop': 10, 'early_drop': 19})

    def test_conntrack_netlink_fallback_is_bounded_and_does_not_dump_flows(self):
        text = 'cpu=0 drop=2 early_drop=3 insert_failed=4\ncpu=1 insert_failed=7 drop=8 early_drop=9\n'
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(conntrack.shutil, 'which', return_value='/usr/sbin/conntrack'), \
                patch.object(conntrack.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, text)) as run:
            self.assertEqual(conntrack.get_counters(Path(tmp)), {'drop': 10, 'early_drop': 12, 'insert_failed': 11})
            run.assert_called_once_with(['/usr/sbin/conntrack', '-S'], check=True,
                                        capture_output=True, text=True, timeout=1)

    def test_conntrack_pressure_and_unavailable_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / 'sys/net/netfilter'
            directory.mkdir(parents=True)
            for name, value in [('count', 950), ('max', 1000), ('tcp_timeout_established', 3600)]:
                (directory / ('nf_conntrack_' + name)).write_text(str(value))
            with patch.object(conntrack, 'get_counters', return_value={'drop': 5}), contextlib.redirect_stdout(io.StringIO()) as out:
                conntrack.show(root)
            self.assertIn('950 / 1,000 (95.0%) HIGH', out.getvalue())
            self.assertIn('3,600 seconds', out.getvalue())
            self.assertIn('Cumulative drops: 5', out.getvalue())
        with patch.object(conntrack, 'get_counters', return_value={}), contextlib.redirect_stdout(io.StringIO()) as out:
            conntrack.show(Path('/nonexistent'))
        self.assertIn('unavailable', out.getvalue())
