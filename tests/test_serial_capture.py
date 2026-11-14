"""Exercise capture against real pseudo terminals without touching the ONT."""
import base64
import errno
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'roles/firewall/files/serial/capture.py'


class SerialCaptureTests(unittest.TestCase):
    def test_absent_attach_binary_capture_exclusive_and_reconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            device = directory / 'adapter'
            log = directory / 'serial.jsonl'
            process = subprocess.Popen(
                [sys.executable, str(SCRIPT), '--device', str(device), '--log', str(log)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            masters = []
            try:
                def records():
                    if not log.exists():
                        return []
                    return [json.loads(line) for line in log.read_text().splitlines()]

                def wait_for(predicate):
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        self.assertIsNone(process.poll(), 'capture process exited')
                        if predicate(records()):
                            return
                        time.sleep(0.03)
                    self.fail('timed out waiting for serial capture')

                def attach():
                    master, slave = pty.openpty()
                    masters.append(master)
                    path = os.ttyname(slave)
                    device.unlink(missing_ok=True)
                    device.symlink_to(path)
                    os.close(slave)
                    return master, path

                wait_for(lambda rows: any(r['event'] == 'unavailable' for r in rows))
                master, path = attach()
                wait_for(lambda rows: any(r['event'] == 'connected' for r in rows))
                # No login, newline, or bootloader-interrupting bytes are transmitted.
                self.assertFalse(select.select([master], [], [], 0.1)[0])
                with self.assertRaises(OSError) as failure:
                    os.open(path, os.O_RDONLY | os.O_NOCTTY)
                self.assertEqual(failure.exception.errno, errno.EBUSY)
                payload = b'boot\x00\xff\r\nUser: '
                os.write(master, payload)
                wait_for(lambda rows: any(r['event'] == 'rx' for r in rows))
                received = b''.join(base64.b64decode(r['data_base64'])
                                    for r in records() if r['event'] == 'rx')
                self.assertEqual(received, payload)
                self.assertEqual(log.stat().st_mode & 0o777, 0o600)
                os.close(master)
                masters.remove(master)
                master, _ = attach()
                wait_for(lambda rows: sum(r['event'] == 'connected' for r in rows) == 2)
                os.write(master, b'new boot')
                wait_for(lambda rows: any(r.get('text') == 'new boot' for r in rows))
            finally:
                process.terminate()
                process.wait(timeout=5)
                for master in masters:
                    os.close(master)


if __name__ == '__main__':
    unittest.main()
