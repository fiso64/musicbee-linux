"""Exercise version selection and verified downloads without network access."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from musicbee_linux import cli, downloads, install, settings


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.payload = b'portable archive contents'
        self.entry = {'filename': 'MusicBeePortable_3.7.9796.33343.zip',
                      'sha256': hashlib.sha256(self.payload).hexdigest()}
        self.index = {'latest': '3.7.9796.33343', 'versions': {'3.7.9796.33343': self.entry}}

    def test_cli_sources_and_version_selection(self):
        parser = cli.create_parser()
        self.assertIsNone(parser.parse_args(['install']).install_version)
        self.assertEqual(parser.parse_args(['install', '--version', '3.7']).install_version, '3.7')
        self.assertEqual(parser.parse_args(['install', '--archive', 'file.zip']).archive, 'file.zip')
        self.assertTrue(parser.parse_args(['install', '--update', '-y']).update)
        self.assertTrue(parser.parse_args(['install', '--update', '-y']).yes)
        self.assertEqual(downloads.select_version(self.index, 'latest'), '3.7.9796.33343')
        self.assertEqual(downloads.select_version(self.index, '3.7.9796'), '3.7.9796.33343')
        with self.assertRaisesRegex(settings.SetupError, 'unavailable'):
            downloads.select_version(self.index, '2.0')
        with self.assertRaisesRegex(settings.SetupError, 'not both'):
            install.install(argparse.Namespace(installer='file.zip', archive=None, install_version='3.7'), {})

    def test_versions_show_installed_and_latest(self):
        with patch.object(downloads, 'catalog', return_value=self.index), \
             patch.object(downloads, 'musicbee_version', return_value='3.7.9796.33343'), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            downloads.list_versions(Path('/unused'))
            self.assertIn('3.7.9796.33343 (latest, installed)', output.getvalue())
        with patch.object(downloads, 'catalog', return_value=self.index), \
             patch.object(downloads, 'musicbee_version', return_value='3.8.1.2'), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            downloads.list_versions(Path('/unused'))
            self.assertIn('Installed: 3.8.1.2', output.getvalue())

    def test_declining_selected_version_does_not_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = argparse.Namespace(installer=None, archive=None, install_version=None, yes=False)
            with patch.object(downloads, 'catalog', return_value=self.index), \
                 patch.object(downloads, 'download') as download, \
                 patch('builtins.input', return_value='n') as prompt:
                install.install(args, {'state': root, 'app': root / 'app'})
                self.assertIn('3.7.9796.33343', prompt.call_args.args[0])
                download.assert_not_called()

    def test_prompt_says_reinstall_for_the_installed_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath('MusicBee.exe').touch()
            args = argparse.Namespace(installer=None, archive=None, install_version=None, yes=False)
            with patch.object(downloads, 'catalog', return_value=self.index), \
                 patch.object(install, 'musicbee_version', return_value=self.index['latest']), \
                 patch.object(downloads, 'download') as download, \
                 patch('builtins.input', return_value='n') as prompt:
                install.install(args, {'state': root, 'app': root})
                self.assertEqual(prompt.call_args.args[0], 'Reinstall MusicBee Portable 3.7.9796.33343? [Y/n] ')
                download.assert_not_called()

    def test_enter_accepts_selected_version_before_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'release.zip'
            source.write_bytes(self.payload)
            args = argparse.Namespace(installer=None, archive=None, install_version=None, yes=False)
            with patch.object(downloads, 'catalog', return_value=self.index), \
                 patch.object(downloads, 'download', return_value=(source, self.index['latest'])) as download, \
                 patch.object(install, 'first_launch'), patch('builtins.input', return_value='') as prompt:
                install.install(args, {'state': root, 'app': root / 'app'})
                self.assertIn('[Y/n]', prompt.call_args.args[0])
                download.assert_called_once_with(root, self.index['latest'], index=self.index)

    def test_update_skips_download_when_installed_version_is_current_or_newer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath('MusicBee.exe').touch()
            args = argparse.Namespace(installer=None, archive=None, install_version=None, update=True, yes=False)
            for current in ('3.7.9796.33343', '3.8.0.0'):
                with self.subTest(current=current), \
                     patch.object(downloads, 'catalog', return_value=self.index), \
                     patch.object(install, 'musicbee_version', return_value=current), \
                     patch.object(downloads, 'download') as download, patch('builtins.input') as prompt:
                    install.install(args, {'state': root, 'app': root})
                    download.assert_not_called()
                    prompt.assert_not_called()

    def test_update_installs_latest_when_older(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, prefix = root / 'app', root / 'prefix'
            app.mkdir()
            prefix.mkdir()
            (app / 'MusicBee.exe').touch()
            (prefix / '.musicbee-bootstrap-complete').touch()
            source = root / 'release.zip'
            with zipfile.ZipFile(source, 'w') as archive:
                archive.writestr('MusicBee.exe', b'program')
            args = argparse.Namespace(installer=None, archive=None, install_version=None, update=True, yes=True)
            with patch.object(downloads, 'catalog', return_value=self.index), \
                 patch.object(downloads, 'download', return_value=(source, self.index['latest'])) as download, \
                 patch.object(install, 'musicbee_version', return_value='3.6.9403.32563'), \
                 patch.object(install, 'unpack_portable'), patch.object(install, 'ensure_aac_decoder'), \
                 patch.object(install, 'regenerate_launcher'), patch('builtins.input') as prompt:
                install.install(args, {'state': root, 'prefix': prefix, 'app': app})
                download.assert_called_once_with(root, self.index['latest'], index=self.index)
                prompt.assert_not_called()

    def test_update_reports_unknown_installed_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath('MusicBee.exe').touch()
            args = argparse.Namespace(installer=None, archive=None, install_version=None, update=True, yes=True)
            with patch.object(downloads, 'catalog', return_value=self.index), \
                 patch.object(install, 'musicbee_version', return_value='unknown'), \
                 patch.object(downloads, 'download') as download:
                with self.assertRaisesRegex(settings.SetupError, 'Could not determine'):
                    install.install(args, {'state': root, 'app': root})
                download.assert_not_called()

    def test_download_verifies_and_reuses_cache(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(downloads, 'catalog', return_value=self.index), \
             patch.object(downloads.urllib.request, 'urlopen', side_effect=lambda *a, **kw: io.BytesIO(self.payload)) as fetch:
            target, version = downloads.download(Path(tmp))
            self.assertEqual(version, '3.7.9796.33343')
            self.assertEqual(target.read_bytes(), self.payload)
            self.assertIn('/3.7.9796.33343/' + self.entry['filename'], fetch.call_args.args[0])
            self.assertEqual(downloads.download(Path(tmp)), (target, version))
            self.assertEqual(fetch.call_count, 1)

    def test_bad_checksum_does_not_replace_existing_archive(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(downloads, 'catalog', return_value=self.index), \
             patch.object(downloads.urllib.request, 'urlopen', return_value=io.BytesIO(b'tampered')):
            directory = Path(tmp) / 'downloads'
            directory.mkdir()
            target = directory / self.entry['filename']
            target.write_bytes(b'old')
            with self.assertRaisesRegex(settings.SetupError, 'checksum'):
                downloads.download(Path(tmp))
            self.assertEqual(target.read_bytes(), b'old')
            self.assertEqual(list(directory.iterdir()), [target])

    def test_first_install_downloads_once_and_passes_archive_to_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'release.zip'
            source.write_bytes(self.payload)
            paths = {'state': root, 'app': root / 'app'}
            args = argparse.Namespace(installer=None, archive=None, install_version='3.7', keep_backup=None, yes=True)
            with patch.object(install.downloads, 'catalog', return_value=self.index), \
                 patch.object(install.downloads, 'download', return_value=(source, '3.7.9796.33343')) as download, \
                 patch.object(install, 'first_launch') as first_launch:
                install.install(args, paths)
                download.assert_called_once_with(root, '3.7.9796.33343', index=self.index)
                self.assertEqual(first_launch.call_args.args[0].archive, str(source))

    def test_installing_a_selected_version_has_no_compatibility_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, prefix = root / 'app', root / 'prefix'
            app.mkdir()
            prefix.mkdir()
            (app / 'MusicBee.exe').touch()
            (prefix / '.musicbee-bootstrap-complete').touch()
            source = root / 'release.zip'
            with zipfile.ZipFile(source, 'w') as archive:
                archive.writestr('MusicBee.exe', b'program')
            self.index['versions']['3.6.9403.32563'] = self.entry
            args = argparse.Namespace(installer=None, archive=None, install_version='3.6', keep_backup=False, yes=True)
            with patch.object(install.downloads, 'catalog', return_value=self.index), \
                 patch.object(install.downloads, 'download', return_value=(source, '3.6.9403.32563')), \
                 patch.object(install, 'unpack_portable'), patch.object(install, 'ensure_aac_decoder'), \
                 patch.object(install, 'regenerate_launcher'), patch('sys.stdout', new_callable=io.StringIO) as out:
                install.install(args, {'state': root, 'prefix': prefix, 'app': app})
                self.assertNotIn('Warning:', out.getvalue())

    def test_catalog_reads_release_assets_and_sorts_versions(self):
        def release(version, *, prerelease=False):
            return {'tag_name': version, 'prerelease': prerelease, 'draft': False, 'assets': [
                {'name': self.entry['filename'], 'state': 'uploaded', 'digest': 'sha256:' + self.entry['sha256']}]}
        # A full first page must be followed, even when most releases are unrelated.
        first = [release('3.6.9403.32563')] + [{'tag_name': 'unrelated'}] * 99
        second = [release('3.7.9796.33343'), release('9.0.0', prerelease=True)]
        responses = [io.BytesIO(json.dumps(page).encode()) for page in (first, second)]
        with patch.object(downloads.urllib.request, 'urlopen', side_effect=responses) as fetch:
            index = downloads.catalog()
            self.assertEqual(index['latest'], '3.7.9796.33343')
            self.assertEqual(set(index['versions']), {'3.6.9403.32563', '3.7.9796.33343'})
            self.assertEqual(index['versions'][index['latest']], self.entry)
            self.assertIn('page=2', fetch.call_args.args[0].full_url)

    def test_catalog_requires_verified_unambiguous_assets(self):
        asset = {'name': self.entry['filename'], 'state': 'uploaded', 'digest': None}
        release = {'tag_name': '3.7.9796.33343', 'assets': [asset]}
        with patch.object(downloads.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps([release]).encode())):
            with self.assertRaisesRegex(settings.SetupError, 'SHA-256'):
                downloads.catalog()
        asset['digest'] = 'sha256:' + self.entry['sha256']
        release['assets'].append(asset.copy())
        with patch.object(downloads.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps([release]).encode())):
            with self.assertRaisesRegex(settings.SetupError, 'ambiguous'):
                downloads.catalog()

    def test_catalog_reports_rate_limit(self):
        error = urllib.error.HTTPError(downloads.RELEASES_URL, 403, 'rate limited', {}, None)
        with patch.object(downloads.urllib.request, 'urlopen', side_effect=error):
            with self.assertRaisesRegex(settings.SetupError, 'rate-limited.*--archive'):
                downloads.catalog()
