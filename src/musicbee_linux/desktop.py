"""Desktop helpers for MusicBee on Linux."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

from . import settings
from .settings import die, read_config, write_config
from .utils import entrypoint, require_ready
from .wine import require_commands, wine_env


TOOL_BRIDGE_MARKER = "# musicbee-linux native tool bridge\n"


def tool_bridge_script(name: str, paths: dict[str, Path]) -> str:
    manager = paths["bin"].with_name("musicbee-manage")
    log = paths["state"] / "native-tools.log"
    return ("#!/bin/sh\n" + TOOL_BRIDGE_MARKER +
            f"exec {shlex.quote(str(manager))} tools-dispatch {shlex.quote(name)} \"$@\" 2>>{shlex.quote(str(log))}\n")


def refresh_tool_bridges(paths: dict[str, Path], config: dict) -> None:
    directory = paths["prefix"] / "drive_c" / "musicbee-linux-tools"
    if directory.is_symlink() or not directory.is_dir():
        return
    for name in native_tools(config):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
            continue
        bridge = directory / tool_bridge_name(name)
        if bridge.is_symlink() or not bridge.is_file():
            continue
        current = bridge.read_text(encoding="utf-8", errors="replace")
        script = tool_bridge_script(name, paths)
        if TOOL_BRIDGE_MARKER in current.splitlines(keepends=True)[:2] and current != script:
            bridge.write_text(script, encoding="utf-8")


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
            print("No native tools configured. Add one with: musicbee-manage tools add NAME 'COMMAND'")
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
        die("Provide a nonempty command, for example: musicbee-manage tools add picard 'flatpak run org.musicbrainz.Picard'.")
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
    launcher = paths["bin"].with_name("musicbee-manage")
    if not launcher.is_file():
        die(f"MusicBee launcher not found: {launcher}. Run musicbee-manage launcher first.")
    bridge.write_text(tool_bridge_script(name, paths), encoding="utf-8")
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
    require_commands("winepath", prefix=paths["prefix"])
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
    launcher = paths["bin"].with_name("musicbee-manage")
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


def install_self(binary: Path, state: Path) -> None:
    if str(binary).startswith("/usr/"):
        return  # RPM owns this launcher; never overwrite system files.
    source = entrypoint()
    binary.parent.mkdir(parents=True, exist_ok=True)
    if source != binary and (not binary.exists() or not source.samefile(binary)):
        # The CLI is copied here so the .desktop shortcut survives moving the download.
        if binary.exists() and binary.read_bytes() != source.read_bytes():
            print(f"Updating installed script at {binary}")
        shutil.copy2(source, binary)
    binary.chmod(binary.stat().st_mode | 0o755)
    manager = binary.with_name("musicbee-manage")
    if source != manager and (not manager.exists() or not source.samefile(manager)):
        shutil.copy2(source, manager)
    manager.chmod(manager.stat().st_mode | 0o755)
    modules = state / "lib"
    modules.mkdir(parents=True, exist_ok=True)
    source_package = Path(settings.__file__).resolve().parent
    target_package = modules / "musicbee_linux"
    if source_package != target_package.resolve():
        shutil.copytree(source_package, target_package, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if binary.parent.resolve() not in {Path(entry or '.').resolve() for entry in os.get_exec_path()}:
        print(f"Note: {binary.parent} is not on PATH; add it to your shell configuration to use musicbee commands.")


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
        print("Note: Install icoutils, then run 'musicbee-manage launcher' for the actual MusicBee icon.")
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


MUSIC_MIME_TYPES = (
    "audio/mpeg;audio/x-mp3;audio/x-flac;audio/flac;audio/x-wav;audio/wav;"
    "audio/aac;audio/mp4;audio/x-m4a;audio/ogg;audio/x-vorbis+ogg;audio/x-opus+ogg;"
    "audio/x-ape;audio/x-wavpack;audio/mpegurl;audio/x-mpegurl;"
    "application/vnd.apple.mpegurl;audio/x-scpls;"
)


def make_launcher(binary: Path, desktop: Path, icon: Path | None = None, *, setup_needed: bool = False) -> None:
    desktop.parent.mkdir(parents=True, exist_ok=True)
    action = "desktop-setup" if setup_needed else "desktop-launch"
    command = f"{desktop_quote(str(binary.with_name('musicbee-manage')))} {action}"
    desktop.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=MusicBee\n"
        "Comment=MusicBee via Wine\n"
        f"Exec={command} %f\n"
        f"Terminal={'true' if setup_needed else 'false'}\n"
        f"Icon={icon if icon and icon.is_file() else 'multimedia-audio-player'}\n"
        "Categories=AudioVideo;Audio;Player;\n"
        f"MimeType={MUSIC_MIME_TYPES}\n"
        "StartupNotify=true\n"
        "StartupWMClass=musicbee.exe\n",
        encoding="utf-8",
    )
    updater = shutil.which("update-desktop-database")
    if updater:
        try:
            result = subprocess.run([updater, str(desktop.parent)], check=False,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode:
                print(f"Warning: could not refresh desktop file associations in {desktop.parent}.", file=sys.stderr)
        except OSError as exc:
            print(f"Warning: could not refresh desktop file associations: {exc}", file=sys.stderr)
    else:
        print("Note: install desktop-file-utils to refresh desktop file associations.")


def show_desktop_error(message: str) -> None:
    """Show a launch error even when the desktop discards standard error."""
    for name, args in (
        ("kdialog", ["--title", "MusicBee", "--error", message]),
        ("zenity", ["--error", "--title=MusicBee", "--no-markup", f"--text={message}"]),
        ("notify-send", ["--urgency=critical", "MusicBee", message]),
    ):
        if shutil.which(name):
            try:
                if subprocess.run([name, *args], check=False).returncode == 0:
                    return
            except OSError:
                continue


def desktop_launch_with_log(paths: dict[str, Path], files: list[str]) -> int | None:
    """Capture desktop launch output, draining it even after the size limit."""
    log = paths["state"] / "desktop-launch.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    except OSError:
        return None  # Logging must not prevent MusicBee from starting.
    env = dict(os.environ, MUSICBEE_DESKTOP_LOG_WORKER="1")
    command = [sys.executable, str(entrypoint()), "--manage", "desktop-launch", *files]
    with os.fdopen(fd, "wb") as output:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        marker = b"\n[Log truncated at 2 MiB]\n"
        remaining = 2 * 1024 * 1024 - len(marker)
        truncated = False
        with process.stdout as stream:
            while chunk := os.read(stream.fileno(), 65536):
                if remaining:
                    data = chunk[:remaining]
                    try:
                        output.write(data)
                        output.flush()
                    except OSError:
                        remaining = 0
                    else:
                        remaining -= len(data)
                if not remaining and not truncated:
                    try:
                        output.write(marker)
                        output.flush()
                    except OSError:
                        pass
                    truncated = True
        return process.wait()


def regenerate_launcher(paths: dict[str, Path]) -> None:
    """Rebuild the user launcher, safely, without rerunning Wine setup."""
    install_self(paths["bin"], paths["state"])
    icon = extract_musicbee_icon(paths["app"], paths["state"])
    make_launcher(paths["bin"], paths["desktop"], icon,
                  setup_needed=not (paths["app"] / "MusicBee.exe").is_file())
    print(f"Desktop launcher: {paths['desktop']}")
    if icon:
        print(f"Desktop icon: {icon}")
    else:
        print("Desktop icon: generic placeholder (actual icon needs MusicBee.exe and icoutils)")
