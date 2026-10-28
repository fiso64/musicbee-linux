#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
format="${1:-rpm}"
if [[ "$format" != rpm || $# -gt 1 ]]; then
  echo 'Usage: ./scripts/build-package.sh [rpm]' >&2
  exit 2
fi
command -v nfpm >/dev/null || { echo 'Missing nfpm: https://nfpm.goreleaser.com/install/' >&2; exit 2; }
mkdir -p "$root/build/packages"
cd "$root"
nfpm package --config packaging/nfpm/nfpm.yaml --packager "$format" --target "$root/build/packages/"
