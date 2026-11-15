"""Verify opting out preserves an existing host layout in a disposable container."""
import os
from pathlib import Path
import re
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    if os.environ.get('RPI_FIREWALL_DISPOSABLE_TEST') != '1':
        raise SystemExit('Requires an explicitly disposable container')
    # Preexisting configuration must survive, rather than just avoiding new files.
    paths = ['/etc/fstab', '/boot/firmware/config.txt', '/etc/modprobe.d/disable-raspi.conf',
             '/etc/systemd/system.conf.d/watchdog.conf']
    for name in paths:
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# private host configuration fixture\n')
    with tempfile.TemporaryDirectory() as tmp:
        play = Path(tmp) / 'play.yml'
        play.write_text(yaml.safe_dump([{'hosts': 'all', 'gather_facts': False,
            'vars': {'rpi_tuning_enabled': False, 'manage_fstab': False},
            'tasks': [{'ansible.builtin.include_role': {'name': 'firewall', 'tasks_from': 'platform'}}]}]))
        for _ in range(2):
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(play)],
                env={**os.environ, 'ANSIBLE_ROLES_PATH': str(ROOT / 'roles')},
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            print(result.stdout, flush=True)
            assert result.returncode == 0 and re.search(r'changed=0\s', result.stdout), result.stdout
            assert 'RUNNING HANDLER' not in result.stdout
            for name in paths:
                assert Path(name).read_text() == '# private host configuration fixture\n'
    print('PASS: generic host configuration preserved without mount or hardware handlers')


if __name__ == '__main__':
    main()
