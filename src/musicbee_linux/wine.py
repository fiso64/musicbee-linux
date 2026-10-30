"""Wine helpers for MusicBee on Linux."""

from __future__ import annotations

import argparse
import fcntl
import os
import platform
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

from . import __version__
from .settings import TWEAKS, SetupError, die, read_config, tweak_value, write_config
from .utils import call, locations, maintenance_lock, require_ready


def musicbee_version(app: Path) -> str:
    exe = app / "MusicBee.exe"
    if not exe.is_file():
        return "not installed"
    if not shutil.which("wrestool"):
        return "unknown (wrestool unavailable)"
    try:
        result = subprocess.run(["wrestool", "--raw", "-x", "-t", "16", "-n", "1", str(exe)],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True, timeout=5)
        data = result.stdout
        length, value_length, value_type = struct.unpack_from("<HHH", data)
        key = "VS_VERSION_INFO\0".encode("utf-16le")
        offset = (6 + len(key) + 3) & ~3
        if (length > len(data) or length < offset + 52 or value_length < 52 or value_type != 0
                or data[6:6 + len(key)] != key):
            return "unknown"
        signature, _, ms, ls = struct.unpack_from("<IIII", data, offset)
        if signature != 0xFEEF04BD:
            return "unknown"
        return ".".join(str(part) for part in (ms >> 16, ms & 0xffff, ls >> 16, ls & 0xffff))
    except (OSError, subprocess.SubprocessError, struct.error):
        return "unknown"


def version_report(paths: dict[str, Path]) -> str:
    try:
        release = platform.freedesktop_os_release()
        os_version = release.get("PRETTY_NAME", "unknown")
    except (OSError, AttributeError):
        os_version = "unknown"
    try:
        env = wine_env(paths["prefix"])
        if shutil.which("wine", path=env["PATH"]):
            result = subprocess.run(["wine", "--version"], env=env, capture_output=True,
                                    text=True, timeout=5, check=True)
            wine = result.stdout.strip() or "unknown"
        else:
            wine = "not found"
    except (SetupError, OSError, subprocess.SubprocessError, ValueError):
        wine = "unknown"
    return (f"musicbee-linux {__version__}\n"
            f"- musicbee/version: {musicbee_version(paths['app'])}\n"
            f"- wine/version: {wine}\n"
            f"- os/version: {os_version}\n"
            f"- os/kernel: {platform.release()} ({platform.machine()})\n"
            f"- python/version: {platform.python_version()}")


def wine_selection(prefix: Path | None = None) -> str:
    state = prefix.parent if prefix is not None else locations()["state"]
    return tweak_value(read_config(state), "wine")


def winehq_bin(prefix: Path | None = None) -> Path | None:
    selection = wine_selection(prefix)
    if selection != "auto":
        directory = Path(f"/opt/wine-{selection}/bin")
        if not os.access(directory / "wine", os.X_OK):
            die(f"WineHQ {selection} not found at {directory / 'wine'}. Install it or set wine to auto.")
        return directory
    path = os.environ.get("PATH", os.defpath)
    if shutil.which("wine", path=path):
        return None
    candidates = [Path(f"/opt/wine-{name}/bin") for name in ("stable", "staging", "devel")
                  if os.access(Path(f"/opt/wine-{name}/bin/wine"), os.X_OK)]
    if len(candidates) > 1:
        die("Multiple WineHQ versions found. Choose one with 'musicbee-manage config wine'.")
    return candidates[0] if candidates else None


def wine_path(prefix: Path | None = None) -> str:
    path = os.environ.get("PATH", os.defpath)
    directory = winehq_bin(prefix)
    return str(directory) + os.pathsep + path if directory else path


def require_commands(*names: str, prefix: Path | None = None) -> None:
    path = wine_path(prefix)
    missing = [name for name in names if shutil.which(name, path=path) is None]
    if missing:
        die(f"Missing required commands: {', '.join(missing)}. Install Wine, Winetricks and cabextract through your package manager.")


def wine_server(prefix: Path | None = None) -> str:
    directory = winehq_bin(prefix)
    if directory:
        candidate = directory / "wineserver"
        if not os.access(candidate, os.X_OK):
            die(f"WineHQ server not found: {candidate}")
        return str(candidate)
    configured = os.environ.get("WINESERVER")
    if configured:
        return configured
    found = shutil.which("wineserver", path=wine_path(prefix))
    if found:
        return found
    multiarch = {"x86_64": "x86_64-linux-gnu", "aarch64": "aarch64-linux-gnu",
                 "i686": "i386-linux-gnu", "armv7l": "arm-linux-gnueabihf"}.get(platform.machine())
    if multiarch:
        candidate = Path("/usr/lib") / multiarch / "wine/wineserver"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    die("Wine server not found. Install a complete Wine package.")


def wine_env(prefix: Path, *, init: bool = False) -> dict[str, str]:
    env = dict(os.environ)
    env["WINEPREFIX"] = str(prefix)
    env["PATH"] = wine_path(prefix)
    directory = winehq_bin(prefix)
    if directory:
        env["WINE"] = str(directory / "wine")
        env["WINELOADER"] = env["WINE"]
        env["WINESERVER"] = wine_server(prefix)
    if not env.get("WINESERVER") and not shutil.which("wineserver", path=env["PATH"]):
        try:
            env["WINESERVER"] = wine_server(prefix)
        except SetupError:
            pass
    env.setdefault("WINEDEBUG", "-all")
    # External overrides would compromise repeatability. Apply the required
    # bootstrap override only during creation, not during .NET installation.
    env.pop("WINEDLLOVERRIDES", None)
    env.pop("WINEARCH", None)
    if init:
        env["WINEARCH"] = "win64"
        env["WINEDLLOVERRIDES"] = "mscoree,mshtml="
    return env


