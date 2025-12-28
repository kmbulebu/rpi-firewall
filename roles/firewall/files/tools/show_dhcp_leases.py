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
import concurrent.futures

# Use dnspython for DNS queries (assumed present)
import dns.resolver
import dns.reversename

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


def parse_leases(data):
    """Parse raw JSON `data` from busctl and return a list of lease dicts.

    Each dict has keys: `ip` (str), `ip_key` (tuple|None), `mac` (str),
    `mac_key` (tuple|None), `remaining` (float|None), and `rem_str` (str).
    """
    try:
        doc = json.loads(data)
    except Exception as e:
        print("Failed to parse JSON from busctl:", e, file=sys.stderr)
        print("First 200 chars of data:", data[:200], file=sys.stderr)
        sys.exit(1)

    leases = doc.get("data", [])
    if not leases:
        return []

    try:
        now_boot = time.clock_gettime(time.CLOCK_BOOTTIME)
    except Exception:
        now_boot = time.time()

    out = []
    for entry in leases:
        try:
            family, client_id, addr, gw, hwaddr, exp = entry
        except Exception:
            # Skip entries we don't understand
            continue

        # IPv4 address
        if family == 2 and isinstance(addr, list) and len(addr) >= 4:
            ip = ".".join(str(b) for b in addr[:4])
            ip_key = tuple(int(b) for b in addr[:4])
        else:
            ip = "?"
            ip_key = None

        # MAC from first 6 bytes of hwaddr
        if isinstance(hwaddr, list) and len(hwaddr) >= 6:
            mac = ":".join(f"{b:02x}" for b in hwaddr[:6])
            mac_key = tuple(int(b) for b in hwaddr[:6])
        else:
            mac = "?"
            mac_key = None

        # Remaining lease time (seconds until expiration)
        try:
            exp_sec = exp / 1_000_000.0
            remaining = exp_sec - now_boot
            rem_str = f"{int(remaining)}s" if remaining > 0 else "expired"
        except Exception:
            remaining = None
            rem_str = "?"

        out.append({
            "ip": ip,
            "ip_key": ip_key,
            "mac": mac,
            "mac_key": mac_key,
            "remaining": remaining,
            "rem_str": rem_str,
        })

    return out


def sort_entries(entries, sort_key, reverse=False):
    """Sort entries in-place and return them.

    sort_key: one of 'ip', 'mac', 'exp'
    reverse: boolean to reverse order
    """
    if sort_key == "ip":
        def keyfn(e):
            return (0, e["ip_key"]) if e["ip_key"] is not None else (1,)
    elif sort_key == "mac":
        def keyfn(e):
            return (0, e["mac_key"]) if e["mac_key"] is not None else (1,)
    else:  # exp
        def keyfn(e):
            # Treat unknown remaining as +inf so they appear last
            return (e["remaining"] if e["remaining"] is not None else float("inf"))

    entries.sort(key=keyfn, reverse=reverse)
    return entries


def print_leases(entries):
    """Print lease entries (no header)."""
    for e in entries:
        print(f"   {e['ip']:15} {e['mac']:17} {e['rem_str']}")


def resolve_hostnames(entries, max_workers=10, per_lookup_timeout=1.0, resolvers=None):
    """Resolve reverse DNS for entries with IPv4 addresses.

    Performs lookups in a ThreadPool to avoid blocking the main thread
    and applies a timeout per lookup. Adds `hostname` key to each entry
    (value is a string or '?').
    """
    # Normalize resolver list: prefer user-provided, else default to both
    if resolvers is None:
        resolvers = ["127.0.0.1", "127.0.0.53"]
    elif isinstance(resolvers, str):
        resolvers = [r.strip() for r in resolvers.split(',') if r.strip()]

    def lookup(ip):
        # Try each configured resolver in order using dnspython. Return the
        # first successful PTR result or '?' on failure.
        rname = dns.reversename.from_address(ip)
        for resolver_addr in resolvers:
            try:
                res = dns.resolver.Resolver(configure=False)
                res.nameservers = [resolver_addr]
                res.timeout = max(0.1, per_lookup_timeout)
                res.lifetime = max(0.1, per_lookup_timeout)
                ans = res.resolve(rname, 'PTR')
                for rr in ans:
                    return rr.to_text().rstrip('.')
            except Exception:
                # try next resolver
                continue
        return '?'

    # Map futures to entries so we can assign results back
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_map = {}
        for e in entries:
            if e.get('ip_key') is None or e.get('ip') == '?':
                e['hostname'] = '?'
                continue
            fut = ex.submit(lookup, e['ip'])
            future_map[fut] = e

        for fut in concurrent.futures.as_completed(future_map):
            e = future_map[fut]
            try:
                name = fut.result(timeout=per_lookup_timeout)
            except Exception:
                name = '?'
            e['hostname'] = name



