#!/usr/bin/env python3
"""Command-line entry point for MusicBee on Linux."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "musicbee/lib"))
from musicbee_linux import __version__
from musicbee_linux.cli import main

if __name__ == "__main__":
    sys.exit(main())