def apply_font_tweak(name: str, value: str, prefix: Path) -> None:
    if name == "allfonts" and value == "off":
        return
    require_commands("winetricks", prefix=prefix)
    with maintenance_lock(prefix, "Close MusicBee before changing font settings."):
        verb = "allfonts" if name == "allfonts" else (
            "fontsmooth=rgb" if value == "on" else "fontsmooth=disable")
        call(["winetricks", "-q", verb], env=wine_env(prefix))


def apply_windows_version(value: str, prefix: Path) -> None:
    require_commands("winetricks", prefix=prefix)
    call(["winetricks", "-q", {"8.1": "win81", "10": "win10"}[value]], env=wine_env(prefix))


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
        print("Use: musicbee-manage config <option> info | <value>")
        return
    name = args.option
    if name not in TWEAKS:
        die(f"Unknown setting {name!r}. Run 'musicbee-manage config' to list settings.")
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
    if name == "wine" and value != "auto":
        directory = Path(f"/opt/wine-{value}/bin")
        for executable in ("wine", "wineboot", "winepath", "wineserver"):
            if not os.access(directory / executable, os.X_OK):
                die(f"WineHQ {value} is missing {executable} in {directory}.")
    bootstrapped = (paths["prefix"] / ".musicbee-bootstrap-complete").is_file()
    if name in ("allfonts", "font-smoothing") and bootstrapped:
        apply_font_tweak(name, value, paths["prefix"])
    if name == "windows-version" and bootstrapped:
        with maintenance_lock(paths["prefix"], "Close MusicBee before changing the Windows version."):
            apply_windows_version(value, paths["prefix"])
    config.setdefault("tweaks", {})[name] = value
    write_config(paths["state"], config)
    print(f"{name} = {value}")
    if name == "allfonts" and value == "off":
        print("Previously installed fonts remain in the Wine prefix.")
    if name in ("allfonts", "font-smoothing", "windows-version") and not bootstrapped:
        print("The setting will be applied during initial setup.")
    else:
        print("Restart MusicBee to apply this setting.")


def musicbee_arguments(arguments: list[str], prefix: Path) -> list[str]:
    """Translate Linux file arguments without changing switches, URLs or Windows paths."""
    result = []
    path_expected = False
    for arg in arguments:
        switch = arg.casefold() in ("/play", "/queuenext", "/queuelast")
        windows_path = bool(re.match(r"^(?:[A-Za-z]:|\\\\)", arg))
        url = bool(re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", arg))
        candidate = Path(arg).expanduser()
        is_path = (path_expected or candidate.exists() or (not arg.startswith(("/", "-")) and bool(candidate.suffix))
                   or arg.startswith(("./", "../", "~/"))
                   or (arg.startswith("/") and "/" in arg[1:]))
        if not switch and not windows_path and not url and is_path:
            require_commands("winepath", prefix=prefix)
            converted = subprocess.run(["winepath", "-w", str(candidate.absolute())],
                                       env=wine_env(prefix), text=True, capture_output=True, check=False)
            if converted.returncode or not converted.stdout.strip():
                die(f"Cannot convert path for Wine: {arg}. {converted.stderr.strip()}")
            result.append(converted.stdout.rstrip("\r\n"))
        else:
            result.append(arg)
        path_expected = switch
    return result


def run_musicbee(args: argparse.Namespace, paths: dict[str, Path]) -> None:
    from .desktop import configure_explorer_redirect, refresh_tool_bridges

    prefix, app = paths["prefix"], paths["app"]
    require_ready(prefix)
    config = read_config(paths["state"])
    require_commands("wine", prefix=prefix)
    if not (app / "MusicBee.exe").is_file():
        die(r"MusicBee.exe not found at C:\MusicBee; use the install command first.")
    env = wine_env(prefix)
    gdiplus = "n,b" if tweak_value(config, "gdiplus") == "native" else "b"
    env["WINEDLLOVERRIDES"] = f"*gdiplus={gdiplus};gdiplus={gdiplus}"
    if tweak_value(config, "audio-latency") == "on":
        env["PULSE_LATENCY_MSEC"] = "60"
    lock_path = prefix / ".musicbee.launch.lock"
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if not args.musicbee_args:
                die("MusicBee already appears to be running from this prefix.")
            # The launch lock is also used by maintenance operations. Only send
            # commands when a running launcher holds the separate active lock.
            with (prefix / ".musicbee.active.lock").open("a+") as active:
                try:
                    fcntl.flock(active, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    call(["wine", "MusicBee.exe", *musicbee_arguments(args.musicbee_args, prefix)], env=env, cwd=app)
                    return
            die("MusicBee is starting or its installation is being modified; try again shortly.")
        bass = app / "basswma.dll"
        disabled = app / "basswma.dll.musicbee-disabled"
        if os.environ.get("MUSICBEE_ENABLE_WMA") == "1":
            if not bass.exists() and disabled.exists():
                disabled.rename(bass)
        elif bass.exists():
            if disabled.exists():
                die(f"Both {bass.name} and {disabled.name} exist; resolve manually.")
            bass.rename(disabled)
        if "windows-version" not in config.get("tweaks", {}):
            version = tweak_value(config, "windows-version")
            apply_windows_version(version, prefix)
            config.setdefault("tweaks", {})["windows-version"] = version
            write_config(paths["state"], config)
        refresh_tool_bridges(paths, config)
        configure_explorer_redirect(paths, tweak_value(config, "native-file-manager") == "on")
        with (prefix / ".musicbee.active.lock").open("a+") as active:
            fcntl.flock(active, fcntl.LOCK_EX)
            call(["wine", "MusicBee.exe", *musicbee_arguments(args.musicbee_args, prefix)], env=env, cwd=app)
