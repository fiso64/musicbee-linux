#!/usr/bin/env python3
"""Unofficial MusicBee Portable for Linux, in an isolated Wine prefix.

Python standard library only; Linux dependencies come from the distro package.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import getpass
import hashlib
import json
import stat
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import PurePosixPath


class SetupError(Exception):
    pass


def locations() -> dict[str, Path]:
    home = Path.home()
    state = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")).expanduser() / "musicbee"
    prefix = state / "wineprefix"
    packaged = Path(__file__).resolve() == Path("/usr/libexec/musicbee/musicbee.py")
    launcher = Path("/usr/bin/musicbee") if packaged else home / ".local/bin/musicbee"
    return {
        "home": home, "state": state, "prefix": prefix,
        "app": prefix / "drive_c" / "MusicBee",
        "bin": launcher, "packaged": packaged,
        "desktop": home / ".local/share/applications/musicbee.desktop",
    }


def die(message: str) -> None:
    raise SetupError(message)


def require_commands(*names: str) -> None:
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        die(f"Missing required commands: {', '.join(missing)}. Install Wine, Winetricks and cabextract through your package manager.")


def wine_env(prefix: Path, *, init: bool = False) -> dict[str, str]:
    env = dict(os.environ)
    env["WINEPREFIX"] = str(prefix)
    env.setdefault("WINEDEBUG", "-all")
    # External overrides would compromise repeatability. Apply the required
    # bootstrap override only during creation, not during .NET installation.
    env.pop("WINEDLLOVERRIDES", None)
    env.pop("WINEARCH", None)
    if init:
        env["WINEARCH"] = "win64"
        env["WINEDLLOVERRIDES"] = "mscoree,mshtml="
    return env


def call(command: list[str], env: dict[str, str] | None = None, cwd: Path | None = None) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, check=True, env=env, cwd=cwd)


def read_config(state: Path) -> dict:
    config_path = state / "config.json"
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                die(f"Unexpected config format in {config_path}: expected JSON object")
            return config
        except (ValueError, OSError) as exc:
            die(f"Could not read {config_path}: {exc}")
    return {}


def write_config(state: Path, config: dict) -> None:
    state.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=state)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(config, stream, indent=2, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
        os.replace(tmp, state / "config.json")
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


TWEAKS = {
    "audio-latency": ("off", ("on", "off"),
        "Set PulseAudio-compatible latency to 60 ms to help with stuttering. "
        "May increase audio delay; off leaves the environment unchanged."),
    "allfonts": ("off", ("on", "off"),
        "Install Winetricks allfonts for more font choices. Downloads can take a long time. "
        "Turning this off does not remove fonts already installed."),
    "font-smoothing": ("off", ("on", "off"),
        "Enable RGB font smoothing in the MusicBee Wine prefix. "
        "Turning it off disables Wine font smoothing."),
    "gdiplus": ("native", ("native", "wine"),
        "Native GDI+ displays blurred backgrounds correctly but may not render grayscale JPEG artwork. "
        "Wine GDI+ supports grayscale JPEG artwork but may not blur backgrounds correctly."),
    "native-file-manager": ("on", ("on", "off"),
        "Open MusicBee folders in a Linux file manager instead of Wine Explorer. "
        "File selection depends on desktop support; restart MusicBee after changing this."),
}


def tweak_value(config: dict, name: str) -> str:
    tweaks = config.get("tweaks", {})
    if not isinstance(tweaks, dict):
        die("Invalid tweaks configuration: expected an object.")
    value = tweaks.get(name, TWEAKS[name][0])
    if value not in TWEAKS[name][1]:
        die(f"Invalid saved value for {name}: {value!r}.")
    return value


def apply_font_tweak(name: str, value: str, prefix: Path) -> None:
    if name == "allfonts" and value == "off":
        return
    require_commands("winetricks")
    lock_path = prefix / ".musicbee.launch.lock"
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            die("Close MusicBee before changing font settings.")
        verb = "allfonts" if name == "allfonts" else (
            "fontsmooth=rgb" if value == "on" else "fontsmooth=disable")
        call(["winetricks", "-q", verb], env=wine_env(prefix))


def configure(args: argparse.Namespace, paths: dict[str, Path]) -> None:
    config = read_config(paths["state"])
    color = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"
    def paint(value: str, code: str) -> str:
        return f"\033[{code}m{value}\033[0m" if color else value
    if args.option is None:
        print(paint("MusicBee settings", "1;36"))
        for name, (_, _, description) in TWEAKS.items():
            print(f"  {paint(name, '1')}  {paint(tweak_value(config, name), '32')}")
            print(f"    {description}")
            print()
        print("Use: musicbee config <option> info | <value>")
        return
    name = args.option
    if name not in TWEAKS:
        die(f"Unknown setting {name!r}. Run 'musicbee config' to list settings.")
    default, values, description = TWEAKS[name]
    current = tweak_value(config, name)
    if args.value in (None, "info"):
        print(paint(name, "1;36"))
        print(description)
        print(f"\nValues:  {', '.join(values)}")
        print(f"Default: {default}")
        print(f"Current: {current}")
        return
    value = args.value.casefold()
    if value not in values:
        die(f"Invalid value for {name}: {args.value!r}. Choose: {', '.join(values)}.")
    if name in ("allfonts", "font-smoothing") and (paths["prefix"] / ".musicbee-bootstrap-complete").is_file():
        apply_font_tweak(name, value, paths["prefix"])
    config.setdefault("tweaks", {})[name] = value
    write_config(paths["state"], config)
    print(f"{name} = {value}")
    if name == "allfonts" and value == "off":
        print("Previously installed fonts remain in the Wine prefix.")
    if name in ("allfonts", "font-smoothing") and not (paths["prefix"] / ".musicbee-bootstrap-complete").is_file():
        print("The setting will be applied during initial setup.")
    else:
        print("Restart MusicBee to apply this setting.")


TOOL_BRIDGE_MARKER = "# musicbee-linux native tool bridge\n"


def tool_bridge_name(name: str) -> str:
    return f"{name}.exe"


def native_tools(config: dict) -> dict:
    tools = config.get("native_tools", {})
    if not isinstance(tools, dict):
        die("Invalid native tools configuration: expected an object.")
    return tools


def tool_instructions(name: str) -> None:
    print("In MusicBee, open Preferences > Tools > External applications and add:")
    print(f"  {'Name:':<18} {name}")
    print(f"  {'Application path:':<18} C:\\musicbee-linux-tools\\{tool_bridge_name(name)}")
    print(f'  {"Parameters:":<18} "<URL>"')
    print("Then select tracks and use Send to > External tools.")


def manage_tools(args: argparse.Namespace, paths: dict[str, Path]) -> None:
    config = read_config(paths["state"])
    registered = native_tools(config)
    if args.action == "list":
        if not registered:
            print("No native tools configured. Add one with: musicbee tools add NAME 'COMMAND'")
        for name, command in sorted(registered.items()):
            print(f"{name}: {shlex.join(command)}")
        return
    name = args.name
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
        die("Tool name must contain only letters, digits, underscores or hyphens.")
    if args.action == "remove":
        if name not in registered:
            die(f"Unknown native tool: {name}")
        del registered[name]
        config["native_tools"] = registered
        write_config(paths["state"], config)
        print(f"Removed {name}. Remove its external application entry in MusicBee too.")
        return
    command = shlex.split(args.tool_command)
    if not command or not command[0]:
        die("Provide a nonempty command, for example: musicbee tools add picard 'flatpak run org.musicbrainz.Picard'.")
    require_ready(paths["prefix"])
    drive = paths["prefix"] / "drive_c"
    if drive.is_symlink() or not drive.is_dir():
        die(f"Expected a normal Wine C: directory: {drive}")
    bridge_dir = drive / "musicbee-linux-tools"
    if bridge_dir.is_symlink() or (bridge_dir.exists() and not bridge_dir.is_dir()):
        die(f"Expected a normal native tool directory: {bridge_dir}")
    bridge_dir.mkdir(exist_ok=True)
    bridge = bridge_dir / tool_bridge_name(name)
    if bridge.is_symlink() or (bridge.exists() and (not bridge.is_file() or TOOL_BRIDGE_MARKER not in bridge.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)[:2])):
        die(f"Refusing to overwrite an existing file: {bridge}")
    launcher = paths["bin"]
    if not launcher.is_file():
        die(f"MusicBee launcher not found: {launcher}. Run musicbee launcher first.")
    log = paths["state"] / "native-tools.log"
    bridge.write_text("#!/bin/sh\n" + TOOL_BRIDGE_MARKER +
                      f"exec {shlex.quote(str(launcher))} tools-dispatch {shlex.quote(name)} \"$@\" 2>>{shlex.quote(str(log))}\n",
                      encoding="utf-8")
    bridge.chmod(0o755)
    registered[name] = command
    config["native_tools"] = registered
    write_config(paths["state"], config)
    if not shutil.which(command[0]):
        print(f"Warning: command not found on PATH: {command[0]}", file=sys.stderr)
    print(f"Configured {name}: {shlex.join(command)}")
    tool_instructions(name)


def dispatch_tool(name: str, windows_paths: list[str], paths: dict[str, Path]) -> None:
    command = native_tools(read_config(paths["state"])).get(name)
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        die(f"Native tool is not configured: {name}")
    if not windows_paths:
        die("No selected files were passed to the native tool.")
    require_commands("winepath")
    translated = []
    for windows_path in windows_paths:
        result = subprocess.run(["winepath", "-u", windows_path], env=wine_env(paths["prefix"]),
                                text=True, capture_output=True, check=False)
        if result.returncode or not result.stdout.strip():
            die(f"Could not translate {windows_path!r}: {result.stderr.strip()}")
        path = Path(result.stdout.rstrip("\r\n"))
        try:
            translated.append(str(path.resolve(strict=True)))
        except (OSError, RuntimeError) as exc:
            die(f"Cannot access {windows_path!r}: {exc}")
    env = dict(os.environ)
    for key in ("WINEPREFIX", "WINEARCH", "WINEDEBUG", "WINEDLLOVERRIDES"):
        env.pop(key, None)
    subprocess.Popen([*command, *translated], env=env, start_new_session=True)


EXPLORER_MARKER = "# musicbee-linux explorer redirect\n"


def configure_explorer_redirect(paths: dict[str, Path], enabled: bool) -> None:
    """Manage only our own application-local explorer.exe."""
    app = paths["app"]
    target = app / "explorer.exe"
    other = next((entry for entry in app.iterdir()
                  if entry.name.casefold() == "explorer.exe" and entry.name != target.name), None)
    if other:
        print(f"Warning: leaving existing Explorer untouched: {other}", file=sys.stderr)
        return
    if target.is_symlink() or (target.exists() and (not target.is_file() or
       EXPLORER_MARKER not in target.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)[:2])):
        print(f"Warning: leaving existing Explorer untouched: {target}", file=sys.stderr)
        return
    if not enabled:
        if target.exists():
            target.unlink()
        return
    launcher = paths["bin"]
    if not launcher.is_file():
        print(f"Warning: native file manager unavailable; launcher missing: {launcher}", file=sys.stderr)
        return
    log = paths["state"] / "explorer-redirect.log"
    script = ("#!/bin/sh\n" + EXPLORER_MARKER +
              f"exec {shlex.quote(str(launcher))} explorer-dispatch \"$@\" 2>>{shlex.quote(str(log))}\n")
    if not target.exists() or target.read_text(encoding="utf-8") != script:
        target.write_text(script, encoding="utf-8")
    target.chmod(0o755)


def explorer_target(arguments: list[str]) -> tuple[bool, str] | None:
    if len(arguments) == 1:
        arg = arguments[0]
        match = re.match(r"^/(select|root),\s*(.+)$", arg, re.IGNORECASE)
        if match:
            return match.group(1).casefold() == "select", match.group(2).strip('"')
        if re.match(r"^[A-Za-z]:[\\/]", arg):
            return False, arg
    elif len(arguments) == 2 and arguments[0].rstrip(',').casefold() in ("/select", "/root"):
        return arguments[0].rstrip(',').casefold() == "/select", arguments[1].strip('"')
    return None


def dispatch_explorer(arguments: list[str], paths: dict[str, Path]) -> None:
    debug = os.environ.get("MUSICBEE_EXPLORER_DEBUG") == "1"
    if debug:
        print(f"Explorer arguments: {arguments!r}", file=sys.stderr, flush=True)
    target = explorer_target(arguments)
    if debug:
        print(f"Parsed target: {target!r}", file=sys.stderr, flush=True)
    env = wine_env(paths["prefix"])
    if target:
        select, windows_path = target
        result = subprocess.run(["winepath", "-u", windows_path], env=env,
                                text=True, capture_output=True, check=False)
        if debug:
            print(f"winepath: status={result.returncode}, output={result.stdout!r}, error={result.stderr!r}",
                  file=sys.stderr, flush=True)
        if result.returncode == 0 and result.stdout.strip():
            path = Path(result.stdout.rstrip("\r\n")).resolve()
            # MusicBee can pass the file directly without Explorer's /select.
            # In that case revealing the file is the useful interpretation.
            if (select or path.is_file()) and shutil.which("gdbus"):
                try:
                    result = subprocess.run([
                        "gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
                        "--object-path", "/org/freedesktop/FileManager1", "--method",
                        "org.freedesktop.FileManager1.ShowItems", f"['{path.as_uri()}']", "",
                    ], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=5)
                    if debug:
                        print(f"FileManager1.ShowItems: status={result.returncode}", file=sys.stderr, flush=True)
                    if result.returncode == 0:
                        return
                except (OSError, subprocess.TimeoutExpired):
                    pass
            folder = path.parent if select or path.is_file() else path
            if shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", str(folder)], env=env, start_new_session=True)
                return
    subprocess.Popen(["wine", r"C:\windows\explorer.exe", *arguments], env=env, start_new_session=True)


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


def install_self(binary: Path) -> None:
    if str(binary).startswith("/usr/"):
        return  # RPM owns this launcher; never overwrite system files.
    source = Path(__file__).resolve()
    binary.parent.mkdir(parents=True, exist_ok=True)
    if source != binary and (not binary.exists() or not source.samefile(binary)):
        # The CLI is copied here so the .desktop shortcut survives moving the download.
        if binary.exists() and binary.read_bytes() != source.read_bytes():
            print(f"Updating installed script at {binary}")
        shutil.copy2(source, binary)
    binary.chmod(binary.stat().st_mode | 0o755)


def desktop_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`").replace("%", "%%") + '"'


def png_dimensions(path: Path) -> tuple[int, int] | None:
    """Validate PNG header and retrieve dimensions without third-party modules."""
    with path.open("rb") as src:
        header = src.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        return None
    width = int.from_bytes(header[16:20], "big")
    height = int.from_bytes(header[20:24], "big")
    return (width, height) if 0 < width <= 4096 and 0 < height <= 4096 else None


def extract_musicbee_icon(app: Path, state: Path) -> Path | None:
    """Convert MusicBee.exe's icon into a Linux PNG; leave previous icon intact on failure."""
    exe = app / "MusicBee.exe"
    icon = state / "musicbee.png"
    if not exe.is_file():
        return icon if icon.is_file() else None
    if not shutil.which("wrestool") or not shutil.which("icotool"):
        print("Note: Install icoutils, then run 'musicbee launcher' for the actual MusicBee icon.")
        return icon if icon.is_file() else None

    with tempfile.TemporaryDirectory(prefix="musicbee-icon-") as temp:
        tmp = Path(temp)
        ico_dir = tmp / "ico"
        png_dir = tmp / "png"
        ico_dir.mkdir()
        png_dir.mkdir()
        extracted = subprocess.run(
            ["wrestool", "-x", "-t", "14", f"--output={ico_dir}", str(exe)],
            capture_output=True, text=True, check=False,
        )
        if extracted.returncode != 0:
            print(f"Note: Could not extract MusicBee's executable icon: {extracted.stderr.strip()}")
            return icon if icon.is_file() else None
        for ico in sorted(ico_dir.glob("*.ico")):
            converted = subprocess.run(
                ["icotool", "-x", f"--output={png_dir}", str(ico)],
                capture_output=True, text=True, check=False,
            )
            if converted.returncode != 0:
                print(f"Note: Could not convert an icon: {converted.stderr.strip()}")
        candidates = []
        for png in png_dir.glob("*.png"):
            dimensions = png_dimensions(png)
            if dimensions:
                w, h = dimensions
                candidates.append((w * h, min(w, h), png.stat().st_size, png))
        if not candidates:
            print("Note: No usable PNG icon found in MusicBee.exe; retaining the fallback icon.")
            return icon if icon.is_file() else None
        best = max(candidates, key=lambda it: it[:3])[-1]
        state.mkdir(parents=True, exist_ok=True)
        fd, temp_icon = tempfile.mkstemp(prefix=".musicbee-icon-", suffix=".png", dir=state)
        try:
            with os.fdopen(fd, "wb") as out, best.open("rb") as src:
                shutil.copyfileobj(src, out)
            os.replace(temp_icon, icon)
        finally:
            if os.path.exists(temp_icon):
                os.unlink(temp_icon)
    print(f"MusicBee icon: {icon}")
    return icon


