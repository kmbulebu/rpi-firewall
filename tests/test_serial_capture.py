"""Exercise capture against real pseudo terminals without touching the ONT."""
import errno
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
    def test_absent_attach_text_capture_exclusive_and_reconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            device = directory / 'adapter'
            log = directory / 'stdout.log'
            output = log.open('wb')
            process = subprocess.Popen(
                [sys.executable, str(SCRIPT), '--device', str(device)],
                stdout=output, stderr=output)
            masters = []
            try:
                def records():
                    if not log.exists():
                        return []
                    return log.read_text()

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

                wait_for(lambda rows: 'Serial capture waiting:' in rows)
                master, path = attach()
                wait_for(lambda rows: 'Serial capture connected' in rows)
                # No login, newline, or bootloader-interrupting bytes are transmitted.
                self.assertFalse(select.select([master], [], [], 0.1)[0])
                with self.assertRaises(OSError) as failure:
                    os.open(path, os.O_RDONLY | os.O_NOCTTY)
                self.assertEqual(failure.exception.errno, errno.EBUSY)
                os.write(master, b'boot\x00\xff\r\nUser: ')
                # Complete lines appear immediately; a partial prompt after idle.
                wait_for(lambda text: 'boot\\x00\ufffd\n' in text)
                wait_for(lambda text: 'User: \n' in text)
                # A UTF-8 character split across serial reads remains readable.
                os.write(master, b'\xe2')
                time.sleep(0.1)
                os.write(master, b'\x82\xac\n')
                wait_for(lambda text: '\u20ac\n' in text)
                os.close(master)
                masters.remove(master)
                master, _ = attach()
                wait_for(lambda rows: rows.count('Serial capture connected') == 2)
                os.write(master, b'new boot')
                wait_for(lambda rows: 'new boot\n' in rows)
            finally:
                process.terminate()
                process.wait(timeout=5)
                output.close()
                for master in masters:
                    os.close(master)


if __name__ == '__main__':
    unittest.main()
