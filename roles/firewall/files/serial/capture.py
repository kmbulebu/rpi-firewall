#!/usr/bin/python3
"""Own the ONT console, log to journald and cache read-only status observations."""
import argparse
import codecs
import fcntl
import json
import os
from pathlib import Path
import re
import select
import signal
import sys
import termios
import time
import uuid

ANSI = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|[78])')
PROMPT = re.compile(r'(User:\s*|Password:\s*|\(WAGD20\)[^\r\n]*>\s*|root@[^\r\n]*#\s*)$')
BOOT = re.compile(r'ROM VER:|U-Boot |Hit any key|Press.*failsafe|Linux version |reboot ONT now|System reset \(')
PAIRS = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=("[^"]*"|\S+)')


def open_serial(path):
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        fcntl.ioctl(fd, termios.TIOCEXCL)
        a = termios.tcgetattr(fd)
        a[0] = a[1] = a[3] = 0
        a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        a[4] = a[5] = termios.B115200
        a[6][termios.VMIN] = a[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, a)
        return fd
    except BaseException:
        try:
            fcntl.ioctl(fd, termios.TIOCNXCL)
        except OSError:
            pass
        os.close(fd)
        raise


def pairs(text):
    result = {}
    for key, value in PAIRS.findall(text):
        if value.startswith('"'):
            result[key] = value[1:-1]
        else:
            try:
                result[key] = int(value, 0)
            except ValueError:
                result[key] = value
    if result.get('errorcode') != 0:
        raise ValueError('PON getter did not return errorcode=0')
    return result


class Console:
    def __init__(self, fd):
        self.fd = fd
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.pending = ''
        self.tail = ''
        self.last_rx = time.monotonic()
        self.booting = False
        self.scan_tail = ''
        self.shell_ready = False

    def pump(self, timeout=1):
        if not select.select([self.fd], [], [], timeout)[0]:
            if self.pending:
                print(self.pending.replace('\x00', '\\x00'), flush=True)
                self.pending = ''
            return ''
        data = os.read(self.fd, 4096)
        if not data:
            raise OSError('serial disconnected')
        text = self.decoder.decode(data)
        self.last_rx = time.monotonic()
        self.tail = (self.tail + ANSI.sub('', text))[-65536:]
        scan = self.scan_tail + text
        offset = len(self.scan_tail)
        if any(match.end() > offset for match in BOOT.finditer(scan)):
            self.booting = True
            self.shell_ready = False
        ready = any(match.end() > offset for match in re.finditer('OMCI daemon initialized', scan))
        if ready or PROMPT.search(self.tail):
            self.booting = False
        self.scan_tail = scan[-256:]
        self.pending += text.replace('\r', '\n')
        lines = self.pending.split('\n')
        self.pending = lines.pop()
        for line in lines:
            if line:
                print(line.replace('\x00', '\\x00'), flush=True)
        if len(self.pending) >= 4096:
            print(self.pending.replace('\x00', '\\x00'), flush=True)
            self.pending = ''
        return text

    def send(self, text):
        if self.booting:
            raise ValueError('boot detected; refusing console writes')
        data = text.encode()
        while data:
            _, writable, _ = select.select([], [self.fd], [], 2)
            if not writable:
                raise TimeoutError('serial write timeout')
            data = data[os.write(self.fd, data):]
        self.tail = ''

    def prompt(self, timeout=15):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            match = PROMPT.search(self.tail)
            if match:
                prompt = match.group(1).strip()
                self.shell_ready = prompt.startswith('root@')
                return prompt
            self.pump(min(1, max(0, end - time.monotonic())))
            if self.booting:
                raise ValueError('ONT reboot detected')
        raise TimeoutError('console prompt timeout')

    def shell(self):
        # Observe first; never send input into a detected boot/autoboot window.
        end = time.monotonic() + 120
        while time.monotonic() < end:
            self.pump(1)
            if PROMPT.search(self.tail):
                break
            if not self.booting and time.monotonic() - self.last_rx >= 10:
                self.send('\r')
                break
        else:
            raise TimeoutError('no safe console readiness observed')
        for _ in range(6):
            prompt = self.prompt()
            if prompt == 'User:':
                self.send('test\r')
            elif prompt == 'Password:':
                self.send('default\r')
            elif prompt.startswith('(WAGD20)'):
                self.send('\x15lsh\r')
            elif prompt.startswith('root@'):
                return
        raise ValueError('could not establish ONT shell')

    def query(self, command):
        # Drain unsolicited messages and detect boot before any input. Refresh
        # the known shell prompt because asynchronous logs can obscure it.
        while select.select([self.fd], [], [], 0)[0]:
            self.pump(0)
        if not self.shell_ready:
            raise ValueError('read-only query requires confirmed shell context')
        self.send('\x15\r')
        if not self.prompt().startswith('root@'):
            raise ValueError('read-only query requires confirmed shell prompt')
        marker = 'WAG_' + uuid.uuid4().hex
        self.send("printf '\\n" + marker + "_BEGIN\\n'; " + command +
                  "; printf '\\n" + marker + "_END\\n'\r")
        self.prompt(15)
        match = re.search(r'(?:^|\n)' + marker + r'_BEGIN\r?\n(.*?)\r?\n' +
                          marker + r'_END(?:\r?\n|$)', self.tail, re.S)
        if not match:
            raise ValueError('incomplete marked query response')
        return match.group(1)


