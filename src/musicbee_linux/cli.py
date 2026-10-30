"""Command-line interface and entry-point dispatch."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import zipfile

from . import desktop, media_controls, settings, utils, wine


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="musicbee-manage",
        description="Manage MusicBee on Linux using Wine.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Download MusicBee Portable, then run: musicbee-manage install /path/to/MusicBeePortable.zip\n"
        ),
    )
    parser.add_argument("--version", action="store_true", help="Show version and system information")
    sub = parser.add_subparsers(dest="command", metavar="{install,init,repair-dependencies,aac,music-dir,config,tabs,tools,backup,launcher,media-controls,status}")
    desktop_setup = sub.add_parser("desktop-setup")
    desktop_setup.add_argument("files", nargs=argparse.REMAINDER)
    explorer = sub.add_parser("explorer-dispatch")
    explorer.add_argument("arguments", nargs=argparse.REMAINDER)
    desktop_launch = sub.add_parser("desktop-launch")
    desktop_launch.add_argument("files", nargs=argparse.REMAINDER)
    dispatch = sub.add_parser("tools-dispatch")
    dispatch.add_argument("name")
    dispatch.add_argument("files", nargs="+")
    ins = sub.add_parser("install", help="Install or update MusicBee from a downloaded portable archive")
    ins.add_argument("installer", help="Path to the MusicBee Portable ZIP or EXE")
    backup_options = ins.add_mutually_exclusive_group()
    backup_options.add_argument("--backup", dest="keep_backup", action="store_true", default=None,
                                help="Keep a backup during updates without prompting")
    backup_options.add_argument("--no-backup", dest="keep_backup", action="store_false",
                                help="Do not keep an update backup; skip the prompt")
    ini = sub.add_parser("init", help="Set up Wine and install MusicBee from a downloaded archive")
    ini.add_argument("--archive", help="Downloaded MusicBee Portable ZIP or EXE")
    ini.add_argument("--accept-license", action="store_true", help="Explicitly accept MusicBee EULA for unattended install")
    ini.add_argument("--no-launch", action="store_true", help="Do not start MusicBee after installing")
    sub.add_parser("repair-dependencies", help="Re-run Wine dependency setup for an existing installation")
    sub.add_parser("aac", help="Install the AAC decoder if it is missing")
    music = sub.add_parser("music-dir", help="Show or configure a Wine link to your music directory")
    music.add_argument("directory", nargs="?", help="Linux music directory")
    music.add_argument("--wine-music-path", metavar="WIN_PATH", help=r"Windows path (default: previous path or C:\Users\<user>\Music)")
    conf = sub.add_parser("config", help="View or change Wine and playback tweaks")
    conf.add_argument("option", nargs="?", help="Setting to inspect or change")
    conf.add_argument("value", nargs="?", help="New value, or info for details")
    tabs = sub.add_parser("tabs", help="List or rearrange saved MusicBee tabs")
    tabs.add_argument("--settings", choices=("portable", "profile"), help="Choose which settings file to use")
    tabs.add_argument("source", nargs="?", type=int, metavar="CURRENT_POSITION")
    tabs.add_argument("destination", nargs="?", type=int, metavar="NEW_POSITION")
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
    sub.add_parser("status", help="Show Wine prefix and installation state")
    return parser


LAUNCH_HELP = """\
Usage: musicbee [FILE_OR_FOLDER | MUSICBEE_OPTIONS]

Launch MusicBee or send a command to an existing instance. Linux paths are
translated to Wine paths; Windows paths and URLs are passed through.

  /Play TARGET       Play a file, folder, playlist, cue sheet or URL
  /QueueNext TARGET  Queue a target next
  /QueueLast TARGET  Add a target to the end of the queue
  /PlayPause         Toggle playback
  /PlayResume        Resume the previous playback state
  /Next, /Previous   Skip to the next or previous track
  /Stop              Stop playback
  /StartAutoDj       Start Auto DJ
  /PlayLibraryShuffled  Play the library shuffled
  /OpenCompactPlayer, /OpenMiniPlayer  Open a smaller player
  /VolumeDown, /VolumeUp  Adjust volume
  -h, --help         Show this help
  --version          Show version and system information

