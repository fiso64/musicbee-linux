#!/usr/bin/env bash
# Create a signed, local GitHub Pages repository. This does not publish it.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -ne 2 ]]; then
  echo 'Usage: ./scripts/prepare-rpm-repo.sh PATH_TO_RPM SIGNING_KEY_FINGERPRINT' >&2
  exit 2
fi
rpm_file="$1"
key_fingerprint="$2"
[[ -f "$rpm_file" ]] || { echo "RPM not found: $rpm_file" >&2; exit 2; }
[[ "$key_fingerprint" =~ ^([[:xdigit:]]{40}|[[:xdigit:]]{64})$ ]] || {
  echo 'Provide the full signing key fingerprint without spaces.' >&2; exit 2;
}
for cmd in createrepo_c rpmsign rpmkeys gpg; do
  command -v "$cmd" >/dev/null || { echo "Missing required command: $cmd" >&2; exit 2; }
done
gpg --batch --list-secret-keys "$key_fingerprint" >/dev/null
mkdir -p "$root/build"
repo_dir="$(mktemp -d "$root/build/rpm-repo.XXXXXXXX")"
completed=false
cleanup() {
  if [[ "$completed" != true ]]; then
    rm -rf -- "$repo_dir"
  fi
}
trap cleanup EXIT
mkdir -p "$repo_dir/rpm"
cp -- "$rpm_file" "$repo_dir/rpm/"
rpm_name="$(basename "$rpm_file")"
rpmsign --define "_gpg_name $key_fingerprint" --addsign "$repo_dir/rpm/$rpm_name"
gpg --batch --armor --export "$key_fingerprint" > "$repo_dir/RPM-GPG-KEY-musicbee"
[[ -s "$repo_dir/RPM-GPG-KEY-musicbee" ]] || { echo 'Could not export the public key.' >&2; exit 1; }
createrepo_c "$repo_dir/rpm"
gpg --batch --yes --local-user "$key_fingerprint" --armor --detach-sign \
  --output "$repo_dir/rpm/repodata/repomd.xml.asc" "$repo_dir/rpm/repodata/repomd.xml"
gpg --batch --verify "$repo_dir/rpm/repodata/repomd.xml.asc" "$repo_dir/rpm/repodata/repomd.xml"
# Verify against an isolated file keyring, without writing to an RPM database.
keyring="$repo_dir/.verification-keyring"
mkdir "$keyring"
key_file="$keyring/gpg-pubkey-${key_fingerprint,,}.key"
cp -- "$repo_dir/RPM-GPG-KEY-musicbee" "$key_file"
echo 'Verifying the signed RPM with the exported public key...'
rpmkeys --define '_keyring fs' --define "_keyringpath $keyring" \
  --define '_pkgverify_level signature' --checksig "$repo_dir/rpm/$rpm_name"
rm -- "$key_file"
rmdir -- "$keyring"
cat > "$repo_dir/musicbee.repo" <<'REPO'
[musicbee-linux]
name=MusicBee on Linux
baseurl=https://fiso64.github.io/musicbee-linux/rpm/
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=https://fiso64.github.io/musicbee-linux/RPM-GPG-KEY-musicbee
REPO
: > "$repo_dir/.nojekyll"
completed=true
printf '\nSigned repository prepared at: %s\n' "$repo_dir"
printf 'Signing key fingerprint: %s\n' "$key_fingerprint"
printf 'Review its contents before publishing.\n'
