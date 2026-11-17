"""Monotonic launch timing shared with the desktop logging worker."""

import os
import time

_started_ns = time.monotonic_ns()


def inherit_start(entry_started_ns: int) -> None:
    """Use the desktop launcher's start time in its logging worker."""
    global _started_ns
    _started_ns = entry_started_ns
    raw = os.environ.pop('MUSICBEE_LAUNCH_STARTED_NS', '')
    if os.environ.get('MUSICBEE_DESKTOP_LOG_WORKER') == '1' and raw.isascii() and raw.isdecimal():
        candidate = int(raw)
        if 0 < candidate <= _started_ns:
            _started_ns = candidate


def started_ns() -> int:
    return _started_ns