def make_launcher(binary: Path, desktop: Path, icon: Path | None = None, *, setup_needed: bool = False) -> None:
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=MusicBee\n"
        "Comment=MusicBee via Wine\n"
        f"Exec={desktop_quote(str(binary))} {'desktop-setup' if setup_needed else 'run'}\n"
        f"Terminal={'true' if setup_needed else 'false'}\n"
        f"Icon={icon if icon and icon.is_file() else 'multimedia-audio-player'}\n"
        "Categories=AudioVideo;Audio;Player;\n"
        "StartupNotify=true\n"
        "StartupWMClass=musicbee.exe\n",
        encoding="utf-8",
    )


def record_environment(state: Path, prefix: Path) -> None:
    lines = [f"Recorded: {datetime.now(timezone.utc).isoformat()}"]
    for cmd in (["wine", "--version"], ["winetricks", "--version"]):
        if shutil.which(cmd[0]):
            result = subprocess.run(cmd, env=wine_env(prefix), text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
            lines.append("$ " + " ".join(cmd) + "\n" + result.stdout.strip())
    (state / "environment.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_bootstrap(prefix: Path, *, refresh: bool) -> None:
    require_commands("wine", "wineboot", "wineserver", "winetricks", "cabextract")
    prefix.parent.mkdir(parents=True, exist_ok=True)
    if not (prefix / "system.reg").is_file():
        print("Creating dedicated 64-bit/WoW64 Wine prefix ...")
        call(["wineboot", "--init"], env=wine_env(prefix, init=True))
        call(["wineserver", "-w"], env=wine_env(prefix))
    marker = prefix / ".musicbee-bootstrap-complete"
    if not marker.exists() or refresh:
        print("Installing .NET 4.8 and MusicBee dependencies (interactive prompts may appear) ...")
        call(["winetricks", "-q", "dotnet48", "xmllite", "corefonts", "cjkfonts", "gdiplus"],
             env=wine_env(prefix))
        call(["winetricks", "-q", "win10"], env=wine_env(prefix))
        marker.touch()
    else:
        print("Existing Wine prefix found; skipping Winetricks dependencies.")


def setup(args: argparse.Namespace, paths: dict[str, Path]) -> None:
    state, prefix = paths["state"], paths["prefix"]
    old = read_config(state)
    source = args.music_dir or old.get("music_dir") or str(paths["home"] / "Music")
    winpath = args.windows_music_path or old.get("windows_music_path")
    if not winpath:
        winpath = f"C:\\Users\\{getpass.getuser()}\\Music"
    parse_windows_path(winpath)  # Fail before Wine does any work.
    if not args.music_dir and not old.get("music_dir") and Path(source).expanduser() == paths["home"] / "Music":
        Path(source).expanduser().mkdir(parents=True, exist_ok=True)
    try:
        host = music_host_dir(source, prefix)
    except (OSError, RuntimeError) as exc:
        die(f"Cannot access music directory {source!r}: {exc}")
    print(f"Linux music directory: {host}")
    print(f"Windows library location: {winpath}")
    ensure_bootstrap(prefix, refresh=args.refresh_dependencies)
    if tweak_value(old, "allfonts") == "on":
        apply_font_tweak("allfonts", "on", prefix)
    if tweak_value(old, "font-smoothing") == "on":
        apply_font_tweak("font-smoothing", "on", prefix)
    target = update_mapping(prefix, host, winpath, old)
    install_self(paths["bin"])
    make_launcher(paths["bin"], paths["desktop"], extract_musicbee_icon(paths["app"], state),
                  setup_needed=not (paths["app"] / "MusicBee.exe").is_file())
    print(f"Desktop launcher: {paths['desktop']}")
    write_config(state, {**old, "schema": 1, "music_dir": str(host), "windows_music_path": winpath})
    record_environment(state, prefix)
    print(f"Mapped {winpath} -> {host} (Wine link: {target})")
    print("\nWine setup complete.")


def require_ready(prefix: Path) -> None:
    if not (prefix / ".musicbee-bootstrap-complete").is_file():
        die("Wine prefix not initialized; run: musicbee init")


def validate_mapping(paths: dict[str, Path], config: dict | None = None) -> None:
    if config is None:
        config = read_config(paths["state"])
    if config.get("music_dir") and config.get("windows_music_path"):
        target = mapping_target(paths["prefix"], config["windows_music_path"], create_parents=False)
        source = Path(config["music_dir"])
        if not source.is_dir() or not symlink_points_to(target, source):
            die(f"Music mapping missing or inaccessible: {config['windows_music_path']} -> {source}. Run setup again.")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install(args: argparse.Namespace, paths: dict[str, Path]) -> None:
    source = Path(args.installer).expanduser().resolve(strict=True)
    if not source.is_file():
        die(f"Archive is not a file: {source}")
    if not (paths["app"] / "MusicBee.exe").is_file():
        first_launch(argparse.Namespace(music_dir=None, wine_music_path=None, archive=str(source),
                                        accept_license=False, no_launch=True), paths)
        print("Installed. Launch from your app menu or run: musicbee")
        return
    validate_portable_archive(source)
    require_ready(paths["prefix"])
    validate_mapping(paths)
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
    lock_path = paths["prefix"] / ".musicbee.launch.lock"
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            die("Close MusicBee before updating it.")
        backup_path = unpack_portable(source, paths["app"], update=True, keep_backup=keep_backup)
        write_json_atomic(paths["state"] / "portable-source.json", {"sha256": digest, "archive": source.name})
    regenerate_launcher(paths)
    if backup_path:
        print(f"Previous application backup: {backup_path}")
    print("Updated. Launch from your app menu or run: musicbee")


def regenerate_launcher(paths: dict[str, Path]) -> None:
    """Rebuild the user launcher, safely, without rerunning Wine setup."""
    install_self(paths["bin"])
    icon = extract_musicbee_icon(paths["app"], paths["state"])
    make_launcher(paths["bin"], paths["desktop"], icon,
                  setup_needed=not (paths["app"] / "MusicBee.exe").is_file())
    print(f"Desktop launcher: {paths['desktop']}")
    if icon:
        print(f"Desktop icon: {icon}")
    else:
        print("Desktop icon: generic placeholder (actual icon needs MusicBee.exe and icoutils)")


def run(args: argparse.Namespace, paths: dict[str, Path]) -> None:
    prefix, app = paths["prefix"], paths["app"]
    require_ready(prefix)
    config = read_config(paths["state"])
    validate_mapping(paths, config)
    require_commands("wine")
    if not (app / "MusicBee.exe").is_file():
        die(r"MusicBee.exe not found at C:\MusicBee; use the install command first.")
    bass = app / "basswma.dll"
    disabled = app / "basswma.dll.musicbee-disabled"
    if os.environ.get("MUSICBEE_ENABLE_WMA") == "1":
        if not bass.exists() and disabled.exists():
            disabled.rename(bass)
    elif bass.exists():
        if disabled.exists():
            die(f"Both {bass.name} and {disabled.name} exist; resolve manually.")
        bass.rename(disabled)
    env = wine_env(prefix)
    gdiplus = "n,b" if tweak_value(config, "gdiplus") == "native" else "b"
    env["WINEDLLOVERRIDES"] = f"*gdiplus={gdiplus};gdiplus={gdiplus}"
    if tweak_value(config, "audio-latency") == "on":
        env["PULSE_LATENCY_MSEC"] = "60"
    lock_path = prefix / ".musicbee.launch.lock"
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            die("MusicBee already appears to be running from this prefix.")
        configure_explorer_redirect(paths, tweak_value(config, "native-file-manager") == "on")
        extra = args.musicbee_args
        if extra and extra[0] == "--":
            extra = extra[1:]
        call(["wine", "MusicBee.exe", *extra], env=env, cwd=app)


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
    if not shutil.which("wine"):
        return "Wine not found"
    result = subprocess.run(["wine", "--version"], env=wine_env(prefix),
                            text=True, capture_output=True, check=False)
    return (result.stdout or result.stderr).strip()


def media_controls(args: argparse.Namespace, paths: dict[str, Path]) -> None:
    plugin = paths["app"] / "Plugins" / "mb_MPRISBee.dll"
    version = wine_version(paths["prefix"])
    print(f"Wine: {version}")
    if "staging" not in version.casefold():
        print("WARNING: MPRISBee v1.0.3 needs Wine Staging or equivalent AF_UNIX patches. "
              "Fedora's standard Wine build might not work. This tool does not replace your Wine packages.")
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
        lock_path = paths["prefix"] / ".musicbee.launch.lock"
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                die("Close MusicBee before repairing MPRISBee dependencies.")
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
    lock_path = paths["prefix"] / ".musicbee.launch.lock"
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            die("Close MusicBee before installing or updating MPRISBee.")
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
              "Retry with: musicbee media-controls install", file=sys.stderr)


def backup(args: argparse.Namespace, paths: dict[str, Path]) -> None:
    require_ready(paths["prefix"])
    output_dir = Path(args.output_dir or paths["state"] / "backups").expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"musicbee-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.tar.gz"
    lock_path = paths["prefix"] / ".musicbee.launch.lock"
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            die("Close MusicBee before backing up its prefix and database.")
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
    config = read_config(paths["state"])
    print(f"Prefix: {prefix}")
    print(f"Bootstrapped: {(prefix / '.musicbee-bootstrap-complete').exists()}")
    print(f"MusicBee installed: {(paths['app'] / 'MusicBee.exe').exists()}")
    print(f"Music directory: {config.get('music_dir', '(not configured)')}")
    print(f"Windows path: {config.get('windows_music_path', '(not configured)')}")
    if config.get("windows_music_path"):
        target = mapping_target(prefix, config["windows_music_path"], create_parents=False)
        print(f"Wine link: {target} -> {os.readlink(target) if target.is_symlink() else '(missing)'}")



# Copyright of the MusicBee application and installer belongs to its upstream
# owner. This independent integration does not imply an official Linux release.
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
        die("Installation cancelled. To accept from a terminal: musicbee init --accept-license")
    state.mkdir(parents=True, exist_ok=True)
    write_json_atomic(marker, {"accepted": True, "recorded_at": datetime.now(timezone.utc).isoformat()})


def write_json_atomic(target: Path, value: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".tmp-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, indent=2)
        os.replace(name, target)
    finally:
        if os.path.lexists(name):
            os.unlink(name)


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
    call([seven_zip, "x", "-y", f"-o{destination}", str(exe)])


def unpack_portable(archive: Path, app: Path, *, update: bool = False, keep_backup: bool = True) -> Path | None:
    """Safely unpack the portable release into the user's Wine C:\\MusicBee.

    Updates retain user files and keep the previous application directory as a backup.
    """
    archive = archive.expanduser().resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="musicbee-stage-", dir=app.parent) as tmp:
        staging = Path(tmp)
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
        if app.exists():
            if (app / "MusicBee.exe").is_file():
                if not update:
                    print("MusicBee is already installed; not overwriting it.")
                    return None
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
                               and rel.name.casefold() != "explorer.exe")
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
            if any(app.iterdir()):
                die(f"Refusing to replace an existing nonempty directory: {app}")
            app.rmdir()
        # Atomic directory rename, preserving MusicBee's portable subdirectories.
        shutil.move(str(source_dir), str(app))
    if not (app / "MusicBee.exe").is_file():
        die("Extraction did not produce C:\\MusicBee\\MusicBee.exe")
    print(f"MusicBee Portable extracted to {app}")
    return None


