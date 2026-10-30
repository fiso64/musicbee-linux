#!/usr/bin/env bash
# Prepare a reviewable local release. This does not push or publish anything.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo 'Usage: ./scripts/prepare-release.sh SIGNING_KEY_FINGERPRINT [PAGES_CHECKOUT]' >&2
  exit 2
fi
fingerprint="$1"
pages="${2:-$root/build/pages-publish}"
for cmd in git python3 nfpm rpmsign rpm rpmkeys gpg createrepo_c ar; do
  command -v "$cmd" >/dev/null || { echo "Missing required command: $cmd" >&2; exit 1; }
done
[[ "$fingerprint" =~ ^([[:xdigit:]]{40}|[[:xdigit:]]{64})$ ]] || {
  echo 'Provide the full signing key fingerprint without spaces.' >&2; exit 2;
}
git -C "$root" rev-parse --is-inside-work-tree >/dev/null
[[ -z "$(git -C "$root" status --porcelain)" ]] || {
  echo 'Commit all source changes before preparing a release.' >&2; exit 1;
}
[[ -d "$pages" ]] && git -C "$pages" rev-parse --is-inside-work-tree >/dev/null || {
  echo "Pages checkout not found: $pages" >&2; exit 1;
}
[[ -z "$(git -C "$pages" status --porcelain)" ]] || {
  echo "Pages checkout has uncommitted changes: $pages" >&2; exit 1;
}
[[ "$(git -C "$pages" branch --show-current)" == gh-pages ]] || {
  echo "Pages checkout must be on gh-pages: $pages" >&2; exit 1;
}
version="$(python3 "$root/src/musicbee-manage.py" --version)"
version="${version#musicbee-manage }"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
  echo "Unsupported release version: $version" >&2; exit 1;
}
tag="v$version"
[[ -z "$(git -C "$root" tag -l "$tag")" ]] || {
  echo "Tag $tag already exists; choose a new version." >&2; exit 1;
}
source_commit="$(git -C "$root" rev-parse HEAD)"
origin="$(git -C "$root" remote get-url origin)"
[[ -f "$pages/RPM-GPG-KEY-musicbee" ]] || { echo 'Pages checkout has no repository signing key.' >&2; exit 1; }
existing_key="$(gpg --batch --with-colons --show-keys "$pages/RPM-GPG-KEY-musicbee" | awk -F: '$1 == "fpr" { print $10; exit }')"
[[ "${existing_key^^}" == "${fingerprint^^}" ]] || {
  echo 'Signing key does not match the existing repository key.' >&2; exit 1;
}

mkdir -p "$root/build"
stage="$(mktemp -d "$root/build/release-$version.XXXXXXXX")"
echo "Preparing release in $stage"
python3 -m unittest discover -s "$root/tests" -q
"$root/scripts/build-package.sh" rpm "$stage/packages"
"$root/scripts/build-package.sh" deb "$stage/packages"
python3 "$root/scripts/generate-aur.py" --commit "$source_commit" --output "$stage/aur"
shopt -s nullglob
packages=("$stage/packages/"*.rpm)
[[ ${#packages[@]} -eq 1 ]] || { echo 'Expected exactly one built RPM.' >&2; exit 1; }
package="${packages[0]}"
debs=("$stage/packages/"*.deb)
[[ ${#debs[@]} -eq 1 ]] || { echo 'Expected exactly one built DEB.' >&2; exit 1; }
new_version="$(rpm -qp --qf '%{VERSION}-%{RELEASE}' "$package")"
[[ "$new_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+-[0-9]+$ ]] || {
  echo "Unexpected RPM version: $new_version" >&2; exit 1;
}
for old in "$pages"/rpm/*.rpm; do
  old_version="$(rpm -qp --qf '%{VERSION}-%{RELEASE}' "$old")"
  [[ "$old_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+-[0-9]+$ ]] || {
    echo "Unexpected existing RPM version: $old_version" >&2; exit 1;
  }
  newer="$(rpm --eval "%{lua:print(rpm.vercmp('$new_version','$old_version'))}")"
  [[ "$newer" == 1 ]] || { echo "RPM $new_version is not newer than $old_version." >&2; exit 1; }
done
"$root/scripts/prepare-rpm-repo.sh" "$package" "$fingerprint" | tee "$stage/prepare.log"
prepared="$(sed -n 's/^Signed repository prepared at: //p' "$stage/prepare.log")"
[[ -n "$prepared" && -d "$prepared/rpm/repodata" ]] || { echo 'Repository preparation failed.' >&2; exit 1; }
python3 "$root/scripts/apt-repository.py" build "$prepared" "${debs[0]}" "$prepared/RPM-GPG-KEY-musicbee" "$fingerprint"
python3 "$root/scripts/apt-repository.py" verify "$prepared" "${debs[0]}" "$prepared/RPM-GPG-KEY-musicbee" "$version"
git clone --no-local --branch gh-pages "$pages" "$stage/pages"
git -C "$stage/pages" remote set-url origin "$origin"
# This is a new disposable clone: replace its RPM directory so only the new
# package and matching metadata are published. Preserve the site's other files.
rm -rf -- "$stage/pages/rpm"
rm -rf -- "$stage/pages/apt"
cp -R -- "$prepared/rpm" "$stage/pages/rpm"
cp -R -- "$prepared/apt" "$stage/pages/apt"
cp -- "$prepared/musicbee.repo" "$prepared/musicbee.sources" "$prepared/RPM-GPG-KEY-musicbee" "$stage/pages/"
cp -- "$prepared/.nojekyll" "$stage/pages/.nojekyll"
[[ "$(git -C "$root" rev-parse HEAD)" == "$source_commit" && -z "$(git -C "$root" status --porcelain)" ]] || {
  echo 'Source changed during preparation; commit the changes and start again.' >&2; exit 1;
}
git -C "$stage/pages" add -A
git -C "$stage/pages" -c commit.gpgsign=false commit -m "Publish musicbee $version"
printf '%s\n' "$source_commit" > "$stage/source-commit"
printf '%s\n' "$tag" > "$stage/tag"
{
  printf '# MusicBee on Linux %s\n\n' "$version"
  printf 'Edit these notes before publishing. Recent source commits:\n\n'
  git -C "$root" log -10 --pretty=format:'- %s%n'
} > "$stage/release-notes.md"
printf '\nPrepared release: %s\n' "$stage"
printf 'Source commit: %s\nTag: %s\n' "$source_commit" "$tag"
printf 'Review pages and edit release-notes.md before publishing. See packaging/README.md.\n'
