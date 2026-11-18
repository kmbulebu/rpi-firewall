"""Exercise the receiver and cache reader without touching the physical ONT."""
import contextlib
import errno
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from wag_serial_fixture import OntEmulator

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'roles/firewall/files/serial/capture.py'
MOTD = ROOT / 'roles/firewall/files/serial/motd.py'
spec = importlib.util.spec_from_file_location('serial_capture', SCRIPT)
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)


class SerialCaptureTests(unittest.TestCase):
    def test_status_queries_reboot_guard_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            log = directory / 'console.log'
            status = directory / 'status.json'
            device = directory / 'adapter'
            output = log.open('wb')
            process = subprocess.Popen([sys.executable, str(SCRIPT), '--device', str(device),
                                        '--status', str(status), '--interval', '10'],
                                       stdout=output, stderr=output)
            emulator = OntEmulator()
            def wait_for(predicate, timeout=10):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    self.assertIsNone(process.poll(), log.read_text())
                    if predicate():
                        return
                    time.sleep(.03)
                self.fail(log.read_text())
            def state():
                return json.loads(status.read_text()) if status.exists() else {}
            try:
                wait_for(lambda: 'WAG status unavailable:' in log.read_text())
                device.symlink_to(emulator.path)
                wait_for(lambda: 'Serial capture connected' in log.read_text())
                emulator.start()
                emulator.ready.set()
                wait_for(lambda: state().get('health') == 'ok')
                self.assertEqual(state()['ploam']['current'], 5)
                self.assertEqual(state()['optics']['rx_los'], 0)
                self.assertEqual(state()['uptime_seconds'], 1234)
                self.assertEqual(status.stat().st_mode & 0o777, 0o644)
                with self.assertRaises(OSError) as failure:
                    os.open(emulator.path, os.O_RDONLY | os.O_NOCTTY)
                self.assertEqual(failure.exception.errno, errno.EBUSY)
                # Spontaneous firmware messages can obscure a previously seen prompt.
                sampled = state()['last_success_monotonic']
                emulator.write(b'\r\nunsolicited firmware message\r\n')
                wait_for(lambda: state().get('last_success_monotonic', 0) > sampled, timeout=13)
                emulator.boot.set()
                self.assertTrue(emulator.boot_sent.wait(3))
                count = len(emulator.commands)
                wait_for(lambda: state().get('health') == 'stale')
                self.assertEqual(state()['ploam']['current'], 5)
                time.sleep(.2)
                self.assertEqual(len(emulator.commands), count)
                emulator.ready.set()
                wait_for(lambda: state().get('health') == 'ok')
                self.assertGreater(emulator.commands.count('test'), 1)
                # Only login/navigation and the four allowlisted queries were sent.
                for command in emulator.commands:
                    self.assertTrue(command in ('', 'test', 'default', 'lsh') or
                                    any(getter in command for getter in (
                                        'ploam_state_get', 'gpon_status_get',
                                        'optic_status_get', 'cat /proc/uptime')), command)
            finally:
                process.terminate()
                process.wait(timeout=5)
                output.close()
                # SIGTERM releases exclusivity even while the PTY master stays open.
                if device.exists():
                    fd = os.open(emulator.path, os.O_RDONLY | os.O_NOCTTY)
                    os.close(fd)
                if emulator.worker.ident is not None:
                    emulator.close()
                else:
                    os.close(emulator.master)

    def test_boot_and_ready_markers_split_across_reads(self):
        console = receiver.Console(123)
        with patch('select.select', return_value=([123], [], [])), \
                patch('os.read', side_effect=[b'U-Bo', b'ot 2016',
                                             b'\r\nOMCI daemon initi', b'alized\n']), \
                contextlib.redirect_stdout(io.StringIO()):
            console.pump()
            console.pump()
            self.assertTrue(console.booting)
            console.pump()
            self.assertTrue(console.booting)
            console.pump()
            self.assertFalse(console.booting)

    def test_silent_start_waits_without_writing(self):
        console = receiver.Console(123)
        now = [console.last_rx]

        def advance(_timeout):
            now[0] += 1

        with patch.object(receiver.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(console, 'pump', side_effect=advance), \
                patch.object(console, 'send') as send:
            with self.assertRaises(TimeoutError):
                console.shell()
            send.assert_not_called()

    def test_bootloader_prompt_without_banner_revokes_shell_access(self):
        console = receiver.Console(123)
        chunks = [b'root@ont:/usr/bin# ', b'\r\nPRX', b'300 #']
        with patch('select.select', side_effect=[([123], [], [])] * 3 + [([], [], [])]), \
                patch('os.read', side_effect=chunks), \
                patch('os.write') as write, contextlib.redirect_stdout(io.StringIO()):
            console.pump()
            self.assertTrue(console.management_ready)
            console.shell_ready = True
            console.pump()
            console.pump()
            self.assertTrue(console.booting)
            self.assertFalse(console.shell_ready)
            with self.assertRaisesRegex(ValueError, 'refusing console writes'):
                console.send('\r')
            write.assert_not_called()

    def test_old_ready_marker_cannot_override_later_boot_in_same_read(self):
        console = receiver.Console(123)
        data = b'OMCI daemon initialized\r\nroot@ont:/usr/bin# \r\nSystem reset (12)\r\n'
        with patch('select.select', return_value=([123], [], [])), \
                patch('os.read', return_value=data), contextlib.redirect_stdout(io.StringIO()):
            console.pump()
        self.assertTrue(console.booting)
        self.assertFalse(console.wake_pending)
        self.assertFalse(console.management_ready)
        self.assertIsNone(receiver.PROMPT.search(console.tail))

    def test_getter_parser_preserves_quotes_and_rejects_failures(self):
        self.assertEqual(receiver.pairs('errorcode=0 pon_id="0 1 2" current=11'),
                         {'errorcode': 0, 'pon_id': '0 1 2', 'current': 11})
        for bad in ['errorcode=-1 current=5', 'current=5']:
            with self.assertRaises(ValueError):
                receiver.pairs(bad)

    def test_motd_fresh_stale_first_poll_and_invalid_cache(self):
        state = {'health': 'ok', 'last_success_monotonic': 988, 'ploam': {'current': 5},
                 'optics': {'rx_los': 0}, 'uptime_seconds': 8070}
        def display(document):
            output = io.StringIO()
            with patch('pathlib.Path.read_text', return_value=json.dumps(document)), \
                    patch('time.monotonic', return_value=1000), contextlib.redirect_stdout(output):
                runpy.run_path(str(MOTD), run_name='__main__')
            rendered = output.getvalue()
            self.assertTrue(rendered.startswith('\n PON status (WAG-D20):\n'))
            self.assertTrue(all(line.startswith('  ') for line in rendered.splitlines()[2:]))
            return rendered
        self.assertIn('PON state: 5 (raw)', display(state))
        self.assertIn('  Last check: 12s ago\n', display(state))
        state['health'] = 'stale'
        self.assertIn('STALE', display(state))
        state.update(health='ok', last_success_monotonic=800)
        self.assertIn('STALE', display(state))
        self.assertIn('first successful poll', display({}))
        self.assertIn('Status: unavailable', display({'last_success_monotonic': 999}))


if __name__ == '__main__':
    unittest.main()
