# MusicBee on Linux

Run MusicBee on Linux with Wine, with a desktop launcher, Linux media controls
and access to your music library. The integration includes:

- A dedicated Wine prefix with .NET 4.8, fonts and other dependencies
- MPRISBee for Linux media controls
- Native file-manager integration
- An easy way to add native Linux external tools
- A workaround for the crashing file converters tab
- Additional optional tweaks (which are usually not needed)

This is an unofficial community project. It is not affiliated with or
supported by MusicBee or its developer, Steven Mayall. MusicBee is proprietary
freeware; download it from its official website and review its license before
installing it.

Packages are currently available for Fedora; packages for other distributions are pending. You can install manually on any distro.

## Install on Fedora

Add the repository and install the Linux integration:

```bash
sudo dnf config-manager addrepo \
  --from-repofile=https://fiso64.github.io/musicbee-linux/musicbee.repo
sudo dnf install musicbee
```

Next, download the **Portable Edition** from the [MusicBee download
page](https://www.getmusicbee.com/downloads/) and give the downloaded file to
`musicbee install`:

```bash
musicbee install ~/Downloads/MusicBeePortable_*.zip
```

When setup finishes, open **MusicBee** from your app menu or run `musicbee`.

## Install manually on other distributions

Install Python, Wine, Winetricks, cabextract, icoutils and 7-Zip using your distribution's package manager.

Download the **Portable Edition** from the [MusicBee download page](https://www.getmusicbee.com/downloads/), then run:

```bash
git clone https://github.com/fiso64/musicbee-linux.git
cd musicbee-linux
python3 src/musicbee.py install ~/Downloads/MusicBeePortable_*.zip
```

Setup installs the script to `~/.local/bin/musicbee` and creates an app menu
launcher. Run `~/.local/bin/musicbee`, or `musicbee` if `~/.local/bin` is on
your PATH. Manual installation has not yet been tested on every distribution.

## Your music

Wine normally makes `~/Music` available in MusicBee's default Music folder (`C:\Users\yourusername\Music`).
To use another folder:

```bash
musicbee setup --music-dir /path/to/music --wine-music-path 'C:\Users\username\Music'
```

## Settings and tools

```bash
musicbee config                    # list settings
musicbee config gdiplus            # describe a setting
musicbee config audio-latency on   # example: sometimes useful against stuttering
musicbee media-controls status     # check MPRISBee
```

To send tracks to a native Linux application, register its command:

```bash
musicbee tools add picard 'flatpak run org.musicbrainz.Picard'
```

Then add the printed path in MusicBee as usual.

## Updating MusicBee, installing plugins

To update MusicBee itself, close it, download a newer Portable archive and run:

```bash
musicbee install ~/Downloads/MusicBeePortable_*.zip
```

or just extract the contents to the MusicBee install location.

Installing plugins works the same: Just follow their usual install process.

## Uninstalling

On Fedora:

```bash
sudo dnf remove musicbee
```

For a manual installation:

```bash
rm -f ~/.local/bin/musicbee ~/.local/share/applications/musicbee.desktop
```

This leaves your music, settings and Wine prefix in place. Integration data is
stored in `~/.local/share/musicbee` by default.

## License

This Linux integration is licensed under [GPL-3.0-only](LICENSE). MusicBee is
distributed separately under its own license.

