"""Download verified MusicBee Portable releases from the project's mirror."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.request

from .settings import die
from .utils import sha256
from .wine import musicbee_version


MIRROR = "https://github.com/fiso64/musicbee-portable/releases/download"
RELEASES_URL = "https://api.github.com/repos/fiso64/musicbee-portable/releases"
MAX_ARCHIVE_SIZE = 128 * 1024 * 1024


def catalog() -> dict:
    versions = {}
    page = 1
    while True:
        request = urllib.request.Request(
            f"{RELEASES_URL}?per_page=100&page={page}",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "musicbee-linux"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                die("GitHub release response is too large.")
            releases = json.loads(data)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                die("GitHub's release API denied the request or is rate-limited. "
                    "Try later or use --archive for a local download.")
            die(f"Could not load MusicBee releases: HTTP {exc.code}. Use --archive for a local download.")
        except (OSError, ValueError) as exc:
            die(f"Could not load MusicBee releases: {exc}. Use --archive for a local download.")
        if not isinstance(releases, list):
            die("Invalid GitHub release response.")
        for release in releases:
            if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
                continue
            version = release.get("tag_name")
            if not isinstance(version, str) or not re.fullmatch(r"\d+(?:\.\d+){2,3}", version):
                continue
            assets = release.get("assets")
            if not isinstance(assets, list):
                die(f"Invalid release assets for MusicBee {version}.")
            matches = [asset for asset in assets if isinstance(asset, dict)
                       and isinstance(asset.get("name"), str)
                       and re.fullmatch(r"MusicBeePortable_[A-Za-z0-9._-]+\.zip", asset["name"])
                       and asset.get("state") == "uploaded"]
            if not matches:
                continue
            if len(matches) != 1 or version in versions:
                die(f"MusicBee {version} has ambiguous release assets.")
            digest = matches[0].get("digest")
            if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                die(f"GitHub has no valid SHA-256 checksum for MusicBee {version}.")
            versions[version] = {"filename": matches[0]["name"], "sha256": digest[7:]}
        if len(releases) < 100:
            break
        page += 1
    if not versions:
        die("No MusicBee Portable releases are available. Use --archive for a local download.")
    return {"latest": max(versions, key=lambda value: tuple(map(int, value.split(".")))), "versions": versions}


def select_version(index: dict, requested: str) -> str:
    versions = index["versions"]
    if requested == "latest":
        return index["latest"]
    if requested in versions:
        return requested
    matches = [version for version in versions if version.startswith(requested + ".")]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        die(f"MusicBee version {requested!r} is ambiguous. Run 'musicbee-manage versions'.")
    die(f"MusicBee version {requested!r} is unavailable. Run 'musicbee-manage versions'.")


def list_versions(app: Path) -> None:
    index = catalog()
    installed = musicbee_version(app)
    for version in sorted(index["versions"], key=lambda value: tuple(map(int, value.split("."))), reverse=True):
        labels = [label for label, applies in (("latest", version == index["latest"]),
                                               ("installed", version == installed)) if applies]
        suffix = f" ({', '.join(labels)})" if labels else ""
        print(f"{version}{suffix}")
    if installed not in index["versions"] and installed != "not installed":
        print(f"Installed: {installed}")


def download(state: Path, requested: str = "latest", *, index: dict | None = None) -> tuple[Path, str]:
    index = index if index is not None else catalog()
    version = select_version(index, requested)
    entry = index["versions"][version]
    directory = state / "downloads"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / entry["filename"]
    print(f"MusicBee Portable {version}")
    if (target.is_file() and not target.is_symlink() and target.stat().st_size <= MAX_ARCHIVE_SIZE
            and sha256(target) == entry["sha256"]):
        print(f"Using cached archive: {target}")
        return target, version
    url = f"{MIRROR}/{version}/{entry['filename']}"
    print(f"Downloading {entry['filename']} ...", flush=True)
    fd, temporary = tempfile.mkstemp(prefix=".musicbee-download-", dir=directory)
    try:
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(fd, "wb") as output, urllib.request.urlopen(url, timeout=60) as response:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_ARCHIVE_SIZE:
                    die("MusicBee archive exceeds the download size limit.")
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != entry["sha256"]:
            die("MusicBee archive checksum does not match the GitHub release.")
        os.replace(temporary, target)
    except OSError as exc:
        die(f"Could not download MusicBee {version}: {exc}. Use --archive for a local download.")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target, version
