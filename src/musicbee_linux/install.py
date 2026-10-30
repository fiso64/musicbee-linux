"""Install, update and repair MusicBee and its Wine dependencies."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

from .archive import unpack_portable, validate_portable_archive
from .paths import matching_child
from .settings import die, read_config, tweak_value, write_config, write_json_atomic
from .desktop import extract_musicbee_icon, install_self, make_launcher, regenerate_launcher
from .media_controls import ensure_media_controls
from .utils import call, maintenance_lock, require_ready, sha256
from .wine import (apply_font_tweak, apply_windows_version, require_commands, run_musicbee,
                   wine_env, wine_server)


def record_environment(state: Path, prefix: Path) -> None:
    lines = [f"Recorded: {datetime.now(timezone.utc).isoformat()}"]
    env = wine_env(prefix)
    for cmd in (["wine", "--version"], ["winetricks", "--version"]):
        if shutil.which(cmd[0], path=env["PATH"]):
            result = subprocess.run(cmd, env=env, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
            lines.append("$ " + " ".join(cmd) + "\n" + result.stdout.strip())
    (state / "environment.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_bootstrap(prefix: Path, *, refresh: bool) -> None:
    require_commands("wine", "wineboot", "winetricks", "cabextract", prefix=prefix)
    server = wine_server(prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    if not (prefix / "system.reg").is_file():
        print("Creating dedicated 64-bit/WoW64 Wine prefix ...")
        call(["wineboot", "--init"], env=wine_env(prefix, init=True))
        call([server, "-w"], env=wine_env(prefix))
    marker = prefix / ".musicbee-bootstrap-complete"
    if not marker.exists() or refresh:
        print("Installing .NET 4.8 and MusicBee dependencies (interactive prompts may appear) ...")
        call(["winetricks", "-q", "dotnet48", "xmllite", "corefonts", "cjkfonts", "gdiplus"],
             env=wine_env(prefix))
        marker.touch()
    else:
        print("Existing Wine prefix found; skipping Winetricks dependencies.")


def initialize_prefix(paths: dict[str, Path]) -> None:
    state, prefix = paths["state"], paths["prefix"]
    config = read_config(state)
    ensure_bootstrap(prefix, refresh=False)
    version = tweak_value(config, "windows-version")
    apply_windows_version(version, prefix)
    config.setdefault("tweaks", {})["windows-version"] = version
    for name in ("allfonts", "font-smoothing"):
        if tweak_value(config, name) == "on":
            apply_font_tweak(name, "on", prefix)
    install_self(paths["bin"], state)
    make_launcher(paths["bin"], paths["desktop"], extract_musicbee_icon(paths["app"], state),
                  setup_needed=not (paths["app"] / "MusicBee.exe").is_file())
    write_config(state, {**config, "schema": 1})
    record_environment(state, prefix)
    print("Wine setup complete.")


def repair_dependencies(paths: dict[str, Path]) -> None:
    prefix = paths["prefix"]
    require_ready(prefix)
    with maintenance_lock(prefix, "Close MusicBee before repairing dependencies."):
        ensure_bootstrap(prefix, refresh=True)
        apply_windows_version(tweak_value(read_config(paths["state"]), "windows-version"), prefix)
    record_environment(paths["state"], prefix)
    print("Dependency setup complete.")


AAC_URL = "https://www.un4seen.com/files/z/2/bass_aac24.zip"


def install_aac_decoder(paths: dict[str, Path]) -> None:
    import urllib.request

    app = paths["app"]
    if not (app / "MusicBee.exe").is_file():
        die("MusicBee is not installed. Run musicbee-manage install first.")
    target = matching_child(app, "bass_aac.dll")
    if target.is_symlink() or (target.exists() and not target.is_file()):
        die(f"Refusing unsafe AAC decoder path: {target}")
    if target.is_file():
        print("AAC decoder already present; keeping it.")
        return
    print("Downloading the BASS AAC decoder from Un4seen ...", flush=True)
    request = urllib.request.Request(AAC_URL, headers={"User-Agent": "musicbee-linux"})
    with urllib.request.urlopen(request, timeout=45) as remote:
        data = remote.read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024:
        die("AAC decoder archive is unexpectedly large.")
    import io
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = [entry for entry in archive.infolist() if entry.filename.casefold() == "bass_aac.dll"]
        if len(entries) != 1 or entries[0].is_dir() or entries[0].file_size > 8 * 1024 * 1024 or \
           stat.S_ISLNK(entries[0].external_attr >> 16):
            die("AAC archive must contain one 32-bit bass_aac.dll at its root.")
        dll = archive.read(entries[0])
    if len(dll) < 64 or dll[:2] != b"MZ":
        die("AAC decoder is not a Windows DLL.")
    pe = int.from_bytes(dll[60:64], "little")
    if pe > len(dll) - 24 or dll[pe:pe + 4] != b"PE\0\0" or \
       int.from_bytes(dll[pe + 4:pe + 6], "little") != 0x14c or \
       not int.from_bytes(dll[pe + 22:pe + 24], "little") & 0x2000:
        die("AAC decoder is not a 32-bit Windows DLL.")
    fd, temporary = tempfile.mkstemp(prefix=".bass-aac-", dir=app)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(dll)
        try:
            os.link(temporary, target)
        except FileExistsError:
            die(f"AAC decoder appeared during installation; leaving it untouched: {target}")
    finally:
        os.unlink(temporary)
    print("AAC decoder installed.")


def ensure_aac_decoder(paths: dict[str, Path]) -> None:
    try:
        install_aac_decoder(paths)
    except Exception as exc:
        print(f"Warning: could not install the AAC decoder: {exc}. "
              "Retry with: musicbee-manage aac", file=sys.stderr)


def repair_aac_decoder(paths: dict[str, Path]) -> None:
    require_ready(paths["prefix"])
    with maintenance_lock(paths["prefix"], "Close MusicBee before installing the AAC decoder."):
        install_aac_decoder(paths)


UNOFFICIAL_NOTICE = (
    "MusicBee for Linux - Unofficial community packaging.\n"
    "Not affiliated with, endorsed by or supported by Steven Mayall or MusicBee.\n"
    "Report Linux, Wine and packaging problems to this project's maintainer,\n"
    "not the MusicBee developer. MusicBee's upstream EULA applies.\n"
    "See https://www.getmusicbee.com/ for upstream information.\n"
)


def confirm_license(state: Path, *, accept: bool = False) -> None:
    marker = state / "license-accepted.json"
    if marker.is_file():
        return
    message = UNOFFICIAL_NOTICE + "\nDo you accept MusicBee's EULA and wish to install it?"
    print(message, flush=True)
    if not accept:
        if sys.stdin.isatty():
            while True:
                answer = input("Accept and continue? [Y/n] ").strip().casefold()
                if answer in ("", "y", "yes", "n", "no"):
                    accept = answer in ("", "y", "yes")
                    break
                print("Please enter y or n.")
        elif os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            # Prefer an existing desktop dialog; do not require GNOME on KDE.
            if shutil.which("kdialog"):
                accept = subprocess.run(["kdialog", "--yesno", message], check=False).returncode == 0
            elif shutil.which("zenity"):
                accept = subprocess.run(["zenity", "--question", "--title=Unofficial MusicBee", f"--text={message}"], check=False).returncode == 0
    if not accept:
        die("Installation cancelled. To accept from a terminal: musicbee-manage init --accept-license")
    state.mkdir(parents=True, exist_ok=True)
    write_json_atomic(marker, {"accepted": True, "recorded_at": datetime.now(timezone.utc).isoformat()})


def packaged_archive(paths: dict[str, Path], override: str | None = None) -> Path:
    if override:
        archive = Path(override).expanduser()
    elif os.environ.get("MUSICBEE_ARCHIVE_OVERRIDE"):
        archive = Path(os.environ["MUSICBEE_ARCHIVE_OVERRIDE"]).expanduser()
    else:
        die("Download the MusicBee Portable ZIP from https://www.getmusicbee.com/downloads/\n"
            "Then run: musicbee-manage install /path/to/MusicBeePortable.zip")
    if not archive.is_file():
        die(f"MusicBee archive not found: {archive}. Download it from https://www.getmusicbee.com/downloads/.")
    return archive


def first_launch(args: argparse.Namespace, paths: dict[str, Path]) -> None:
    # Do not hold a persistent lock over the Wine process; init only protects
    # concurrent setup operations. State under XDG_DATA_HOME is per-user.
    state = paths["state"]
    state.mkdir(parents=True, exist_ok=True)
    lockfile = state / ".installation.lock"
    with lockfile.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (paths["app"] / "MusicBee.exe").is_file():
            archive = packaged_archive(paths, args.archive)
            validate_portable_archive(archive)
            confirm_license(state, accept=args.accept_license)
            if not (paths["prefix"] / ".musicbee-bootstrap-complete").is_file() or not (state / "config.json").is_file():
                initialize_prefix(paths)
            unpack_portable(archive, paths["app"])
            ensure_aac_decoder(paths)
            regenerate_launcher(paths)
            write_json_atomic(state / "portable-source.json", {"sha256": sha256(archive), "archive": archive.name})
            ensure_media_controls(paths)
        else:
            print("Existing MusicBee installation found.")
    if not args.no_launch:
        run_musicbee(argparse.Namespace(musicbee_args=[]), paths)


def install(args: argparse.Namespace, paths: dict[str, Path]) -> None:
    source = Path(args.installer).expanduser().resolve(strict=True)
    if not source.is_file():
        die(f"Archive is not a file: {source}")
    if not (paths["app"] / "MusicBee.exe").is_file():
        first_launch(argparse.Namespace(archive=str(source),
                                        accept_license=False, no_launch=True), paths)
        print("Installed. Launch from your app menu or run: musicbee")
        return
    validate_portable_archive(source)
    require_ready(paths["prefix"])
    digest = sha256(source)
    keep_backup = getattr(args, "keep_backup", None)
    if keep_backup is None:
        if sys.stdin.isatty():
            while True:
                answer = input("Keep a backup of the current MusicBee installation? [Y/n] ").strip().casefold()
                if answer in ("", "y", "yes", "n", "no"):
                    keep_backup = answer in ("", "y", "yes")
                    break
                print("Please enter y or n.")
        else:
            keep_backup = True
            print("Keeping a backup (use --no-backup to skip it).")
    with maintenance_lock(paths["prefix"], "Close MusicBee before updating it."):
        backup_path = unpack_portable(source, paths["app"], update=True, keep_backup=keep_backup)
        ensure_aac_decoder(paths)
        write_json_atomic(paths["state"] / "portable-source.json", {"sha256": digest, "archive": source.name})
    regenerate_launcher(paths)
    if backup_path:
        print(f"Previous application backup: {backup_path}")
    print("Updated. Launch from your app menu or run: musicbee")