def packaged_archive(paths: dict[str, Path], override: str | None = None) -> Path:
    if override:
        archive = Path(override).expanduser()
    elif os.environ.get("MUSICBEE_ARCHIVE_OVERRIDE"):
        archive = Path(os.environ["MUSICBEE_ARCHIVE_OVERRIDE"]).expanduser()
    else:
        die("Download the MusicBee Portable ZIP from https://www.getmusicbee.com/downloads/\n"
            "Then run: musicbee install /path/to/MusicBeePortable.zip")
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
                setup(argparse.Namespace(
                    music_dir=args.music_dir,
                    windows_music_path=args.wine_music_path,
                    refresh_dependencies=False,
                ), paths)
            else:
                validate_mapping(paths)
            unpack_portable(archive, paths["app"])
            regenerate_launcher(paths)
            write_json_atomic(state / "portable-source.json", {"sha256": sha256(archive), "archive": archive.name})
            ensure_media_controls(paths)
        else:
            print("Existing MusicBee installation found.")
    if not args.no_launch:
        run(argparse.Namespace(musicbee_args=[]), paths)


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Unofficial MusicBee Portable on Linux using Wine.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Download MusicBee Portable, then run: musicbee install /path/to/MusicBeePortable.zip\n"
            "To customize music mapping: musicbee setup --music-dir ~/Music\n"
            "Media controls: installed automatically; check with musicbee media-controls status\n"
            "Keep upstream MusicBee issues separate from Linux packaging issues."
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="{init,setup,install,run,config,tools,backup,launcher,media-controls,status}")
    sub.add_parser("desktop-setup")
    explorer = sub.add_parser("explorer-dispatch")
    explorer.add_argument("arguments", nargs=argparse.REMAINDER)
    dispatch = sub.add_parser("tools-dispatch")
    dispatch.add_argument("name")
    dispatch.add_argument("files", nargs="+")
    ini = sub.add_parser("init", help="Set up Wine and install MusicBee from a downloaded archive")
    ini.add_argument("--music-dir", help="Host music directory (default: ~/Music)")
    ini.add_argument("--wine-music-path", help=r"Windows library path (default: C:\Users\<user>\Music)")
    ini.add_argument("--archive", help="Downloaded MusicBee Portable ZIP or EXE")
    ini.add_argument("--accept-license", action="store_true", help="Explicitly accept MusicBee EULA for unattended install")
    ini.add_argument("--no-launch", action="store_true", help="Do not start MusicBee after installing")
    setp = sub.add_parser("setup", help="Initialize Wine and configure the Linux -> Windows music path")
    setp.add_argument("--music-dir", metavar="LINUX_DIR", help="Existing directory containing your music (default: ~/Music)")
    setp.add_argument("--wine-music-path", "--windows-music-path", dest="windows_music_path", metavar="WIN_PATH", help=r"Absolute location inside Wine, e.g. C:\Users\oldname\Music or M:")
    setp.add_argument("--refresh-dependencies", action="store_true", help="Re-run winetricks for an existing prefix")
    ins = sub.add_parser("install", help="Install or update MusicBee from a downloaded portable archive")
    ins.add_argument("installer", help="Path to the MusicBee Portable ZIP or EXE")
    backup_options = ins.add_mutually_exclusive_group()
    backup_options.add_argument("--backup", dest="keep_backup", action="store_true", default=None,
                                help="Keep a backup during updates without prompting")
    backup_options.add_argument("--no-backup", dest="keep_backup", action="store_false",
                                help="Do not keep an update backup; skip the prompt")
    runp = sub.add_parser("run", help="Run MusicBee; show installation instructions if missing")
    runp.add_argument("musicbee_args", nargs=argparse.REMAINDER)
    conf = sub.add_parser("config", help="View or change Wine and playback tweaks")
    conf.add_argument("option", nargs="?", help="Setting to inspect or change")
    conf.add_argument("value", nargs="?", help="New value, or info for details")
    toolp = sub.add_parser("tools", help="Configure native Linux external tools")
    tool_actions = toolp.add_subparsers(dest="action", required=True)
    add_tool = tool_actions.add_parser("add", help="Register a tool using a Linux command")
    add_tool.add_argument("name", help="Short name used in MusicBee")
    add_tool.add_argument("tool_command", metavar="COMMAND", help="Linux command and arguments, quoted as one argument")
    tool_actions.add_parser("list", help="List configured tools")
    remove_tool = tool_actions.add_parser("remove", help="Remove a configured tool")
    remove_tool.add_argument("name")
    bk = sub.add_parser("backup", help="Back up the Wine prefix, application and settings (not the music)")
    bk.add_argument("output_dir", nargs="?", help="Backup destination (default: ~/.local/share/musicbee/backups)")
    sub.add_parser("launcher", help="Create/update a desktop launcher and extract the icon")
    mpris = sub.add_parser("media-controls", help="Install/test native Linux MPRIS support via the MusicBee plugin")
    mpris.add_argument("action", choices=("install", "repair", "status"))
    mpris.add_argument("--archive", metavar="ZIP", help="Use a locally downloaded MPRISBee release ZIP instead of downloading")
    mpris.add_argument("--sha256", metavar="HEX", help="Optional expected ZIP SHA-256 for integrity checking")
    sub.add_parser("status", help="Show configured music mapping and installation state")
    return parser


