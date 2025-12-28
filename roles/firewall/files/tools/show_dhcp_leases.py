#!/usr/bin/env python3
"""
Show DHCP leases from systemd-networkd via `busctl`.

This script reads JSON from stdin if piped; otherwise it resolves the
link id from an interface (default `lan0`) and invokes `busctl` itself.

It is intentionally a single-file, pure-Python replacement for the
previous bash+heredoc approach.
"""

import sys
import json
import time
import subprocess
import argparse
import shutil
import socket


def read_stdin_if_any():
    try:
        if not sys.stdin:
            return None
        # If stdin is not a tty, assume piped input
        if not sys.stdin.isatty():
            data = sys.stdin.read()
            return data if data else None
    except Exception:
        return None
    return None


def run_busctl(link_id):
    cmd = [
        "busctl",
        "-j",
        "get-property",
        "org.freedesktop.network1",
        f"/org/freedesktop/network1/link/{link_id}",
        "org.freedesktop.network1.DHCPServer",
        "Leases",
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return p.stdout
    except FileNotFoundError:
        raise RuntimeError("`busctl` not found on PATH")
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip() if e.stderr else str(e)
        raise RuntimeError(f"busctl failed: {stderr}")


def get_link_id_from_iface(iface):
    """Return the kernel link index for `iface`.

    Prefer Python's `socket.if_nametoindex()` (no external commands). If
    that's unavailable, fall back to reading
    `/sys/class/net/<iface>/ifindex`. Only as a last resort call the
    `ip` command.
    """
    # Preferred: socket.if_nametoindex
    try:
        idx = socket.if_nametoindex(iface)
        return str(idx)
    except Exception:
        pass

    # Fallback: read /sys/class/net/<iface>/ifindex
    try:
        with open(f"/sys/class/net/{iface}/ifindex", "r") as f:
            return f.read().strip()
    except Exception:
        pass

    # Last resort: call `ip` if present
    ip_path = shutil.which("ip")
    if not ip_path:
        raise RuntimeError("cannot determine link id: neither socket.if_nametoindex nor /sys/class/net are available, and `ip` is missing")
    try:
        p = subprocess.run([ip_path, "--oneline", "link", "show", "dev", iface], capture_output=True, text=True, check=True)
        out = p.stdout.strip()
        if not out:
            raise RuntimeError(f"no output from `ip` for iface {iface}")
        link_id = out.split(":", 1)[0]
        return link_id
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip() if e.stderr else str(e)
        raise RuntimeError(f"`ip` command failed: {stderr}")


def parse_and_print(data):
    try:
        doc = json.loads(data)
    except Exception as e:
        print("Failed to parse JSON from busctl:", e, file=sys.stderr)
        print("First 200 chars of data:", data[:200], file=sys.stderr)
        sys.exit(1)

    leases = doc.get("data", [])
    if not leases:
        print("No active DHCP leases.")
        return

    try:
        now_boot = time.clock_gettime(time.CLOCK_BOOTTIME)
    except Exception:
        now_boot = time.time()

    # No header row per user request

    for entry in leases:
        try:
            family, client_id, addr, gw, hwaddr, exp = entry
        except Exception:
            print("Skipping unknown lease entry:", entry, file=sys.stderr)
            continue

        if family == 2 and isinstance(addr, list) and len(addr) >= 4:
            ip = ".".join(str(b) for b in addr[:4])
        else:
            ip = "?"

        if isinstance(hwaddr, list) and len(hwaddr) >= 6:
            mac = ":".join(f"{b:02x}" for b in hwaddr[:6])
        else:
            mac = "?"

        try:
            exp_sec = exp / 1_000_000.0
            remaining = exp_sec - now_boot
            rem_str = f"{int(remaining)}s" if remaining > 0 else "expired"
        except Exception:
            rem_str = "?"

        print(f"{ip:15} {mac:17} {rem_str}")


def main():
    parser = argparse.ArgumentParser(description="Show DHCP leases from systemd-networkd")
    parser.add_argument("--iface", default="lan0", help="interface used by DHCP server (default: lan0)")
    parser.add_argument("--link-id", help="explicit link id to use instead of resolving from iface")
    parser.add_argument("--debug", action="store_true", help="print debug info to stderr")
    args = parser.parse_args()

    data = read_stdin_if_any()
    if data:
        if args.debug:
            print("Using piped stdin as input", file=sys.stderr)
        parse_and_print(data)
        return

    link_id = args.link_id
    if not link_id:
        try:
            link_id = get_link_id_from_iface(args.iface)
        except Exception as e:
            print(f"Failed to determine link id: {e}", file=sys.stderr)
            sys.exit(1)

    try:
        out = run_busctl(link_id)
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(1)

    parse_and_print(out)


if __name__ == '__main__':
    main()
