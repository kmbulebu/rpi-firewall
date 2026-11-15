"""Test remote syslog enable/disable using real services in a disposable container."""
import os
from pathlib import Path
import re
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONF = Path('/etc/rsyslog.d/60-remote.conf')


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT)


def main():
    if os.environ.get('RPI_FIREWALL_DISPOSABLE_TEST') != '1' or not Path('/run/systemd/system').exists():
        raise SystemExit('Requires an explicitly disposable systemd container')
    with tempfile.TemporaryDirectory() as tmp:
        play = Path(tmp) / 'play.yml'

        def apply(variables):
            play.write_text(yaml.safe_dump([{'hosts': 'all', 'gather_facts': False,
                'vars': variables, 'tasks': [{'ansible.builtin.include_role':
                {'name': 'firewall', 'tasks_from': 'rsyslog'}}]}]))
            output = subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(play)],
                env={**os.environ, 'ANSIBLE_ROLES_PATH': str(ROOT / 'roles')},
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            print(output.stdout, flush=True)
            assert output.returncode == 0, output.stdout
            return output.stdout

        command('systemctl', 'stop', 'rsyslog')
        command('dpkg', '--remove', 'rsyslog')
        command('systemctl', 'daemon-reload')
        CONF.parent.mkdir(exist_ok=True)
        CONF.write_text('*.* @@192.0.2.9\n')
        apply({})
        assert not CONF.exists()
        assert re.search(r'changed=0\s', apply({}))
        # apt reinstalls from the image's package cache without WAN access.
        apply({'rsyslog_tcp_server': '127.0.0.1:5514'})
        assert '@@127.0.0.1:5514' in CONF.read_text()
        command('systemctl', 'is-active', 'rsyslog')
        assert re.search(r'changed=0\s', apply({'rsyslog_tcp_server': '127.0.0.1:5514'}))
        apply({'rsyslog_tcp_server': '', 'rsyslog_udp_server': ''})
        assert not CONF.exists()
        assert subprocess.run(['systemctl', 'is-active', '--quiet', 'rsyslog']).returncode != 0
        assert subprocess.run(['systemctl', 'is-enabled', '--quiet', 'rsyslog']).returncode != 0
        assert re.search(r'changed=0\s', apply({}))
    print('PASS: absent rsyslog, remote enable, disable, and idempotence')


if __name__ == '__main__':
    main()