def main() -> int:
    parser = create_parser()
    args = parser.parse_args()
    if args.command is None:
        args = parser.parse_args(["run"])
    if os.geteuid() == 0:
        print("Error: run as your desktop user, not root.", file=sys.stderr)
        return 1
    paths = locations()
    try:
        if args.command == "setup":
            setup(args, paths)
        elif args.command == "install":
            install(args, paths)
        elif args.command == "run":
            if not paths["app"].joinpath("MusicBee.exe").is_file():
                first_launch(argparse.Namespace(music_dir=None, wine_music_path=None, archive=None, accept_license=False, no_launch=True), paths)
            run(args, paths)
        elif args.command == "init":
            first_launch(args, paths)
        elif args.command == "desktop-setup":
            first_launch(argparse.Namespace(music_dir=None, wine_music_path=None, archive=None,
                                            accept_license=False, no_launch=True), paths)
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "run"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        elif args.command == "backup":
            backup(args, paths)
        elif args.command == "config":
            configure(args, paths)
        elif args.command == "tools":
            manage_tools(args, paths)
        elif args.command == "tools-dispatch":
            dispatch_tool(args.name, args.files, paths)
        elif args.command == "explorer-dispatch":
            dispatch_explorer(args.arguments, paths)
        elif args.command == "launcher":
            regenerate_launcher(paths)
        elif args.command == "status":
            status(paths)
        elif args.command == "media-controls":
            media_controls(args, paths)
        return 0
    except (SetupError, OSError, subprocess.CalledProcessError, zipfile.BadZipFile, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if args.command == "desktop-setup" and sys.stdin.isatty():
            input("Press Enter to close this window.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
