"""Test AUR generation from the package metadata and installed file list."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('aur_generator', ROOT / 'scripts/generate-aur.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class AurGeneratorTests(unittest.TestCase):
    def test_shared_metadata_and_files_are_used(self):
        config = yaml.safe_load((ROOT / 'packaging/nfpm/nfpm.yaml').read_text())
        config['description'] = 'Test description\n'
        config['homepage'] = 'https://example.test/project'
        config['license'] = 'MIT'
        config['overrides']['archlinux']['depends'].append('test-dependency')
        commit = 'a' * 40
        pkgbuild, srcinfo = generator.generate(config, '1.2.3', commit)
        for value in ('Test description', 'https://example.test/project', 'MIT', 'test-dependency', commit):
            self.assertIn(value, pkgbuild)
            self.assertIn(value, srcinfo)
        self.assertLess(srcinfo.index('\tmakedepends = git'), srcinfo.index('\tdepends = python'))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'src/musicbee-linux'
            source.mkdir(parents=True)
            for name in ('src', 'packaging', 'desktop'):
                shutil.copytree(ROOT / name, source / name)
            for name in ('README.md', 'LICENSE'):
                shutil.copy2(ROOT / name, source / name)
            build = root / 'PKGBUILD'
            build.write_text(pkgbuild)
            subprocess.run(['bash', '-n', str(build)], check=True)
            env = {**os.environ, 'srcdir': str(source.parent), 'pkgdir': str(root / 'pkg')}
            subprocess.run(['bash', '-c', 'source "$1"; package', 'bash', str(build)], env=env, check=True)
            for item in config['contents']:
                installed = root / 'pkg' / item['dst'].lstrip('/')
                self.assertTrue(installed.is_file(), installed)
                self.assertEqual(installed.stat().st_mode & 0o777, item['file_info']['mode'])

    def test_requires_full_commit_and_release_version(self):
        config = yaml.safe_load((ROOT / 'packaging/nfpm/nfpm.yaml').read_text())
        with self.assertRaises(ValueError):
            generator.generate(config, '0.3.0', 'main')
        with self.assertRaises(ValueError):
            generator.generate(config, 'v0.3.0', 'a' * 40)
