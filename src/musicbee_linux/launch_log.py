"""Capture and rotate desktop launch logs without importing the launcher."""

from __future__ import annotations

from datetime import datetime
import fcntl
import os
from pathlib import Path
import subprocess
import sys

from . import timing
from .paths import entrypoint


_LAUNCH_LOG_LIMIT = 2 * 1024 * 1024
_DESKTOP_LOG_LIMIT = 4 * 1024 * 1024


def _append_desktop_log(log: Path, lock_fd: int, data: bytes) -> None:
    # The lock is separate from the log so rotation does not change what it guards.
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    try:
        try:
            size = log.stat().st_size
        except FileNotFoundError:
            size = 0
        if size + len(data) > _DESKTOP_LOG_LIMIT:
            log.replace(log.with_name('desktop-launch.log.1'))
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)


def desktop_launch_with_log(state: Path, files: list[str]) -> int | None:
    """Append timestamped desktop launch output to the rolling log."""
    log = state / "desktop-launch.log"
    timestamp = datetime.now().astimezone().isoformat(timespec='seconds')
    pid = os.getpid()
    header = f'\n[{timestamp}] Desktop launch (PID {pid})\n'.encode('utf-8')
    prefix = f'[{pid}] '.encode('ascii')
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        lock = log.with_suffix('.lock').open('a')
    except OSError:
        return None  # Logging must not prevent MusicBee from starting.
    env = dict(os.environ, MUSICBEE_DESKTOP_LOG_WORKER="1",
               MUSICBEE_LAUNCH_STARTED_NS=str(timing.started_ns()))
    command = [sys.executable, str(entrypoint()), "--manage", "desktop-launch", *files]
    with lock:
        try:
            _append_desktop_log(log, lock.fileno(), header)
        except OSError:
            return None
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        marker = b'\n' + prefix + b'[Log truncated at 2 MiB]\n'
        remaining = _LAUNCH_LOG_LIMIT - len(header) - len(marker)
        with process.stdout as stream:
            while line := stream.readline(65536):
                if not remaining:
                    continue  # Keep draining output after the limit or a write error.
                data = prefix + line
                if not data.endswith(b'\n'):
                    data += b'\n'
                if len(data) >= remaining:
                    data = data[:remaining] + marker
                    remaining = 0
                else:
                    remaining -= len(data)
                try:
                    _append_desktop_log(log, lock.fileno(), data)
                except OSError:
                    remaining = 0
        return process.wait()
