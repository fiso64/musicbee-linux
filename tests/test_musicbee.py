"""Offline tests: python3 -m unittest discover -s tests -v"""
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import io
import json
import subprocess
import unittest
import zipfile
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / 'src' / 'musicbee-manage.py'
loader = SourceFileLoader('musicbee_linux', str(SRC))
spec = spec_from_loader(loader.name, loader)
mb = module_from_spec(spec)
loader.exec_module(mb)


class PortableExtractionTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(mb, 'ensure_aac_decoder')
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
                mb.install_aac_decoder({'app': app})
                self.assertEqual((app / 'bass_aac.dll').read_bytes(), bytes(dll))
                mb.install_aac_decoder({'app': app})
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
                with self.assertRaisesRegex(mb.SetupError, '32-bit'):
                    mb.install_aac_decoder({'app': app})
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
            mb.unpack_portable(archive, app, update=True, keep_backup=False)
            self.assertEqual((app / 'bass_aac.dll').read_bytes(), b'existing decoder')

    def test_ubuntu_wineserver_is_used_for_wine_and_winetricks(self):
        server = '/usr/lib/x86_64-linux-gnu/wine/wineserver'
        with patch.dict(mb.os.environ, {}, clear=True), \
             patch.object(mb, 'wine_selection', return_value='auto'), \
             patch.object(mb.shutil, 'which', return_value=None), \
             patch.object(mb.platform, 'machine', return_value='x86_64'), \
             patch.object(Path, 'is_file', return_value=True), \
             patch.object(mb.os, 'access', side_effect=lambda path, mode: str(path) == server):
            self.assertEqual(mb.wine_server(), server)
            self.assertEqual(mb.wine_env(Path('/tmp/prefix'))['WINESERVER'], server)

    def test_winehq_staging_fallback_keeps_helpers_together(self):
        with patch.dict(mb.os.environ, {'PATH': '/usr/bin'}, clear=True), \
             patch.object(mb.shutil, 'which', side_effect=lambda name, path=None:
                          f'/opt/wine-staging/bin/{name}' if path.startswith('/opt/wine-staging/bin:') else None), \
             patch.object(mb.os, 'access', side_effect=lambda path, mode:
                          str(path).startswith('/opt/wine-staging/bin/')):
            env = mb.wine_env(Path('/tmp/prefix'))
            self.assertTrue(env['PATH'].startswith('/opt/wine-staging/bin:'))
            self.assertEqual(mb.wine_server(), '/opt/wine-staging/bin/wineserver')
            mb.require_commands('wine', 'winepath', 'wineboot', 'wineserver')

    def test_winehq_stable_fallback_uses_matching_server(self):
        with patch.dict(mb.os.environ, {'PATH': '/usr/bin', 'WINESERVER': '/wrong/server'}, clear=True), \
             patch.object(mb, 'wine_selection', return_value='auto'), \
             patch.object(mb.shutil, 'which', return_value=None), \
             patch.object(mb.os, 'access', side_effect=lambda path, mode:
                          str(path).startswith('/opt/wine-stable/bin/')):
            env = mb.wine_env(Path('/tmp/prefix'))
            self.assertEqual(env['WINE'], '/opt/wine-stable/bin/wine')
            self.assertEqual(env['WINESERVER'], '/opt/wine-stable/bin/wineserver')
            self.assertTrue(env['PATH'].startswith('/opt/wine-stable/bin:'))

    def test_multiple_winehq_branches_require_selection(self):
        with patch.dict(mb.os.environ, {'PATH': '/usr/bin'}, clear=True), \
             patch.object(mb, 'wine_selection', return_value='auto'), \
             patch.object(mb.shutil, 'which', return_value=None), \
             patch.object(mb.os, 'access', side_effect=lambda path, mode:
                          str(path).startswith(('/opt/wine-stable/', '/opt/wine-staging/'))):
            with self.assertRaisesRegex(mb.SetupError, 'Multiple WineHQ versions'):
                mb.wine_path()

    def test_existing_wine_on_path_takes_precedence_over_staging(self):
        with patch.dict(mb.os.environ, {'PATH': '/custom/bin:/usr/bin'}, clear=True), \
             patch.object(mb.shutil, 'which', return_value='/custom/bin/wine'), \
             patch.object(mb.os, 'access', return_value=True):
            self.assertEqual(mb.wine_path(), '/custom/bin:/usr/bin')

    def test_explicit_staging_uses_matching_wine_and_helpers(self):
        with TemporaryDirectory() as tmp:
            state = Path(tmp)
            prefix = state / 'wineprefix'
            paths = {'state': state, 'prefix': prefix}
            with patch.dict(mb.os.environ, {'PATH': '/usr/bin', 'WINESERVER': '/old/wineserver'}, clear=True), \
                 patch.object(mb.os, 'access', return_value=True):
                mb.configure(argparse.Namespace(option='wine', value='staging'), paths)
                env = mb.wine_env(prefix)
                self.assertEqual(env['WINE'], '/opt/wine-staging/bin/wine')
                self.assertEqual(env['WINESERVER'], '/opt/wine-staging/bin/wineserver')
                self.assertTrue(env['PATH'].startswith('/opt/wine-staging/bin:'))
                mb.configure(argparse.Namespace(option='wine', value='auto'), paths)
                self.assertEqual(mb.tweak_value(mb.read_config(state), 'wine'), 'auto')

    def test_staging_setting_rejects_missing_installation_without_saving(self):
        with TemporaryDirectory() as tmp:
            state = Path(tmp)
            with patch.object(mb.os, 'access', return_value=False):
                with self.assertRaisesRegex(mb.SetupError, 'missing wine'):
                    mb.configure(argparse.Namespace(option='wine', value='staging'),
                                 {'state': state, 'prefix': state / 'wineprefix'})
            self.assertEqual(mb.read_config(state), {})

    def test_musicbee_path_arguments(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            song = root / 'a song.flac'
            song.touch()
            paths = [str(song), '/Play', 'relative list.m3u', '/QueueLast',
                     'https://example.com/music', '/QueueNext', r'C:\Music\track.flac', '/Next']
            conversions = [subprocess.CompletedProcess([], 0, 'Z:\\song.flac\n', ''),
                           subprocess.CompletedProcess([], 0, 'Z:\\list.m3u\n', '')]
            with patch.object(mb, 'require_commands'), \
                 patch.object(mb.subprocess, 'run', side_effect=conversions) as winepath:
                result = mb.musicbee_arguments(paths, root)
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
                mb.fcntl.flock(main_lock, mb.fcntl.LOCK_EX | mb.fcntl.LOCK_NB)
                with patch.object(mb, 'require_commands'), patch.object(mb, 'call') as call, \
                     patch.object(mb, 'configure_explorer_redirect') as prepare:
                    args = argparse.Namespace(musicbee_args=['/Next'])
                    with self.assertRaisesRegex(mb.SetupError, 'being modified'):
                        mb.run(args, paths)
                    with (prefix / '.musicbee.active.lock').open('a+') as active:
                        mb.fcntl.flock(active, mb.fcntl.LOCK_EX | mb.fcntl.LOCK_NB)
                        mb.run(args, paths)
                    self.assertEqual(call.call_args.args[0], ['wine', 'MusicBee.exe', '/Next'])
                    prepare.assert_not_called()
                    with self.assertRaisesRegex(mb.SetupError, 'already appears to be running'):
                        mb.run(argparse.Namespace(musicbee_args=[]), paths)

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
            mb.configure_music_dir(args, paths)
            link = prefix / 'drive_c' / 'MyMusic'
            self.assertEqual(link.resolve(), first)
            self.assertEqual(mb.read_config(paths['state'])['windows_music_path'], r'C:\MyMusic')
            mb.configure_music_dir(argparse.Namespace(directory=str(second), wine_music_path=None), paths)
            self.assertEqual(link.resolve(), second)
            unrelated = prefix / 'drive_c' / 'OtherMusic'
            unrelated.symlink_to(first, target_is_directory=True)
            with self.assertRaisesRegex(mb.SetupError, 'unrelated symlink'):
                mb.configure_music_dir(argparse.Namespace(directory=str(second), wine_music_path=r'C:\OtherMusic'), paths)
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
            link = prefix / 'drive_c' / 'users' / mb.getpass.getuser() / 'Music'
            link.parent.mkdir(parents=True)
            link.symlink_to(default_music, target_is_directory=True)
            with patch.object(mb.Path, 'home', return_value=root):
                mb.configure_music_dir(argparse.Namespace(directory=str(custom_music), wine_music_path=None),
                                       {'prefix': prefix, 'state': root / 'state'})
            self.assertEqual(link.resolve(), custom_music)
            self.assertTrue(default_music.is_dir())

    def test_launch_arguments_and_management_entrypoint(self):
        with TemporaryDirectory() as tmp:
            app = Path(tmp) / 'app'
            app.mkdir()
            (app / 'MusicBee.exe').touch()
            paths = {'app': app}
            with patch.object(mb.os, 'geteuid', return_value=1000), \
                 patch.object(mb, 'locations', return_value=paths), \
                 patch.object(mb, 'run') as run, patch.object(mb, 'configure') as configure:
                for argv in (['musicbee', '/Play', r'C:\Music\song.mp3'],
                             ['musicbee-manage.py', '--launch', '/Play', r'C:\Music\song.mp3']):
                    with patch.object(mb.sys, 'argv', argv):
                        self.assertEqual(mb.main(), 0)
                        self.assertEqual(run.call_args.args[0].musicbee_args, ['/Play', r'C:\Music\song.mp3'])
                for argv in (['musicbee', 'config'], ['musicbee', '--manage']):
                    with patch.object(mb.sys, 'argv', argv):
                        self.assertEqual(mb.main(), 0)
                        self.assertEqual(run.call_args.args[0].musicbee_args, argv[1:])
                for argv in (['musicbee-manage', 'config'], ['musicbee-manage.py', '--manage', 'config']):
                    with patch.object(mb.sys, 'argv', argv):
                        self.assertEqual(mb.main(), 0)
                self.assertEqual(configure.call_count, 2)

    def test_launcher_help_does_not_start_musicbee(self):
        for argv in (['musicbee', '-h'], ['musicbee', '--help'],
                     ['musicbee-manage.py', '--launch', '--help']):
            with self.subTest(argv=argv), patch.object(mb.sys, 'argv', argv), \
                 patch('sys.stdout', new_callable=io.StringIO) as output, \
                 patch.object(mb, 'run') as run:
                self.assertEqual(mb.main(), 0)
                self.assertIn('musicbee-manage --help', output.getvalue())
                self.assertIn('/QueueNext', output.getvalue())
                run.assert_not_called()

    def test_manager_version_and_launcher_version_argument(self):
        with patch.object(mb.sys, 'argv', ['musicbee-manage', '--version']), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            with self.assertRaises(SystemExit) as result:
                mb.main()
            self.assertEqual(result.exception.code, 0)
            self.assertEqual(output.getvalue().strip(), f'musicbee-manage {mb.__version__}')
        with TemporaryDirectory() as tmp:
            app = Path(tmp)
            (app / 'MusicBee.exe').touch()
            with patch.object(mb.sys, 'argv', ['musicbee', '--version']), \
                 patch.object(mb.os, 'geteuid', return_value=1000), \
                 patch.object(mb, 'locations', return_value={'app': app}), \
                 patch.object(mb, 'run') as run:
                self.assertEqual(mb.main(), 0)
                self.assertEqual(run.call_args.args[0].musicbee_args, ['--version'])

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
                mb.manage_tabs(argparse.Namespace(settings=None, source=None, destination=None), paths)
            self.assertIn('2. Two & More', output.getvalue())
            mb.manage_tabs(argparse.Namespace(settings=None, source=2, destination=1), paths)
            changed = settings.read_bytes()
            self.assertLess(changed.index(b'Two &amp; More'), changed.index(b'One'))
            self.assertIn(b'<NavigatorInitialTabIndex>0</NavigatorInitialTabIndex>', changed)
            self.assertIn(b'<Value>\x00</Value>', changed)
            mb.manage_tabs(argparse.Namespace(settings=None, source=1, destination=2), paths)
            self.assertEqual(settings.read_bytes(), original)

    def test_tabs_requires_choice_when_settings_are_ambiguous(self):
        with TemporaryDirectory() as tmp:
            prefix = Path(tmp) / 'prefix'
            app = prefix / 'drive_c' / 'MusicBee'
            portable = app / 'AppData' / 'MusicBee3Settings.ini'
            profile = prefix / 'drive_c' / 'users' / mb.getpass.getuser() / 'AppData' / 'Roaming' / 'MusicBee' / 'MusicBee3Settings.ini'
            for path in (portable, profile):
                path.parent.mkdir(parents=True)
                path.touch()
            paths = {'prefix': prefix, 'app': app}
            with self.assertRaisesRegex(mb.SetupError, 'Use --settings'):
                mb.tab_settings_path(paths, None)
            self.assertEqual(mb.tab_settings_path(paths, 'portable'), portable)
            self.assertEqual(mb.tab_settings_path(paths, 'profile'), profile)

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
            mb.unpack_portable(archive, target)
            self.assertEqual((target / 'MusicBee.exe').read_bytes(), b'test executable')
            self.assertTrue((target / 'Plugins/example.dll').is_file())
            mb.unpack_portable(archive, target)  # does not overwrite existing installation
            self.assertTrue((target / 'MusicBee.exe').is_file())

    def test_portable_exe_installs_and_updates_directly(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.create_archive(root)
            exe = root / 'MusicBeePortable_3_6.exe'
            app = root / 'MusicBee'
            mb.unpack_portable(exe, app)
            self.assertEqual((app / 'MusicBee.exe').read_bytes(), b'test executable')
            (app / 'MusicBee.exe').write_bytes(b'old version')
            backup = mb.unpack_portable(exe, app, update=True)
            self.assertEqual((app / 'MusicBee.exe').read_bytes(), b'test executable')
            self.assertEqual((backup / 'MusicBee.exe').read_bytes(), b'old version')

    def test_preexisting_app_data_not_overwritten(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = self.create_archive(root)
            target = root / 'prefix' / 'drive_c' / 'MusicBee'
            target.mkdir(parents=True)
            (target / 'important').write_text('do not touch')
            with self.assertRaises(mb.SetupError):
                mb.unpack_portable(archive, target)
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
            backup = mb.unpack_portable(archive, app, update=True)
            self.assertEqual((app / 'MusicBee.exe').read_bytes(), b'new executable')
            self.assertEqual((app / 'basswma.dll.musicbee-disabled').read_bytes(), b'new wma')
            self.assertFalse((app / 'basswma.dll').exists())
            self.assertEqual((app / 'Configuration.xml').read_bytes(), b'user config')
            self.assertEqual((app / 'Plugins/example.dll').read_bytes(), b'user plugin')
            self.assertEqual((app / 'library.db').read_bytes(), b'user library')
            self.assertEqual((backup / 'MusicBee.exe').read_bytes(), b'old executable')

    def test_missing_archive_instructs_user_to_download(self):
        with patch.dict(mb.os.environ, {}, clear=True):
            with self.assertRaisesRegex(mb.SetupError, 'musicbee-manage install'):
                mb.packaged_archive({})

    def test_nonportable_zip_rejected_before_first_setup(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'MusicBeeSetup.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('MusicBeeSetup_3_6.exe', b'installer')
            paths = {'state': root / 'state', 'prefix': root / 'prefix', 'app': root / 'app'}
            args = argparse.Namespace(music_dir=None, wine_music_path=None, archive=str(archive),
                                      accept_license=False, no_launch=True)
            with patch.object(mb, 'initialize_prefix') as setup_mock, patch.object(mb, 'confirm_license') as consent_mock:
                with self.assertRaisesRegex(mb.SetupError, 'non-portable.*Portable Edition'):
                    mb.first_launch(args, paths)
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
                with self.assertRaisesRegex(mb.SetupError, 'non-portable.*Portable Edition'):
                    mb.install(argparse.Namespace(installer=str(archive), keep_backup=None), paths)
                prompt.assert_not_called()
            self.assertEqual((app / 'MusicBee.exe').read_bytes(), b'original')
            self.assertEqual(list(root.glob('MusicBee.backup-*')), [])

    def test_nonportable_exe_is_rejected(self):
        with TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'MusicBeeSetup_3_6.exe'
            exe.write_bytes(b'installer')
            with self.assertRaisesRegex(mb.SetupError, 'non-portable.*Portable Edition'):
                mb.validate_portable_archive(exe)

    def test_update_can_skip_retained_backup(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'update.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('MusicBee.exe', b'updated')
            app = root / 'MusicBee'
            app.mkdir()
            (app / 'MusicBee.exe').write_bytes(b'original')
            self.assertIsNone(mb.unpack_portable(archive, app, update=True, keep_backup=False))
            self.assertEqual((app / 'MusicBee.exe').read_bytes(), b'updated')
            self.assertEqual(list(root.glob('MusicBee.backup-*')), [])

    def test_install_backup_flags(self):
        parser = mb.create_parser()
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
                with patch.object(mb.sys.stdin, 'isatty', return_value=True), \
                     patch('builtins.input', return_value=answer), \
                     patch.object(mb, 'regenerate_launcher'):
                    mb.install(argparse.Namespace(installer=str(archive), keep_backup=None),
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
            with patch.object(mb, 'regenerate_launcher'):
                mb.install(argparse.Namespace(installer=str(archive)), paths)
            self.assertEqual((app / 'MusicBee.exe').read_bytes(), b'updated')
            self.assertEqual(json.loads((paths['state'] / 'portable-source.json').read_text())['sha256'], mb.sha256(archive))
            self.assertEqual(len(list(app.parent.glob('MusicBee.backup-*'))), 1)

    def test_archive_traversal_refused(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            zip_path = root / 'bad.zip'
            with zipfile.ZipFile(zip_path, 'w') as z:
                z.writestr('../escape', 'pwn')
            with self.assertRaises(mb.SetupError):
                mb.safe_zip_extract(zip_path, root / 'output')
            self.assertFalse((root / 'escape').exists())

    def test_no_automatic_license_acceptance(self):
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / 'state'
            with patch.object(mb.sys.stdin, 'isatty', return_value=False):
                with patch.dict(mb.os.environ, {'DISPLAY': '', 'WAYLAND_DISPLAY': ''}):
                    with self.assertRaises(mb.SetupError):
                        mb.confirm_license(state)
            self.assertFalse((state / 'license-accepted.json').exists())
            mb.confirm_license(state, accept=True)
            self.assertTrue(json.loads((state / 'license-accepted.json').read_text())['accepted'])

    def test_terminal_license_prompt(self):
        for answer in ('', 'y', 'YES'):
            with self.subTest(answer=answer), TemporaryDirectory() as tmp, \
                 patch.object(mb.sys.stdin, 'isatty', return_value=True), \
                 patch('builtins.input', return_value=answer):
                state = Path(tmp)
                mb.confirm_license(state)
                self.assertTrue((state / 'license-accepted.json').exists())
        with TemporaryDirectory() as tmp, \
             patch.object(mb.sys.stdin, 'isatty', return_value=True), \
             patch('builtins.input', side_effect=['invalid', 'n']):
            state = Path(tmp)
            with self.assertRaises(mb.SetupError):
                mb.confirm_license(state)
            self.assertFalse((state / 'license-accepted.json').exists())

    def test_cli_init_arguments(self):
        parser = mb.create_parser()
        args = parser.parse_args(['init','--accept-license','--no-launch'])
        self.assertTrue(args.accept_license)
        self.assertTrue(args.no_launch)

    def test_internal_desktop_command_is_not_in_public_help(self):
        parser = mb.create_parser()
        self.assertNotIn('desktop-setup', parser.format_help())
        self.assertNotIn('==SUPPRESS==', parser.format_help())
        self.assertEqual(parser.parse_args(['desktop-setup']).command, 'desktop-setup')

    def test_config_defaults_updates_and_validation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {'state': root / 'state', 'prefix': root / 'prefix'}
            mb.write_config(paths['state'], {'music_dir': '/music', 'windows_music_path': 'M:'})
            config = mb.read_config(paths['state'])
            self.assertEqual(mb.tweak_value(config, 'gdiplus'), 'native')
            self.assertEqual(mb.tweak_value(config, 'audio-latency'), 'off')
            mb.configure(argparse.Namespace(option='audio-latency', value='on'), paths)
            config = mb.read_config(paths['state'])
            self.assertEqual(config['music_dir'], '/music')
            self.assertEqual(mb.tweak_value(config, 'audio-latency'), 'on')
            with self.assertRaises(mb.SetupError):
                mb.configure(argparse.Namespace(option='gdiplus', value='invalid'), paths)
            self.assertEqual(mb.read_config(paths['state']), config)

    def test_font_tweaks_apply_and_fail_without_saving(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / 'prefix'
            prefix.mkdir()
            (prefix / '.musicbee-bootstrap-complete').touch()
            paths = {'state': root / 'state', 'prefix': prefix}
            with patch.object(mb, 'require_commands'), patch.object(mb, 'call') as command:
                mb.configure(argparse.Namespace(option='font-smoothing', value='on'), paths)
                self.assertEqual(command.call_args.args[0], ['winetricks', '-q', 'fontsmooth=rgb'])
                mb.configure(argparse.Namespace(option='font-smoothing', value='off'), paths)
                self.assertEqual(command.call_args.args[0], ['winetricks', '-q', 'fontsmooth=disable'])
                command.side_effect = mb.SetupError('failed')
                with self.assertRaises(mb.SetupError):
                    mb.configure(argparse.Namespace(option='allfonts', value='on'), paths)
            self.assertEqual(mb.tweak_value(mb.read_config(paths['state']), 'allfonts'), 'off')

    def test_windows_version_applied_and_saved_only_on_success(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / 'prefix'
            prefix.mkdir()
            (prefix / '.musicbee-bootstrap-complete').touch()
            paths = {'state': root / 'state', 'prefix': prefix}
            with patch.object(mb, 'apply_windows_version') as apply:
                mb.configure(argparse.Namespace(option='windows-version', value='10'), paths)
                apply.assert_called_once_with('10', prefix)
                self.assertEqual(mb.tweak_value(mb.read_config(paths['state']), 'windows-version'), '10')
                apply.side_effect = mb.SetupError('failed')
                with self.assertRaises(mb.SetupError):
                    mb.configure(argparse.Namespace(option='windows-version', value='8.1'), paths)
            self.assertEqual(mb.tweak_value(mb.read_config(paths['state']), 'windows-version'), '10')

    def test_repair_dependencies_reuses_configured_windows_version(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / 'prefix'
            prefix.mkdir()
            (prefix / '.musicbee-bootstrap-complete').touch()
            paths = {'state': root / 'state', 'prefix': prefix}
            mb.write_config(paths['state'], {'tweaks': {'windows-version': '10'}})
            with patch.object(mb, 'ensure_bootstrap') as bootstrap, \
                 patch.object(mb, 'apply_windows_version') as version, \
                 patch.object(mb, 'record_environment'):
                mb.repair_dependencies(paths)
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
            with patch.object(mb, 'require_ready'), \
                 patch.object(mb, 'require_commands'), patch.object(mb, 'call') as command, \
                 patch.object(mb, 'apply_windows_version') as windows_version, \
                 patch.dict(mb.os.environ, {'PULSE_LATENCY_MSEC': '25'}):
                mb.run(args, paths)
                windows_version.assert_called_once_with('8.1', paths['prefix'])
                self.assertEqual(command.call_args.kwargs['env']['WINEDLLOVERRIDES'], '*gdiplus=n,b;gdiplus=n,b')
                self.assertEqual(command.call_args.kwargs['env']['PULSE_LATENCY_MSEC'], '25')
                mb.write_config(paths['state'], {'music_dir': '/missing', 'windows_music_path': 'M:',
                                                  'tweaks': {'gdiplus': 'wine', 'audio-latency': 'on', 'windows-version': '10'}})
                mb.run(args, paths)
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
            mb.manage_tools(args, paths)
            self.assertEqual(mb.native_tools(mb.read_config(paths['state']))['picard'],
                             ['flatpak', 'run', 'org.musicbrainz.Picard'])
            bridge = prefix / 'drive_c' / 'musicbee-linux-tools' / mb.tool_bridge_name('picard')
            self.assertIn('tools-dispatch picard "$@"', bridge.read_text())
            bridge.write_text('#!/bin/sh\n' + mb.TOOL_BRIDGE_MARKER + 'exec old-launcher tools-dispatch picard "$@"\n')
            mb.refresh_tool_bridges(paths, mb.read_config(paths['state']))
            self.assertIn('musicbee-manage tools-dispatch picard', bridge.read_text())
            files = [root / 'a file.flac', root / '音楽.flac']
            for file in files:
                file.touch()
            responses = [subprocess.CompletedProcess([], 0, str(file) + '\n', '') for file in files]
            with patch.object(mb, 'require_commands'), \
                 patch.object(mb.subprocess, 'run', side_effect=responses) as convert, \
                 patch.object(mb.subprocess, 'Popen') as launch:
                mb.dispatch_tool('picard', [r'M:\a file.flac', r'M:\音楽.flac'], paths)
                self.assertEqual(convert.call_count, 2)
                self.assertEqual(launch.call_args.args[0], ['flatpak', 'run', 'org.musicbrainz.Picard', *map(str, files)])
                self.assertNotIn('WINEDLLOVERRIDES', launch.call_args.kwargs['env'])
            mb.manage_tools(argparse.Namespace(action='remove', name='picard'), paths)
            self.assertNotIn('picard', mb.native_tools(mb.read_config(paths['state'])))

    def test_native_tool_refuses_existing_unmanaged_bridge(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / 'prefix'
            (prefix / 'drive_c').mkdir(parents=True)
            (prefix / '.musicbee-bootstrap-complete').touch()
            bridge = prefix / 'drive_c' / 'musicbee-linux-tools' / mb.tool_bridge_name('picard')
            bridge.parent.mkdir()
            bridge.write_text('user data')
            paths = {'state': root / 'state', 'prefix': prefix, 'bin': root / 'musicbee'}
            with self.assertRaises(mb.SetupError):
                mb.manage_tools(argparse.Namespace(action='add', name='picard', tool_command='picard'), paths)
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
            with patch.object(mb.sys, 'stderr', new_callable=io.StringIO):
                mb.configure_explorer_redirect(paths, True)
                mb.configure_explorer_redirect(paths, False)
            self.assertEqual(target.read_bytes(), b'existing explorer')
            target.unlink()
            mb.configure_explorer_redirect(paths, True)
            self.assertIn(mb.EXPLORER_MARKER, target.read_text())
            mb.configure_explorer_redirect(paths, False)
            self.assertFalse(target.exists())

    def test_explorer_redirect_selection_and_fallback(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "音's file.flac"
            path.touch()
            paths = {'prefix': root / 'prefix'}
            conversion = subprocess.CompletedProcess([], 0, str(path) + '\n', '')
            failed_dbus = subprocess.CompletedProcess([], 1, '', '')
            with patch.object(mb.shutil, 'which', return_value='/usr/bin/program'), \
                 patch.object(mb.subprocess, 'run', side_effect=[conversion, failed_dbus]) as run, \
                 patch.object(mb.subprocess, 'Popen') as launch:
                mb.dispatch_explorer([r'/select,M:\Music\file.flac'], paths)
                self.assertIn('%27', run.call_args.args[0][-2])
                self.assertEqual(launch.call_args.args[0], ['xdg-open', str(root)])
            with patch.object(mb.subprocess, 'Popen') as launch:
                mb.dispatch_explorer(['/unsupported'], paths)
                self.assertEqual(launch.call_args.args[0], ['wine', r'C:\windows\explorer.exe', '/unsupported'])

    def test_explorer_direct_file_path_is_revealed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'track.flac'
            path.touch()
            responses = [subprocess.CompletedProcess([], 0, str(path) + '\n', ''),
                         subprocess.CompletedProcess([], 0, '', '')]
            with patch.object(mb.shutil, 'which', return_value='/usr/bin/gdbus'), \
                 patch.object(mb.subprocess, 'run', side_effect=responses) as run, \
                 patch.object(mb.subprocess, 'Popen') as launch:
                mb.dispatch_explorer([r'M:\Music\track.flac'], {'prefix': root})
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
            mb.write_config(state, {'tweaks': {'gdiplus': 'wine'}})
            with patch.object(mb, 'ensure_bootstrap', side_effect=fake_wine_bootstrap), \
                 patch.object(mb, 'apply_windows_version') as windows_version, \
                 patch.object(mb, 'record_environment'), \
                 patch.object(mb, 'extract_musicbee_icon', return_value=None), \
                 patch.object(mb, 'ensure_media_controls') as media_mock:
                mb.first_launch(args, paths)
                media_mock.assert_called_once_with(paths)
                mb.ensure_aac_decoder.assert_called_once_with(paths)
                windows_version.assert_called_once_with('8.1', prefix)
            self.assertTrue((paths['app'] / 'MusicBee.exe').is_file())
            self.assertEqual(json.loads((state/'config.json').read_text())['tweaks']['gdiplus'], 'wine')
            self.assertFalse((prefix/'drive_c'/'Users').exists())
            self.assertFalse((prefix/'dosdevices'/'m:').exists())
            self.assertTrue(paths['desktop'].is_file())
            self.assertIn('musicbee-manage" desktop-launch %f\nTerminal=false\n', paths['desktop'].read_text())
            self.assertEqual(len(json.loads((state/'portable-source.json').read_text())['sha256']), 64)

    def test_desktop_launcher_shows_setup_in_terminal_until_installed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            desktop = root / 'musicbee.desktop'
            mb.make_launcher(root / 'musicbee', desktop, setup_needed=True)
            self.assertIn(' desktop-setup %f\nTerminal=true\n', desktop.read_text())
            mb.make_launcher(root / 'musicbee', desktop)
            self.assertIn('musicbee-manage" desktop-launch %f\nTerminal=false\n', desktop.read_text())
            self.assertIn('audio/x-flac;', desktop.read_text())
            self.assertIn('audio/x-mpegurl;', desktop.read_text())

    def test_desktop_setup_starts_musicbee_after_setup(self):
        paths = {'app': Path('/unused')}
        with patch.object(mb.sys, 'argv', ['musicbee-manage', 'desktop-setup', '/music/song.flac']), \
             patch.object(mb.os, 'geteuid', return_value=1000), \
             patch.object(mb, 'locations', return_value=paths), \
             patch.object(mb, 'first_launch') as setup_mock, \
             patch.object(mb.subprocess, 'Popen') as launch_mock:
            self.assertEqual(mb.main(), 0)
            self.assertTrue(setup_mock.call_args.args[0].no_launch)
            self.assertEqual(launch_mock.call_args.args[0][-3:], ['--manage', 'desktop-launch', '/music/song.flac'])
            self.assertTrue(launch_mock.call_args.kwargs['start_new_session'])

    def test_desktop_launch_displays_errors(self):
        with TemporaryDirectory() as tmp:
            app = Path(tmp)
            (app / 'MusicBee.exe').touch()
            with patch.object(mb.sys, 'argv', ['musicbee-manage', 'desktop-launch']), \
                 patch.object(mb.os, 'geteuid', return_value=1000), \
                 patch.dict(mb.os.environ, {'MUSICBEE_DESKTOP_LOG_WORKER': '1'}), \
                 patch.object(mb, 'locations', return_value={'app': app, 'state': app}), \
                 patch.object(mb, 'run', side_effect=mb.SetupError('Wine not found')), \
                 patch.object(mb, 'show_desktop_error') as show:
                self.assertEqual(mb.main(), 1)
                show.assert_called_once_with(f'Wine not found\n\nLog: {app / "desktop-launch.log"}')

    def test_desktop_log_captures_and_limits_output(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            popen = subprocess.Popen
            def noisy_process(_command, **kwargs):
                return popen([mb.sys.executable, '-c',
                              'import sys; sys.stdout.write("x" * (3 * 1024 * 1024))'], **kwargs)
            with patch.object(mb.subprocess, 'Popen', side_effect=noisy_process):
                self.assertEqual(mb.desktop_launch_with_log({'state': root}, []), 0)
            log = (root / 'desktop-launch.log').read_bytes()
            self.assertEqual(len(log), 2 * 1024 * 1024)
            self.assertTrue(log.endswith(b'[Log truncated at 2 MiB]\n'))

    def test_desktop_log_failure_does_not_prevent_launch(self):
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / 'not-a-directory'
            state.write_text('file')
            self.assertIsNone(mb.desktop_launch_with_log({'state': state}, []))

    def test_desktop_error_tries_available_dialogs(self):
        with patch.object(mb.shutil, 'which', side_effect=lambda name: name if name in ('kdialog', 'zenity') else None), \
             patch.object(mb.subprocess, 'run', side_effect=[subprocess.CompletedProcess([], 1),
                                                               subprocess.CompletedProcess([], 0)]) as calls:
            mb.show_desktop_error('Missing Wine')
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
            with patch.object(mb, 'initialize_prefix') as setup_mock, \
                 patch.object(mb, 'ensure_media_controls') as media_mock:
                mb.first_launch(args, paths)
                setup_mock.assert_not_called()
                media_mock.assert_not_called()

    def test_media_controls_installed_only_when_missing(self):
        with TemporaryDirectory() as tmp:
            app = Path(tmp) / 'app'
            paths = {'app': app}
            with patch.object(mb, 'media_controls') as media_mock:
                mb.ensure_media_controls(paths)
                self.assertEqual(media_mock.call_args.args[0].action, 'install')
                plugin = app / 'Plugins' / 'mb_MPRISBee.dll'
                plugin.parent.mkdir(parents=True)
                plugin.touch()
                mb.ensure_media_controls(paths)
                media_mock.assert_called_once()

    def test_media_controls_install_failure_is_nonfatal(self):
        with TemporaryDirectory() as tmp, \
             patch.object(mb, 'media_controls', side_effect=mb.SetupError('offline')), \
             patch.object(mb.sys, 'stderr', new_callable=io.StringIO) as stderr:
            mb.ensure_media_controls({'app': Path(tmp)})
            self.assertIn('offline', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
