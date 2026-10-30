"""Check that the Arch database verification detects mismatched packages."""
import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('verify_arch_repo', ROOT / 'scripts/verify-arch-repo.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class ArchRepositoryTests(unittest.TestCase):
    def test_database_must_match_package_and_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / 'musicbee-1.2.3-1-any.pkg.tar.zst'
            package.write_bytes(b'package contents')
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            desc = (f'%NAME%\nmusicbee\n\n%VERSION%\n1.2.3-1\n\n'
                    f'%FILENAME%\n{package.name}\n\n%SHA256SUM%\n{digest}\n\n').encode()
            with tarfile.open(root / 'musicbee-linux.db', 'w:gz') as db:
                info = tarfile.TarInfo('musicbee-1.2.3-1/desc')
                info.size = len(desc)
                db.addfile(info, io.BytesIO(desc))
            verifier.verify(root, '1.2.3')
            with self.assertRaises(ValueError):
                verifier.verify(root, '1.2.4')
            package.write_bytes(b'tampered')
            with self.assertRaises(ValueError):
                verifier.verify(root, '1.2.3')
