# Distribution packaging

The shared application and launcher are independent of package format:

- `/usr/bin/musicbee` runs `/usr/libexec/musicbee/musicbee.py`.
- `/usr/share/applications/musicbee.desktop` provides the desktop entry.
- Users download the proprietary MusicBee Portable archive themselves and run
  `musicbee install /path/to/MusicBeePortable.zip` to install or update it.

`nfpm/nfpm.yaml` defines the package contents and Fedora RPM dependencies.
Build with `./scripts/build-package.sh rpm`. Add DEB-specific dependencies and
test on supported Debian/Ubuntu releases before enabling DEB builds. AUR
support will need a separate PKGBUILD. Both can reuse the application files and
per-user Wine setup.

The Linux integration is licensed under GPL-3.0-only. MusicBee itself is a
separate proprietary application downloaded by the user.

See [PUBLISHING.md](PUBLISHING.md) for signing and publishing the Fedora
repository.
