#!/usr/bin/python3
"""Display lightweight conntrack occupancy and cumulative kernel counters."""
from pathlib import Path
import shutil
import subprocess

COUNTERS = ('drop', 'early_drop', 'insert_failed')


def read_number(path):
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def proc_counters(text):
    lines = text.splitlines()
    if not lines:
        return {}
    header = lines[0].split()
    totals = {}
    for line in lines[1:]:
        for name, value in zip(header, line.split()):
            if name in COUNTERS:
                totals[name] = totals.get(name, 0) + int(value, 16)
    return totals


def netlink_counters(text):
    totals = {}
    for line in text.splitlines():
        for field in line.split():
            name, separator, value = field.partition('=')
            if separator and name in COUNTERS:
                totals[name] = totals.get(name, 0) + int(value)
    return totals


def get_counters(proc=Path('/proc')):
    try:
        result = proc_counters((proc / 'net/stat/nf_conntrack').read_text())
        if result:
            return result
    except (OSError, ValueError):
        pass
    tool = shutil.which('conntrack')
    if tool:
        try:
            output = subprocess.run([tool, '-S'], check=True, capture_output=True,
                                    text=True, timeout=1).stdout
            return netlink_counters(output)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return {}


def show(proc=Path('/proc')):
    directory = proc / 'sys/net/netfilter'
    count = read_number(directory / 'nf_conntrack_count')
    maximum = read_number(directory / 'nf_conntrack_max')
    print('\n Conntrack:')
    if count is not None and maximum is not None and maximum > 0:
        percent = count * 100 / maximum
        warning = ' FULL' if count >= maximum else ' HIGH' if percent >= 90 else ''
        print(f'  Entries: {count:,} / {maximum:,} ({percent:.1f}%){warning}')
    else:
        print('  Entry count/capacity unavailable')
    timeout = read_number(directory / 'nf_conntrack_tcp_timeout_established')
    if timeout is not None:
        print(f'  TCP established idle timeout: {timeout:,} seconds')
    counters = get_counters(proc)
    if counters:
        values = [f'{label}: {counters[key]:,}' for key, label in (
            ('drop', 'drops'), ('early_drop', 'early evictions'), ('insert_failed', 'insert failures')
        ) if key in counters]
        print('  Cumulative ' + ', '.join(values))
    else:
        print('  Drop counters unavailable')


if __name__ == '__main__':
    show()
