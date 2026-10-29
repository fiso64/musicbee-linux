#!/usr/bin/env python3
"""Verify repository metadata and its RPM after the repomd signature is checked."""
import gzip
import hashlib
import lzma
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


def check_file(root: Path, location: str, checksum: str, kind: str) -> Path:
    if kind != 'sha256':
        raise ValueError(f'Unsupported checksum: {kind}')
    path = (root / location).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f'Invalid repository path: {location}')
    if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
        raise ValueError(f'Checksum mismatch: {location}')
    return path


def verify(root: Path) -> None:
    root = root.resolve(strict=True)
    repomd = ET.parse(root / 'repodata' / 'repomd.xml').getroot()
    ns = {'r': 'http://linux.duke.edu/metadata/repo', 'c': 'http://linux.duke.edu/metadata/common'}
    primary = None
    for entry in repomd.findall('r:data', ns):
        location = entry.find('r:location', ns)
        checksum = entry.find('r:checksum', ns)
        if location is None or checksum is None or not checksum.text:
            raise ValueError('Incomplete repository metadata')
        path = check_file(root, location.attrib['href'], checksum.text, checksum.attrib['type'])
        if entry.attrib.get('type') == 'primary':
            if primary is not None:
                raise ValueError('Multiple primary metadata files')
            primary = path
    if primary is None:
        raise ValueError('Missing primary metadata')
    if primary.suffix == '.zst':
        from compression import zstd
        data = zstd.decompress(primary.read_bytes())
    elif primary.suffix == '.gz':
        data = gzip.decompress(primary.read_bytes())
    elif primary.suffix == '.xz':
        data = lzma.decompress(primary.read_bytes())
    else:
        data = primary.read_bytes()
    packages = ET.fromstring(data).findall('c:package', ns)
    if len(packages) != 1:
        raise ValueError('Expected exactly one RPM in the repository')
    location = packages[0].find('c:location', ns)
    checksum = packages[0].find('c:checksum', ns)
    if location is None or checksum is None or not checksum.text:
        raise ValueError('Incomplete RPM metadata')
    rpm = check_file(root, location.attrib['href'], checksum.text, checksum.attrib['type'])
    if rpm.suffix != '.rpm' or len(list(root.glob('*.rpm'))) != 1:
        raise ValueError('Expected exactly one RPM at the repository root')


if __name__ == '__main__':
    try:
        verify(Path(sys.argv[1]))
    except (IndexError, OSError, ValueError, ET.ParseError, KeyError) as exc:
        raise SystemExit(f'Invalid RPM repository: {exc}') from exc
