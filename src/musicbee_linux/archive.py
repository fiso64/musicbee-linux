"""Validation, extraction and safe updates of MusicBee Portable archives."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tempfile
import zipfile

from .paths import matching_child
from .settings import die


def safe_zip_extract(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(source) as zf:
        for member in zf.infolist():
            normalized = PurePosixPath(member.filename.replace("\\", "/"))
            parts = normalized.parts
            if not parts or normalized.is_absolute() or any(x in ("..", ".") for x in parts):
                die(f"Unsafe ZIP entry: {member.filename}")
            if (member.external_attr >> 16) & 0o170000 == stat.S_IFLNK:
                die(f"Symlink in ZIP: {member.filename}")
            if member.file_size > 256 * 1024 * 1024:
                die(f"Oversized ZIP member: {member.filename}")
        if sum(i.file_size for i in zf.infolist()) > 768 * 1024 * 1024:
            die("Unexpectedly large MusicBee ZIP archive")
        zf.extractall(destination)


def validate_portable_archive(archive: Path) -> None:
    """Reject clearly incorrect downloads before Wine setup or an update prompt."""
    instruction = ("Download the MusicBee Portable Edition from "
                   "https://www.getmusicbee.com/downloads/ and supply its ZIP or EXE.")
    if archive.suffix.casefold() == ".exe" and archive.name.casefold().startswith("musicbeesetup"):
        die(f"This is the non-portable MusicBee installer. {instruction}")
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            names = [PurePosixPath(entry.filename.replace("\\", "/")).name.casefold()
                     for entry in zf.infolist() if not entry.is_dir()]
        if not any(name == "musicbee.exe" or
                   (name.startswith("musicbeeportable") and name.endswith(".exe")) for name in names):
            if any(name.startswith("musicbeesetup") and name.endswith(".exe") for name in names):
                die(f"This ZIP contains the non-portable MusicBee installer. {instruction}")
            die(f"This ZIP does not contain MusicBee Portable. {instruction}")
    elif archive.suffix.casefold() != ".exe":
        die(f"This is not a readable MusicBee Portable ZIP or EXE. {instruction}")


def extract_sfx(exe: Path, destination: Path) -> None:
    if zipfile.is_zipfile(exe):
        safe_zip_extract(exe, destination)
        return
    seven_zip = next((shutil.which(c) for c in ("7z", "7zz", "7za") if shutil.which(c)), None)
    if not seven_zip:
        die("7-Zip is required to unpack the MusicBee self-extracting executable")
    # 7-Zip supports many NSIS/SFX formats; it does NOT necessarily support
    # every future MusicBee executable. Failure is reported rather than silently
    # launching an interactive Windows setup.
    command = [seven_zip, "x", "-y", f"-o{destination}", str(exe)]
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, check=True)


def extract_portable(archive: Path, staging: Path) -> Path:
    """Extract a portable archive and locate its application directory."""
    incoming = staging / "incoming"
    incoming.mkdir()
    if zipfile.is_zipfile(archive):
        safe_zip_extract(archive, incoming)
    else:
        if archive.suffix.lower() != ".exe":
            die("Supply an official MusicBee Portable ZIP or .exe")
        extract_sfx(archive, incoming)
    candidates = list(incoming.rglob("MusicBee.exe"))
    if not candidates:
        exes = [p for p in incoming.rglob("*.exe") if "musicbeeportable" in p.name.casefold()]
        if len(exes) != 1:
            die("Archive did not contain MusicBee Portable. Download the Portable Edition from "
                "https://www.getmusicbee.com/downloads/.")
        expanded = staging / "expanded"
        expanded.mkdir()
        extract_sfx(exes[0], expanded)
        candidates = list(expanded.rglob("MusicBee.exe"))
    if len(candidates) != 1:
        die("Could not uniquely locate MusicBee.exe after extraction; inspect the archive")
    source_dir = candidates[0].parent
    for path in source_dir.rglob("*"):
        if path.is_symlink() or not path.resolve().is_relative_to(source_dir.resolve()):
            die(f"Unsafe extracted entry: {path}")
    return source_dir


def update_portable(source_dir: Path, app: Path, staging: Path, keep_backup: bool) -> Path | None:
    """Stage an update, retaining user files and restoring the old tree on failure."""
    if app.is_symlink():
        die(f"Refusing to update a symlinked application directory: {app}")
    merged = staging / "updated"
    shutil.copytree(app, merged, symlinks=True)
    for source in source_dir.rglob("*"):
        if not source.is_file():
            continue
        rel = source.relative_to(source_dir)
        # Never copy the self-extractor's internal NSIS files.
        if rel.parts[0].casefold() == "$pluginsdir":
            continue
        current = merged
        for part in rel.parts[:-1]:
            current = matching_child(current, part)
            if current.is_symlink() or (current.exists() and not current.is_dir()):
                die(f"Refusing to traverse an unsafe update directory: {current}")
            current.mkdir(exist_ok=True)
        dst = matching_child(current, rel.name)
        # Keep WMA disabled when it was disabled before the update.
        if len(rel.parts) == 1 and rel.name.casefold() == "basswma.dll":
            disabled = matching_child(current, "basswma.dll.musicbee-disabled")
            if disabled.exists() and not dst.exists():
                dst = disabled
        if dst.is_symlink() or (dst.exists() and not dst.is_file()):
            die(f"Refusing to overwrite an unsafe update entry: {dst}")
        # Update program binaries; preserve existing settings, plugins and assets.
        replace = (len(rel.parts) == 1 and source.suffix.casefold() in (".exe", ".dll")
                   and rel.name.casefold() not in ("explorer.exe", "bass_aac.dll"))
        if not dst.exists() or replace:
            shutil.copy2(source, dst)
    if keep_backup:
        backup = Path(tempfile.mkdtemp(prefix="MusicBee.backup-", dir=app.parent))
        backup.rmdir()
    else:
        backup = staging / "previous"
    app.rename(backup)
    try:
        merged.rename(app)
    except OSError:
        backup.rename(app)
        raise
    print(f"MusicBee Portable updated at {app}")
    return backup if keep_backup else None


def unpack_portable(archive: Path, app: Path, *, update: bool = False, keep_backup: bool = True) -> Path | None:
    """Install or update MusicBee from a portable archive."""
    archive = archive.expanduser().resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="musicbee-stage-", dir=app.parent) as tmp:
        staging = Path(tmp)
        source_dir = extract_portable(archive, staging)
        if app.exists():
            if (app / "MusicBee.exe").is_file():
                if not update:
                    print("MusicBee is already installed; not overwriting it.")
                    return None
                return update_portable(source_dir, app, staging, keep_backup)
            if any(app.iterdir()):
                die(f"Refusing to replace an existing nonempty directory: {app}")
            app.rmdir()
        # Atomic directory rename, preserving MusicBee's portable subdirectories.
        shutil.move(str(source_dir), str(app))
    if not (app / "MusicBee.exe").is_file():
        die("Extraction did not produce C:\\MusicBee\\MusicBee.exe")
    print(f"MusicBee Portable extracted to {app}")
    return None
