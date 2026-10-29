# MusicBee on Linux

Run MusicBee on Linux with Wine. Includes most of the known tweaks to get MusicBee running smoothly.

Packages are currently available for Fedora; packages for other distributions are pending. You can install manually on any distro.

## Installing

Download the **Portable Edition** from the [MusicBee download page](https://www.getmusicbee.com/downloads/). 

### Fedora

```bash
sudo dnf config-manager addrepo \
  --from-repofile=https://fiso64.github.io/musicbee-linux/musicbee.repo

sudo dnf install musicbee

musicbee-manage install ~/Downloads/MusicBeePortable_*.zip
```

### Manual install

Install Python, Wine, Winetricks, cabextract, icoutils and 7-Zip using your distribution's package manager. Then:

```bash
git clone https://github.com/fiso64/musicbee-linux.git
cd musicbee-linux
python3 src/musicbee-manage.py install ~/Downloads/MusicBeePortable_*.zip
```

Manual installation has not yet been tested on every distribution.

## Usage

### Your music

Wine normally makes `~/Music` available in MusicBee's default Music folder (`C:\Users\yourusername\Music`). To use another folder, close MusicBee and run:

```bash
musicbee-manage music-dir /path/to/music --wine-music-path 'C:\MyMusic'
```

### Using external tools

To send tracks to a native Linux application, register its command:

```bash
musicbee-manage tools add picard 'flatpak run org.musicbrainz.Picard'
```

Then add the printed path in the MusicBee Tools configuration as usual.

### MusicBee command line

`musicbee` accepts MusicBee's usual command-line options, including when it is
already running. Linux file and folder paths are translated to Wine paths (Windows paths also work).

```bash
musicbee ~/Music/song.flac
musicbee /QueueNext ~/Music/playlist.m3u
musicbee /Next
musicbee --help   # View all options
```

See MusicBee's [documented options](https://getmusicbee.com/forum/index.php?topic=6885.0).

## Tweaks & troubleshooting

This package already includes most of the known tweaks to get MusicBee running smoothly:

- CJK fonts and other dependencies
- MPRISBee for Linux media controls
- Native file-manager integration
- A workaround for the crashing file converters tab

Additional optional tweaks can be listed with `musicbee-manage config`. If you're having issues, look here first. Example usage:

```bash
musicbee-manage --version                 # version of this integration
musicbee-manage config                    # list all settings
musicbee-manage config gdiplus            # describe a setting
musicbee-manage config audio-latency on   # example: sometimes useful against stuttering
```

<details>
  <summary>Known problems</summary>

### Dragging tabs to rearrange them fails
Close MusicBee, then list and rearrange the saved tabs:

```bash
musicbee-manage tabs       # list tabs in their current order
musicbee-manage tabs 3 1   # move tab 3 to the first position
```

### Some skins have broken/ugly window borders or missing min/max/close buttons

Try enabling or disabling `View > Skins > Skin Window Borders`. 

</details>

## Updating MusicBee, installing plugins

To update MusicBee itself, close it, download a newer Portable archive and run:

```bash
musicbee-manage install ~/Downloads/MusicBeePortable_*.zip
```

or just extract the contents to the MusicBee install location.

Installing plugins works as usual: Just follow their normal install process.

## Uninstalling

On Fedora:

```bash
sudo dnf remove musicbee
```

For a manual installation:

```bash
rm -f ~/.local/bin/musicbee ~/.local/bin/musicbee-manage ~/.local/share/applications/musicbee.desktop
```

This leaves your music, settings and Wine prefix in place. Integration data is
stored in `~/.local/share/musicbee` by default.

## License

This Linux integration is licensed under [GPL-3.0-only](LICENSE). MusicBee is
distributed separately under its own license.

