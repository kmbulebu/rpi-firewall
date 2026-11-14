#!/usr/bin/python3
"""MOTD reads only the local cache; never opens the serial device."""
import json
from pathlib import Path
import time

try:
    state = json.loads(Path('/run/wag-d20/status.json').read_text()[:65536])
    sampled = state.get('last_success_monotonic')
    if sampled is None:
        print(' WAG-D20: status unavailable (waiting for first successful poll)')
    else:
        age = max(0, int(time.monotonic() - sampled))
        stale = state.get('health') != 'ok' or age > max(90, state.get('poll_interval', 30) * 3)
        los = state['optics'].get('rx_los')
        los_text = {0: 'clear', 1: 'asserted'}.get(los, 'unknown')
        seconds = int(state['uptime_seconds'])
        uptime = f'{seconds // 3600}h {(seconds % 3600) // 60}m'
        label = 'STALE; last observation' if stale else 'observed'
        print(f" WAG-D20: PON state {state['ploam']['current']} (raw) | optical LOS {los_text}")
        print(f'          uptime {uptime} | {label} {age}s ago')
except (OSError, ValueError, KeyError, TypeError):
    print(' WAG-D20: status unavailable')
