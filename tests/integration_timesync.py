"""Switch a disposable Ubuntu image from chrony to timesyncd and rerun.

No external NTP access and no permission to change the host's clock are needed.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT)


def main():
    if os.environ.get('RPI_FIREWALL_DISPOSABLE_TEST') != '1' or not Path('/run/systemd/system').exists():
        raise SystemExit('Requires an explicitly disposable systemd container')
    # Permit startup without CAP_SYS_TIME. Never grant it to this test container.
    for name in ('chrony', 'systemd-timesyncd'):
        dropin = Path(f'/etc/systemd/system/{name}.service.d')
        dropin.mkdir(exist_ok=True)
        (dropin / '99-container.conf').write_text(
            '[Unit]\nConditionCapability=\nConditionVirtualization=\n[Service]\nCapabilityBoundingSet=~CAP_SYS_TIME\nAmbientCapabilities=\n')
    # chronyd's -x mode also works without permission to set the clock.
    Path('/etc/default/chrony').write_text('DAEMON_OPTS="-x"\n')
    command('systemctl', 'daemon-reload')
    command('systemctl', 'enable', '--now', 'chrony')
    command('systemctl', 'is-active', 'chrony')
    assert not Path('/usr/lib/systemd/system/systemd-timesyncd.service').exists()
    with tempfile.TemporaryDirectory() as tmp:
        play = Path(tmp) / 'play.yml'
        play.write_text(yaml.safe_dump([{'hosts': 'all', 'gather_facts': False,
            'vars': {'ntp_servers': ['192.0.2.123', '198.51.100.123']},
            'tasks': [{'ansible.builtin.include_role': {'name': 'firewall', 'tasks_from': 'time'}}]}]))

        def apply():
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(play)],
                env={**os.environ, 'ANSIBLE_ROLES_PATH': str(ROOT / 'roles')},
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            print(result.stdout, flush=True)
            assert result.returncode == 0, result.stdout
            command('systemctl', 'is-active', 'systemd-timesyncd')
            command('systemctl', 'is-enabled', 'systemd-timesyncd')
            for name in ('chrony', 'ntp', 'ntpsec'):
                assert subprocess.run(['systemctl', 'is-active', '--quiet', name]).returncode != 0
            config = Path('/etc/systemd/timesyncd.conf').read_text()
            assert 'NTP=192.0.2.123' in config and 'NTP=198.51.100.123' in config
            return result.stdout

        apply()
        assert re.search(r'changed=0\s', apply())
    print('PASS: competing daemon replaced, configured servers retained, idempotence')


if __name__ == '__main__':
    main()
