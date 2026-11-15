"""Exercise actual authentication tasks with a local, disposable CLI fixture."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class TailscaleAuthTests(unittest.TestCase):
    def run_state(self, state, *, output=None, rc=0, login=False, fails=False):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            cli = path / 'tailscale'
            cli.write_text('''#!/usr/bin/env python3
import os, pathlib, sys
if sys.argv[1] == 'status':
    print(os.environ['STATUS_FIXTURE'])
    sys.exit(int(os.environ['STATUS_RC']))
assert sys.argv[1:3] == ['login', '--auth-key']
pathlib.Path(os.environ['LOGIN_MARKER']).touch()
''')
            cli.chmod(0o755)
            play = [{'hosts': 'all', 'gather_facts': False,
                     'vars': {'tailscale_auth_key': 'test-secret-never-log'},
                     'tasks': [{'ansible.builtin.include_role':
                                {'name': 'firewall', 'tasks_from': 'tailscale_auth'}}]}]
            (path / 'play.yml').write_text(yaml.safe_dump(play))
            result = subprocess.run(
                ['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(path / 'play.yml')],
                env={**os.environ, 'PATH': tmp + os.pathsep + os.environ['PATH'],
                     'ANSIBLE_ROLES_PATH': str(ROOT / 'roles'), 'ANSIBLE_NOCOLOR': '1',
                     'STATUS_FIXTURE': output if output is not None else json.dumps({'BackendState': state}),
                     'STATUS_RC': str(rc), 'LOGIN_MARKER': str(path / 'login')},
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            self.assertEqual(result.returncode != 0, fails, result.stdout)
            self.assertEqual((path / 'login').exists(), login, result.stdout)
            self.assertNotIn('test-secret-never-log', result.stdout)
            if not fails and not login:
                self.assertRegex(result.stdout, r'changed=0\s')
            return result.stdout

    def test_expected_states_are_read_only(self):
        for state in ('Running', 'Starting', 'Stopped', 'NeedsMachineAuth'):
            with self.subTest(state=state):
                self.run_state(state)

    def test_needs_login_authenticates_even_with_an_existing_node_key(self):
        self.run_state('NeedsLogin', output='{"BackendState":"NeedsLogin","HaveNodeKey":true}', login=True)

    def test_failures_never_attempt_login(self):
        for output, rc in [('daemon unavailable', 1), ('invalid json', 0), ('{}', 0),
                           ('{"BackendState":"NoState"}', 0)]:
            with self.subTest(output=output):
                self.run_state(None, output=output, rc=rc, fails=True)
