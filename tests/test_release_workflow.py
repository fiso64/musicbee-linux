"""Exercise release staging locally; signing commands are replaced with test doubles."""
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FINGERPRINT = '0123456789ABCDEF0123456789ABCDEF01234567'
VERSION = runpy.run_path(str(ROOT / 'src' / 'musicbee_linux' / '__init__.py'))['__version__']
REQUIRED = ('git', 'nfpm', 'rpm', 'createrepo_c', 'gpg', 'rpmsign', 'rpmkeys')


@unittest.skipUnless(all(shutil.which(tool) for tool in REQUIRED), 'RPM release tools not installed')
class ReleaseWorkflowTests(unittest.TestCase):
    def test_stages_signed_repository_and_preserves_pages(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source'
            pages = root / 'pages'
            source.mkdir()
            for name in ('src', 'scripts', 'packaging', 'desktop'):
                shutil.copytree(ROOT / name, source / name)
            for name in ('README.md', 'LICENSE'):
                shutil.copy2(ROOT / name, source / name)
            (source / 'tests').mkdir()
            (source / 'tests' / 'test_empty.py').write_text(
                'import unittest\nclass Smoke(unittest.TestCase):\n    def test_pass(self): pass\n')
            (source / '.gitignore').write_text('build/\n__pycache__/\n')
            env = {**os.environ, 'GIT_AUTHOR_NAME': 'Test', 'GIT_AUTHOR_EMAIL': 'test@example.com',
                   'GIT_COMMITTER_NAME': 'Test', 'GIT_COMMITTER_EMAIL': 'test@example.com'}

            def git(cwd, *args):
                return subprocess.run(['git', '-C', str(cwd), *args], env=env, check=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            git(source, 'init', '-b', 'main')
            git(source, 'remote', 'add', 'origin', 'https://example.com/project.git')
            git(source, 'add', '.')
            git(source, '-c', 'commit.gpgsign=false', 'commit', '-m', 'Source')
            pages.mkdir()
            git(pages, 'init', '-b', 'gh-pages')
            (pages / 'rpm').mkdir()
            old_env = {**env, 'MUSICBEE_VERSION': '0.0.0'}
            subprocess.run(['nfpm', 'package', '--config', 'packaging/nfpm/nfpm.yaml', '--packager',
                            'rpm', '--target', str(pages / 'rpm') + '/'], cwd=source, env=old_env,
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            (pages / 'index.html').write_text('keep this page')
            (pages / 'RPM-GPG-KEY-musicbee').write_text('test public key')
            git(pages, 'add', '.')
            git(pages, '-c', 'commit.gpgsign=false', 'commit', '-m', 'Old release')

            fakebin = root / 'bin'
            fakebin.mkdir()
            gpg = fakebin / 'gpg'
            gpg.write_text(f'''#!/bin/bash
case " $* " in
  *" --show-keys "*) printf 'fpr:::::::::{FINGERPRINT}:\\n' ;;
  *" --export "*) printf '%s\\n' '-----BEGIN PGP PUBLIC KEY BLOCK-----' '' 'TEST' '-----END PGP PUBLIC KEY BLOCK-----' ;;
  *" --detach-sign "*)
    while [[ $# -gt 0 ]]; do
      if [[ "$1" == --output ]]; then printf 'test signature\\n' > "$2"; break; fi
      shift
    done ;;
  *" --clearsign "*)
    while [[ $# -gt 0 ]]; do
      if [[ "$1" == --output ]]; then output="$2"; shift 2; else input="$1"; shift; fi
    done
    cp "$input" "$output" ;;
  *" --decrypt "*) cat "${{@: -1}}" ;;
esac
''')
            (fakebin / 'rpmsign').write_text('#!/bin/sh\nprintf "%s\\n" "$*"\n')
            (fakebin / 'rpmkeys').write_text('#!/bin/sh\nexit 0\n')
            for script in fakebin.iterdir():
                script.chmod(0o755)
            env['PATH'] = str(fakebin) + os.pathsep + env['PATH']
            passphrase = root / 'signing passphrase'
            passphrase.write_text('test passphrase\n')
            env['MUSICBEE_SIGNING_PASSPHRASE_FILE'] = str(passphrase)
            completed = subprocess.run([str(source / 'scripts' / 'prepare-release.sh'), FINGERPRINT, str(pages)],
                                       cwd=source, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            stages = list((source / 'build').glob('release-*'))
            self.assertEqual(len(stages), 1)
            stage = stages[0]
            self.assertIn('--pinentry-mode loopback --passphrase-file', (stage / 'prepare.log').read_text())
            self.assertNotIn('test passphrase', (stage / 'prepare.log').read_text())
            self.assertEqual((stage / 'pages' / 'index.html').read_text(), 'keep this page')
            self.assertEqual([p.name for p in (stage / 'pages' / 'rpm').glob('*.rpm')],
                             [f'musicbee-{VERSION}-1.noarch.rpm'])
            self.assertTrue((stage / 'pages' / 'rpm' / 'repodata' / 'repomd.xml.asc').is_file())
            self.assertTrue((stage / 'pages' / 'apt/dists/stable/InRelease').is_file())
            self.assertTrue((stage / 'pages' / 'musicbee.sources').is_file())
            self.assertTrue((stage / 'aur/PKGBUILD').is_file())
            self.assertTrue((stage / 'aur/.SRCINFO').is_file())
            verifier = source / 'scripts' / 'verify-rpm-metadata.py'
            subprocess.run(['python3', str(verifier), str(stage / 'pages' / 'rpm')], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            rpm = next((stage / 'pages' / 'rpm').glob('*.rpm'))
            with rpm.open('ab') as stream:
                stream.write(b'corrupt')
            corrupted = subprocess.run(['python3', str(verifier), str(stage / 'pages' / 'rpm')],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertNotEqual(corrupted.returncode, 0)
            self.assertEqual((stage / 'tag').read_text().strip(), f'v{VERSION}')
            self.assertEqual((stage / 'source-commit').read_text().strip(), git(source, 'rev-parse', 'HEAD').stdout.strip())
            self.assertTrue((stage / 'release-notes.md').is_file())
            self.assertTrue((pages / 'rpm' / 'musicbee-0.0.0-1.noarch.rpm').exists())


if __name__ == '__main__':
    unittest.main()
