#!/usr/bin/env python3
"""Verify that a signed pacman database describes the expected package."""
import hashlib
from pathlib import Path
import sys
import tarfile


def verify(directory: Path, version: str) -> None:
    packages = list(directory.glob('*.pkg.tar.zst'))
    if len(packages) != 1:
        raise ValueError('Expected exactly one Arch package')
    package = packages[0]
    with tarfile.open(directory / 'musicbee-linux.db', 'r:*') as archive:
        entries = [m for m in archive.getmembers() if m.isfile() and m.name.endswith('/desc')]
        if len(entries) != 1:
            raise ValueError('Expected exactly one package in the Arch database')
        fields = archive.extractfile(entries[0]).read().decode('utf-8').split('\n\n')
    metadata = {}
    for field in fields:
        lines = field.splitlines()
        if lines and lines[0].startswith('%') and lines[0].endswith('%'):
            metadata[lines[0][1:-1]] = lines[1:]
    expected = {
        'NAME': ['musicbee'],
        'VERSION': [version + '-1'],
        'FILENAME': [package.name],
        'SHA256SUM': [hashlib.sha256(package.read_bytes()).hexdigest()],
    }
    for field, value in expected.items():
        if metadata.get(field) != value:
            raise ValueError(f'Arch database has incorrect {field}')


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('Usage: verify-arch-repo.py REPOSITORY VERSION')
    verify(Path(sys.argv[1]), sys.argv[2])
