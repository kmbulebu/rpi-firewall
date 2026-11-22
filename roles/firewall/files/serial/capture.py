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
PROMPT = re.compile(r'(?:^|[\r\n])(User:|Password:|\(WAGD20\)[^\r\n]*>|root@[^\r\n]*#)[ \t\r\n]*$')
BOOT = re.compile(r'ROM VER:|U-Boot |Hit any key|Press.*failsafe|Linux version |'
                  r'reboot ONT now|System reset \(|(?:^|[\r\n])PRX300[ \t]+#')
BOOTLOADER_PROMPT = re.compile(r'(?:^|[\r\n])PRX300 #(?:[ \t\r\n]*)\Z')
READY = re.compile(r'OMCI daemon initialized')
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
    def __init__(self, fd, auto_recovery=False, boot_attempted=False):
        self.fd = fd
        self.auto_recovery = auto_recovery
        self.boot_attempted = boot_attempted
        self.reported_boot_failure = False
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.pending = ''
        self.tail = ''
        self.last_rx = time.monotonic()
        self.last_tx = self.last_rx
        self.booting = False
        self.boot_generation = 0
        self.management_ready = False
        self.wake_pending = False
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
        clean = ANSI.sub('', text)
        self.tail = (self.tail + clean)[-65536:]
        scan = self.scan_tail + clean
        offset = len(self.scan_tail)
        boot = list(match for match in BOOT.finditer(scan) if match.end() > offset)
        if boot:
            # Discard prompts preceding the latest boot marker, including in this read.
            offset = boot[-1].end()
            self.tail = scan[boot[-1].start():] if BOOTLOADER_PROMPT.search(scan) else scan[offset:]
            self.booting = True
            self.boot_generation += 1
            self.management_ready = False
            self.wake_pending = False
            self.shell_ready = False
        if any(match.end() > offset for match in READY.finditer(scan)):
            self.wake_pending = True
            self.management_ready = True
        if PROMPT.search(self.tail):
            self.wake_pending = False
            self.management_ready = True
        if self.management_ready:
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

    def probe_allowed(self):
        return (self.auto_recovery and not self.booting and not self.boot_attempted
                and time.monotonic() - max(self.last_rx, self.last_tx) >= 10)

    def send(self, text, *, purpose='management'):
        generation = self.boot_generation
        while select.select([self.fd], [], [], 0)[0]:
            self.pump(0)
        if self.boot_generation != generation:
            raise ValueError('console state changed; refusing console writes')
        if purpose == 'boot':
            if (not self.auto_recovery or not self.booting or self.boot_attempted
                    or not BOOTLOADER_PROMPT.search(self.tail)
                    or time.monotonic() - self.last_rx < 1):
                raise ValueError('bootloader recovery conditions changed')
            self.boot_attempted = True
            print('WAG recovery: sending run bootcmd at PRX300 prompt', file=sys.stderr, flush=True)
        elif purpose == 'probe':
            if not self.probe_allowed():
                raise ValueError('console no longer quiet or boot detected')
        elif not self.management_ready:
            raise ValueError('management console not confirmed; refusing console writes')
        self.last_tx = time.monotonic()
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
        # Automatic recovery may probe an unknown console. A detected boot
        # suppresses probes, with one explicit exception for the U-Boot prompt.
        end = time.monotonic() + 120
        while time.monotonic() < end:
            if PROMPT.search(self.tail):
                break
            if (self.auto_recovery and BOOTLOADER_PROMPT.search(self.tail)
                    and time.monotonic() - self.last_rx >= 1):
                if not self.boot_attempted:
                    self.send('run bootcmd\r', purpose='boot')
                elif not self.reported_boot_failure:
                    print('WAG recovery: bootloader prompt returned; stopping recovery writes',
                          file=sys.stderr, flush=True)
                    self.reported_boot_failure = True
            if self.wake_pending and time.monotonic() - self.last_rx >= 10:
                self.wake_pending = False
                self.send('\r')
                break
            if self.probe_allowed():
                self.send('\r', purpose='probe')
                break
            self.pump(1)
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
    parser.add_argument('--interval', type=int, default=60)
    parser.add_argument('--no-auto-recovery', dest='auto_recovery', action='store_false')
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
    boot_attempted = False
    while True:
        fd = console = None
        try:
            fd = open_serial(args.device)
            console = Console(fd, args.auto_recovery, boot_attempted)
            print('Serial capture connected', file=sys.stderr, flush=True)
            while True:
                try:
                    if not console.shell_ready:
                        console.shell()
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
                    console.boot_attempted = False
                    console.reported_boot_failure = False
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
                    # Keep receiving through reboot or a silent console.
                    console.shell_ready = False
                    if not console.booting:
                        until = time.monotonic() + 5
                        while not console.booting and time.monotonic() < until:
                            console.pump(min(1, max(0, until - time.monotonic())))
        except (OSError, termios.error) as error:
            state.update(health='stale', last_attempt=time.time(), error=str(error))
            snapshot(path, state)
            if str(error) != last_error:
                print('WAG status unavailable: ' + str(error), file=sys.stderr, flush=True)
                last_error = str(error)
        finally:
            if console is not None:
                boot_attempted = console.boot_attempted
            if fd is not None:
                try:
                    fcntl.ioctl(fd, termios.TIOCNXCL)
                except OSError:
                    pass
                os.close(fd)
        time.sleep(5)


if __name__ == '__main__':
    main()
