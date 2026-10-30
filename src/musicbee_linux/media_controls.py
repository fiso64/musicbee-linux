"""Media helpers for MusicBee on Linux."""

from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import zipfile

from .settings import SetupError, die
from .utils import maintenance_lock, require_ready, sha256
from .wine import wine_env


MPRISBEE_VERSION = "1.0.3"


MPRISBEE_URL = (
    "https://github.com/UrbanCMC/mb_MPRISBee/releases/download/"
    f"{MPRISBEE_VERSION}/mb_MPRISBee.zip"
)


def mpris_archive(path: Path) -> list[tuple[zipfile.ZipInfo, Path]]:
    """Accept only MusicBee's plugin DLL and its private dependency directory."""
    entries: list[tuple[zipfile.ZipInfo, Path]] = []
    names: set[str] = set()
    total_bytes = 0
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            name = info.filename
            if not name or "\\" in name or name.startswith("/"):
                die(f"Unsafe filename in MPRISBee archive: {name!r}")
            parts = PurePosixPath(name).parts
            if not parts or any(part in (".", "..") for part in parts):
                die(f"Unsafe path in MPRISBee archive: {name!r}")
            if info.is_dir():
                continue
            # A Unix symlink can have been stored in a ZIP; refuse it.
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                die(f"Refusing symlink in MPRISBee archive: {name!r}")
            if (len(parts) == 1 and parts[0].casefold() == "mb_mprisbee.dll") or (
                len(parts) >= 2 and parts[0].casefold() == "mprisbee"
            ):
                relative = Path(*parts)
                if relative.as_posix().casefold() in names:
                    die(f"Duplicate plugin entry in archive: {name!r}")
                names.add(relative.as_posix().casefold())
                total_bytes += info.file_size
                if info.file_size > 32 * 1024 * 1024 or total_bytes > 128 * 1024 * 1024:
                    die("MPRISBee archive is unexpectedly large.")
                entries.append((info, relative))
        if not any(str(rel).casefold() == "mb_mprisbee.dll" for _, rel in entries):
            die("Not an MPRISBee plugin archive: missing mb_MPRISBee.dll at ZIP root.")
    return entries


def install_mpris_archive(archive_path: Path, app: Path) -> int:
    """Install only this plugin's files. Preserve every other MusicBee plugin."""
    entries = mpris_archive(archive_path)
    plugin_dir = app / "Plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    for info, rel in entries:
        target = plugin_dir / rel
        # Reject symlinked directories/files, including Plugins itself.
        current = plugin_dir
        if current.is_symlink():
            die(f"Refusing symlinked plugin directory: {current}")
        for part in rel.parts[:-1]:
            current = current / part
            if current.is_symlink():
                die(f"Refusing symlinked plugin directory: {current}")
        if target.is_symlink() or (target.exists() and not target.is_file()):
            die(f"Refusing to replace non-file plugin entry: {target}")
    # Stage data before replacing any installed file.
    with tempfile.TemporaryDirectory(prefix="musicbee-mpris-") as stage:
        staged = Path(stage)
        with zipfile.ZipFile(archive_path) as zf:
            for info, rel in entries:
                out = staged / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as inp, out.open("wb") as dst:
                    shutil.copyfileobj(inp, dst)
        for _, rel in entries:
            src = staged / rel
            dst = plugin_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=f".{dst.name}-", dir=dst.parent)
            try:
                with os.fdopen(fd, "wb") as out, src.open("rb") as inp:
                    shutil.copyfileobj(inp, out)
                os.replace(tmp, dst)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
    return len(entries)


def expose_mpris_dependencies(app: Path) -> tuple[int, int]:
    """Make MPRISBee's managed dependencies visible to MusicBee's early type scan.

    MusicBee calls Assembly.GetTypes before constructing Plugin, i.e. before
    MPRISBee's own AssemblyResolve handler is registered. A private subfolder
    is therefore too late for dependencies referenced by inspected types.
    Keep original subfolder files and copy the DLLs beside mb_MPRISBee.dll.
    Never replace an unrelated plug-in's dependency with different bytes.
    """
    plugins = app / "Plugins"
    private = plugins / "MPRISBee"
    if not (plugins / "mb_MPRISBee.dll").is_file():
        die(f"MPRISBee's primary DLL is missing: {plugins / 'mb_MPRISBee.dll'}")
    if plugins.is_symlink() or private.is_symlink() or not private.is_dir():
        die(f"Expected a normal MPRISBee dependency directory: {private}")
    dependencies = sorted(f for f in private.iterdir() if f.suffix.casefold() == ".dll")
    if not dependencies:
        die(f"No MPRISBee dependency DLLs found in {private}")
    pending: list[tuple[Path, Path]] = []
    already_present = 0
    for src in dependencies:
        if not src.is_file() or src.is_symlink():
            die(f"Refusing unusual dependency entry: {src}")
        dst = plugins / src.name
        if dst.is_symlink() or (dst.exists() and not dst.is_file()):
            die(f"Refusing to overwrite an unusual file in Plugins: {dst}")
        if dst.exists():
            if sha256(src) != sha256(dst):
                die(f"Conflicting existing DLL: {dst}. "
                    "Resolve this DLL version conflict manually; other plugins may depend on it.")
            already_present += 1
        else:
            pending.append((src, dst))
    # Validate all destinations first. Stage + atomic replace so a partial
    # interrupted write cannot create a truncated assembly.
    for src, dst in pending:
        fd, temp = tempfile.mkstemp(prefix=f".{dst.name}-", dir=plugins)
        try:
            with os.fdopen(fd, "wb") as out, src.open("rb") as inp:
                shutil.copyfileobj(inp, out)
            if dst.exists() or dst.is_symlink():
                die(f"New conflicting DLL appeared during install: {dst}")
            os.replace(temp, dst)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    return len(pending), already_present


