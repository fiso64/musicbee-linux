"""Safe Wine path and music-directory mapping helpers."""

from __future__ import annotations

import getpass
import os
from pathlib import Path
import re
import tempfile

from .settings import die


def parse_windows_path(value: str) -> tuple[str, list[str]]:
    """Return (drive lowercase, path components). [] is a bare drive mapping.

    Only drive-qualified absolute paths and bare drives are accepted. This
    prevents any '..', UNC, device path, or accidental Wine system-root mapping.
    """
    if not re.fullmatch(r"[A-Za-z]:(?:[\\/].*)?", value, re.DOTALL):
        die(f"Invalid Windows path {value!r}. Use M: or e.g. C:\\Users\\oldname\\Music")
    drive = value[0].lower()
    rest = value[2:]
    if rest in ("", "\\", "/"):
        if drive == "c":
            die("C: cannot be mapped as a whole drive; select a directory below C:.")
        if drive == "z":
            die("Refusing to replace Wine's Z: drive (normally mapped to /).")
        return drive, []
    pieces = rest.replace("/", "\\").lstrip("\\").split("\\")
    if not pieces or any(
        not piece or piece in (".", "..") or piece.endswith((" ", "."))
        or any(c in piece for c in '<>:"|?*\x00\n\r')
        for piece in pieces
    ):
        die(f"Unsafe Windows path {value!r}.")
    if drive == "z":
        die("Refusing to change Wine's Z: drive.")
    if drive == "c" and pieces[0].casefold() in {"windows", "program files", "program files (x86)"}:
        die("Refusing to mount a music library inside Windows system/program directories.")
    return drive, pieces


def matching_child(parent: Path, name: str) -> Path:
    """Use an existing case-insensitive spelling, avoiding Users/ vs users/."""
    if not parent.is_dir():
        return parent / name
    matches = [entry for entry in parent.iterdir() if entry.name.casefold() == name.casefold()]
    if len(matches) > 1:
        die(f"Ambiguous case-insensitive directory names in {parent}: {name}")
    return matches[0] if matches else parent / name


def mapping_target(prefix: Path, value: str, *, create_parents: bool) -> Path:
    drive, pieces = parse_windows_path(value)
    if not pieces:
        return prefix / "dosdevices" / f"{drive}:"
    base = prefix / ("drive_c" if drive == "c" else f"drive_{drive}")
    if create_parents:
        if base.is_symlink():
            die(f"Refusing a symlinked Wine drive root: {base}")
        base.mkdir(parents=True, exist_ok=True)
        if drive != "c":
            make_drive_link(prefix, drive, base)
    current = base
    for part in pieces[:-1]:
        if current.is_symlink():
            die(f"Refusing to traverse symlinked parent directory: {current}")
        current = matching_child(current, part)
        if create_parents:
            if current.is_symlink():
                die(f"Refusing to traverse symlinked parent directory: {current}")
            current.mkdir(exist_ok=True)
    if current.is_symlink():
        die(f"Refusing to traverse symlinked parent directory: {current}")
    return matching_child(current, pieces[-1])


def symlink_points_to(link: Path, destination: Path) -> bool:
    return link.is_symlink() and link.resolve(strict=False) == destination.resolve(strict=False)


def make_drive_link(prefix: Path, drive: str, root: Path) -> None:
    devices = prefix / "dosdevices"
    devices.mkdir(parents=True, exist_ok=True)
    link = devices / f"{drive}:"
    if link.is_symlink() and symlink_points_to(link, root):
        return
    if link.exists() or link.is_symlink():
        die(f"The {drive.upper()}: Wine drive is already in use: {link}")
    link.symlink_to(root, target_is_directory=True)


def music_host_dir(value: str, prefix: Path) -> Path:
    host = Path(value).expanduser().resolve(strict=True)
    if not host.is_dir():
        die(f"Music location is not a directory: {host}")
    if host == prefix or prefix in host.parents or host in prefix.parents:
        die("The music directory must not contain or be inside the Wine prefix (prevents recursive links).")
    return host


def is_wine_default_music_link(prefix: Path, target: Path) -> bool:
    """Identify Wine's initial ~/Music shortcut in the current user's profile.

    Wine commonly pre-populates drive_c/users/<current-linux-user>/Music with a
    symlink to ~/Music. That shortcut is safe to replace with an explicitly
    requested library mapping; arbitrary symlinks are *not* safe to replace.
    This check also works if ~/Music has not yet been created.
    """
    try:
        relative = target.relative_to(prefix / "drive_c" / "users")
    except ValueError:
        return False
    parts = relative.parts
    return (
        len(parts) == 2
        and parts[0].casefold() == getpass.getuser().casefold()
        and parts[1].casefold() == "music"
        and symlink_points_to(target, Path.home() / "Music")
    )


def replace_symlink_atomically(target: Path, destination: Path) -> None:
    """Keep the original shortcut in place until its replacement is ready."""
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}-", dir=target.parent)
    os.close(fd)
    tmp = Path(temporary)
    try:
        tmp.unlink()
        tmp.symlink_to(destination, target_is_directory=True)
        os.replace(tmp, target)
    finally:
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()


def update_mapping(prefix: Path, host: Path, windows_path: str, old: dict) -> Path:
    """Create/update a link without replacing user data or unrelated links.

    The only unmanaged shortcut we replace is Wine's own default ~/Music
    shortcut in the current user's Wine profile. This does not touch ~/Music.
    """
    target = mapping_target(prefix, windows_path, create_parents=True)
    prev_path = old.get("windows_music_path")
    prev_host = Path(old["music_dir"]).expanduser() if old.get("music_dir") else None
    was_managed = (
        prev_host is not None and prev_path and
        prev_path.casefold().replace("/", "\\") == windows_path.casefold().replace("/", "\\")
        and symlink_points_to(target, prev_host)
    )
    if symlink_points_to(target, host):
        pass  # Already correctly mapped.
    elif target.is_symlink():
        wine_default = is_wine_default_music_link(prefix, target)
        if not (was_managed or wine_default):
            die(f"Refusing to replace an unrelated symlink: {target} -> {os.readlink(target)}")
        if wine_default:
            print(f"Replacing Wine's default Music shortcut: {target} -> {os.readlink(target)}")
        replace_symlink_atomically(target, host)
    elif target.is_dir():
        if any(target.iterdir()):
            die(f"Refusing to replace a nonempty Wine directory: {target}")
        target.rmdir()  # Wine may create an empty Music folder itself.
        target.symlink_to(host, target_is_directory=True)
    elif target.exists():
        die(f"Refusing to replace existing Wine file: {target}")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(host, target_is_directory=True)

    # Never modify a previously configured Windows path. Reconfiguration only
    # creates/updates the explicitly requested target. Music files are untouched.
    if prev_path and prev_path.casefold().replace("/", "\\") != windows_path.casefold().replace("/", "\\"):
        print(f"Note: Previous Wine mapping at {prev_path} was left unchanged.")
    return target