def snapshot(path, document):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as output:
        json.dump(document, output)
        output.write('\n')
    temporary.chmod(0o644)
    os.replace(temporary, path)


def terminate(_signum, _frame):
    raise SystemExit(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', required=True)
    parser.add_argument('--status', default='/run/wag-d20/status.json')
    parser.add_argument('--interval', type=int, default=30)
    args = parser.parse_args()
    if args.interval < 10:
        parser.error('poll interval must be at least ten seconds')
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    path = Path(args.status)
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    state = {'schema': 1, 'receiver_boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
             'health': 'starting', 'last_success': None, 'poll_interval': args.interval}
    snapshot(path, state)
    previous = None
    last_error = None
    boot_guard = False
    while True:
        fd = None
        try:
            fd = open_serial(args.device)
            console = Console(fd)
            console.booting = boot_guard
            print('Serial capture connected', file=sys.stderr, flush=True)
            console.shell()
            while True:
                try:
                    ploam = pairs(console.query('/opt/intel/bin/pon ploam_state_get'))
                    pon = pairs(console.query('/opt/intel/bin/pon gpon_status_get'))
                    optics = pairs(console.query('/opt/intel/bin/pon optic_status_get '
                        '/sys/bus/i2c/devices/0-0050/eeprom /sys/bus/i2c/devices/0-0051/eeprom'))
                    raw_uptime = console.query('cat /proc/uptime')
                    match = re.search(r'^\s*(\d+\.\d+)\s+\d+\.\d+\s*$', raw_uptime, re.M)
                    if not match or not isinstance(ploam.get('current'), int) or not isinstance(optics.get('rx_los'), int):
                        raise ValueError('required status fields missing')
                    state.update(health='ok', last_success=time.time(), last_attempt=time.time(),
                                 last_success_monotonic=time.monotonic(), error=None,
                                 ploam=ploam, pon=pon, optics=optics, uptime_seconds=float(match.group(1)))
                    snapshot(path, state)
                    transition = (ploam['current'], optics['rx_los'])
                    if transition != previous or last_error is not None:
                        print('WAG status: PON raw=%s; optical LOS=%s; polling healthy' % transition,
                              file=sys.stderr, flush=True)
                    previous, last_error = transition, None
                    end = time.monotonic() + args.interval
                    while time.monotonic() < end:
                        console.pump(min(1, end - time.monotonic()))
                        if console.booting:
                            raise ValueError('ONT reboot detected')
                except (ValueError, TimeoutError) as error:
                    state.update(health='stale', last_attempt=time.time(), error=str(error))
                    snapshot(path, state)
                    if str(error) != last_error:
                        print('WAG status unavailable: ' + str(error), file=sys.stderr, flush=True)
                        last_error = str(error)
                    # Keep receiving through reboot; do not reopen and lose boot context.
                    console.shell()
                    until = time.monotonic() + 5
                    while time.monotonic() < until:
                        console.pump(min(1, until - time.monotonic()))
        except (OSError, termios.error, ValueError, TimeoutError) as error:
            state.update(health='stale', last_attempt=time.time(), error=str(error))
            snapshot(path, state)
            if str(error) != last_error:
                print('WAG status unavailable: ' + str(error), file=sys.stderr, flush=True)
                last_error = str(error)
        finally:
            if fd is not None:
                boot_guard = console.booting
                try:
                    fcntl.ioctl(fd, termios.TIOCNXCL)
                except OSError:
                    pass
                os.close(fd)
        time.sleep(5)


if __name__ == '__main__':
    main()
