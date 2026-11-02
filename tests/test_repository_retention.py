"""Cached indexes must keep their referenced immutable files available."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('retention', ROOT / 'scripts/retain-repository-files.py')
retention = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retention)


class RepositoryRetentionTests(unittest.TestCase):
    def test_preserves_old_files_across_successive_releases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, new, later = (root / name for name in ('old', 'new', 'later'))
            payload = b'old rpm metadata'
            checksum = hashlib.sha256(payload).hexdigest()
            files = {
                'rpm/musicbee-1.rpm': b'old rpm',
                f'rpm/repodata/{checksum}-primary.xml.gz': payload,
                'apt/pool/main/musicbee_1.deb': b'old deb',
                'apt/dists/stable/main/binary-amd64/Packages.gz': b'old apt index',
                'arch/musicbee-1.pkg.tar.zst': b'old arch',
                'arch/musicbee-1.pkg.tar.zst.sig': b'old signature',
            }
            for name, data in files.items():
                path = old / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            retention.retain(old, new)
            retention.retain(new, later)
            for name in files:
                if 'Packages.gz' not in name:
                    self.assertEqual((later / name).read_bytes(), files[name])
            digest = hashlib.sha256(b'old apt index').hexdigest()
            self.assertEqual((later / 'apt/dists/stable/main/binary-amd64/by-hash/SHA256' / digest).read_bytes(), b'old apt index')

    def test_rejects_reusing_package_filename_for_different_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, data in (('old', b'old'), ('new', b'changed')):
                path = root / name / 'rpm/musicbee-1.rpm'
                path.parent.mkdir(parents=True)
                path.write_bytes(data)
            with self.assertRaisesRegex(ValueError, 'Immutable repository file changed'):
                retention.retain(root / 'old', root / 'new')
