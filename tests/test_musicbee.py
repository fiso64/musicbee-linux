"""Offline tests: python3 -m unittest discover -s tests -v"""
from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import fcntl
import getpass
import os
import platform
import shutil
import io
import json
import subprocess
import sys
import unittest
import zipfile
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / 'src'
sys.path.insert(0, str(SRC))
from musicbee_linux import archive as portable_archive, cli, desktop as desktop_integration, media_controls, settings, utils, wine, install as installation
from musicbee_linux import __version__



class PortableExtractionTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(installation, 'ensure_aac_decoder')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_aac_install_extracts_32bit_dll_and_preserves_existing(self):
        import urllib.request
        with TemporaryDirectory() as tmp:
            app = Path(tmp)
            (app / 'MusicBee.exe').touch()
            dll = bytearray(128)
            dll[:2] = b'MZ'
            dll[60:64] = (64).to_bytes(4, 'little')
            dll[64:68] = b'PE\0\0'
            dll[68:70] = (0x14c).to_bytes(2, 'little')
            dll[86:88] = (0x2000).to_bytes(2, 'little')
            data = io.BytesIO()
            with zipfile.ZipFile(data, 'w') as archive:
                archive.writestr('bass_aac.dll', dll)
                archive.writestr('x64/bass_aac.dll', b'wrong architecture')
            with patch.object(urllib.request, 'urlopen', return_value=io.BytesIO(data.getvalue())) as download:
                installation.install_aac_decoder({'app': app})
                self.assertEqual((app / 'bass_aac.dll').read_bytes(), bytes(dll))
                installation.install_aac_decoder({'app': app})
                self.assertEqual(download.call_count, 1)

    def test_aac_rejects_wrong_architecture_without_creating_file(self):
        import urllib.request
        with TemporaryDirectory() as tmp:
            app = Path(tmp)
            (app / 'MusicBee.exe').touch()
            dll = bytearray(128)
            dll[:2] = b'MZ'
            dll[60:64] = (64).to_bytes(4, 'little')
            dll[64:68] = b'PE\0\0'
            dll[68:70] = (0x8664).to_bytes(2, 'little')
            dll[86:88] = (0x2000).to_bytes(2, 'little')
            data = io.BytesIO()
            with zipfile.ZipFile(data, 'w') as archive:
                archive.writestr('bass_aac.dll', dll)
            with patch.object(urllib.request, 'urlopen', return_value=io.BytesIO(data.getvalue())):
                with self.assertRaisesRegex(settings.SetupError, '32-bit'):
                    installation.install_aac_decoder({'app': app})
            self.assertFalse((app / 'bass_aac.dll').exists())

    def test_update_preserves_existing_aac_decoder(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / 'MusicBee'
            app.mkdir()
            (app / 'MusicBee.exe').write_bytes(b'old')
            (app / 'bass_aac.dll').write_bytes(b'existing decoder')
            archive = root / 'update.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('MusicBee.exe', b'new')
                z.writestr('bass_aac.dll', b'incoming decoder')
            portable_archive.unpack_portable(archive, app, update=True, keep_backup=False)
            self.assertEqual((app / 'bass_aac.dll').read_bytes(), b'existing decoder')

    def test_ubuntu_wineserver_is_used_for_wine_and_winetricks(self):
        server = '/usr/lib/x86_64-linux-gnu/wine/wineserver'
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(wine, 'wine_selection', return_value='auto'), \
             patch.object(shutil, 'which', return_value=None), \
             patch.object(platform, 'machine', return_value='x86_64'), \
             patch.object(Path, 'is_file', return_value=True), \
             patch.object(os, 'access', side_effect=lambda path, mode: str(path) == server):
            self.assertEqual(wine.wine_server(), server)
            self.assertEqual(wine.wine_env(Path('/tmp/prefix'))['WINESERVER'], server)

    def test_winehq_staging_fallback_keeps_helpers_together(self):
        with patch.dict(os.environ, {'PATH': '/usr/bin'}, clear=True), \
             patch.object(shutil, 'which', side_effect=lambda name, path=None:
                          f'/opt/wine-staging/bin/{name}' if path.startswith('/opt/wine-staging/bin:') else None), \
             patch.object(os, 'access', side_effect=lambda path, mode:
                          str(path).startswith('/opt/wine-staging/bin/')):
            env = wine.wine_env(Path('/tmp/prefix'))
            self.assertTrue(env['PATH'].startswith('/opt/wine-staging/bin:'))
            self.assertEqual(wine.wine_server(), '/opt/wine-staging/bin/wineserver')
            wine.require_commands('wine', 'winepath', 'wineboot', 'wineserver')

    def test_winehq_stable_fallback_uses_matching_server(self):
        with patch.dict(os.environ, {'PATH': '/usr/bin', 'WINESERVER': '/wrong/server'}, clear=True), \
             patch.object(wine, 'wine_selection', return_value='auto'), \
             patch.object(shutil, 'which', return_value=None), \
             patch.object(os, 'access', side_effect=lambda path, mode:
                          str(path).startswith('/opt/wine-stable/bin/')):
            env = wine.wine_env(Path('/tmp/prefix'))
            self.assertEqual(env['WINE'], '/opt/wine-stable/bin/wine')
            self.assertEqual(env['WINESERVER'], '/opt/wine-stable/bin/wineserver')
            self.assertTrue(env['PATH'].startswith('/opt/wine-stable/bin:'))

    def test_multiple_winehq_branches_require_selection(self):
        with patch.dict(os.environ, {'PATH': '/usr/bin'}, clear=True), \
             patch.object(wine, 'wine_selection', return_value='auto'), \
             patch.object(shutil, 'which', return_value=None), \
             patch.object(os, 'access', side_effect=lambda path, mode:
                          str(path).startswith(('/opt/wine-stable/', '/opt/wine-staging/'))):
            with self.assertRaisesRegex(settings.SetupError, 'Multiple WineHQ versions'):
                wine.wine_path()

    def test_existing_wine_on_path_takes_precedence_over_staging(self):
        with patch.dict(os.environ, {'PATH': '/custom/bin:/usr/bin'}, clear=True), \
             patch.object(shutil, 'which', return_value='/custom/bin/wine'), \
             patch.object(os, 'access', return_value=True):
            self.assertEqual(wine.wine_path(), '/custom/bin:/usr/bin')

    def test_explicit_staging_uses_matching_wine_and_helpers(self):
        with TemporaryDirectory() as tmp:
            state = Path(tmp)
            prefix = state / 'wineprefix'
            paths = {'state': state, 'prefix': prefix}
            with patch.dict(os.environ, {'PATH': '/usr/bin', 'WINESERVER': '/old/wineserver'}, clear=True), \
                 patch.object(os, 'access', return_value=True):
                wine.configure(argparse.Namespace(option='wine', value='staging'), paths)
                env = wine.wine_env(prefix)
                self.assertEqual(env['WINE'], '/opt/wine-staging/bin/wine')
                self.assertEqual(env['WINESERVER'], '/opt/wine-staging/bin/wineserver')
                self.assertTrue(env['PATH'].startswith('/opt/wine-staging/bin:'))
                wine.configure(argparse.Namespace(option='wine', value='auto'), paths)
                self.assertEqual(settings.tweak_value(settings.read_config(state), 'wine'), 'auto')

    def test_staging_setting_rejects_missing_installation_without_saving(self):
        with TemporaryDirectory() as tmp:
            state = Path(tmp)
            with patch.object(os, 'access', return_value=False):
                with self.assertRaisesRegex(settings.SetupError, 'missing wine'):
                    wine.configure(argparse.Namespace(option='wine', value='staging'),
                                 {'state': state, 'prefix': state / 'wineprefix'})
            self.assertEqual(settings.read_config(state), {})

    def test_musicbee_path_arguments(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            song = root / 'a song.flac'
            song.touch()
            paths = [str(song), '/Play', 'relative list.m3u', '/QueueLast',
                     'https://example.com/music', '/QueueNext', r'C:\Music\track.flac', '/Next']
            conversions = [subprocess.CompletedProcess([], 0, 'Z:\\song.flac\n', ''),
                           subprocess.CompletedProcess([], 0, 'Z:\\list.m3u\n', '')]
            with patch.object(wine, 'require_commands'), \
                 patch.object(subprocess, 'run', side_effect=conversions) as winepath:
                result = wine.musicbee_arguments(paths, root)
            self.assertEqual(result, ['Z:\\song.flac', '/Play', 'Z:\\list.m3u', '/QueueLast',
                                      'https://example.com/music', '/QueueNext', r'C:\Music\track.flac', '/Next'])
            self.assertEqual(winepath.call_count, 2)
            self.assertTrue(Path(winepath.call_args.args[0][-1]).is_absolute())

    def test_running_instance_accepts_commands_but_maintenance_does_not(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / 'prefix'
            app = prefix / 'drive_c' / 'MusicBee'
            app.mkdir(parents=True)
            (app / 'MusicBee.exe').touch()
            (prefix / '.musicbee-bootstrap-complete').touch()
            paths = {'prefix': prefix, 'app': app, 'state': root / 'state', 'bin': root / 'musicbee'}
            with (prefix / '.musicbee.launch.lock').open('a+') as main_lock:
                fcntl.flock(main_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(wine, 'require_commands'), patch.object(wine, 'call') as call, \
                     patch.object(desktop_integration, 'configure_explorer_redirect') as prepare:
                    args = argparse.Namespace(musicbee_args=['/Next'])
                    with self.assertRaisesRegex(settings.SetupError, 'being modified'):
                        wine.run_musicbee(args, paths)
                    with (prefix / '.musicbee.active.lock').open('a+') as active:
                        fcntl.flock(active, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        wine.run_musicbee(args, paths)
                    self.assertEqual(call.call_args.args[0], ['wine', 'MusicBee.exe', '/Next'])
                    prepare.assert_not_called()
                    with self.assertRaisesRegex(settings.SetupError, 'already appears to be running'):
                        wine.run_musicbee(argparse.Namespace(musicbee_args=[]), paths)

    def test_music_dir_helper_creates_and_updates_only_its_mapping(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / 'prefix'
            (prefix / 'drive_c').mkdir(parents=True)
            (prefix / '.musicbee-bootstrap-complete').touch()
            first, second = root / 'first', root / 'second'
            first.mkdir()
            second.mkdir()
            paths = {'prefix': prefix, 'state': root / 'state'}
            args = argparse.Namespace(directory=str(first), wine_music_path=r'C:\MyMusic')
            utils.configure_music_dir(args, paths)
            link = prefix / 'drive_c' / 'MyMusic'
            self.assertEqual(link.resolve(), first)
            self.assertEqual(settings.read_config(paths['state'])['windows_music_path'], r'C:\MyMusic')
            utils.configure_music_dir(argparse.Namespace(directory=str(second), wine_music_path=None), paths)
            self.assertEqual(link.resolve(), second)
            unrelated = prefix / 'drive_c' / 'OtherMusic'
            unrelated.symlink_to(first, target_is_directory=True)
            with self.assertRaisesRegex(settings.SetupError, 'unrelated symlink'):
                utils.configure_music_dir(argparse.Namespace(directory=str(second), wine_music_path=r'C:\OtherMusic'), paths)
            self.assertEqual(unrelated.resolve(), first)
            self.assertEqual(link.resolve(), second)

    def test_music_dir_helper_replaces_wines_default_music_link(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / 'prefix'
            (prefix / '.musicbee-bootstrap-complete').parent.mkdir()
            (prefix / '.musicbee-bootstrap-complete').touch()
            default_music = root / 'Music'
            custom_music = root / 'custom'
            default_music.mkdir()
            custom_music.mkdir()
            link = prefix / 'drive_c' / 'users' / getpass.getuser() / 'Music'
            link.parent.mkdir(parents=True)
            link.symlink_to(default_music, target_is_directory=True)
            with patch.object(Path, 'home', return_value=root):
                utils.configure_music_dir(argparse.Namespace(directory=str(custom_music), wine_music_path=None),
                                       {'prefix': prefix, 'state': root / 'state'})
            self.assertEqual(link.resolve(), custom_music)
            self.assertTrue(default_music.is_dir())

    def test_launch_arguments_and_management_entrypoint(self):
        with TemporaryDirectory() as tmp:
            app = Path(tmp) / 'app'
            app.mkdir()
            (app / 'MusicBee.exe').touch()
            paths = {'app': app}
            with patch.object(os, 'geteuid', return_value=1000), \
                 patch.object(utils, 'locations', return_value=paths), \
                 patch.object(wine, 'run_musicbee') as run, patch.object(wine, 'configure') as configure:
                for argv in (['musicbee', '/Play', r'C:\Music\song.mp3'],
                             ['musicbee-manage.py', '--launch', '/Play', r'C:\Music\song.mp3']):
                    with patch.object(sys, 'argv', argv):
                        self.assertEqual(cli.main(), 0)
                        self.assertEqual(run.call_args.args[0].musicbee_args, ['/Play', r'C:\Music\song.mp3'])
                for argv in (['musicbee', 'config'], ['musicbee', '--manage']):
                    with patch.object(sys, 'argv', argv):
                        self.assertEqual(cli.main(), 0)
                        self.assertEqual(run.call_args.args[0].musicbee_args, argv[1:])
                for argv in (['musicbee-manage', 'config'], ['musicbee-manage.py', '--manage', 'config']):
                    with patch.object(sys, 'argv', argv):
                        self.assertEqual(cli.main(), 0)
                self.assertEqual(configure.call_count, 2)

    def test_launcher_help_does_not_start_musicbee(self):
        for argv in (['musicbee', '-h'], ['musicbee', '--help'],
                     ['musicbee-manage.py', '--launch', '--help']):
            with self.subTest(argv=argv), patch.object(sys, 'argv', argv), \
                 patch('sys.stdout', new_callable=io.StringIO) as output, \
                 patch.object(wine, 'run_musicbee') as run:
                self.assertEqual(cli.main(), 0)
                self.assertIn('musicbee-manage --help', output.getvalue())
                self.assertIn('/QueueNext', output.getvalue())
                run.assert_not_called()

    def test_manager_and_launcher_report_same_versions(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {'app': root / 'app', 'prefix': root / 'prefix'}
            reports = []
            for executable in ('musicbee', 'musicbee-manage'):
                with patch.object(sys, 'argv', [executable, '--version']), \
                     patch.object(utils, 'locations', return_value=paths), \
                     patch.object(wine, 'wine_env', side_effect=settings.SetupError('missing')), \
                     patch('sys.stdout', new_callable=io.StringIO) as output, \
                     patch.object(wine, 'run_musicbee') as run:
                    self.assertEqual(cli.main(), 0)
                    reports.append(output.getvalue())
                    run.assert_not_called()
            self.assertEqual(reports[0], reports[1])
            self.assertIn(f'musicbee-linux {__version__}', reports[0])
            self.assertIn('- musicbee/version: not installed', reports[0])

    def test_musicbee_version_reads_version_resource(self):
        import struct
        with TemporaryDirectory() as tmp:
            app = Path(tmp)
            (app / 'MusicBee.exe').touch()
            key = 'VS_VERSION_INFO\0'.encode('utf-16le')
            header = struct.pack('<HHH', 92, 52, 0) + key
            header += bytes((-len(header)) % 4)
            resource = header + struct.pack('<IIII', 0xFEEF04BD, 0x10000,
                                            (3 << 16) | 7, (9773 << 16) | 28129) + bytes(36)
            with patch.object(shutil, 'which', return_value='/usr/bin/wrestool'), \
                 patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, resource)):
                self.assertEqual(wine.musicbee_version(app), '3.7.9773.28129')
            with patch.object(shutil, 'which', return_value='/usr/bin/wrestool'), \
                 patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'bad')):
                self.assertEqual(wine.musicbee_version(app), 'unknown')

    def test_tabs_move_preserves_settings_and_selected_tab(self):
        with TemporaryDirectory() as tmp:
            prefix = Path(tmp) / 'prefix'
            app = prefix / 'drive_c' / 'MusicBee'
            settings = app / 'AppData' / 'MusicBee3Settings.ini'
            settings.parent.mkdir(parents=True)
            original = (b'<Settings>\r\n<NavigatorInitialTabs>\r\n'
                        b' <NavigationLocation><TextOverride>One</TextOverride><Value>\x00</Value></NavigationLocation>\r\n'
                        b' <NavigationLocation><TextOverride>Two &amp; More</TextOverride></NavigationLocation>\r\n'
                        b' <NavigationLocation><FolderCategory>7</FolderCategory></NavigationLocation>\r\n'
                        b'</NavigatorInitialTabs>\r\n<NavigatorInitialTabIndex>1</NavigatorInitialTabIndex>\r\n</Settings>')
            settings.write_bytes(original)
            paths = {'prefix': prefix, 'app': app}
            output = io.StringIO()
            with patch('sys.stdout', output):
                utils.manage_tabs(argparse.Namespace(settings=None, source=None, destination=None), paths)
            self.assertIn('2. Two & More', output.getvalue())
            utils.manage_tabs(argparse.Namespace(settings=None, source=2, destination=1), paths)
            changed = settings.read_bytes()
            self.assertLess(changed.index(b'Two &amp; More'), changed.index(b'One'))
            self.assertIn(b'<NavigatorInitialTabIndex>0</NavigatorInitialTabIndex>', changed)
            self.assertIn(b'<Value>\x00</Value>', changed)
            utils.manage_tabs(argparse.Namespace(settings=None, source=1, destination=2), paths)
            self.assertEqual(settings.read_bytes(), original)

    def test_tabs_requires_choice_when_settings_are_ambiguous(self):
        with TemporaryDirectory() as tmp:
            prefix = Path(tmp) / 'prefix'
            app = prefix / 'drive_c' / 'MusicBee'
            portable = app / 'AppData' / 'MusicBee3Settings.ini'
            profile = prefix / 'drive_c' / 'users' / getpass.getuser() / 'AppData' / 'Roaming' / 'MusicBee' / 'MusicBee3Settings.ini'
            for path in (portable, profile):
                path.parent.mkdir(parents=True)
                path.touch()
            paths = {'prefix': prefix, 'app': app}
            with self.assertRaisesRegex(settings.SetupError, 'Use --settings'):
                utils.tab_settings_path(paths, None)
            self.assertEqual(utils.tab_settings_path(paths, 'portable'), portable)
            self.assertEqual(utils.tab_settings_path(paths, 'profile'), profile)

    def create_archive(self, tmp: Path) -> Path:
        sfx = tmp / 'MusicBeePortable_3_6.exe'
        # Python's zipfile accepts plain ZIP-format SFX executables.
        with zipfile.ZipFile(sfx, 'w') as z:
            z.writestr('payload/MusicBee.exe', b'test executable')
            z.writestr('payload/Plugins/example.dll', b'dummy')
        outer = tmp / 'MusicBeePortable_3_6_Update2.zip'
        with zipfile.ZipFile(outer, 'w') as z:
            z.write(sfx, arcname=sfx.name)
        return outer

    def test_nested_portable_sfx_is_automatic(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = self.create_archive(root)
            target = root / 'prefix' / 'drive_c' / 'MusicBee'
            target.parent.mkdir(parents=True)
            portable_archive.unpack_portable(archive, target)
            self.assertEqual((target / 'MusicBee.exe').read_bytes(), b'test executable')
            self.assertTrue((target / 'Plugins/example.dll').is_file())
            portable_archive.unpack_portable(archive, target)  # does not overwrite existing installation
            self.assertTrue((target / 'MusicBee.exe').is_file())

    def test_portable_exe_installs_and_updates_directly(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.create_archive(root)
            exe = root / 'MusicBeePortable_3_6.exe'
            app = root / 'MusicBee'
            portable_archive.unpack_portable(exe, app)
            self.assertEqual((app / 'MusicBee.exe').read_bytes(), b'test executable')
            (app / 'MusicBee.exe').write_bytes(b'old version')
            backup = portable_archive.unpack_portable(exe, app, update=True)
            self.assertEqual((app / 'MusicBee.exe').read_bytes(), b'test executable')
            self.assertEqual((backup / 'MusicBee.exe').read_bytes(), b'old version')

    def test_preexisting_app_data_not_overwritten(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = self.create_archive(root)
            target = root / 'prefix' / 'drive_c' / 'MusicBee'
            target.mkdir(parents=True)
            (target / 'important').write_text('do not touch')
            with self.assertRaises(settings.SetupError):
                portable_archive.unpack_portable(archive, target)
            self.assertEqual((target / 'important').read_text(), 'do not touch')

    def test_update_preserves_user_data_and_keeps_backup(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'update.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('MusicBee.exe', b'new executable')
                z.writestr('basswma.dll', b'new wma')
                z.writestr('Configuration.xml', b'new default config')
                z.writestr('Plugins/example.dll', b'new plugin')
            app = root / 'MusicBee'
            (app / 'Plugins').mkdir(parents=True)
            (app / 'MusicBee.exe').write_bytes(b'old executable')
            (app / 'basswma.dll.musicbee-disabled').write_bytes(b'old wma')
            (app / 'Configuration.xml').write_bytes(b'user config')
            (app / 'Plugins/example.dll').write_bytes(b'user plugin')
            (app / 'library.db').write_bytes(b'user library')
            backup = portable_archive.unpack_portable(archive, app, update=True)
            self.assertEqual((app / 'MusicBee.exe').read_bytes(), b'new executable')
            self.assertEqual((app / 'basswma.dll.musicbee-disabled').read_bytes(), b'new wma')
            self.assertFalse((app / 'basswma.dll').exists())
            self.assertEqual((app / 'Configuration.xml').read_bytes(), b'user config')
            self.assertEqual((app / 'Plugins/example.dll').read_bytes(), b'user plugin')
            self.assertEqual((app / 'library.db').read_bytes(), b'user library')
            self.assertEqual((backup / 'MusicBee.exe').read_bytes(), b'old executable')

    def test_missing_archive_instructs_user_to_download(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(settings.SetupError, 'musicbee-manage install'):
                installation.packaged_archive({})

    def test_nonportable_zip_rejected_before_first_setup(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'MusicBeeSetup.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('MusicBeeSetup_3_6.exe', b'installer')
            paths = {'state': root / 'state', 'prefix': root / 'prefix', 'app': root / 'app'}
            args = argparse.Namespace(music_dir=None, wine_music_path=None, archive=str(archive),
                                      accept_license=False, no_launch=True)
            with patch.object(installation, 'initialize_prefix') as setup_mock, patch.object(installation, 'confirm_license') as consent_mock:
                with self.assertRaisesRegex(settings.SetupError, 'non-portable.*Portable Edition'):
                    installation.first_launch(args, paths)
                setup_mock.assert_not_called()
                consent_mock.assert_not_called()

    def test_nonportable_update_rejected_without_prompt_or_changes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'MusicBeeSetup.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('MusicBeeSetup_3_6.exe', b'installer')
            app = root / 'MusicBee'
            app.mkdir()
            (app / 'MusicBee.exe').write_bytes(b'original')
            paths = {'state': root / 'state', 'prefix': root / 'prefix', 'app': app}
            with patch('builtins.input') as prompt:
                with self.assertRaisesRegex(settings.SetupError, 'non-portable.*Portable Edition'):
                    installation.install(argparse.Namespace(installer=str(archive), keep_backup=None), paths)
                prompt.assert_not_called()
            self.assertEqual((app / 'MusicBee.exe').read_bytes(), b'original')
            self.assertEqual(list(root.glob('MusicBee.backup-*')), [])

    def test_nonportable_exe_is_rejected(self):
        with TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'MusicBeeSetup_3_6.exe'
            exe.write_bytes(b'installer')
            with self.assertRaisesRegex(settings.SetupError, 'non-portable.*Portable Edition'):
                portable_archive.validate_portable_archive(exe)

    def test_update_can_skip_retained_backup(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'update.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('MusicBee.exe', b'updated')
            app = root / 'MusicBee'
            app.mkdir()
            (app / 'MusicBee.exe').write_bytes(b'original')
            self.assertIsNone(portable_archive.unpack_portable(archive, app, update=True, keep_backup=False))
            self.assertEqual((app / 'MusicBee.exe').read_bytes(), b'updated')
            self.assertEqual(list(root.glob('MusicBee.backup-*')), [])

    def test_install_backup_flags(self):
        parser = cli.create_parser()
        self.assertIsNone(parser.parse_args(['install', 'file.zip']).keep_backup)
        self.assertTrue(parser.parse_args(['install', 'file.zip', '--backup']).keep_backup)
        self.assertFalse(parser.parse_args(['install', '--no-backup', 'file.zip']).keep_backup)

    def test_update_backup_prompt_defaults_yes_and_accepts_no(self):
        for answer, expected_backups in [('', 1), ('n', 0)]:
            with self.subTest(answer=answer), TemporaryDirectory() as tmp:
                root = Path(tmp)
                archive = root / 'update.zip'
                with zipfile.ZipFile(archive, 'w') as z:
                    z.writestr('MusicBee.exe', b'updated')
                prefix = root / 'prefix'
                app = prefix / 'drive_c' / 'MusicBee'
                app.mkdir(parents=True)
                (prefix / '.musicbee-bootstrap-complete').touch()
                (app / 'MusicBee.exe').write_bytes(b'original')
                state = root / 'state'
                state.mkdir()
                with patch.object(sys.stdin, 'isatty', return_value=True), \
                     patch('builtins.input', return_value=answer), \
                     patch.object(installation, 'regenerate_launcher'):
                    installation.install(argparse.Namespace(installer=str(archive), keep_backup=None),
                               {'state': state, 'prefix': prefix, 'app': app})
                self.assertEqual(len(list(app.parent.glob('MusicBee.backup-*'))), expected_backups)

    def test_install_command_updates_existing_installation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'update.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('MusicBee.exe', b'updated')
            prefix = root / 'prefix'
            app = prefix / 'drive_c' / 'MusicBee'
            app.mkdir(parents=True)
            (prefix / '.musicbee-bootstrap-complete').touch()
            (app / 'MusicBee.exe').write_bytes(b'original')
            paths = {'state': root / 'state', 'prefix': prefix, 'app': app}
            paths['state'].mkdir()
            with patch.object(installation, 'regenerate_launcher'):
                installation.install(argparse.Namespace(installer=str(archive)), paths)
            self.assertEqual((app / 'MusicBee.exe').read_bytes(), b'updated')
            self.assertEqual(json.loads((paths['state'] / 'portable-source.json').read_text())['sha256'], utils.sha256(archive))
            self.assertEqual(len(list(app.parent.glob('MusicBee.backup-*'))), 1)

    def test_archive_traversal_refused(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            zip_path = root / 'bad.zip'
            with zipfile.ZipFile(zip_path, 'w') as z:
                z.writestr('../escape', 'pwn')
            with self.assertRaises(settings.SetupError):
                portable_archive.safe_zip_extract(zip_path, root / 'output')
            self.assertFalse((root / 'escape').exists())

    def test_no_automatic_license_acceptance(self):
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / 'state'
            with patch.object(sys.stdin, 'isatty', return_value=False):
                with patch.dict(os.environ, {'DISPLAY': '', 'WAYLAND_DISPLAY': ''}):
                    with self.assertRaises(settings.SetupError):
                        installation.confirm_license(state)
            self.assertFalse((state / 'license-accepted.json').exists())
            installation.confirm_license(state, accept=True)
            self.assertTrue(json.loads((state / 'license-accepted.json').read_text())['accepted'])

    def test_terminal_license_prompt(self):
        for answer in ('', 'y', 'YES'):
            with self.subTest(answer=answer), TemporaryDirectory() as tmp, \
                 patch.object(sys.stdin, 'isatty', return_value=True), \
                 patch('builtins.input', return_value=answer):
                state = Path(tmp)
                installation.confirm_license(state)
                self.assertTrue((state / 'license-accepted.json').exists())
        with TemporaryDirectory() as tmp, \
             patch.object(sys.stdin, 'isatty', return_value=True), \
             patch('builtins.input', side_effect=['invalid', 'n']):
            state = Path(tmp)
            with self.assertRaises(settings.SetupError):
                installation.confirm_license(state)
            self.assertFalse((state / 'license-accepted.json').exists())

    def test_cli_init_arguments(self):
        parser = cli.create_parser()
        args = parser.parse_args(['init','--accept-license','--no-launch'])
        self.assertTrue(args.accept_license)
        self.assertTrue(args.no_launch)

    def test_internal_desktop_command_is_not_in_public_help(self):
        parser = cli.create_parser()
        self.assertNotIn('desktop-setup', parser.format_help())
        self.assertNotIn('==SUPPRESS==', parser.format_help())
        self.assertEqual(parser.parse_args(['desktop-setup']).command, 'desktop-setup')

    def test_config_defaults_updates_and_validation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {'state': root / 'state', 'prefix': root / 'prefix'}
            settings.write_config(paths['state'], {'music_dir': '/music', 'windows_music_path': 'M:'})
            config = settings.read_config(paths['state'])
            self.assertEqual(settings.tweak_value(config, 'gdiplus'), 'native')
            self.assertEqual(settings.tweak_value(config, 'audio-latency'), 'off')
            wine.configure(argparse.Namespace(option='audio-latency', value='on'), paths)
            config = settings.read_config(paths['state'])
            self.assertEqual(config['music_dir'], '/music')
            self.assertEqual(settings.tweak_value(config, 'audio-latency'), 'on')
            with self.assertRaises(settings.SetupError):
                wine.configure(argparse.Namespace(option='gdiplus', value='invalid'), paths)
            self.assertEqual(settings.read_config(paths['state']), config)

    def test_font_tweaks_apply_and_fail_without_saving(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / 'prefix'
            prefix.mkdir()
            (prefix / '.musicbee-bootstrap-complete').touch()
            paths = {'state': root / 'state', 'prefix': prefix}
            with patch.object(wine, 'require_commands'), patch.object(wine, 'call') as command:
                wine.configure(argparse.Namespace(option='font-smoothing', value='on'), paths)
                self.assertEqual(command.call_args.args[0], ['winetricks', '-q', 'fontsmooth=rgb'])
                wine.configure(argparse.Namespace(option='font-smoothing', value='off'), paths)
                self.assertEqual(command.call_args.args[0], ['winetricks', '-q', 'fontsmooth=disable'])
                command.side_effect = settings.SetupError('failed')
                with self.assertRaises(settings.SetupError):
                    wine.configure(argparse.Namespace(option='allfonts', value='on'), paths)
            self.assertEqual(settings.tweak_value(settings.read_config(paths['state']), 'allfonts'), 'off')

    def test_windows_version_applied_and_saved_only_on_success(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / 'prefix'
            prefix.mkdir()
            (prefix / '.musicbee-bootstrap-complete').touch()
            paths = {'state': root / 'state', 'prefix': prefix}
            with patch.object(wine, 'apply_windows_version') as apply:
                wine.configure(argparse.Namespace(option='windows-version', value='10'), paths)
                apply.assert_called_once_with('10', prefix)
                self.assertEqual(settings.tweak_value(settings.read_config(paths['state']), 'windows-version'), '10')
                apply.side_effect = settings.SetupError('failed')
                with self.assertRaises(settings.SetupError):
                    wine.configure(argparse.Namespace(option='windows-version', value='8.1'), paths)
            self.assertEqual(settings.tweak_value(settings.read_config(paths['state']), 'windows-version'), '10')

    def test_repair_dependencies_reuses_configured_windows_version(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / 'prefix'
            prefix.mkdir()
            (prefix / '.musicbee-bootstrap-complete').touch()
            paths = {'state': root / 'state', 'prefix': prefix}
            settings.write_config(paths['state'], {'tweaks': {'windows-version': '10'}})
            with patch.object(installation, 'ensure_bootstrap') as bootstrap, \
                 patch.object(installation, 'apply_windows_version') as version, \
                 patch.object(installation, 'record_environment'):
                installation.repair_dependencies(paths)
            bootstrap.assert_called_once_with(prefix, refresh=True)
            version.assert_called_once_with('10', prefix)

    def test_run_applies_gdiplus_and_audio_settings(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / 'prefix' / 'app'
            app.mkdir(parents=True)
            (app / 'MusicBee.exe').touch()
            launcher = root / 'musicbee'
            launcher.touch()
            launcher.with_name('musicbee-manage').touch()
            paths = {'state': root / 'state', 'prefix': root / 'prefix', 'app': app, 'bin': launcher}
            args = argparse.Namespace(musicbee_args=[])
            with patch.object(wine, 'require_ready'), \
                 patch.object(wine, 'require_commands'), patch.object(wine, 'call') as command, \
                 patch.object(wine, 'apply_windows_version') as windows_version, \
                 patch.dict(os.environ, {'PULSE_LATENCY_MSEC': '25'}):
                wine.run_musicbee(args, paths)
                windows_version.assert_called_once_with('8.1', paths['prefix'])
                self.assertEqual(command.call_args.kwargs['env']['WINEDLLOVERRIDES'], '*gdiplus=n,b;gdiplus=n,b')
                self.assertEqual(command.call_args.kwargs['env']['PULSE_LATENCY_MSEC'], '25')
                settings.write_config(paths['state'], {'music_dir': '/missing', 'windows_music_path': 'M:',
                                                  'tweaks': {'gdiplus': 'wine', 'audio-latency': 'on', 'windows-version': '10'}})
                wine.run_musicbee(args, paths)
                windows_version.assert_called_once()
                self.assertEqual(command.call_args.kwargs['env']['WINEDLLOVERRIDES'], '*gdiplus=b;gdiplus=b')
                self.assertEqual(command.call_args.kwargs['env']['PULSE_LATENCY_MSEC'], '60')

    def test_native_tools_registration_and_dispatch(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / 'prefix'
            (prefix / 'drive_c').mkdir(parents=True)
            (prefix / '.musicbee-bootstrap-complete').touch()
            launcher = root / 'musicbee'
            launcher.touch()
            launcher.with_name('musicbee-manage').touch()
            paths = {'state': root / 'state', 'prefix': prefix, 'bin': launcher}
            args = argparse.Namespace(action='add', name='picard', tool_command='flatpak run org.musicbrainz.Picard')
            desktop_integration.manage_tools(args, paths)
            self.assertEqual(desktop_integration.native_tools(settings.read_config(paths['state']))['picard'],
                             ['flatpak', 'run', 'org.musicbrainz.Picard'])
            bridge = prefix / 'drive_c' / 'musicbee-linux-tools' / desktop_integration.tool_bridge_name('picard')
            self.assertIn('tools-dispatch picard "$@"', bridge.read_text())
            bridge.write_text('#!/bin/sh\n' + desktop_integration.TOOL_BRIDGE_MARKER + 'exec old-launcher tools-dispatch picard "$@"\n')
            desktop_integration.refresh_tool_bridges(paths, settings.read_config(paths['state']))
            self.assertIn('musicbee-manage tools-dispatch picard', bridge.read_text())
            files = [root / 'a file.flac', root / '音楽.flac']
            for file in files:
                file.touch()
            responses = [subprocess.CompletedProcess([], 0, str(file) + '\n', '') for file in files]
            with patch.object(desktop_integration, 'require_commands') as required, \
                 patch.object(desktop_integration, 'wine_env', return_value={}), \
                 patch.object(shutil, 'which', return_value=None), \
                 patch.object(subprocess, 'run', side_effect=responses) as convert, \
                 patch.object(subprocess, 'Popen') as launch:
                desktop_integration.dispatch_tool('picard', [r'M:\a file.flac', r'M:\音楽.flac'], paths)
                required.assert_called_once_with('winepath', prefix=prefix)
                self.assertEqual(convert.call_count, 2)
                self.assertEqual(launch.call_args.args[0], ['flatpak', 'run', 'org.musicbrainz.Picard', *map(str, files)])
                self.assertNotIn('WINEDLLOVERRIDES', launch.call_args.kwargs['env'])
            desktop_integration.manage_tools(argparse.Namespace(action='remove', name='picard'), paths)
            self.assertNotIn('picard', desktop_integration.native_tools(settings.read_config(paths['state'])))

    def test_native_tool_refuses_existing_unmanaged_bridge(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / 'prefix'
            (prefix / 'drive_c').mkdir(parents=True)
            (prefix / '.musicbee-bootstrap-complete').touch()
            bridge = prefix / 'drive_c' / 'musicbee-linux-tools' / desktop_integration.tool_bridge_name('picard')
            bridge.parent.mkdir()
            bridge.write_text('user data')
            paths = {'state': root / 'state', 'prefix': prefix, 'bin': root / 'musicbee'}
            with self.assertRaises(settings.SetupError):
                desktop_integration.manage_tools(argparse.Namespace(action='add', name='picard', tool_command='picard'), paths)
            self.assertEqual(bridge.read_text(), 'user data')

    def test_explorer_redirect_preserves_unmanaged_file_and_can_be_disabled(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / 'app'
            app.mkdir()
            launcher = root / 'launcher'
            launcher.touch()
            launcher.with_name('musicbee-manage').touch()
            paths = {'app': app, 'bin': launcher, 'state': root}
            target = app / 'explorer.exe'
            target.write_bytes(b'existing explorer')
            with patch.object(sys, 'stderr', new_callable=io.StringIO):
                desktop_integration.configure_explorer_redirect(paths, True)
                desktop_integration.configure_explorer_redirect(paths, False)
            self.assertEqual(target.read_bytes(), b'existing explorer')
            target.unlink()
            desktop_integration.configure_explorer_redirect(paths, True)
            self.assertIn(desktop_integration.EXPLORER_MARKER, target.read_text())
            desktop_integration.configure_explorer_redirect(paths, False)
            self.assertFalse(target.exists())

    def test_explorer_redirect_selection_and_fallback(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "音's file.flac"
            path.touch()
            paths = {'prefix': root / 'prefix'}
            conversion = subprocess.CompletedProcess([], 0, str(path) + '\n', '')
            failed_dbus = subprocess.CompletedProcess([], 1, '', '')
            with patch.object(shutil, 'which', return_value='/usr/bin/program'), \
                 patch.object(subprocess, 'run', side_effect=[conversion, failed_dbus]) as run, \
                 patch.object(subprocess, 'Popen') as launch:
                desktop_integration.dispatch_explorer([r'/select,M:\Music\file.flac'], paths)
                self.assertIn('%27', run.call_args.args[0][-2])
                self.assertEqual(launch.call_args.args[0], ['xdg-open', str(root)])
            with patch.object(subprocess, 'Popen') as launch:
                desktop_integration.dispatch_explorer(['/unsupported'], paths)
                self.assertEqual(launch.call_args.args[0], ['wine', r'C:\windows\explorer.exe', '/unsupported'])

    def test_explorer_direct_file_path_is_revealed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'track.flac'
            path.touch()
            responses = [subprocess.CompletedProcess([], 0, str(path) + '\n', ''),
                         subprocess.CompletedProcess([], 0, '', '')]
            with patch.object(shutil, 'which', return_value='/usr/bin/gdbus'), \
                 patch.object(subprocess, 'run', side_effect=responses) as run, \
                 patch.object(subprocess, 'Popen') as launch:
                desktop_integration.dispatch_explorer([r'M:\Music\track.flac'], {'prefix': root})
                self.assertIn('org.freedesktop.FileManager1.ShowItems', run.call_args.args[0])
                self.assertIn(path.as_uri(), run.call_args.args[0][-2])
                launch.assert_not_called()

    def test_first_launch_wiring_without_wine(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = self.create_archive(root)
            home = root / 'home'
            home.mkdir()
            state = home / '.local' / 'share' / 'musicbee'
            prefix = state / 'wineprefix'
            paths = {
                'home': home, 'state': state, 'prefix': prefix,
                'app': prefix / 'drive_c' / 'MusicBee',
                'bin': home / '.local/bin/musicbee',
                'desktop': home / '.local/share/applications/musicbee.desktop',
            }
            def fake_wine_bootstrap(p, refresh):
                (p / 'drive_c').mkdir(parents=True, exist_ok=True)
                (p / '.musicbee-bootstrap-complete').touch()
            args = argparse.Namespace(music_dir=None, wine_music_path=None, archive=str(archive),
                                      accept_license=True, no_launch=True)
            settings.write_config(state, {'tweaks': {'gdiplus': 'wine'}})
            with patch.object(installation, 'ensure_bootstrap', side_effect=fake_wine_bootstrap), \
                 patch.object(installation, 'apply_windows_version') as windows_version, \
                 patch.object(installation, 'record_environment'), \
                 patch.object(installation, 'extract_musicbee_icon', return_value=None), \
                 patch.object(installation, 'ensure_media_controls') as media_mock:
                installation.first_launch(args, paths)
                media_mock.assert_called_once_with(paths)
                installation.ensure_aac_decoder.assert_called_once_with(paths)
                windows_version.assert_called_once_with('8.1', prefix)
            self.assertTrue((paths['app'] / 'MusicBee.exe').is_file())
            self.assertEqual(json.loads((state/'config.json').read_text())['tweaks']['gdiplus'], 'wine')
            self.assertFalse((prefix/'drive_c'/'Users').exists())
            self.assertFalse((prefix/'dosdevices'/'m:').exists())
            self.assertTrue(paths['desktop'].is_file())
            self.assertIn('musicbee-manage" desktop-launch %f\nTerminal=false\n', paths['desktop'].read_text())
            self.assertEqual(len(json.loads((state/'portable-source.json').read_text())['sha256']), 64)
            for name in ('settings.py', 'paths.py', 'archive.py', 'install.py', 'cli.py',
                         'wine.py', 'desktop.py', 'media_controls.py', 'utils.py'):
                self.assertTrue((state / 'lib' / 'musicbee_linux' / name).is_file())
            env = {**os.environ, 'HOME': str(home), 'XDG_DATA_HOME': str(home / '.local/share'),
                   'PYTHONPATH': ''}
            for executable in ('musicbee', 'musicbee-manage'):
                report = subprocess.check_output([str(paths['bin'].with_name(executable)), '--version'],
                                                 cwd=root, env=env, text=True)
                self.assertIn(f'musicbee-linux {__version__}', report)

    def test_desktop_launcher_shows_setup_in_terminal_until_installed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            desktop = root / 'musicbee.desktop'
            desktop_integration.make_launcher(root / 'musicbee', desktop, setup_needed=True)
            self.assertIn(' desktop-setup %f\nTerminal=true\n', desktop.read_text())
            desktop_integration.make_launcher(root / 'musicbee', desktop)
            self.assertIn('musicbee-manage" desktop-launch %f\nTerminal=false\n', desktop.read_text())
            self.assertIn('audio/x-flac;', desktop.read_text())
            self.assertIn('audio/x-mpegurl;', desktop.read_text())

    def test_desktop_setup_starts_musicbee_after_setup(self):
        paths = {'app': Path('/unused')}
        with patch.object(sys, 'argv', ['musicbee-manage', 'desktop-setup', '/music/song.flac']), \
             patch.object(os, 'geteuid', return_value=1000), \
             patch.object(utils, 'locations', return_value=paths), \
             patch.object(installation, 'first_launch') as setup_mock, \
             patch.object(subprocess, 'Popen') as launch_mock:
            self.assertEqual(cli.main(), 0)
            self.assertTrue(setup_mock.call_args.args[0].no_launch)
            self.assertEqual(launch_mock.call_args.args[0][-3:], ['--manage', 'desktop-launch', '/music/song.flac'])
            self.assertTrue(launch_mock.call_args.kwargs['start_new_session'])

    def test_desktop_launch_displays_errors(self):
        with TemporaryDirectory() as tmp:
            app = Path(tmp)
            (app / 'MusicBee.exe').touch()
            with patch.object(sys, 'argv', ['musicbee-manage', 'desktop-launch']), \
                 patch.object(os, 'geteuid', return_value=1000), \
                 patch.dict(os.environ, {'MUSICBEE_DESKTOP_LOG_WORKER': '1'}), \
                 patch.object(utils, 'locations', return_value={'app': app, 'state': app}), \
                 patch.object(wine, 'run_musicbee', side_effect=settings.SetupError('Wine not found')), \
                 patch.object(desktop_integration, 'show_desktop_error') as show:
                self.assertEqual(cli.main(), 1)
                show.assert_called_once_with(f'Wine not found\n\nLog: {app / "desktop-launch.log"}')

    def test_desktop_launch_reports_missing_musicbee(self):
        with TemporaryDirectory() as tmp:
            paths = {'app': Path(tmp), 'state': Path(tmp)}
            with patch.object(sys, 'argv', ['musicbee-manage', 'desktop-launch']), \
                 patch.object(os, 'geteuid', return_value=1000), \
                 patch.dict(os.environ, {'MUSICBEE_DESKTOP_LOG_WORKER': '1'}), \
                 patch.object(utils, 'locations', return_value=paths), \
                 patch.object(desktop_integration, 'show_desktop_error') as show:
                self.assertEqual(cli.main(), 1)
                self.assertIn('MusicBee is not installed', show.call_args.args[0])

    def test_desktop_log_captures_and_limits_output(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            popen = subprocess.Popen
            def noisy_process(_command, **kwargs):
                return popen([sys.executable, '-c',
                              'import sys; sys.stdout.write("x" * (3 * 1024 * 1024))'], **kwargs)
            with patch.object(subprocess, 'Popen', side_effect=noisy_process):
                self.assertEqual(desktop_integration.desktop_launch_with_log({'state': root}, []), 0)
            log = (root / 'desktop-launch.log').read_bytes()
            self.assertEqual(len(log), 2 * 1024 * 1024)
            self.assertTrue(log.endswith(b'[Log truncated at 2 MiB]\n'))

    def test_desktop_log_failure_does_not_prevent_launch(self):
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / 'not-a-directory'
            state.write_text('file')
            self.assertIsNone(desktop_integration.desktop_launch_with_log({'state': state}, []))

    def test_desktop_error_tries_available_dialogs(self):
        with patch.object(shutil, 'which', side_effect=lambda name: name if name in ('kdialog', 'zenity') else None), \
             patch.object(subprocess, 'run', side_effect=[subprocess.CompletedProcess([], 1),
                                                               subprocess.CompletedProcess([], 0)]) as calls:
            desktop_integration.show_desktop_error('Missing Wine')
            self.assertEqual(calls.call_count, 2)
            self.assertEqual(calls.call_args.args[0][-1], '--text=Missing Wine')

    def test_run_uses_no_install_if_present(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / 'data'
            app = state / 'wineprefix' / 'drive_c' / 'MusicBee'
            app.mkdir(parents=True)
            (app / 'MusicBee.exe').write_bytes(b'a')
            paths = {'state':state, 'prefix':state/'wineprefix', 'app':app}
            args = argparse.Namespace(music_dir=None, wine_music_path='M:', archive=None, accept_license=True, no_launch=True)
            with patch.object(installation, 'initialize_prefix') as setup_mock, \
                 patch.object(installation, 'ensure_media_controls') as media_mock:
                installation.first_launch(args, paths)
                setup_mock.assert_not_called()
                media_mock.assert_not_called()

    def test_media_controls_installed_only_when_missing(self):
        with TemporaryDirectory() as tmp:
            app = Path(tmp) / 'app'
            paths = {'app': app}
            with patch.object(media_controls, 'media_controls') as media_mock:
                media_controls.ensure_media_controls(paths)
                self.assertEqual(media_mock.call_args.args[0].action, 'install')
                plugin = app / 'Plugins' / 'mb_MPRISBee.dll'
                plugin.parent.mkdir(parents=True)
                plugin.touch()
                media_controls.ensure_media_controls(paths)
                media_mock.assert_called_once()

    def test_media_controls_install_failure_is_nonfatal(self):
        with TemporaryDirectory() as tmp, \
             patch.object(media_controls, 'media_controls', side_effect=settings.SetupError('offline')), \
             patch.object(sys, 'stderr', new_callable=io.StringIO) as stderr:
            media_controls.ensure_media_controls({'app': Path(tmp)})
            self.assertIn('offline', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
