#!/usr/bin/env python3
"""Minimal QEMU guest agent helper."""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import sys
import time


class QgaError(RuntimeError):
    pass


class QgaClient:
    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path

    def _request(self, payload: dict) -> dict:
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(self.socket_path)
        except OSError as exc:
            raise QgaError(f"Unable to connect to {self.socket_path}: {exc}") from exc

        with sock, sock.makefile("rwb") as stream:
            stream.write((json.dumps(payload) + "\n").encode())
            stream.flush()
            response = stream.readline()
            if not response:
                raise QgaError("No response from guest agent")
            return json.loads(response.decode())

    def exec(self, command: str, timeout: int) -> dict:
        payload = {
            "execute": "guest-exec",
            "arguments": {
                "path": "/bin/sh",
                "arg": ["-c", command],
                "capture-output": True,
            },
        }
        response = self._request(payload)
        pid = response.get("return", {}).get("pid")
        if pid is None:
            raise QgaError(f"Missing pid in response: {response}")

        start = time.time()
        while True:
            status = self._request(
                {"execute": "guest-exec-status", "arguments": {"pid": pid}}
            ).get("return", {})
            if status.get("exited"):
                return status
            if time.time() - start > timeout:
                raise QgaError("guest-exec timed out")
            time.sleep(0.2)

    def push(self, src: str, dest: str) -> None:
        if not os.path.isfile(src):
            raise QgaError(f"Source file does not exist: {src}")

        handle = self._request(
            {
                "execute": "guest-file-open",
                "arguments": {"path": dest, "mode": "wb"},
            }
        ).get("return")
        if handle is None:
            raise QgaError("Failed to open remote file")

        try:
            with open(src, "rb") as fh:
                while True:
                    chunk = fh.read(65536)
                    if not chunk:
                        break
                    encoded = base64.b64encode(chunk).decode()
                    result = self._request(
                        {
                            "execute": "guest-file-write",
                            "arguments": {"handle": handle, "buf-b64": encoded},
                        }
                    ).get("return", {})
                    if result.get("count", 0) != len(chunk):
                        raise QgaError("Incomplete write to guest")
        finally:
            self._request(
                {"execute": "guest-file-close", "arguments": {"handle": handle}}
            )


def run_exec(client: QgaClient, command: str, timeout: int) -> int:
    status = client.exec(command, timeout)
    out_data = status.get("out-data")
    err_data = status.get("err-data")
    if out_data:
        sys.stdout.write(base64.b64decode(out_data).decode(errors="ignore"))
    if err_data:
        sys.stderr.write(base64.b64decode(err_data).decode(errors="ignore"))
    return int(status.get("exitcode", 1))


def main() -> int:
    parser = argparse.ArgumentParser(description="QEMU guest agent helper")
    parser.add_argument("--socket", required=True, help="Path to QGA socket")

    subparsers = parser.add_subparsers(dest="command", required=True)

    exec_parser = subparsers.add_parser("exec", help="Execute a command")
    exec_parser.add_argument("--timeout", type=int, default=300)
    exec_parser.add_argument("cmd", nargs=argparse.REMAINDER)

    push_parser = subparsers.add_parser("push", help="Push a file")
    push_parser.add_argument("--src", required=True)
    push_parser.add_argument("--dest", required=True)

    args = parser.parse_args()
    client = QgaClient(args.socket)

    if args.command == "exec":
        cmd = args.cmd
        if cmd and cmd[0] == "--":
            cmd = cmd[1:]
        if not cmd:
            raise QgaError("No command provided")
        command = " ".join(cmd)
        return run_exec(client, command, args.timeout)

    if args.command == "push":
        client.push(args.src, args.dest)
        return 0

    raise QgaError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except QgaError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
