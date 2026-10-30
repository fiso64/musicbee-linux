"""Utils helpers for MusicBee on Linux."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import getpass
import hashlib
import html
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import tempfile

from .settings import die, read_config, write_config
from .paths import matching_child, music_host_dir, parse_windows_path, update_mapping


def entrypoint() -> Path:
    executable = Path(sys.argv[0]).resolve()
    if executable.name in ("musicbee", "musicbee-manage", "musicbee-manage.py") and executable.is_file():
        return executable
    return Path(__file__).resolve().parent.parent / "musicbee-manage.py"


def locations() -> dict[str, Path]:
    home = Path.home()
    state = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")).expanduser() / "musicbee"
    prefix = state / "wineprefix"
    packaged = entrypoint() == Path("/usr/libexec/musicbee/musicbee-manage.py")
    launcher = Path("/usr/bin/musicbee") if packaged else home / ".local/bin/musicbee"
    return {
        "home": home, "state": state, "prefix": prefix,
        "app": prefix / "drive_c" / "MusicBee",
        "bin": launcher, "packaged": packaged,
        "desktop": home / ".local/share/applications/musicbee.desktop",
    }


@contextmanager
def maintenance_lock(prefix: Path, message: str):
    """Prevent changes while MusicBee or another maintenance task is running."""
    with (prefix / ".musicbee.launch.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            die(message)
        yield


def call(command: list[str], env: dict[str, str] | None = None, cwd: Path | None = None) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, check=True, env=env, cwd=cwd)


def tab_settings_path(paths: dict[str, Path], selection: str | None) -> Path:
    portable = paths["app"]
    for part in ("AppData", "MusicBee3Settings.ini"):
        portable = matching_child(portable, part)
    profile = paths["prefix"] / "drive_c"
    for part in ("users", getpass.getuser(), "AppData", "Roaming", "MusicBee", "MusicBee3Settings.ini"):
        profile = matching_child(profile, part)
    candidates = {name: path for name, path in (("portable", portable), ("profile", profile)) if path.is_file()}
    if selection:
        if selection not in candidates:
            die(f"No {selection} MusicBee settings file found.")
        return candidates[selection]
    if len(candidates) == 1:
        return next(iter(candidates.values()))
    if not candidates:
        die("MusicBee settings not found. Start and close MusicBee first.")
    print("Both MusicBee settings files exist. Choose the one MusicBee uses:")
    for name, path in candidates.items():
        print(f"  {name}: {path}")
    if not sys.stdin.isatty():
        die("Use --settings portable or --settings profile to choose the active file.")
    selection = input("Settings [portable/profile]: ").strip().casefold()
    if selection not in candidates:
        die("Choose portable or profile, or use --settings on the command line.")
    return candidates[selection]


def manage_tabs(args: argparse.Namespace, paths: dict[str, Path]) -> None:
    if (args.source is None) != (args.destination is None):
        die("Provide both the current and new position, or neither to list tabs.")
    prefix = paths["prefix"]
    if not prefix.is_dir():
        die("MusicBee is not set up yet.")
    with maintenance_lock(prefix, "Close MusicBee before managing tabs."):
        path = tab_settings_path(paths, args.settings).resolve()
        data = path.read_bytes()
        sections = list(re.finditer(rb"<NavigatorInitialTabs>(.*?)</NavigatorInitialTabs>", data, re.DOTALL))
        if len(sections) != 1:
            die("Could not find a unique tab list in the settings file.")
        section = sections[0]
        body = section.group(1)
        entries = list(re.finditer(rb"<NavigationLocation>.*?</NavigationLocation>", body, re.DOTALL))
        if not entries or b"".join(re.split(rb"<NavigationLocation>.*?</NavigationLocation>", body, flags=re.DOTALL)).strip():
            die("The saved tab list has an unsupported format.")
        blocks = [entry.group() for entry in entries]
        if args.source is None:
            print(f"Settings: {path}")
            for index, block in enumerate(blocks, 1):
                def field(name: bytes) -> str:
                    match = re.search(rb"<" + name + rb">(.*?)</" + name + rb">", block, re.DOTALL)
                    return html.unescape(match.group(1).decode("utf-8", errors="replace")).strip() if match else ""
                label = field(b"TextOverride") or field(b"CustLayoutName") or f"Category {field(b'FolderCategory') or '?'}"
                print(f"  {index}. {label}")
            print("Move a tab: musicbee-manage tabs CURRENT_POSITION NEW_POSITION")
            return
        source, destination = args.source - 1, args.destination - 1
        if not (0 <= source < len(blocks) and 0 <= destination < len(blocks)):
            die(f"Tab positions must be between 1 and {len(blocks)}.")
        if source == destination:
            print("Tab is already in that position.")
            return
        order = list(range(len(blocks)))
        order.insert(destination, order.pop(source))
        parts, end = [], 0
        for entry, original_index in zip(entries, order):
            parts.extend((body[end:entry.start()], blocks[original_index]))
            end = entry.end()
        parts.append(body[end:])
        result = data[:section.start(1)] + b"".join(parts) + data[section.end(1):]
        index_pattern = rb"(<NavigatorInitialTabIndex>)(\d+)(</NavigatorInitialTabIndex>)"
        indexes = list(re.finditer(index_pattern, result))
        if len(indexes) == 1 and int(indexes[0].group(2)) < len(order):
            index = indexes[0]
            new_index = str(order.index(int(index.group(2)))).encode("ascii")
            result = result[:index.start(2)] + new_index + result[index.end(2):]
        fd, temp = tempfile.mkstemp(prefix=".musicbee-tabs-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(result)
            os.chmod(temp, stat.S_IMODE(path.stat().st_mode))
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        print(f"Moved tab {source + 1} to position {destination + 1}.")


def require_ready(prefix: Path) -> None:
    if not (prefix / ".musicbee-bootstrap-complete").is_file():
        die("Wine prefix not initialized; run: musicbee-manage init")


def configure_music_dir(args: argparse.Namespace, paths: dict[str, Path]) -> None:
    prefix, state = paths["prefix"], paths["state"]
    require_ready(prefix)
    config = read_config(state)
    if args.directory is None:
        if args.wine_music_path:
            die("Provide a music directory when specifying a Windows path.")
        print(f"Music directory: {config.get('music_dir', '(not configured by this tool)')}")
        print(f"Windows path: {config.get('windows_music_path', '(not configured by this tool)')}")
        return
    windows_path = args.wine_music_path or config.get("windows_music_path") or f"C:\\Users\\{getpass.getuser()}\\Music"
    parse_windows_path(windows_path)
    try:
        host = music_host_dir(args.directory, prefix)
    except (OSError, RuntimeError) as exc:
        die(f"Cannot access music directory {args.directory!r}: {exc}")
    with maintenance_lock(prefix, "Close MusicBee before changing the music directory."):
        target = update_mapping(prefix, host, windows_path, config)
        write_config(state, {**config, "music_dir": str(host), "windows_music_path": windows_path})
    print(f"Mapped {windows_path} -> {host} (Wine link: {target})")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup(args: argparse.Namespace, paths: dict[str, Path]) -> None:
    require_ready(paths["prefix"])
    output_dir = Path(args.output_dir or paths["state"] / "backups").expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"musicbee-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.tar.gz"
    with maintenance_lock(paths["prefix"], "Close MusicBee before backing up its prefix and database."):
        with tarfile.open(out, "w:gz", dereference=False) as tar:
            tar.add(paths["prefix"], arcname="wineprefix", recursive=True)
            for name in ("config.json", "environment.txt", "portable-installer.sha256"):
                path = paths["state"] / name
                if path.is_file():
                    tar.add(path, arcname=name)
    print(f"Backup: {out}")
    print("Music files are not included (Wine symlinks are preserved, not followed).")
    print("Cached installers are not included; preserve ~/.local/share/musicbee/installers separately.")


def status(paths: dict[str, Path]) -> None:
    prefix = paths["prefix"]
    print(f"Prefix: {prefix}")
    print(f"Bootstrapped: {(prefix / '.musicbee-bootstrap-complete').exists()}")
    print(f"MusicBee installed: {(paths['app'] / 'MusicBee.exe').exists()}")
