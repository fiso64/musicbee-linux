#!/usr/bin/env bash
set -euo pipefail
if [[ $# != 3 ]]; then
  echo 'Usage: bash scripts/prepare-arch-repo.sh PACKAGE OUTPUT_DIRECTORY FINGERPRINT' >&2
  exit 2
fi
package="$1"
directory="$2"
fingerprint="$3"
[[ -f "$package" && "$package" == *.pkg.tar.zst ]] || { echo 'Expected an Arch package.' >&2; exit 1; }
[[ "$fingerprint" =~ ^([[:xdigit:]]{40}|[[:xdigit:]]{64})$ ]] || { echo 'Expected a full signing key fingerprint.' >&2; exit 1; }
[[ ! -e "$directory" ]] || { echo "Output already exists: $directory" >&2; exit 1; }
mkdir -p "$directory"
cp -- "$package" "$directory/"
package="$directory/$(basename "$package")"
sign=(gpg --batch --yes --local-user "$fingerprint")
if [[ -n "${MUSICBEE_SIGNING_PASSPHRASE_FILE:-}" ]]; then
  sign+=(--pinentry-mode loopback --passphrase-file "$MUSICBEE_SIGNING_PASSPHRASE_FILE")
fi
"${sign[@]}" --detach-sign "$package"
repo-add --include-sigs "$directory/musicbee-linux.db.tar.gz" "$package"
# GitHub Pages artifacts cannot contain symlinks. Pacman accepts ordinary files.
python3 - "$directory" <<'PY'
from pathlib import Path
import sys
for suffix in ('db', 'files'):
    path = Path(sys.argv[1]) / ('musicbee-linux.' + suffix)
    if path.is_symlink():
        data = path.read_bytes()
        path.unlink()
        path.write_bytes(data)
PY
for file in "$directory/musicbee-linux.db" "$directory/musicbee-linux.files"; do
  "${sign[@]}" --detach-sign "$file"
done
gpg --batch --verify "$package.sig" "$package"
gpg --batch --verify "$directory/musicbee-linux.db.sig" "$directory/musicbee-linux.db"
