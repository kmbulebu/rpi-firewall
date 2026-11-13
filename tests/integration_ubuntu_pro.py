"""Exercise real Pro Ansible tasks with a fake CLI in an offline disposable container.

No subscription is attached. The installed real CLI is used only to validate the
unattached status schema. No private inventory or production token is read.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/firewall'
WORK = Path('/run/rpi-ubuntu-pro-test')
TOKEN = 'TEST-ONLY-PRO-TOKEN-DO-NOT-PRINT'
FAKE = r'''#!/usr/bin/python3
import json, os, pathlib, stat, sys, yaml
root = pathlib.Path('/run/rpi-ubuntu-pro-test')
statefile = root / 'state.json'
state = json.loads(statefile.read_text())
args = sys.argv[1:]
assert (root / 'core-handler').exists(), 'Pro ran before core handler'
assert all('TEST-ONLY-PRO-TOKEN' not in arg for arg in args)
with (root / 'calls.jsonl').open('a') as f:
    f.write(json.dumps(args) + '\n')

def save(): statefile.write_text(json.dumps(state))
def error():
    # Deliberately echo the fake secret to prove task failures are censored.
    print('TEST-ONLY-PRO-TOKEN-DO-NOT-PRINT', file=sys.stderr)
    print('TEST-ONLY-PRO-TOKEN-DO-NOT-PRINT')
    sys.exit(1)

if args == ['status', '--format', 'json']:
    if state.get('fail_status'): error()
    if state.get('bad_schema'):
        print(json.dumps({'attached': 'unknown', 'services': []}))
    else:
        print(json.dumps({'attached': state['attached'], 'services': [
            ({'name': name, 'status': value} if state['attached'] else {'name': name, 'available': 'yes'})
            for name, value in state['services'].items()]}))
elif args[0] == 'attach':
    assert '--no-auto-enable' in args
    config = pathlib.Path(args[args.index('--attach-config') + 1])
    assert config.parent == pathlib.Path('/run')
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert config.stat().st_uid == 0
    content = yaml.safe_load(config.read_text())
    assert content == {'token': 'TEST-ONLY-PRO-TOKEN-DO-NOT-PRINT', 'enable_services': []}
    state['attach_path'] = str(config)
    state['attach_count'] = state.get('attach_count', 0) + 1
    if state.get('partial_attach'): state['attached'] = True
    save()
    if state.get('fail_attach'): error()
    state['attached'] = True
    save()
elif args[:2] == ['enable', '--assume-yes']:
    service = args[2]
    assert state['attached']
    if state.get('fail_enable') == service: error()
    if not state.get('noop_enable'):
        state['services'][service] = 'enabled'
    save()
else:
    raise AssertionError('Unexpected Pro command: ' + repr(args))
'''


def command(*args, **kwargs):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=60, **kwargs)


def main():
    if os.environ.get('RPI_FIREWALL_DISPOSABLE_TEST') != '1' or not (
            Path('/.dockerenv').exists() or Path('/run/.containerenv').exists()):
        raise SystemExit('Requires an explicitly disposable container')
    # Use the real client for a read-only check of our JSON assumptions before
    # intercepting the CLI. The test image starts unattached, with networking off.
    real = subprocess.run(['/usr/bin/pro', 'status', '--format', 'json'],
                          text=True, capture_output=True, timeout=40)
    status = json.loads(real.stdout)
    if real.returncode:
        # A fresh client needs Canonical's public resources endpoint even when
        # unattached. With networking off it returns a separate error schema.
        assert status['result'] == 'failure' and status['errors']
    else:
        assert status['attached'] is False and isinstance(status['services'], list)
        assert all('name' in service for service in status['services'])
    print('PASS: real client read-only status/offline error is JSON', flush=True)
    WORK.mkdir(mode=0o700)
    fake = Path('/usr/local/sbin/pro')
    fake.write_text(FAKE)
    fake.chmod(0o755)
    os.environ['PATH'] = '/usr/local/sbin:' + os.environ['PATH']
    statefile = WORK / 'state.json'
    callsfile = WORK / 'calls.jsonl'

    def reset(**extra):
        statefile.write_text(json.dumps(dict(attached=False, services={}) | extra))
        callsfile.write_text('')

    def state():
        return json.loads(statefile.read_text())

    def calls():
        return [json.loads(line) for line in callsfile.read_text().splitlines()]

    with tempfile.TemporaryDirectory(dir=WORK) as tmp:
        root = Path(tmp)
        role = root / 'roles/pro_test'
        (role / 'tasks').mkdir(parents=True)
        (role / 'defaults').symlink_to(ROLE / 'defaults')
        main_tasks = yaml.safe_load((ROLE / 'tasks/main.yml').read_text())
        flush = next(i for i, task in enumerate(main_tasks) if task.get('ansible.builtin.meta') == 'flush_handlers')
        pro_index = next(i for i, task in enumerate(main_tasks)
                         if task.get('ansible.builtin.include_tasks') == 'ubuntu_pro.yml')
        assert flush < pro_index
        pro_include = dict(main_tasks[pro_index])
        pro_include['ansible.builtin.include_tasks'] = str(ROLE / 'tasks/ubuntu_pro.yml')
        (role / 'tasks/main.yml').write_text(yaml.safe_dump([
            {'name': 'Apply core configuration fixture', 'ansible.builtin.copy': {
                'content': 'configured', 'dest': str(WORK / 'core')}, 'notify': 'apply core handler'},
            main_tasks[flush], pro_include,
            {'name': 'Continue after optional Pro configuration', 'ansible.builtin.debug': {
                'msg': 'PRO_TEST_CONTINUED'}},
        ]))
        (root / 'play.yml').write_text(yaml.safe_dump([{
            'hosts': 'localhost', 'gather_facts': False, 'connection': 'local', 'roles': ['pro_test'],
            'handlers': [{'name': 'apply core handler', 'ansible.builtin.copy': {
                'content': 'applied', 'dest': str(WORK / 'core-handler')}}],
        }]))
        varfile = root / 'vars.yml'

        def apply(values=None, fail=False, check=False):
            values = {'ubuntu_pro_token': TOKEN} if values is None else values
            varfile.write_text(yaml.safe_dump(values))
            varfile.chmod(0o600)
            args = ['ansible-playbook', '-i', 'localhost,', str(root / 'play.yml'), '-e', '@' + str(varfile), '--diff']
            if check:
                args.append('--check')
            result = subprocess.run(args, text=True, capture_output=True, timeout=90)
            output = result.stdout + result.stderr
            assert TOKEN not in output, 'Token leaked into Ansible output'
            assert not list(Path('/run').glob('rpi-ubuntu-pro-*.yaml')), 'Temporary token file left behind'
            if (result.returncode != 0) != fail:
                raise AssertionError(output)
            if not fail and not check:
                assert 'PRO_TEST_CONTINUED' in output, output
            return output

        def unchanged():
            output = apply()
            assert re.search(r'changed=0\s', output), output
            assert 'WARNING: Ubuntu Pro' not in output, output

        reset()
        disabled = [{}, {'ubuntu_pro_token': ''}, {'ubuntu_pro_token': None}, {'ubuntu_pro_token': '  '}]
        for values in disabled:
            apply(values)
            assert calls() == [] and not state()['attached']
        print('PASS: absent/empty/null/whitespace token skips management', flush=True)
        apply(check=True)
        assert calls() == []
        print('PASS: check mode does not inspect or mutate subscription', flush=True)
        for values in [dict(ubuntu_pro_services='esm-apps'), dict(ubuntu_pro_services=['--invalid']),
                       dict(ubuntu_pro_token=True)]:
            apply({'ubuntu_pro_token': TOKEN} | values, fail=True)
            assert not any(call[0] in ('attach', 'enable') for call in calls())
        print('PASS: invalid inventory fails before mutation', flush=True)
        for flag in ('fail_status', 'bad_schema'):
            reset(**{flag: True})
            output = apply()
            assert 'WARNING: Ubuntu Pro' in output and not state()['attached']
            assert not any(call[0] in ('attach', 'enable') for call in calls())
        print('PASS: unavailable/malformed status preserves core run and skips attachment', flush=True)
        reset()
        output = apply({'ubuntu_pro_token': TOKEN})
        assert 'WARNING: Ubuntu Pro' not in output, output
        assert state()['attached'] and state()['attach_count'] == 1
        assert state()['services'] == {'esm-apps': 'enabled', 'esm-infra': 'enabled'}
        assert not Path(state()['attach_path']).exists()
        before = len([call for call in calls() if call[0] in ('attach', 'enable')])
        unchanged()
        assert len([call for call in calls() if call[0] in ('attach', 'enable')]) == before
        print('PASS: attach, explicit ESM services, token cleanup and unchanged retry with token retained', flush=True)
        before = calls()
        for values in disabled:
            apply(values)
            assert calls() == before and state()['attached']
        apply({'ubuntu_pro_token': TOKEN, 'ubuntu_pro_services': []})
        assert state()['services'] == {'esm-apps': 'enabled', 'esm-infra': 'enabled'}
        print('PASS: disabling management or removing services never detaches/disables', flush=True)
        for partial in (False, True):
            reset(fail_attach=True, partial_attach=partial)
            output = apply({'ubuntu_pro_token': TOKEN})
            assert 'WARNING: Ubuntu Pro' in output
            assert not Path(state()['attach_path']).exists()
            prior = state()
            prior['fail_attach'] = False
            statefile.write_text(json.dumps(prior))
            apply()
            assert state()['attach_count'] == (1 if partial else 2)
            assert state()['services'] == {'esm-apps': 'enabled', 'esm-infra': 'enabled'}
        print('PASS: failed/partial attachment cleans token and retries from observed state', flush=True)
        reset(attached=True, fail_enable='esm-infra')
        output = apply()
        assert 'WARNING: Ubuntu Pro' in output and state()['services'] == {'esm-apps': 'enabled'}
        prior = state()
        prior.pop('fail_enable')
        statefile.write_text(json.dumps(prior))
        apply()
        assert state()['services'] == {'esm-apps': 'enabled', 'esm-infra': 'enabled'}
        unchanged()
        print('PASS: partial enable failure is visible and retry enables only missing services', flush=True)
        reset(attached=True, noop_enable=True)
        assert 'WARNING: Ubuntu Pro' in apply()
        print('PASS: verification detects a service that did not become enabled', flush=True)
        reset()
        apply({'ubuntu_pro_services': [], 'ubuntu_pro_token': TOKEN})
        assert state()['attached'] and state()['services'] == {}
        print('PASS: empty requested service list attaches without enabling defaults', flush=True)
    fake.unlink()


if __name__ == '__main__':
    main()
