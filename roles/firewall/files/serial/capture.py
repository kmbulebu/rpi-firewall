#!/usr/bin/python3
"""Receive-only serial console output for systemd-journald."""
import argparse
import codecs
import fcntl
import os
import select
import sys
import termios
import time


def open_serial(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        fcntl.ioctl(fd, termios.TIOCEXCL)
        attrs = termios.tcgetattr(fd)
        attrs[0] = 0
        attrs[1] = 0
        # No software/hardware flow control, no hangup on close, 115200 8N1.
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[3] = 0
        attrs[4] = attrs[5] = termios.B115200
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        # Do not flush: bytes already received may contain the start of boot.
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        return fd
    except BaseException:
        os.close(fd)
        raise


def capture(fd):
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    pending = ""

    def emit(text):
        # Journald frames stdout on newline/NUL; keep NULs visible in the text.
        if text:
            print(text.replace("\x00", "\\x00"), flush=True)

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 1)
            if not ready:
                # Prompts often have no newline. Publish after an idle second.
                emit(pending)
                pending = ""
                continue
            try:
                data = os.read(fd, 4096)
            except BlockingIOError:
                continue
            if not data:
                raise OSError("serial device disconnected")
            pending += decoder.decode(data).replace("\r", "\n")
            lines = pending.split("\n")
            pending = lines.pop()
            for line in lines:
                emit(line)
            if len(pending) >= 4096:
                emit(pending)
                pending = ""
    finally:
        emit(pending + decoder.decode(b"", final=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True)
    args = parser.parse_args()
    last_error = None
    while True:
        fd = None
        try:
            fd = open_serial(args.device)
            print("Serial capture connected", file=sys.stderr, flush=True)
            last_error = None
            capture(fd)
        except (OSError, termios.error) as error:
            message = str(error)
            if message != last_error:
                print(f"Serial capture waiting: {message}", file=sys.stderr, flush=True)
                last_error = message
        finally:
            if fd is not None:
                os.close(fd)
        time.sleep(1)


if __name__ == "__main__":
    main()
