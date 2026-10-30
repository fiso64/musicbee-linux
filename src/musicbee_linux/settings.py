"""Settings and persistent state for MusicBee on Linux."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile


TWEAKS = {
    "wine": ("auto", ("auto", "stable", "staging", "devel"),
        "Choose Wine for MusicBee. Auto uses Wine on PATH, or a single WineHQ installation in /opt. "
        "Other values select that WineHQ branch. Switching Wine can update the prefix."),
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
    "windows-version": ("8.1", ("8.1", "10"),
        "Windows version reported by the MusicBee Wine prefix. Windows 8.1 can fix "
        "window border artifacts; use Windows 10 for troubleshooting."),
}


class SetupError(Exception):
    pass


def die(message: str) -> None:
    raise SetupError(message)


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


def write_json_atomic(target: Path, value: dict, *, config: bool = False) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".config-" if config else ".tmp-",
                                suffix=".json" if config else "", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, indent=2, ensure_ascii=not config, sort_keys=config)
            if config:
                out.write("\n")
        os.replace(name, target)
    finally:
        if os.path.lexists(name):
            os.unlink(name)


def write_config(state: Path, config: dict) -> None:
    write_json_atomic(state / "config.json", config, config=True)


def tweak_value(config: dict, name: str) -> str:
    tweaks = config.get("tweaks", {})
    if not isinstance(tweaks, dict):
        die("Invalid tweaks configuration: expected an object.")
    value = tweaks.get(name, TWEAKS[name][0])
    if value not in TWEAKS[name][1]:
        die(f"Invalid saved value for {name}: {value!r}.")
    return value


