#!/usr/bin/env python3
"""Keep immutable files referenced by previously published repository indexes."""
import hashlib
from pathlib import Path
import re
import shutil
import sys


def retain(previous: Path, site: Path) -> None:
    def copy(source: Path, target: Path) -> None:
        if not source.is_file() or source.is_symlink():
            raise ValueError(f'Invalid repository file: {source}')
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if not target.is_file() or target.read_bytes() != source.read_bytes():
                raise ValueError(f'Immutable repository file changed: {target}')
        else:
            shutil.copyfile(source, target)

    for pattern in ('rpm/*.rpm', 'apt/pool/main/*.deb', 'arch/*.pkg.tar.zst',
                    'arch/*.pkg.tar.zst.sig', 'apt/dists/stable/main/binary-*/by-hash/SHA256/*'):
        for source in previous.glob(pattern):
            if '/by-hash/SHA256/' in source.as_posix():
                if hashlib.sha256(source.read_bytes()).hexdigest() != source.name:
                    raise ValueError(f'Invalid APT by-hash checksum: {source}')
            copy(source, site / source.relative_to(previous))
    for source in (previous / 'rpm/repodata').glob('*'):
        if re.fullmatch(r'[0-9a-f]{64}-.+', source.name):
            if hashlib.sha256(source.read_bytes()).hexdigest() != source.name[:64]:
                raise ValueError(f'Invalid RPM metadata checksum: {source}')
            copy(source, site / source.relative_to(previous))
    # Preserve even indexes published before Acquire-By-Hash was enabled.
    for source in (previous / 'apt/dists/stable/main').glob('binary-*/Packages*'):
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        target = site / source.parent.relative_to(previous) / 'by-hash/SHA256' / digest
        copy(source, target)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('Usage: retain-repository-files.py PREVIOUS_SITE NEW_SITE')
    retain(Path(sys.argv[1]), Path(sys.argv[2]))
