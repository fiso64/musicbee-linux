# Packaging and releases

`nfpm/nfpm.yaml` describes the Linux integration RPM and DEB. MusicBee itself is
separate. The integration version lives in `src/musicbee-manage.py`; nFPM and
`musicbee-manage --version` use that same value. AUR packaging is not implemented yet.
The APT repository publishes `musicbee.sources` with its scoped signing key embedded.

## One-time setup

1. In GitHub **Settings → Pages**, set the source to **GitHub Actions**.
2. Add the existing repository signing private key as the `RPM_SIGNING_KEY` Actions
   secret and its passphrase as `RPM_SIGNING_PASSPHRASE`. If the key has no
   passphrase, leave the passphrase secret unset. The expected public key
   fingerprint is in `rpm-signing-fingerprint`; changing keys requires a
   coordinated migration for existing repository users.

For example, from an authenticated `gh` session in the source checkout:

```bash
set -o pipefail
gpg --armor --export-secret-keys "$(cat packaging/rpm-signing-fingerprint)" | gh secret set RPM_SIGNING_KEY
gh secret set RPM_SIGNING_PASSPHRASE  # if the key has a passphrase
```

The signing job has access to the private key. Store a backup of the key outside
GitHub; don't commit it. GitHub supplies `GITHUB_TOKEN` automatically.

## Release

1. Update the code, bump `__version__` in `src/musicbee-manage.py`, then commit
   and push to `main`.
2. Create and push the matching tag, for example:

   ```bash
   git tag v0.3.0
   git push origin v0.3.0
   ```

3. Actions tests the tagged commit, checks the version, builds both packages,
   signs the RPM and both repositories, and creates a draft GitHub Release. Review its
   generated notes and publish it. The publish event verifies the signed assets
   and deploys them to Pages. DNF and APT users can then upgrade normally.

The draft contains the DEB, signed RPM and `musicbee-repository.tar.gz`, the exact
repository deployed to Pages. Only the newest published release is deployable;
older RPMs remain available from their GitHub Releases. Re-run a failed prepare
or deployment workflow from Actions with the existing tag; if a draft already
exists, remove it before re-running preparation. Check the deployment status
and test DNF and APT upgrades after publishing.

For a local package build, run `./scripts/build-package.sh rpm` or
`./scripts/build-package.sh deb`. The local
`prepare-rpm-repo.sh` and `prepare-release.sh` scripts remain available as a
fallback; they require Fedora's `createrepo_c`, `rpm-sign` and `gnupg2`, plus
nFPM. Update `website/index.html` to change the Pages landing page.
