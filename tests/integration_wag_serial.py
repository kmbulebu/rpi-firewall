"""Ansible enable/reapply/disable against systemd and an emulated serial ONT."""
import json
import os
from pathlib import Path
import subprocess
import time

from wag_serial_fixture import OntEmulator

ROOT = Path(__file__).resolve().parents[1]


def command(*args):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode:
        raise AssertionError(result.stdout)
    return result.stdout


def main():
    if os.environ.get('RPI_FIREWALL_DISPOSABLE_TEST') != '1' or not Path('/run/systemd/system').exists():
        raise SystemExit('Requires an explicitly disposable container running systemd')
    fixture = Path('/run/wag-serial-test')
    fixture.mkdir(exist_ok=True)
    play = fixture / 'play.yml'
    play.write_text(f'''---
- hosts: localhost
  connection: local
  gather_facts: false
  vars_files:
    - {ROOT}/roles/firewall/defaults/main.yml
  tasks:
    - ansible.builtin.include_role:
        name: {ROOT}/roles/firewall
        tasks_from: wag_d20_serial
''')
    def apply(enabled):
        return command('ansible-playbook', '-i', 'localhost,', str(play), '-e',
                       json.dumps({'wag_d20_serial_enabled': enabled}))
    unit = Path('/etc/systemd/system/wag-d20-serial.service')
    motd = Path('/etc/update-motd.d/62-wag-d20-status')
    status = Path('/run/wag-d20/status.json')
    output = apply(False)
    assert 'changed=0' in output, output
    assert not unit.exists() and not motd.exists() and not status.exists()
    print('PASS: fresh disabled configuration installs nothing', flush=True)
    emulator = OntEmulator()
    device = Path('/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0')
    device.parent.mkdir(parents=True, exist_ok=True)
    device.unlink(missing_ok=True)
    device.symlink_to(emulator.path)
    emulator.start()
    try:
        apply(True)
        emulator.ready.set()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if status.exists() and json.loads(status.read_text())['health'] == 'ok':
                break
            time.sleep(.1)
        else:
            raise AssertionError(command('journalctl', '-u', 'wag-d20-serial.service', '--no-pager'))
        assert 'PON state: 5 (raw)' in command(str(motd))
        assert 'enabled' == command('systemctl', 'is-enabled', 'wag-d20-serial.service').strip()
        print('PASS: enable installs service, polls ONT, exposes cached MOTD', flush=True)
        pid = command('systemctl', 'show', 'wag-d20-serial.service', '--property=MainPID', '--value')
        output = apply(True)
        assert 'changed=0' in output, output
        assert pid == command('systemctl', 'show', 'wag-d20-serial.service', '--property=MainPID', '--value')
        print('PASS: reapplying is idempotent without restarting receiver', flush=True)
        apply(False)
        assert not motd.exists() and not status.exists()
        assert unit.exists()
        assert command('systemctl', 'show', 'wag-d20-serial.service', '--property=ActiveState', '--value').strip() == 'inactive'
        # Open succeeds without root override because the receiver released exclusivity.
        fd = os.open(emulator.path, os.O_RDONLY | os.O_NOCTTY)
        os.close(fd)
        output = apply(False)
        assert 'changed=0' in output, output
        print('PASS: disable releases port, removes MOTD/cache, and is idempotent', flush=True)
    finally:
        subprocess.run(['systemctl', 'stop', 'wag-d20-serial.service'], check=False)
        emulator.close()


if __name__ == '__main__':
    main()
