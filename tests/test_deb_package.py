"""Check the built DEB rather than only its packaging configuration."""
import io
import runpy
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('nfpm') and shutil.which('ar'), 'nFPM and ar are required')
class DebPackageTests(unittest.TestCase):
    def test_build_has_expected_metadata_and_launchers(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([str(ROOT / 'scripts/build-package.sh'), 'deb', tmp],
                           cwd=ROOT, check=True, stdout=subprocess.PIPE, text=True)
            packages = list(Path(tmp).glob('*.deb'))
            self.assertEqual(len(packages), 1)
            version = runpy.run_path(str(ROOT / 'src/musicbee_linux/__init__.py'))['__version__']
            self.assertEqual(packages[0].name, f'musicbee_{version}-1_all.deb')

            def archive(name):
                data = subprocess.check_output(['ar', 'p', str(packages[0]), name])
                return tarfile.open(fileobj=io.BytesIO(data), mode='r:*')

            with archive('control.tar.gz') as control_archive:
                control = control_archive.extractfile('./control').read().decode()
            self.assertIn(f'Version: {version}-1\n', control)
            self.assertIn('Architecture: all\n', control)
            self.assertIn('wine | wine-staging', control)
            self.assertIn('7zip | p7zip-full', control)
            self.assertIn('winetricks', control)

            with archive('data.tar.gz') as data_archive:
                for path in ('./usr/bin/musicbee', './usr/bin/musicbee-manage',
                             './usr/libexec/musicbee/musicbee-manage.py'):
                    self.assertTrue(data_archive.getmember(path).mode & 0o111, path)
                for path in ('./usr/libexec/musicbee/musicbee_linux/settings.py',
                             './usr/libexec/musicbee/musicbee_linux/paths.py',
                             './usr/libexec/musicbee/musicbee_linux/archive.py',
                             './usr/libexec/musicbee/musicbee_linux/install.py',
                             './usr/libexec/musicbee/musicbee_linux/cli.py',
                             './usr/libexec/musicbee/musicbee_linux/wine.py',
                             './usr/libexec/musicbee/musicbee_linux/desktop.py',
                             './usr/libexec/musicbee/musicbee_linux/media_controls.py',
                             './usr/libexec/musicbee/musicbee_linux/utils.py'):
                    self.assertIsNotNone(data_archive.getmember(path))
                self.assertIsNotNone(data_archive.getmember('./usr/share/applications/musicbee.desktop'))
