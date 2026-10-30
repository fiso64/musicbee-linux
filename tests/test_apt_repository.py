"""Exercise signed APT metadata with an isolated disposable signing key."""
import importlib.util
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('apt_repository', ROOT / 'scripts/apt-repository.py')
apt_repo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apt_repo)


@unittest.skipUnless(all(shutil.which(tool) for tool in ('nfpm', 'ar', 'gpg')), 'nFPM, ar and gpg required')
class AptRepositoryTests(unittest.TestCase):
    def test_signed_repository_and_corruption_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / 'gpg'
            home.mkdir(mode=0o700)
            env = {**os.environ, 'GNUPGHOME': str(home)}
            generated = subprocess.run(['gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                                        '--quick-generate-key', 'APT test <apt-test@example.invalid>',
                                        'ed25519', 'sign', '0'], env=env, capture_output=True, text=True)
            if generated.returncode and 'gpg-agent' in generated.stderr and not os.environ.get('CI'):
                self.skipTest('gpg-agent unavailable in this environment')
            generated.check_returncode()
            keys = subprocess.check_output(['gpg', '--batch', '--with-colons', '--list-secret-keys'],
                                           env=env, text=True, stderr=subprocess.DEVNULL)
            fingerprint = next(line.split(':')[9] for line in keys.splitlines() if line.startswith('fpr:'))
            key = root / 'key.asc'
            key.write_bytes(subprocess.check_output(['gpg', '--batch', '--armor', '--export', fingerprint], env=env))
            subprocess.run([str(ROOT / 'scripts/build-package.sh'), 'deb', str(root / 'packages')],
                           cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
            deb = next((root / 'packages').glob('*.deb'))
            version = runpy.run_path(str(ROOT / 'src/musicbee_linux/__init__.py'))['__version__']
            site = root / 'site'
            site.mkdir()
            with patch.dict(os.environ, {'GNUPGHOME': str(home)}):
                apt_repo.build(site, deb, key, fingerprint)
                apt_repo.verify(site, deb, key, version)
                if shutil.which('apt-get') and shutil.which('apt-cache'):
                    source = root / 'test.sources'
                    source.write_text((site / 'musicbee.sources').read_text().replace(
                        apt_repo.BASE, (site / 'apt').as_uri()))
                    lists = root / 'lists'
                    (lists / 'partial').mkdir(parents=True)
                    options = ['-o', f'Dir::Etc::sourcelist={source}',
                               '-o', 'Dir::Etc::sourceparts=-', '-o', f'Dir::State::lists={lists}',
                               '-o', 'APT::Architecture=amd64', '-o', 'APT::Architectures::=amd64',
                               '-o', 'APT::Sandbox::User=root', '-o', 'Debug::NoLocking=1']
                    subprocess.run(['apt-get', *options, 'update'], check=True, capture_output=True, text=True)
                    policy = subprocess.check_output(['apt-cache', *options, 'policy', 'musicbee'], text=True)
                    self.assertIn(f'Candidate: {version}-1', policy)
                index = site / 'apt/dists/stable/main/binary-amd64/Packages.gz'
                original = index.read_bytes()
                index.write_bytes(original + b'corrupt')
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    apt_repo.verify(site, deb, key, version)
                index.write_bytes(original)
                (site / 'musicbee.sources').write_text('tampered\n')
                with self.assertRaisesRegex(ValueError, 'configuration or embedded key'):
                    apt_repo.verify(site, deb, key, version)
