"""Build safety and test-isolation regressions; never run or kill a live service."""
import ast
import ctypes
import http.server
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from test_support import isolate_test_environment


TEST_ENV = isolate_test_environment()
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_exe  # noqa: E402
from windows_job import OwnedProcessJob  # noqa: E402


class BuildSafetyTests(unittest.TestCase):
    def setUp(self):
        self.work = TEST_ENV.temporary_dir(prefix='build-test-')
        self.source = self.work / 'source'
        self.source.mkdir()
        (self.source / 'lanshare.py').write_text('pass\n', encoding='ascii')

    def tearDown(self):
        TEST_ENV.remove_test_dir(self.work)

    def test_source_ancestors_children_and_volume_root_refused(self):
        sentinel = self.source / 'keep.txt'
        sentinel.write_bytes(b'preserve-source')
        for target in (self.source, self.work, self.source / 'build', Path(self.source.anchor)):
            with self.subTest(target=str(target)), self.assertRaises(ValueError):
                build_exe.prepare_staging(self.source, target)
        self.assertEqual(sentinel.read_bytes(), b'preserve-source')

    def test_unmarked_existing_directory_refused_without_changes(self):
        target = self.work / 'existing'
        target.mkdir()
        sentinel = target / 'important.txt'
        sentinel.write_bytes(b'not-a-build')
        with self.assertRaises(ValueError):
            build_exe.prepare_staging(self.source, target)
        self.assertEqual(sorted(p.name for p in target.iterdir()), ['important.txt'])
        self.assertEqual(sentinel.read_bytes(), b'not-a-build')

    def test_owned_container_creates_new_children_and_preserves_old_exe(self):
        target = self.work / 'container'
        first = build_exe.prepare_staging(self.source, target)
        (first / 'dist').mkdir()
        old_exe = first / 'dist' / 'lanshare.exe'
        old_exe.write_bytes(b'old-release')
        second = build_exe.prepare_staging(self.source, target)
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, second.parent)
        self.assertEqual(old_exe.read_bytes(), b'old-release')
        self.assertEqual(json.loads((target / build_exe.OWNER_FILE).read_text()), build_exe.OWNER)

    def test_wrong_ownership_marker_and_nonascii_refused(self):
        target = self.work / 'untrusted'
        target.mkdir()
        (target / build_exe.OWNER_FILE).write_text('{}', encoding='ascii')
        with self.assertRaises(ValueError):
            build_exe.prepare_staging(self.source, target)
        with self.assertRaises(ValueError):
            build_exe.prepare_staging(self.source, self.work / '\u4e2d\u6587-build')
        self.assertEqual((target / build_exe.OWNER_FILE).read_text(), '{}')

    def test_not_a_venv_never_installs_packages(self):
        venv = self.work / 'not-a-venv'
        python = build_exe.venv_python(venv)
        python.parent.mkdir(parents=True)
        python.write_bytes(b'not-an-interpreter')
        with mock.patch.object(build_exe, 'run') as run:
            with self.assertRaises(ValueError):
                build_exe.prepare_environment(sys.executable, venv)
            run.assert_not_called()

    def test_venv_runtime_prefix_must_match_requested_directory(self):
        venv = self.work / 'venv'
        python = build_exe.venv_python(venv)
        python.parent.mkdir(parents=True)
        python.write_bytes(b'fixture')
        (venv / 'pyvenv.cfg').write_text('home = fixture\n', encoding='ascii')
        cases = [(str(venv), str(venv)), (str(self.work / 'elsewhere'), str(self.work / 'base'))]
        for prefix, base in cases:
            response = subprocess.CompletedProcess([], 0, json.dumps([prefix, base]), '')
            with self.subTest(prefix=prefix), mock.patch.object(build_exe.subprocess, 'run', return_value=response), \
                    mock.patch.object(build_exe, 'run') as install:
                with self.assertRaises(ValueError):
                    build_exe.prepare_environment(sys.executable, venv)
                install.assert_not_called()

    def test_environment_probe_ignores_parent_python_and_qt_vars(self):
        venv = self.work / 'env'
        venv.mkdir()
        (venv / 'pyvenv.cfg').write_text('home = fixture\n', encoding='ascii')
        probe = subprocess.CompletedProcess([], 0, json.dumps([str(venv), str(self.work / 'base')]), '')
        foreign = {'PYTHONPATH': 'foreign', 'PYTHONHOME': 'foreign', 'QT_PLUGIN_PATH': 'foreign'}
        with mock.patch.dict(os.environ, foreign), \
                mock.patch.object(build_exe.subprocess, 'run', return_value=probe) as run:
            build_exe.validate_environment(venv / 'Scripts/python.exe', venv)
        environment = run.call_args.kwargs['env']
        for name in foreign:
            self.assertNotIn(name, environment)
        self.assertEqual(environment['PYTHONNOUSERSITE'], '1')

    @unittest.skipUnless(os.name == 'nt', 'Windows dependency filtering')
    def test_windows_directory_venv_is_rejected_before_install(self):
        windows = self.work / 'Windows'
        windows.mkdir()
        target = windows / 'Temp' / 'env'
        with mock.patch.dict(os.environ, {'SystemRoot': str(windows)}), \
                mock.patch.object(build_exe, 'run') as install:
            with self.assertRaisesRegex(ValueError, 'PyInstaller'):
                build_exe.prepare_environment(sys.executable, target)
            install.assert_not_called()
        self.assertFalse(target.exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows dependency filtering')
    def test_default_work_does_not_put_venv_in_windows_temp(self):
        windows = self.work / 'Windows'
        public = self.work / 'Public'
        with mock.patch.dict(os.environ, {'SystemRoot': str(windows), 'PUBLIC': str(public)}), \
                mock.patch.object(build_exe.tempfile, 'gettempdir', return_value=str(windows / 'Temp')):
            self.assertEqual(build_exe.default_work(), public / 'LanShare-builds')

    @unittest.skipUnless(os.name == 'nt', 'Windows dependency filtering')
    def test_build_environment_drops_external_python_qt_and_dll_paths(self):
        windows = self.work / 'Windows'
        base = self.work / 'Python'
        venv = self.work / 'env'
        for path in (windows / 'System32', base / 'DLLs', venv / 'Scripts'):
            path.mkdir(parents=True, exist_ok=True)
        foreign = self.work / 'foreign-native-libs'
        settings = {'SystemRoot': str(windows), 'PATH': str(foreign), 'PYTHONPATH': str(foreign),
                    'PYTHONHOME': str(foreign), 'QT_PLUGIN_PATH': str(foreign),
                    'QT_QPA_PLATFORM_PLUGIN_PATH': str(foreign), 'QTDIR': str(foreign)}
        probe = subprocess.CompletedProcess([], 0, json.dumps(str(base)), '')
        with mock.patch.dict(os.environ, settings), \
                mock.patch.object(build_exe.subprocess, 'run', return_value=probe):
            environment = build_exe.build_environment(venv / 'Scripts/python.exe', self.work)
        self.assertNotIn(str(foreign), environment['PATH'])
        self.assertIn(str(base), environment['PATH'])
        self.assertIn(str(venv / 'Scripts'), environment['PATH'])
        for name in ('PYTHONPATH', 'PYTHONHOME', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QTDIR'):
            self.assertNotIn(name, environment)
        self.assertEqual(environment['BUILD_OUT'], str(self.work))
        self.assertEqual(environment['LANSHARE_DATA_DIR'], str(self.work / '_preflight/chat_data'))
        self.assertEqual(environment['PYINSTALLER_CONFIG_DIR'], str(self.work / '_pyinstaller-cache'))

    @unittest.skipUnless(os.name == 'nt', 'Windows runtime temp directory')
    def test_build_environment_drops_inherited_bootloader_state(self):
        foreign = {'_PYI_APPLICATION_HOME_DIR': 'foreign-extraction',
                   '_PYI_PARENT_PROCESS_LEVEL': '1', 'PYINSTALLER_RESET_ENVIRONMENT': '0',
                   'PYINSTALLER_CONFIG_DIR': 'foreign-cache'}
        with mock.patch.dict(os.environ, foreign):
            environment = build_exe.clean_python_environment()
        for name in foreign:
            self.assertNotIn(name, environment)

    @unittest.skipUnless(os.name == 'nt', 'Windows runtime temp directory')
    def test_build_does_not_depend_on_localappdata(self):
        with mock.patch.dict(os.environ, {'LOCALAPPDATA': str(self.work / 'builder-profile')}):
            with mock.patch.object(build_exe, 'qt_runtime_flags', return_value=[]), \
                    mock.patch.object(build_exe, 'image_plugin_flags', return_value=[]):
                command = build_exe.pyinstaller_command(sys.executable, self.work, {})
        self.assertNotIn('--runtime-tmpdir', command)
        self.assertNotIn('builder-profile', ' '.join(command))

    @unittest.skipUnless(os.name == 'nt', 'Windows runtime temp directory')
    def test_archive_rejects_any_custom_runtime_directory_option(self):
        for options in ([], ['pyi-contents-directory _internal'],
                        ['pyi-runtime-tmpdir %LOCALAPPDATA%/LanShare/Temp'],
                        ['pyi-runtime-tmpdir C:/Users/builder/Temp'], None, [42]):
            probe = subprocess.CompletedProcess([], 0, json.dumps(options), '')
            with self.subTest(options=options), \
                    mock.patch.object(build_exe.subprocess, 'run', return_value=probe):
                if options in ([], ['pyi-contents-directory _internal']):
                    build_exe.verify_default_runtime_archive(sys.executable, self.work / 'fixture.exe', {})
                else:
                    with self.assertRaises(RuntimeError):
                        build_exe.verify_default_runtime_archive(sys.executable, self.work / 'fixture.exe', {})

    @unittest.skipUnless(os.name == 'nt', 'Windows runtime temp directory')
    def test_smoke_owns_temp_and_resets_child_bootloader_state(self):
        original = {'LOCALAPPDATA': 'real-user-profile', 'TEMP': 'inherited-temp', 'TMP': 'inherited-tmp',
                    '_PYI_ARCHIVE_FILE': 'foreign.exe', 'PYINSTALLER_CONFIG_DIR': 'build-cache',
                    'PYINSTALLER_RESET_ENVIRONMENT': '0'}
        profile = self.work / 'owned-profile'
        result = build_exe.smoke_environment(original, profile)
        self.assertEqual(original['LOCALAPPDATA'], 'real-user-profile')
        self.assertEqual(result['LOCALAPPDATA'], str(profile / 'local-app-data'))
        self.assertEqual(result['LANSHARE_DATA_DIR'], str(profile / 'chat_data'))
        self.assertEqual(result['QT_QPA_PLATFORM'], 'offscreen')
        self.assertEqual(result['TEMP'], str(profile / 'temp'))
        self.assertEqual(result['TMP'], result['TEMP'])
        self.assertNotIn('_PYI_ARCHIVE_FILE', result)
        self.assertNotIn('PYINSTALLER_CONFIG_DIR', result)
        self.assertEqual(result['PYINSTALLER_RESET_ENVIRONMENT'], '1')
        self.assertFalse(profile.exists(), 'Environment construction must not precreate fixtures')
        self.assertEqual(original['PYINSTALLER_RESET_ENVIRONMENT'], '0')

    @unittest.skipUnless(os.name == 'nt', 'Windows runtime temp directory')
    def test_packaging_command_keeps_default_temp_and_onefile_flags(self):
        with mock.patch.object(build_exe, 'qt_runtime_flags', return_value=[]), \
                mock.patch.object(build_exe, 'image_plugin_flags', return_value=[]):
            command = build_exe.pyinstaller_command(sys.executable, self.work, {})
        self.assertIn('--onefile', command)
        self.assertIn('--windowed', command)
        self.assertNotIn('--runtime-tmpdir', command)
        self.assertEqual(command[-1], 'lanshare.py')

    @unittest.skipUnless(os.name == 'nt', 'Windows dependency filtering')
    def test_archive_cannot_silently_omit_main_qt_dlls(self):
        required = ('Qt5Core.dll', 'Qt5Gui.dll', 'Qt5Widgets.dll', 'Qt5Network.dll',
                    'QtCore.pyd', 'qwindows.dll', 'qoffscreen.dll', 'MSVCP140_1.dll', 'concrt140.dll')
        for names in (required, required[1:]):
            probe = subprocess.CompletedProcess([], 0, json.dumps(['PyQt5/Qt5/bin/' + n for n in names]), '')
            with mock.patch.object(build_exe.subprocess, 'run', return_value=probe):
                if len(names) == len(required):
                    build_exe.verify_qt_archive(sys.executable, self.work / 'fixture.exe', {})
                else:
                    with self.assertRaisesRegex(RuntimeError, 'qt5core.dll'):
                        build_exe.verify_qt_archive(sys.executable, self.work / 'fixture.exe', {})

    @unittest.skipUnless(os.name == 'nt', 'Windows delayed CRT dependency')
    def test_delayed_qt_runtime_is_collected_from_the_selected_venv(self):
        venv = self.work / 'env'
        binaries = venv / 'Lib/site-packages/PyQt5/Qt5/bin'
        binaries.mkdir(parents=True)
        runtime = binaries / 'concrt140.dll'
        runtime.write_bytes(b'synthetic-qt-delay-runtime')
        probe = subprocess.CompletedProcess([], 0, json.dumps(str(binaries)), '')
        with mock.patch.object(build_exe.subprocess, 'run', return_value=probe):
            flags = build_exe.qt_runtime_flags(venv / 'Scripts/python.exe', {})
            self.assertEqual(flags, ['--add-binary', str(runtime) + os.pathsep + 'PyQt5/Qt5/bin'])
            with self.assertRaisesRegex(RuntimeError, 'concrt140.dll'):
                build_exe.qt_runtime_flags(self.work / 'foreign-env/Scripts/python.exe', {})

    def test_preflight_reports_missing_pillow_as_required_not_optional(self):
        import preflight
        def importer(name):
            if name == 'PIL':
                raise ImportError('synthetic missing Pillow')
            return mock.Mock(__version__='fixture')
        with mock.patch.object(preflight.importlib, 'import_module', side_effect=importer), \
                mock.patch.object(preflight, 'line') as line:
            preflight.check_optional()
        self.assertEqual(line.call_args_list[0].args[0], preflight.BAD)
        self.assertIn('Pillow', line.call_args_list[0].args[1])

    def test_clean_source_regenerates_assets_before_snapshot(self):
        for directory in ('server', 'ui', 'tools', 'web/js', 'web/pages'):
            (self.source / directory).mkdir(parents=True, exist_ok=True)
        for name in ('build_assets.py', 'preflight.py', 'test_support.py'):
            shutil.copy2(ROOT / 'tools' / name, self.source / 'tools' / name)
        shutil.copy2(ROOT / 'icon.ico', self.source / 'icon.ico')
        for name in ('tokens.css', 'app.css', 'files-search.css'):
            (self.source / 'web' / name).write_text('/* fresh-%s */\n' % name, encoding='ascii')
        (self.source / 'web/theme-boot.js').write_text('/* fresh-theme */\n', encoding='ascii')
        for name in ('00-core.js', '50-files.js', '80-chat.js', '88-login.js'):
            (self.source / 'web/js' / name).write_text('/* fresh-%s */\n' % name, encoding='ascii')
        for name in ('layout', 'files', 'chat_list', 'chat_room', 'login'):
            (self.source / 'web/pages' / (name + '.html')).write_text('<main>%s</main>\n' % name,
                                                                   encoding='ascii')
        staging = build_exe.prepare_staging(self.source, self.work / 'builds')
        self.assertFalse((self.source / 'webassets.py').exists())
        build_exe.snapshot_source(self.source, staging, sys.executable)
        generated = (self.source / 'webassets.py').read_bytes()
        self.assertEqual(generated, (staging / 'webassets.py').read_bytes())
        self.assertIn(b'fresh-files-search.css', generated)
        self.assertTrue((staging / 'tools/test_support.py').is_file())
        (self.source / 'web/app.css').write_text('/* newer-source */\n', encoding='ascii')
        second = build_exe.prepare_staging(self.source, self.work / 'builds')
        build_exe.snapshot_source(self.source, second, sys.executable)
        self.assertIn(b'newer-source', (second / 'webassets.py').read_bytes())
        self.assertEqual(generated, (staging / 'webassets.py').read_bytes())

    def test_graceful_stop_uses_only_owned_profile_and_handle(self):
        profile = self.work / 'profile'
        profile.mkdir()
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        build_exe.stop_owned_process(process, profile)
        self.assertEqual((profile / 'stop.request').read_text(), 'stop\n')
        process.terminate.assert_not_called()
        process.kill.assert_not_called()
        process.wait.assert_called_once_with(timeout=20)

    @unittest.skipUnless(os.name == 'nt', 'Windows process-tree ownership test')
    def test_private_job_ends_only_its_owned_parent_and_child(self):
        job = OwnedProcessJob()
        process = subprocess.Popen(
            [sys.executable, '-c', 'import subprocess,sys,time; p=subprocess.Popen([sys.executable,'
             '"-c","import time; time.sleep(120)"]); print(p.pid,flush=True); time.sleep(120)'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            job.assign(process)
            lines = []
            reader = threading.Thread(target=lambda: lines.append(process.stdout.readline()), daemon=True)
            reader.start()
            reader.join(10)
            self.assertTrue(lines and lines[0].strip(), 'Owned child PID was not received')
            child_pid = int(lines[0])
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            child_handle = kernel.OpenProcess(0x100000, False, child_pid)
            self.assertTrue(child_handle)
            try:
                job.close()
                process.wait(timeout=5)
                self.assertEqual(kernel.WaitForSingleObject(child_handle, 5000), 0)
            finally:
                kernel.CloseHandle(child_handle)
        finally:
            job.close()
            TEST_ENV.stop_process(process)
            process.stdout.close()
            process.stderr.close()


class IsolationTests(unittest.TestCase):
    def test_cleanup_rejects_external_directory_and_preserves_sentinel(self):
        with tempfile.TemporaryDirectory(prefix='unrelated-sentinel-') as outside:
            sentinel = Path(outside) / 'keep.txt'
            sentinel.write_bytes(b'not-owned-by-test-support')
            with self.assertRaises(ValueError):
                TEST_ENV.remove_test_dir(outside)
            self.assertEqual(sentinel.read_bytes(), b'not-owned-by-test-support')
        with self.assertRaises(ValueError):
            TEST_ENV.remove_test_dir(TEST_ENV.root)

    def test_gui_optional_none_cannot_fall_back_to_real_settings(self):
        with mock.patch('ui.main_window.FileShareGUI') as gui:
            TEST_ENV.gui(settings=None)
            arguments = gui.call_args.kwargs
            Path(arguments['settings'].fileName()).resolve().relative_to(TEST_ENV.root)
            self.assertFalse(arguments['system_integration_enabled'])
            self.assertIsNone(arguments['context_menu'].registry)
        external = mock.Mock()
        external.fileName.return_value = str(Path(TEST_ENV.root.anchor) / 'outside.ini')
        with self.assertRaises(ValueError):
            TEST_ENV.gui(settings=external)

    def test_failed_child_readiness_never_contacts_any_http_server(self):
        process = mock.Mock(stdout=io.BytesIO(b'FAILED bind failed\n'))
        process.poll.return_value = None
        with mock.patch('test_support.urllib.request.urlopen') as request:
            self.assertFalse(TEST_ENV.wait_server(process, 5995, TEST_ENV.root, timeout=1))
            request.assert_not_called()

    def test_ready_child_with_wrong_share_is_rejected(self):
        process = mock.Mock(stdout=io.BytesIO(b'READY\n'))
        process.poll.return_value = None
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(json.dumps({'path': 'C:/not-our-share'}).encode())
        with mock.patch('test_support.urllib.request.urlopen', return_value=response):
            self.assertFalse(TEST_ENV.wait_server(process, 5995, TEST_ENV.data, timeout=1))

    def test_owned_browser_profile_supplies_its_own_endpoint(self):
        profile = TEST_ENV.temporary_dir(prefix='browser-endpoint-')
        try:
            (profile / 'DevToolsActivePort').write_text('12345\n/devtools/browser/owned\n', encoding='ascii')
            process = mock.Mock()
            process.poll.return_value = None
            self.assertEqual(TEST_ENV.wait_browser(process, profile, timeout=1), 12345)
            with self.assertRaises(ValueError):
                TEST_ENV.wait_browser(process, Path(TEST_ENV.root.anchor), timeout=0)
        finally:
            TEST_ENV.remove_test_dir(profile)

    def test_legacy_tools_enable_isolation_before_app_import(self):
        names = ('smoke_test', 'auth_matrix_test', 'protocol_test', 'search_test', 'fsops_test',
                 'zip_test', 'sse_test', 'thumb_test', 'origin_test', 'paging_test',
                 'share_switch_test', 'limits_test', 'preflight', 'desktop_test', 'ui_test',
                 'admin_flow_test', 'mobile_shot', 'screenshots', 'theme_flash_test', 'ui_detail_test')
        for name in names:
            with self.subTest(name=name):
                text = (ROOT / 'tools' / (name + '.py')).read_text(encoding='utf-8')
                self.assertIn('TEST_ENV = isolate_test_environment()', text)
                if 'import lanshare' in text:
                    self.assertLess(text.index('TEST_ENV ='), text.index('import lanshare'))
        thumb = (ROOT / 'tools/thumb_test.py').read_text(encoding='utf-8')
        self.assertNotIn('Path(ROOT, "chat_data"', thumb)
        self.assertNotIn('.glob("*")', thumb)

    def test_busy_port_smoke_aborts_without_touching_existing_service(self):
        requests = []

        class SentinelServer(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(('GET', self.path))
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'{}')

            def do_POST(self):
                requests.append(('POST', self.path))
                self.send_response(500)
                self.end_headers()

            def log_message(self, *unused):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), SentinelServer)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run([sys.executable, str(ROOT / 'tools/smoke_test.py'), '--port',
                                     str(server.server_port)], capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 2, result.stdout.decode('utf-8', 'replace'))
            self.assertEqual(requests, [], 'The failed test contacted an existing service')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
