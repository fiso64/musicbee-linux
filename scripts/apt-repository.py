#!/usr/bin/env python3
"""Build and verify the small signed APT repository published with a release."""
import argparse
from datetime import datetime, timezone
from email.utils import format_datetime
import gzip
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile


ARCHES = ("amd64", "arm64")
BASE = "https://fiso64.github.io/musicbee-linux/apt"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def control(deb: Path) -> str:
    members = subprocess.check_output(["ar", "t", str(deb)], text=True).splitlines()
    controls = [name for name in members if name.startswith("control.tar")]
    if len(controls) != 1:
        raise ValueError("DEB must contain exactly one control archive")
    data = subprocess.check_output(["ar", "p", str(deb), controls[0]])
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        matches = [member for member in archive.getmembers() if member.name.lstrip("./") == "control"]
        if len(matches) != 1 or not matches[0].isfile():
            raise ValueError("DEB has no unique control file")
        return archive.extractfile(matches[0]).read().decode("utf-8").strip()


def fields(text: str) -> dict[str, str]:
    result = {}
    for line in text.splitlines():
        if line.startswith((" ", "\t")):
            continue
        name, value = line.split(":", 1)
        if name in result:
            raise ValueError(f"Duplicate field: {name}")
        result[name] = value.strip()
    return result


def package_index(deb: Path) -> bytes:
    metadata = control(deb)
    values = fields(metadata)
    if values.get("Package") != "musicbee" or values.get("Architecture") != "all":
        raise ValueError("Unexpected DEB package or architecture")
    data = deb.read_bytes()
    return (metadata + f"\nFilename: pool/main/{deb.name}\nSize: {len(data)}\n"
            f"SHA256: {digest(data)}\n\n").encode()


def sources(public_key: str) -> str:
    lines = public_key.strip().splitlines()
    if not lines or lines[0] != "-----BEGIN PGP PUBLIC KEY BLOCK-----":
        raise ValueError("Expected an ASCII-armored public key")
    embedded = "\n".join("  " + (line if line else ".") for line in lines)
    return (f"Types: deb\nURIs: {BASE}\nSuites: stable\nComponents: main\n"
            f"Architectures: {' '.join(ARCHES)}\nSigned-By:\n{embedded}\n")


def build(site: Path, deb: Path, key: Path, fingerprint: str) -> None:
    repo = site / "apt"
    if repo.exists():
        raise ValueError(f"APT directory already exists: {repo}")
    pool = repo / "pool/main"
    pool.mkdir(parents=True)
    package = pool / deb.name
    shutil.copyfile(deb, package)
    index = package_index(package)
    entries = []
    for arch in ARCHES:
        directory = repo / f"dists/stable/main/binary-{arch}"
        directory.mkdir(parents=True)
        for name, data in (("Packages", index), ("Packages.gz", gzip.compress(index, mtime=0))):
            (directory / name).write_bytes(data)
            entries.append((f"main/binary-{arch}/{name}", data))
    release_dir = repo / "dists/stable"
    release = ("Origin: MusicBee on Linux\nLabel: MusicBee on Linux\nSuite: stable\n"
               f"Codename: stable\nDate: {format_datetime(datetime.now(timezone.utc), usegmt=True)}\n"
               f"Architectures: {' '.join(ARCHES)}\nComponents: main\n"
               "Description: MusicBee on Linux integration\nSHA256:\n" +
               "".join(f" {digest(data)} {len(data):16d} {name}\n" for name, data in entries))
    (release_dir / "Release").write_text(release, encoding="utf-8")
    args = ["gpg", "--batch", "--yes"]
    passphrase = os.environ.get("MUSICBEE_SIGNING_PASSPHRASE_FILE")
    if passphrase:
        args += ["--pinentry-mode", "loopback", "--passphrase-file", passphrase]
    subprocess.run([*args, "--local-user", fingerprint, "--clearsign", "--output",
                    str(release_dir / "InRelease"), str(release_dir / "Release")], check=True)
    (site / "musicbee.sources").write_text(sources(key.read_text(encoding="utf-8")), encoding="utf-8")


def verify(site: Path, deb: Path, key: Path, version: str) -> None:
    repo = site / "apt"
    release_dir = repo / "dists/stable"
    release = (release_dir / "Release").read_bytes()
    signed = subprocess.check_output(["gpg", "--batch", "--decrypt", str(release_dir / "InRelease")])
    if signed != release:
        raise ValueError("Signed APT metadata differs from Release")
    if (site / "musicbee.sources").read_text(encoding="utf-8") != sources(key.read_text(encoding="utf-8")):
        raise ValueError("APT source configuration or embedded key differs")
    package = repo / "pool/main" / deb.name
    if package.read_bytes() != deb.read_bytes():
        raise ValueError("APT package differs from the release asset")
    index = package_index(package)
    if fields(control(package)).get("Version") != f"{version}-1":
        raise ValueError("DEB version does not match release")
    listed = release.decode().split("SHA256:\n", 1)[1].splitlines()
    hashes = {}
    for line in listed:
        checksum, size, name = line.split()
        if name in hashes:
            raise ValueError("Duplicate APT index checksum")
        hashes[name] = (checksum, int(size))
    expected = set()
    for arch in ARCHES:
        for name in ("Packages", "Packages.gz"):
            path = f"main/binary-{arch}/{name}"
            expected.add(path)
            data = (release_dir / path).read_bytes()
            if hashes.get(path) != (digest(data), len(data)):
                raise ValueError(f"APT index checksum mismatch: {path}")
            if (gzip.decompress(data) if name.endswith(".gz") else data) != index:
                raise ValueError(f"APT index does not match DEB: {path}")
    if set(hashes) != expected:
        raise ValueError("Unexpected APT index entries")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("build", "verify"))
    parser.add_argument("site", type=Path)
    parser.add_argument("deb", type=Path)
    parser.add_argument("key", type=Path)
    parser.add_argument("value", help="Signing fingerprint for build; version for verify")
    args = parser.parse_args()
    if args.action == "build":
        build(args.site, args.deb, args.key, args.value)
    else:
        verify(args.site, args.deb, args.key, args.value)


if __name__ == "__main__":
    main()
