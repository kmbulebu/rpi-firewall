"""Pseudo-terminal ONT for testing serial dialogue without physical hardware."""
import os
import pty
import re
import select
import threading
import time


class OntEmulator:
    def __init__(self):
        self.master, slave = pty.openpty()
        self.path = os.ttyname(slave)
        os.close(slave)
        self.commands = []
        self.stop = threading.Event()
        self.boot = threading.Event()
        self.boot_sent = threading.Event()
        self.ready = threading.Event()
        self.mode = 'user'
        self.bad_getter = False
        self.worker = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.worker.start()

    def write(self, data):
        os.write(self.master, data)

    def run(self):
        pending = b''
        while not self.stop.is_set():
            if self.boot.is_set():
                self.mode = 'boot'
                self.write(b'\r\nU-Boot 2016.07\r\nHit any key to stop autoboot\r\n')
                self.boot.clear()
                self.boot_sent.set()
            if self.ready.is_set():
                self.mode = 'user'
                self.write(b'OMCI daemon initialized\r\nUser: ')
                self.ready.clear()
            if not select.select([self.master], [], [], .1)[0]:
                continue
            try:
                data = os.read(self.master, 4096)
            except OSError:
                time.sleep(.05)
                continue
            pending += data
            while b'\r' in pending:
                command, pending = pending.split(b'\r', 1)
                command = command.decode().replace('\x15', '')
                self.commands.append(command)
                if self.mode == 'user' and command == '':
                    reply = 'User: '
                elif self.mode == 'user' and command == 'test':
                    self.mode = 'password'; reply = 'Password: '
                elif self.mode == 'password' and command == 'default':
                    self.mode = 'vendor'; reply = '(WAGD20)> '
                elif self.mode == 'vendor' and command == 'lsh':
                    self.mode = 'shell'; reply = 'root@ont:/usr/bin# '
                elif self.mode == 'shell':
                    marker = re.search(r'WAG_[0-9a-f]+', command)
                    if not marker:
                        reply = 'root@ont:/usr/bin# '
                    else:
                        if self.bad_getter:
                            body = 'errorcode=-1'
                        elif 'ploam_state_get' in command:
                            body = 'errorcode=0 current=5 previous=4 time_prev=1'
                        elif 'gpon_status_get' in command:
                            body = 'errorcode=0 ploam_state=5 auth_status=1 pon_id="0 1 2 3 4 5 6"'
                        elif 'optic_status_get' in command:
                            body = 'errorcode=0 rx_los=0 rx_power=-1800 tx_power=100'
                        elif 'cat /proc/uptime' in command:
                            body = '1234.00 1111.00'
                        else:
                            body = 'errorcode=-1'
                        marker = marker.group()
                        # Include echoed command, CRLF, and a shell prompt as on hardware.
                        reply = command + '\r\n' + marker + '_BEGIN\r\n' + body + '\r\n' + marker + '_END\r\nroot@ont:/usr/bin# '
                else:
                    reply = ''
                self.write(reply.encode())

    def close(self):
        self.stop.set()
        self.worker.join(timeout=2)
        os.close(self.master)
