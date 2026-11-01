"""Check the Wine dependency in the built RPM."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('nfpm') and shutil.which('rpm'), 'nFPM and rpm are required')
class RpmPackageTests(unittest.TestCase):
    def test_wine_alternatives(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([str(ROOT / 'scripts/build-package.sh'), 'rpm', tmp],
                           cwd=ROOT, check=True, stdout=subprocess.PIPE)
            package, = Path(tmp).glob('*.rpm')
            dependencies = subprocess.check_output(['rpm', '-qp', '--requires', str(package)], text=True)
            self.assertIn('(wine or wine-staging or winehq-stable or winehq-staging or winehq-devel)',
                          dependencies.splitlines())
