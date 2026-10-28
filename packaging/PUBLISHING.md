# Publishing the Fedora repository

These steps prepare a signed repository locally and publish it with GitHub
Pages. The build contains the Linux integration only; it does not bundle
MusicBee. Confirm the repository name and Pages URL match the fixed URLs in
`scripts/prepare-rpm-repo.sh` before publishing.

## First-time setup

1. Install [nFPM](https://nfpm.goreleaser.com/install/) and the Fedora tools:

   ```bash
   sudo dnf install createrepo_c rpm-sign gnupg2
   ```

2. Create a dedicated OpenPGP signing key with `gpg --full-generate-key`, or
   use an existing signing key you control. Choose an RPM-compatible key (for
   example RSA) and protect and back up the private key. Never commit or upload
   the private key. List its full fingerprint with:

   ```bash
   gpg --list-secret-keys --fingerprint --keyid-format=long
   ```

   Publish that fingerprint in the project README or release notes and through
   an independent trusted channel so users can check the key. A key downloaded
   from the same site as the repository is not an independent trust anchor.

3. Create the public `fiso64/musicbee-linux` GitHub repository if needed and
   push the source code. Confirm that generated packages and private MusicBee
   downloads are excluded from the source repository.

## Prepare a release

Increment `version` or `release` in `packaging/nfpm/nfpm.yaml` for every new
release; DNF will not treat a replacement with the same version and release as
an upgrade. Run the offline tests, build, then use the full fingerprint from
above:

```bash
python3 -m unittest discover -s tests -v
./scripts/build-package.sh rpm
./scripts/prepare-rpm-repo.sh build/packages/musicbee-0.1.0-1.noarch.rpm FULL_FINGERPRINT
```

Use the actual RPM filename. The preparation script signs a copy of the RPM,
creates repository metadata, signs that metadata, verifies both signatures,
and prints the path to a new directory under `build/`. It leaves the build RPM
unsigned and does not publish anything. GPG may ask for your key's passphrase.
Review the generated files and test the RPM before publishing.

## Publish with GitHub Pages

The following example creates a new `gh-pages` branch from a temporary Git
worktree. Do this **only if that branch does not already exist**. Replace the
example path with the directory printed by the preparation script.

```bash
release_repo=/absolute/path/to/musicbee-linux/build/rpm-repo.XXXXXXXX
pages_worktree="$(mktemp -d /tmp/musicbee-pages.XXXXXXXX)"
git worktree add --detach "$pages_worktree" HEAD
git -C "$pages_worktree" switch --orphan gh-pages
cp -R "$release_repo"/. "$pages_worktree"/
git -C "$pages_worktree" add -f .nojekyll musicbee.repo RPM-GPG-KEY-musicbee rpm/
git -C "$pages_worktree" commit -m 'Publish MusicBee RPM repository'
git -C "$pages_worktree" push -u origin gh-pages
```

In the GitHub repository's **Settings → Pages**, choose **Deploy from a
branch**, select `gh-pages` and `/ (root)`, and save. Wait for deployment.
The published `.repo` file points to
`https://fiso64.github.io/musicbee-linux/` and enables verification of both
RPMs and repository metadata.

For later releases, fetch the existing `gh-pages` branch into a clean worktree,
copy the newly prepared directory into it, commit and push. Keep the same
signing key: changing it requires a coordinated key rotation for users. Do not
replace a release with another RPM using the same version and release.

After deployment, verify the public `.repo` and public-key URLs, compare the
published key fingerprint against your trusted copy, and test adding the repo,
installing the package, and upgrading from an earlier version on a Fedora test
system. Also test first-time MusicBee setup and an update using a user-supplied
Portable archive. `dnf` should verify the RPM and metadata signatures; do not
disable signature checks to make an installation pass.
