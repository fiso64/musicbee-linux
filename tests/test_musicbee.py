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

SRC = Path(__file__).resolve().parents[1] / 'src' / 'musicbee.py'
loader = SourceFileLoader('musicbee_linux', str(SRC))
spec = spec_from_loader(loader.name, loader)
mb = module_from_spec(spec)
loader.exec_module(mb)


class PortableExtractionTests(unittest.TestCase):
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
            with self.assertRaisesRegex(mb.SetupError, 'musicbee install'):
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
            with patch.object(mb, 'setup') as setup_mock, patch.object(mb, 'confirm_license') as consent_mock:
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
        args = parser.parse_args(['init','--music-dir','/tmp/music','--accept-license','--no-launch'])
        self.assertIsNone(args.wine_music_path)
        self.assertTrue(args.accept_license)
        self.assertTrue(args.no_launch)

    def test_existing_wine_music_link_is_kept(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / 'prefix'
            music = root / 'Music'
            music.mkdir()
            target = prefix / 'drive_c' / 'users' / 'testuser' / 'Music'
            target.parent.mkdir(parents=True)
            target.symlink_to(music, target_is_directory=True)
            original_inode = target.lstat().st_ino
            result = mb.update_mapping(prefix, music, r'C:\Users\testuser\Music', {})
            self.assertEqual(result, target)
            self.assertEqual(target.lstat().st_ino, original_inode)
            self.assertEqual(target.resolve(), music)

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

    def test_run_applies_gdiplus_and_audio_settings(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / 'prefix' / 'app'
            app.mkdir(parents=True)
            (app / 'MusicBee.exe').touch()
            launcher = root / 'musicbee'
            launcher.touch()
            paths = {'state': root / 'state', 'prefix': root / 'prefix', 'app': app, 'bin': launcher}
            args = argparse.Namespace(musicbee_args=[])
            with patch.object(mb, 'require_ready'), patch.object(mb, 'validate_mapping'), \
                 patch.object(mb, 'require_commands'), patch.object(mb, 'call') as command, \
                 patch.object(mb, 'apply_windows_version') as windows_version, \
                 patch.dict(mb.os.environ, {'PULSE_LATENCY_MSEC': '25'}):
                mb.run(args, paths)
                windows_version.assert_called_once_with('8.1', paths['prefix'])
                self.assertEqual(command.call_args.kwargs['env']['WINEDLLOVERRIDES'], '*gdiplus=n,b;gdiplus=n,b')
                self.assertEqual(command.call_args.kwargs['env']['PULSE_LATENCY_MSEC'], '25')
                mb.write_config(paths['state'], {'tweaks': {'gdiplus': 'wine', 'audio-latency': 'on', 'windows-version': '10'}})
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
            paths = {'state': root / 'state', 'prefix': prefix, 'bin': launcher}
            args = argparse.Namespace(action='add', name='picard', tool_command='flatpak run org.musicbrainz.Picard')
            mb.manage_tools(args, paths)
            self.assertEqual(mb.native_tools(mb.read_config(paths['state']))['picard'],
                             ['flatpak', 'run', 'org.musicbrainz.Picard'])
            bridge = prefix / 'drive_c' / 'musicbee-linux-tools' / mb.tool_bridge_name('picard')
            self.assertIn('tools-dispatch picard "$@"', bridge.read_text())
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
                windows_version.assert_called_once_with('8.1', prefix)
            self.assertTrue((paths['app'] / 'MusicBee.exe').is_file())
            self.assertEqual(json.loads((state/'config.json').read_text())['windows_music_path'],
                             f'C:\\Users\\{mb.getpass.getuser()}\\Music')
            self.assertEqual(json.loads((state/'config.json').read_text())['tweaks']['gdiplus'], 'wine')
            self.assertEqual((prefix/'drive_c'/'Users'/mb.getpass.getuser()/'Music').resolve(),
                             (home/'Music').resolve())
            self.assertFalse((prefix/'dosdevices'/'m:').exists())
            self.assertTrue(paths['desktop'].is_file())
            self.assertIn(' run\nTerminal=false\n', paths['desktop'].read_text())
            self.assertEqual(len(json.loads((state/'portable-source.json').read_text())['sha256']), 64)

    def test_desktop_launcher_shows_setup_in_terminal_until_installed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            desktop = root / 'musicbee.desktop'
            mb.make_launcher(root / 'musicbee', desktop, setup_needed=True)
            self.assertIn(' desktop-setup\nTerminal=true\n', desktop.read_text())
            mb.make_launcher(root / 'musicbee', desktop)
            self.assertIn(' run\nTerminal=false\n', desktop.read_text())

    def test_desktop_setup_starts_musicbee_after_setup(self):
        paths = {'app': Path('/unused')}
        with patch.object(mb.sys, 'argv', ['musicbee', 'desktop-setup']), \
             patch.object(mb.os, 'geteuid', return_value=1000), \
             patch.object(mb, 'locations', return_value=paths), \
             patch.object(mb, 'first_launch') as setup_mock, \
             patch.object(mb.subprocess, 'Popen') as launch_mock:
            self.assertEqual(mb.main(), 0)
            self.assertTrue(setup_mock.call_args.args[0].no_launch)
            self.assertEqual(launch_mock.call_args.args[0][-1], 'run')
            self.assertTrue(launch_mock.call_args.kwargs['start_new_session'])

    def test_run_uses_no_install_if_present(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / 'data'
            app = state / 'wineprefix' / 'drive_c' / 'MusicBee'
            app.mkdir(parents=True)
            (app / 'MusicBee.exe').write_bytes(b'a')
            paths = {'state':state, 'prefix':state/'wineprefix', 'app':app}
            args = argparse.Namespace(music_dir=None, wine_music_path='M:', archive=None, accept_license=True, no_launch=True)
            with patch.object(mb, 'setup') as setup_mock, \
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
