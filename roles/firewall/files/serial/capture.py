#!/usr/bin/python3
"""Receive-only serial capture with bounded, byte-preserving JSONL logs."""
import argparse
import base64
from datetime import datetime, timezone
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
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


def record(logger, event, **fields):
    logger.info(json.dumps({
        "time": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }, ensure_ascii=True))


def capture(fd, logger):
    while True:
        ready, _, _ = select.select([fd], [], [], 1)
        if not ready:
            continue
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            continue
        if not data:
            raise OSError("serial device disconnected")
        record(logger, "rx", data_base64=base64.b64encode(data).decode("ascii"),
               text=data.decode("utf-8", errors="replace"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True)
    parser.add_argument("--log", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    logger = logging.getLogger("serial-capture")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(args.log, maxBytes=10 * 1024 * 1024,
                                  backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    last_error = None
    while True:
        fd = None
        try:
            fd = open_serial(args.device)
            record(logger, "connected", device=args.device)
            print("Serial capture connected", file=sys.stderr, flush=True)
            last_error = None
            capture(fd, logger)
        except (OSError, termios.error) as error:
            message = str(error)
            if message != last_error:
                record(logger, "unavailable", error=message)
                print(f"Serial capture waiting: {message}", file=sys.stderr, flush=True)
                last_error = message
        finally:
            if fd is not None:
                os.close(fd)
        time.sleep(1)


if __name__ == "__main__":
    main()