For installation and configuration, run musicbee-manage --help.
"""


def manage_command(args: argparse.Namespace, paths: dict[str, Path]) -> int:
    """Dispatch management commands, including the private desktop bridges."""
    from . import install as installation

    with_args = {
        "music-dir": utils.configure_music_dir, "install": installation.install, "init": installation.first_launch,
        "backup": utils.backup, "config": wine.configure, "tabs": utils.manage_tabs,
        "tools": desktop.manage_tools, "media-controls": media_controls.media_controls,
    }
    paths_only = {
        "repair-dependencies": installation.repair_dependencies, "aac": installation.repair_aac_decoder,
        "launcher": desktop.regenerate_launcher, "status": utils.status,
    }
    if args.command in with_args:
        with_args[args.command](args, paths)
    elif args.command in paths_only:
        paths_only[args.command](paths)
    elif args.command == "desktop-setup":
        installation.first_launch(argparse.Namespace(archive=None, accept_license=False, no_launch=True), paths)
        subprocess.Popen([sys.executable, str(utils.entrypoint()), "--manage", "desktop-launch", *args.files],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    elif args.command == "desktop-launch":
        if os.environ.get("MUSICBEE_DESKTOP_LOG_WORKER") != "1":
            result = desktop.desktop_launch_with_log(paths, args.files)
            if result is not None:
                return result
        if not (paths["app"] / "MusicBee.exe").is_file():
            settings.die("MusicBee is not installed. Run musicbee-manage install in a terminal.")
        wine.run_musicbee(argparse.Namespace(musicbee_args=args.files), paths)
    elif args.command == "tools-dispatch":
        desktop.dispatch_tool(args.name, args.files, paths)
    elif args.command == "explorer-dispatch":
        desktop.dispatch_explorer(args.arguments, paths)
    return 0


def main() -> int:
    parser = create_parser()
    arguments = sys.argv[1:]
    executable = Path(sys.argv[0]).name
    manager = executable in ("musicbee-manage", "musicbee-manage.py")
    if manager and arguments and arguments[0] == "--manage":
        manager = True
        arguments = arguments[1:]
    elif executable == "musicbee-manage.py" and arguments and arguments[0] == "--launch":
        manager = False
        arguments = arguments[1:]
    if arguments == ["--version"]:
        print(wine.version_report(utils.locations()))
        return 0
    if not manager and arguments in (["-h"], ["--help"]):
        print(LAUNCH_HELP, end="")
        return 0
    args = parser.parse_args(arguments) if manager else None
    if manager and args.version:
        print(wine.version_report(utils.locations()))
        return 0
    if manager and args.command is None:
        parser.print_help()
        return 0
    if os.geteuid() == 0:
        print("Error: run as your desktop user, not root.", file=sys.stderr)
        return 1
    paths = utils.locations()
    try:
        if not manager:
            if not paths["app"].joinpath("MusicBee.exe").is_file():
                from . import install as installation
                installation.first_launch(argparse.Namespace(archive=None, accept_license=False, no_launch=True), paths)
            wine.run_musicbee(argparse.Namespace(musicbee_args=arguments), paths)
        else:
            return manage_command(args, paths)
        return 0
    except (settings.SetupError, OSError, subprocess.CalledProcessError, zipfile.BadZipFile, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if manager and args.command == "desktop-launch":
            message = str(exc)
            if os.environ.get("MUSICBEE_DESKTOP_LOG_WORKER") == "1":
                message += f"\n\nLog: {paths['state'] / 'desktop-launch.log'}"
            desktop.show_desktop_error(message)
        if manager and args.command == "desktop-setup" and sys.stdin.isatty():
            input("Press Enter to close this window.")
        return 1