def main():
    parser = argparse.ArgumentParser(description="Show DHCP leases from systemd-networkd")
    parser.add_argument("--iface", default="lan0", help="interface used by DHCP server (default: lan0)")
    parser.add_argument("--link-id", help="explicit link id to use instead of resolving from iface")
    parser.add_argument("--sort", choices=["ip", "mac", "exp"], default="exp",
                        help="sort output by: ip, mac, or exp (remaining). Default: exp")
    parser.add_argument("--reverse", action="store_true", help="reverse sort order")
    parser.add_argument("--hostnames", action="store_true", help="resolve reverse DNS for IPs and show hostname column")
    parser.add_argument("--debug", action="store_true", help="print debug info to stderr")
    parser.add_argument("--resolvers", default="127.0.0.1,127.0.0.53",
                        help="comma-separated list of DNS resolver IPs to try for PTR lookups (default: 127.0.0.1,127.0.0.53)")
    parser.add_argument("--resolver-timeout", type=float, default=1.0,
                        help="per-resolver lookup timeout in seconds (default: 1.0)")
    parser.add_argument("--limit", type=int,
                        help="maximum number of entries to show (applied after sorting). If set, reverse DNS is only performed on these entries")
    args = parser.parse_args()

    data = read_stdin_if_any()
    if data:
        if args.debug:
            print("Using piped stdin as input", file=sys.stderr)
        entries = parse_leases(data)
        if not entries:
            print("   No active DHCP leases.")
            return
        # sort per args
        entries = sort_entries(entries, args.sort, args.reverse)
        # apply optional limit (after sorting). non-positive values produce no results.
        if args.limit is not None:
            try:
                if args.limit <= 0:
                    entries = []
                else:
                    entries = entries[:args.limit]
            except Exception:
                # ignore invalid limit and continue without limiting
                pass
        if args.hostnames:
            # resolve hostnames (adds 'hostname' key) only for the limited set
            resolve_hostnames(entries, per_lookup_timeout=args.resolver_timeout, resolvers=args.resolvers)
            # print with hostname column
            for e in entries:
                print(f"   {e['ip']:15} {e.get('hostname','?'):30} {e['mac']:17} {e['rem_str']}")
        else:
            print_leases(entries)
        return

    link_id = args.link_id
    if not link_id:
        try:
            link_id = get_link_id_from_iface(args.iface)
        except Exception as e:
            print(f"   Failed to determine link id: {e}", file=sys.stderr)
            sys.exit(1)

    try:
        out = run_busctl(link_id)
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(1)

    entries = parse_leases(out)
    if not entries:
        print("   No active DHCP leases.")
        return
    entries = sort_entries(entries, args.sort, args.reverse)
    # apply optional limit (after sorting). non-positive values produce no results.
    if args.limit is not None:
        try:
            if args.limit <= 0:
                entries = []
            else:
                entries = entries[:args.limit]
        except Exception:
            pass
    if args.hostnames:
        # resolve hostnames only for the limited set
        resolve_hostnames(entries, per_lookup_timeout=args.resolver_timeout, resolvers=args.resolvers)
        for e in entries:
            print(f"   {e['ip']:15} {e.get('hostname','?'):30} {e['mac']:17} {e['rem_str']}")
    else:
        print_leases(entries)


if __name__ == '__main__':
    main()