def download_mpris_archive(state: Path) -> Path:
    import urllib.request

    downloads = state / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    path = downloads / f"mb_MPRISBee-{MPRISBEE_VERSION}.zip"
    if path.exists():
        print(f"Using cached MPRISBee archive: {path}")
        return path
    print(f"Downloading pinned MPRISBee v{MPRISBEE_VERSION} from {MPRISBEE_URL}")
    request = urllib.request.Request(MPRISBEE_URL, headers={"User-Agent": "musicbee-linux/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=45) as remote:
            data = remote.read(32 * 1024 * 1024 + 1)
    except Exception as exc:
        die(f"MPRISBee download failed ({exc}). Use --archive /path/to/mb_MPRISBee.zip instead.")
    if len(data) > 32 * 1024 * 1024:
        die("MPRISBee ZIP download exceeded 32 MiB.")
    fd, temp = tempfile.mkstemp(prefix=".mprisbee-", suffix=".zip", dir=downloads)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        mpris_archive(Path(temp))  # Reject invalid downloads before caching.
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return path


def wine_version(prefix: Path) -> str:
    env = wine_env(prefix)
    if not shutil.which("wine", path=env["PATH"]):
        return "Wine not found"
    result = subprocess.run(["wine", "--version"], env=env,
                            text=True, capture_output=True, check=False)
    return (result.stdout or result.stderr).strip()


def media_controls(args: argparse.Namespace, paths: dict[str, Path]) -> None:
    plugin = paths["app"] / "Plugins" / "mb_MPRISBee.dll"
    version = wine_version(paths["prefix"])
    print(f"Wine: {version}")
    if "staging" not in version.casefold():
        print("WARNING: MPRISBee v1.0.3 needs Wine Staging or equivalent AF_UNIX patches. "
              "Your Wine build may not support media controls.")
    if args.action == "status":
        print(f"MPRISBee installed: {plugin.is_file()}")
        private = plugin.parent / "MPRISBee"
        if private.is_dir() and not private.is_symlink():
            for src in sorted(private.glob("*.dll")):
                dst = plugin.parent / src.name
                state = "missing" if not dst.exists() else (
                    "OK" if dst.is_file() and not dst.is_symlink() and sha256(src) == sha256(dst)
                    else "CONFLICT"
                )
                print(f"  {src.name}: {state} beside plugin")
        if shutil.which("playerctl"):
            subprocess.run(["playerctl", "--list-all"], check=False)
        else:
            print("Install playerctl to check the D-Bus player.")
        return
    require_ready(paths["prefix"])
    if not (paths["app"] / "MusicBee.exe").is_file():
        die(r"Install MusicBee into C:\MusicBee first.")
    if args.action == "repair":
        with maintenance_lock(paths["prefix"], "Close MusicBee before repairing MPRISBee dependencies."):
            copied, existing = expose_mpris_dependencies(paths["app"])
        print(f"MPRISBee repaired: {copied} dependency DLLs copied, {existing} already present.")
        print("Restart MusicBee, enable MPRISBee, then check: playerctl -l")
        return
    archive = Path(args.archive).expanduser().resolve(strict=True) if args.archive else download_mpris_archive(paths["state"])
    if not archive.is_file():
        die(f"Archive not a file: {archive}")
    digest = sha256(archive)
    if args.sha256 and digest.casefold() != args.sha256.casefold():
        die(f"Checksum mismatch: expected {args.sha256}, got {digest}")
    with maintenance_lock(paths["prefix"], "Close MusicBee before installing or updating MPRISBee."):
        count = install_mpris_archive(archive, paths["app"])
        copied, existing = expose_mpris_dependencies(paths["app"])
    (paths["state"] / "mprisbee-archive.sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"Installed MPRISBee v{MPRISBEE_VERSION}: {count} plugin files")
    print(f"Made dependencies discoverable: {copied} copied, {existing} already present")
    print(f"Archive SHA-256: {digest}")


def ensure_media_controls(paths: dict[str, Path]) -> None:
    """Install media controls when absent without blocking MusicBee on failure."""
    if (paths["app"] / "Plugins" / "mb_MPRISBee.dll").is_file():
        return
    try:
        media_controls(argparse.Namespace(action="install", archive=None, sha256=None), paths)
    except (SetupError, OSError, subprocess.CalledProcessError, zipfile.BadZipFile, ValueError) as exc:
        print(f"Warning: could not install MPRISBee: {exc}. "
              "Retry with: musicbee-manage media-controls install", file=sys.stderr)
