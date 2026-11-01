# Troubleshooting & Known problems

This package already includes most of the known tweaks to get MusicBee running smoothly:

- CJK fonts and other dependencies
- BASS AAC decoder for AAC and M4A playback
- MPRISBee for Linux media controls
- Native file-manager integration
- A workaround for the crashing file converters tab

## Optional tweaks

Optional tweaks can be listed with `musicbee-manage config`. If you're having issues, look there first. Example usage:

```bash
musicbee-manage config                    # list all settings
musicbee-manage config gdiplus            # describe a setting
musicbee-manage config audio-latency on   # example: sometimes useful against stuttering
```

## Known problems

### Dragging tabs to rearrange them fails
Close MusicBee, then list and rearrange the saved tabs:

```bash
musicbee-manage tabs       # list tabs in their current order
musicbee-manage tabs 3 1   # move tab 3 to the first position
```

### Some skins have broken/ugly window borders or missing min/max/close buttons

Try enabling or disabling `View > Skins > Skin Window Borders`. Unfortunately, there is no confirmed solution that works for every skin.
