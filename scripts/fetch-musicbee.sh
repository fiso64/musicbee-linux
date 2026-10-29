#!/usr/bin/env bash
# Optionally download upstream's current portable ZIP for personal installation.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v megadl >/dev/null || { echo 'Install megatools first (Fedora: sudo dnf install megatools)' >&2; exit 2; }
command -v python3 >/dev/null || exit 2
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
url="$(python3 - <<'PY'
from html.parser import HTMLParser
from urllib.request import Request, urlopen
class Links(HTMLParser):
    found=[]
    def handle_starttag(self, tag, attrs):
        if tag=='a':
            self.href=dict(attrs).get('href','')
    def handle_data(self, data):
        if 'Get MusicBee Portable' in data and getattr(self,'href','').startswith('https://mega.nz/file/'):
            self.found.append(self.href)
response=urlopen(Request('https://www.getmusicbee.com/downloads/', headers={'User-Agent':'Mozilla/5.0'}),timeout=25).read().decode('utf-8')
p=Links(); p.feed(response)
if not p.found:
    raise SystemExit('Could not locate official portable MEGA link; download the ZIP manually')
print(p.found[0])
PY
)"
[[ "$url" == https://mega.nz/file/*\#* ]] || { echo 'Official MEGA link lacks an encryption key; download ZIP manually' >&2; exit 2; }
echo 'Downloading MusicBee Portable from the official site-listed MEGA link.'
megadl --path "$tmp" "$url"
mapfile -d '' archives < <(find "$tmp" -type f -iname '*portable*.zip' -print0)
[[ "${#archives[@]}" -eq 1 ]] || { echo "Expected exactly one MusicBee Portable ZIP; found ${#archives[@]}" >&2; exit 2; }
mkdir -p "$root/vendor"
python3 - "${archives[0]}" <<'PY'
import sys,zipfile
path=sys.argv[1]
assert zipfile.is_zipfile(path), 'Downloaded file is not ZIP'
with zipfile.ZipFile(path) as z:
    assert any(n.lower().endswith('.exe') for n in z.namelist()), 'No portable EXE in ZIP'
PY
cp "${archives[0]}" "$root/vendor/musicbee-portable.zip"
sha256sum "$root/vendor/musicbee-portable.zip"
echo "Download complete. Run: musicbee-manage install '$root/vendor/musicbee-portable.zip'"
