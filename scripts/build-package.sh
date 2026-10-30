#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
format="${1:-rpm}"
target="${2:-$root/build/packages}"
if [[ "$format" != rpm && "$format" != deb && "$format" != archlinux ]] || (( $# > 2 )); then
  echo 'Usage: ./scripts/build-package.sh [rpm|deb|archlinux [OUTPUT_DIRECTORY]]' >&2
  exit 2
fi
command -v nfpm >/dev/null || { echo 'Missing nfpm: https://nfpm.goreleaser.com/install/' >&2; exit 2; }
mkdir -p "$target"
target="$(cd "$target" && pwd)"
cd "$root"
export MUSICBEE_VERSION
MUSICBEE_VERSION="$(python3 -c 'import runpy; print(runpy.run_path("src/musicbee-manage.py")["__version__"])')"
nfpm package --config packaging/nfpm/nfpm.yaml --packager "$format" --target "$target/"
