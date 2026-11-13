import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'roles/firewall/files/unbound/update-ad-blocklists.py'
spec = importlib.util.spec_from_file_location('blocklists', SCRIPT)
blocklists = importlib.util.module_from_spec(spec)
spec.loader.exec_module(blocklists)


class BlocklistTests(unittest.TestCase):
    def test_supported_formats_and_source_exceptions(self):
        blocked, allowed = blocklists.parse_list('''[Adblock Plus]
! comment
||ads.example.com^
@@||allowed.ads.example.com^
0.0.0.0 tracker.example.com another.example.com # hosts
127.0.0.1 localhost
ff02::1 ip6-allnodes
plain.example.com
''')
        self.assertEqual(blocked, {'ads.example.com', 'tracker.example.com',
                                   'another.example.com', 'plain.example.com'})
        self.assertEqual(allowed, {'allowed.ads.example.com'})

    def test_bad_or_empty_download_cannot_become_a_policy(self):
        for text in ('<html>server error</html>', '! empty', '||example.com^$script',
                     '||com^', '||bad".example^', '||127.0.0.1^'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                blocklists.parse_list(text)

    def test_allowlist_overrides_parent_and_descendant_blocks(self):
        policy, count = blocklists.compile_policy(
            {'example.com', 'safe.example.com', 'sub.safe.example.com'},
            {'safe.example.com'}, set())
        self.assertEqual(count, 1)
        self.assertIn('"example.com." always_nxdomain', policy)
        self.assertIn('"safe.example.com." transparent', policy)
        self.assertNotIn('"sub.safe.example.com."', policy)

    def test_local_zones_and_their_parents_are_protected(self):
        policy, count = blocklists.compile_policy(
            {'home.example', 'printer.home.example', 'example', 'ads.other.example'},
            set(), {'home.example'})
        self.assertEqual(count, 1)
        self.assertIn('"ads.other.example." always_nxdomain', policy)

    def test_identical_policy_does_not_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'policy.conf'
            path.write_text('server:\n')
            with patch.object(blocklists.subprocess, 'run') as run:
                blocklists.apply_policy(path, 'server:\n', Path(tmp) / 'unbound.conf')
                run.assert_not_called()

    def test_invalid_candidate_never_replaces_existing_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'policy.conf'
            path.write_text('old')
            with patch.object(blocklists.subprocess, 'run',
                              side_effect=subprocess.CalledProcessError(1, 'checkconf')):
                with self.assertRaises(subprocess.CalledProcessError):
                    blocklists.apply_policy(path, 'new', Path(tmp) / 'unbound.conf')
            self.assertEqual(path.read_text(), 'old')

    def test_full_validation_or_reload_failure_rolls_back(self):
        for failure in (1, 2):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'policy.conf'
                path.write_text('old')
                outcomes = [None] * failure + [subprocess.CalledProcessError(1, 'command'), None]
                with patch.object(blocklists.subprocess, 'run', side_effect=outcomes):
                    with self.assertRaises(subprocess.CalledProcessError):
                        blocklists.apply_policy(path, 'new', Path(tmp) / 'unbound.conf')
                self.assertEqual(path.read_text(), 'old')

    def test_empty_subscriptions_clear_blocking(self):
        policy, count = blocklists.compile_policy(set(), {'safe.example.com'}, set())
        self.assertEqual(count, 0)
        self.assertNotIn('local-zone:', policy)

    def test_rollback_reload_failure_is_critical(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'policy.conf'
            path.write_text('old')
            failed = subprocess.CalledProcessError(1, 'reload')
            with patch.object(blocklists.subprocess, 'run', side_effect=[None, None, failed, failed]):
                with self.assertRaises(blocklists.PolicyRollbackError):
                    blocklists.apply_policy(path, 'new', Path(tmp) / 'unbound.conf')
            self.assertEqual(path.read_text(), 'old')

    def test_interrupted_transaction_restores_previous_state(self):
        for previous in (None, 'old'):
            with self.subTest(previous=previous), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'policy.conf'
                if previous is not None:
                    path.write_text(previous)
                with patch.object(blocklists.subprocess, 'run', side_effect=[None, InterruptedError(), None]):
                    with self.assertRaises(InterruptedError):
                        blocklists.apply_policy(path, 'new', Path(tmp) / 'unbound.conf')
                self.assertEqual(path.read_text() if path.exists() else None, previous)

    def test_status_is_local_and_distinguishes_empty_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'policy.conf'
            self.assertEqual(blocklists.policy_status(path), {'checksum': None, 'blocked_domains': 0})
            path.write_text('server:\n')
            empty = blocklists.policy_status(path)
            self.assertEqual(empty['blocked_domains'], 0)
            path.write_text('server:\n  local-zone: "ads.example.test." always_nxdomain\n')
            active = blocklists.policy_status(path)
            self.assertEqual(active['blocked_domains'], 1)
            self.assertNotEqual(active['checksum'], empty['checksum'])


if __name__ == '__main__':
    unittest.main()
