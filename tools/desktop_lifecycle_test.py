"""Desktop lifecycle, local IPC and mocked Explorer registration regressions.

Uses offscreen Qt, isolated INI QSettings, isolated chat data and fake winreg.
Never installs a real Explorer entry or writes the user's application settings.
    python -B tools/desktop_lifecycle_test.py
"""
import ctypes
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import uuid
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
MODULE_PATH = Path(__file__).resolve()
MODULE_HASH = hashlib.sha256(MODULE_PATH.read_bytes()).hexdigest()
ROOT = MODULE_PATH.parent.parent
sys.path.insert(0, str(ROOT))
WORK = tempfile.TemporaryDirectory(prefix='lanshare-desktop-tests-')
WORK_PATH = Path(WORK.name).resolve()
os.environ['LANSHARE_DATA_DIR'] = str(WORK_PATH / 'chat')

from PyQt5.QtCore import QSettings  # noqa: E402
from PyQt5.QtNetwork import QLocalSocket  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402
from server import runner, storage  # noqa: E402
from ui.context_menu import ContextMenuIntegration, START_CAPTION, SWITCH_CAPTION  # noqa: E402
from ui.main_window import FileShareGUI  # noqa: E402
from ui.single_instance import (MAX_MESSAGE_BYTES, MAX_LOCAL_CLIENTS, SingleInstance,
                                validate_request, validate_test_profile)  # noqa: E402
from ui import single_instance  # noqa: E402
from lanshare import watch_test_exit  # noqa: E402

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


def pump_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    APP.processEvents()
    return bool(predicate())


def unused_port():
    with socket.socket() as sock:
        if os.name == 'nt':
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def check_cleanup_target(path):
    target = Path(path).resolve()
    if target == Path(target.anchor) or Path(path).is_symlink():
        raise RuntimeError('Fixture cleanup cannot target a link or volume root')
    try:
        target.relative_to(WORK_PATH)
    except ValueError:
        raise RuntimeError('Fixture cleanup escaped its owned temporary directory')
    try:
        MODULE_PATH.relative_to(target)
    except ValueError:
        pass
    else:
        raise RuntimeError('Fixture cleanup cannot contain the test source')
    return target


def cleanup_fixture(fixture):
    check_cleanup_target(fixture.name)
    fixture.cleanup()


class FakeKey:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeRegistry:
    HKEY_CURRENT_USER = 'HKCU'
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {}
        self.writes = []

    def CreateKey(self, hive, path):
        assert hive == self.HKEY_CURRENT_USER
        self.values.setdefault(path, {})
        self.writes.append(('create', hive, path))
        return FakeKey(path)

    def OpenKey(self, hive, path, _reserved=0, _access=0):
        assert hive == self.HKEY_CURRENT_USER
        if path not in self.values:
            raise FileNotFoundError(path)
        return FakeKey(path)

    def SetValueEx(self, key, name, _reserved, _kind, value):
        self.values[key.path][name] = value
        self.writes.append(('set', self.HKEY_CURRENT_USER, key.path))

    def DeleteKey(self, hive, path):
        assert hive == self.HKEY_CURRENT_USER
        if path not in self.values:
            raise FileNotFoundError(path)
        del self.values[path]
        self.writes.append(('delete', hive, path))


class ContextMenuTests(unittest.TestCase):
    def setUp(self):
        self.registry = FakeRegistry()
        self.menu = ContextMenuIntegration(registry=self.registry, command_parts=[
            r'C:\Program Files\Python\pythonw.exe', r'C:\Source Code\LanShare\lanshare.py'])

    def test_current_user_background_and_selected_folder(self):
        self.assertFalse(self.menu.is_installed())
        self.menu.install(False)
        self.assertTrue(self.menu.is_installed())
        for path, placeholder in self.menu.paths:
            self.assertEqual(self.registry.values[path][''], START_CAPTION)
            command = self.registry.values[path + r'\command']['']
            self.assertIn('--open-here "%s\\."' % placeholder, command)
            self.assertIn('"C:\\Program Files\\Python\\pythonw.exe"', command)
            self.assertIn('"C:\\Source Code\\LanShare\\lanshare.py"', command)
            self.assertNotIn('--startup', command)
        self.menu.update_caption(True)
        for path, _placeholder in self.menu.paths:
            self.assertEqual(self.registry.values[path][''], SWITCH_CAPTION)
        self.menu.update_caption(False)
        self.menu.remove()
        self.assertFalse(self.menu.is_installed())
        self.assertTrue(all(write[1] == 'HKCU' for write in self.registry.writes))

    @unittest.skipUnless(os.name == 'nt', 'Windows command line quoting')
    def test_drive_root_is_one_argument(self):
        from ctypes import wintypes
        shell = ctypes.WinDLL('shell32', use_last_error=True)
        shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        self.menu.install()
        for path, placeholder in self.menu.paths:
            command = self.registry.values[path + r'\command'][''].replace(placeholder, 'C:\\')
            count = ctypes.c_int()
            argv = shell.CommandLineToArgvW(command, ctypes.byref(count))
            try:
                parts = [argv[i] for i in range(count.value)]
            finally:
                kernel.LocalFree(ctypes.cast(argv, ctypes.c_void_p))
            self.assertEqual(len(parts), 4)
            self.assertEqual(parts[-2], '--open-here')
            self.assertEqual(Path(parts[-1]).resolve(), Path('C:\\').resolve())


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=WORK.name)
        self.share = Path(self.temp.name)
        self.sharers = []
        self.ips = mock.patch.object(runner.FileSharer, 'get_all_local_ips', return_value=['127.0.0.1'])
        self.ips.start()

    def tearDown(self):
        try:
            for sharer in self.sharers:
                sharer.stop()
                self.assertTrue(sharer.wait_stopped(5))
        finally:
            self.ips.stop()
            cleanup_fixture(self.temp)

    def new_sharer(self):
        sharer = runner.FileSharer()
        self.sharers.append(sharer)
        return sharer

    def test_exclusive_listener(self):
        server = runner.FastBindHTTPServer(('127.0.0.1', 0), lambda *_args: None)
        try:
            with self.assertRaises(OSError):
                duplicate = runner.FastBindHTTPServer(server.server_address, lambda *_args: None)
                duplicate.server_close()
        finally:
            server.server_close()

    def test_stop_completes_before_stopped_status_and_restart(self):
        sharer = self.new_sharer()
        port = unused_port()
        ok, result = sharer.start(str(self.share), port)
        self.assertTrue(ok, result)
        self.assertEqual(sharer.state, sharer.RUNNING)
        entered, release = threading.Event(), threading.Event()
        shutdown = sharer.server.shutdown

        def delayed_shutdown():
            entered.set()
            release.wait(5)
            shutdown()

        events = []
        sharer.set_status_callback(lambda message, running: events.append((message, running)))
        try:
            with mock.patch.object(sharer.server, 'shutdown', delayed_shutdown):
                self.assertFalse(sharer.stop())
                self.assertTrue(entered.wait(2))
                self.assertEqual(sharer.state, sharer.STOPPING)
                self.assertFalse(sharer.wait_stopped(0))
                self.assertFalse(sharer.start(str(self.share), port)[0])
                release.set()
                self.assertTrue(sharer.wait_stopped(5))
        finally:
            release.set()
        self.assertEqual(sharer.state, sharer.STOPPED)
        self.assertIsNone(sharer.server)
        self.assertEqual(events[-1], ('\u5df2\u505c\u6b62', False))
        ok, result = sharer.start(str(self.share), port)
        self.assertTrue(ok, result)
        self.assertEqual(sharer.state, sharer.RUNNING)

    def test_cancel_start_closes_unpublished_listener(self):
        sharer = self.new_sharer()
        port = unused_port()
        entered, release = threading.Event(), threading.Event()
        original = runner.FastBindHTTPServer
        answers = []

        def delayed_factory(*args, **kwargs):
            server = original(*args, **kwargs)
            entered.set()
            release.wait(5)
            return server

        with mock.patch.object(runner, 'FastBindHTTPServer', side_effect=delayed_factory):
            worker = threading.Thread(target=lambda: answers.append(sharer.start(str(self.share), port)))
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                self.assertEqual(sharer.state, sharer.STARTING)
                self.assertFalse(sharer.stop())
                self.assertFalse(sharer.wait_stopped(0))
                self.assertFalse(sharer.set_shared_path(str(self.share))[0])
            finally:
                release.set()
                worker.join(5)
            self.assertFalse(worker.is_alive())
        self.assertFalse(answers[0][0])
        self.assertTrue(sharer.wait_stopped(1))
        self.assertEqual(sharer.state, sharer.STOPPED)
        self.assertIsNone(sharer.server)
        with socket.socket() as sock:
            if os.name == 'nt':
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(('127.0.0.1', port))

    def test_pre_cancelled_start_has_no_side_effects(self):
        sharer = self.new_sharer()
        cancelled = threading.Event()
        cancelled.set()
        before = storage.shared_root_snapshot()
        with mock.patch.object(runner, 'FastBindHTTPServer') as create:
            ok, _result = sharer.start(str(self.share), unused_port(), cancel_event=cancelled)
        self.assertFalse(ok)
        create.assert_not_called()
        self.assertEqual(storage.shared_root_snapshot(), before)
        self.assertTrue(sharer.wait_stopped(0))
        self.assertEqual(sharer.state, sharer.STOPPED)

    def test_failed_bind_preserves_existing_root_and_sessions(self):
        first = self.new_sharer()
        second = self.new_sharer()
        port = unused_port()
        ok, result = first.start(str(self.share), port)
        self.assertTrue(ok, result)
        storage.SESSIONS_STORE['isolated-session'] = {'create_time': time.time(), 'authenticated': True}
        before_root = storage.shared_root_snapshot()
        before_sessions = dict(storage.SESSIONS_STORE)
        other = self.share / 'other'
        other.mkdir()
        ok, _result = second.start(str(other), port)
        self.assertFalse(ok)
        self.assertTrue(second.wait_stopped(1))
        self.assertEqual(storage.shared_root_snapshot(), before_root)
        self.assertEqual(storage.SESSIONS_STORE, before_sessions)
        self.assertTrue(first.is_running)


class LocalIpcTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=WORK.name)
        self.path = Path(self.temp.name).resolve()
        self.name = 'LanShare-desktop-test-' + uuid.uuid4().hex
        self.owner = None
        self.sockets = []

    def tearDown(self):
        for sock in self.sockets:
            sock.abort()
            sock.deleteLater()
        if self.owner is not None:
            self.owner.close()
        APP.processEvents()
        cleanup_fixture(self.temp)

    def start_owner(self):
        self.owner = SingleInstance(name=self.name)
        self.assertTrue(self.owner.acquire())
        self.owner.set_handler(lambda request: {'ok': True, 'action': request['action']})
        return self.owner

    def connect(self):
        sock = QLocalSocket()
        self.sockets.append(sock)
        sock.connectToServer(self.name)
        self.assertTrue(sock.waitForConnected(1000), sock.errorString())
        APP.processEvents()
        return sock

    def raw_request(self, payload):
        sock = self.connect()
        sock.write(payload)
        sock.flush()
        self.assertTrue(pump_until(lambda: sock.bytesAvailable() > 0, 3))
        data = bytearray()
        deadline = time.monotonic() + 3
        while b'\n' not in data and time.monotonic() < deadline:
            APP.processEvents()
            data.extend(bytes(sock.readAll()))
            time.sleep(0.005)
        self.assertLessEqual(len(data), MAX_MESSAGE_BYTES)
        self.assertIn(b'\n', data)
        return json.loads(bytes(data).split(b'\n', 1)[0].decode('utf-8'))

    def test_validates_whitelisted_actions_and_absolute_directories(self):
        self.assertEqual(validate_request({'action': 'activate'}), {'action': 'activate'})
        self.assertEqual(validate_request({'action': 'status'}), {'action': 'status'})
        valid = validate_request({'action': 'open_here', 'path': str(self.path)})
        self.assertEqual(Path(valid['path']).resolve(), self.path)
        invalid = [None, [], {}, {'action': 'quit'}, {'action': 'status', 'path': str(self.path)},
                   {'action': 'open_here'}, {'action': 'open_here', 'path': '.'},
                   {'action': 'open_here', 'path': str(self.path / 'missing')},
                   {'action': 'open_here', 'path': 'x\x00y'},
                   {'action': 'open_here', 'path': 'x' * 4097}]
        file_path = self.path / 'not-a-directory.txt'
        file_path.write_text('owned', encoding='ascii')
        invalid.append({'action': 'open_here', 'path': str(file_path)})
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(ValueError):
                validate_request(request)

    @unittest.skipUnless(os.name == 'nt', 'Real Windows mutex and named pipe')
    def test_same_user_ipc_forwarding_and_crash_owner_recovery(self):
        child = '''import sys
from PyQt5.QtWidgets import QApplication
sys.path.insert(0, sys.argv[1])
from ui.single_instance import SingleInstance
app = QApplication([])
owner = SingleInstance(name=sys.argv[2])
if not owner.acquire():
    print('FAILED', flush=True)
    sys.exit(2)
owner.set_handler(lambda request: dict(request, ok=True))
print('READY', flush=True)
try:
    app.exec_()
finally:
    owner.close()
'''
        proc = subprocess.Popen([sys.executable, '-B', '-u', '-c', child, str(ROOT), self.name],
                                cwd=str(ROOT), env=dict(os.environ), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
        readiness = []
        reader = threading.Thread(target=lambda: readiness.append(proc.stdout.readline()), daemon=True)
        reader.start()
        try:
            self.assertTrue(pump_until(lambda: bool(readiness) or proc.poll() is not None, 8))
            self.assertEqual(readiness, ['READY\n'])
            secondary = SingleInstance(name=self.name)
            try:
                self.assertFalse(secondary.acquire())
                response = secondary.request({'action': 'open_here', 'path': str(self.path)})
                self.assertTrue(response['ok'])
                self.assertEqual(Path(response['path']).resolve(), self.path)
                self.assertEqual(secondary.request({'action': 'status'})['action'], 'status')
            finally:
                secondary.close()
            # Only this test's direct Python process is crashed, never a production instance.
            proc.terminate()
            proc.communicate(timeout=5)
            self.owner = SingleInstance(name=self.name)
            self.assertTrue(self.owner.acquire())
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.communicate(timeout=5)
            reader.join(1)

    def test_rejects_malformed_unknown_and_multiple_frames(self):
        self.start_owner()
        for payload in (b'not-json\n', b'{"action":"shutdown"}\n',
                        b'{"action":"status"}\n{"action":"activate"}\n',
                        b'{"action":"status","extra":1}\n'):
            with self.subTest(payload=payload):
                self.assertFalse(self.raw_request(payload)['ok'])
        self.assertTrue(self.raw_request(b'{"action":"status"}\n')['ok'])

    def test_oversize_idle_and_client_limit(self):
        self.start_owner()
        self.assertFalse(self.raw_request(b'x' * (MAX_MESSAGE_BYTES + 1))['ok'])
        idle = self.connect()
        self.assertTrue(pump_until(lambda: idle.state() == QLocalSocket.UnconnectedState, 7))
        with mock.patch.object(single_instance, 'REQUEST_TIMEOUT_MS', 2000):
            clients = [self.connect() for _index in range(MAX_LOCAL_CLIENTS)]
            self.assertTrue(pump_until(lambda: len(self.owner._clients) == MAX_LOCAL_CLIENTS, 1))
            extra = self.connect()
            self.assertTrue(pump_until(lambda: extra.state() == QLocalSocket.UnconnectedState, 1))
            self.assertLessEqual(len(self.owner._clients), MAX_LOCAL_CLIENTS)
            for client in clients:
                client.abort()
        self.assertTrue(pump_until(lambda: not self.owner._clients, 2))

    def test_handler_failure_returns_error_and_server_recovers(self):
        self.start_owner()

        def fail(_request):
            raise RuntimeError('isolated handler failure')

        self.owner.set_handler(fail)
        response = self.raw_request(b'{"action":"activate"}\n')
        self.assertFalse(response['ok'])
        self.assertEqual(response['error'], 'isolated handler failure')
        self.owner.set_handler(lambda _request: {'ok': True})
        self.assertTrue(self.raw_request(b'{"action":"status"}\n')['ok'])

    def test_test_profile_validation_and_separate_namespace(self):
        environment = {'QT_QPA_PLATFORM': 'offscreen', 'LANSHARE_DATA_DIR': str(self.path / 'chat_data')}
        self.assertIsNone(validate_test_profile(None, {}))
        self.assertEqual(validate_test_profile(str(self.path), environment), self.path)
        invalid = [('.', environment), (str(self.path), {}),
                   (str(self.path), dict(environment, QT_QPA_PLATFORM='windows')),
                   (str(self.path), dict(environment, LANSHARE_DATA_DIR=str(WORK_PATH / 'outside')))]
        for profile, env in invalid:
            with self.subTest(profile=profile, env=env), self.assertRaises(ValueError):
                validate_test_profile(profile, env)
        production = SingleInstance()
        isolated = SingleInstance(profile=self.path)
        other = SingleInstance(profile=self.path / 'other')
        try:
            self.assertNotEqual(production.name, isolated.name)
            self.assertNotEqual(isolated.name, other.name)
            identity_hash = hashlib.sha256(single_instance.current_user_identity().encode('utf-8')).hexdigest()[:24]
            self.assertIn(identity_hash, production.name)
            self.assertIn('-test-', isolated.name)
        finally:
            production.close()
            isolated.close()
            other.close()


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=WORK.name)
        self.path = Path(self.temp.name).resolve()
        self.share = self.path / 'share'
        self.share.mkdir()
        self.guis = []
        self.patches = [mock.patch.object(runner.FileSharer, 'get_all_local_ips', return_value=['127.0.0.1'])]
        for name in ('warning', 'critical', 'information'):
            self.patches.append(mock.patch.object(QMessageBox, name, return_value=QMessageBox.Ok))
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        try:
            for gui in self.guis:
                gui.sharer.stop()
                self.assertTrue(gui.sharer.wait_stopped(5))
                gui.log_timer.stop()
                for name in ('_quit_timer', '_test_exit_timer'):
                    timer = getattr(gui, name, None)
                    if timer is not None:
                        timer.stop()
                APP.processEvents()
                gui.close()
                gui.tray_icon.hide()
                gui.settings.sync()
                gui.deleteLater()
            APP.processEvents()
        finally:
            for patch in reversed(self.patches):
                patch.stop()
            cleanup_fixture(self.temp)

    def new_gui(self, **kwargs):
        if kwargs.get('settings') is None:
            kwargs['settings'] = QSettings(str(self.path / (uuid.uuid4().hex + '.ini')), QSettings.IniFormat)
        try:
            Path(kwargs['settings'].fileName()).resolve().relative_to(self.path)
        except ValueError:
            raise ValueError('GUI test settings must be inside its owned fixture')
        kwargs.setdefault('initial_path', str(self.share))
        kwargs.setdefault('initial_port', unused_port())
        kwargs['auto_start'] = False
        kwargs['system_integration_enabled'] = False
        gui = FileShareGUI(**kwargs)
        self.guis.append(gui)
        APP.processEvents()
        return gui

    def start_gui(self, gui):
        self.assertTrue(gui.start_share())
        self.assertTrue(pump_until(lambda: gui.sharer.is_running and gui.start_result is not None, 5))

    def test_transitions_disable_window_and_tray_until_listener_closed(self):
        gui = self.new_gui()
        entered, release = threading.Event(), threading.Event()
        original = runner.FastBindHTTPServer

        def delayed_factory(*args, **kwargs):
            server = original(*args, **kwargs)
            entered.set()
            release.wait(5)
            return server

        with mock.patch.object(runner, 'FastBindHTTPServer', side_effect=delayed_factory):
            try:
                self.assertTrue(gui.start_share())
                self.assertTrue(entered.wait(3))
                APP.processEvents()
                self.assertFalse(gui.toggle_share_btn.isEnabled())
                self.assertFalse(gui.tray_toggle_action.isEnabled())
                self.assertFalse(gui.handle_instance_request({'action': 'open_here', 'path': str(self.share)})['ok'])
                release.set()
                self.assertTrue(pump_until(lambda: gui.sharer.is_running and gui.start_result is not None, 5))
            finally:
                release.set()
        self.assertTrue(gui.toggle_share_btn.isEnabled())
        self.assertTrue(gui.tray_toggle_action.isEnabled())
        stopping, finish = threading.Event(), threading.Event()
        shutdown = gui.sharer.server.shutdown

        def delayed_shutdown():
            stopping.set()
            finish.wait(5)
            shutdown()

        with mock.patch.object(gui.sharer.server, 'shutdown', delayed_shutdown):
            try:
                gui.stop_share()
                self.assertTrue(stopping.wait(2))
                APP.processEvents()
                self.assertFalse(gui.toggle_share_btn.isEnabled())
                self.assertFalse(gui.tray_toggle_action.isEnabled())
                self.assertFalse(gui.sharer.wait_stopped(0))
                finish.set()
                self.assertTrue(pump_until(lambda: gui.sharer.wait_stopped(0) and gui.toggle_share_btn.isEnabled(), 5))
            finally:
                finish.set()
        self.assertEqual(gui.toggle_share_btn.text(), '\u542f\u52a8\u670d\u52a1')
        self.assertFalse(gui.browser_btn.isEnabled())

    def test_open_here_switches_running_without_restart_or_starts_stopped(self):
        gui = self.new_gui()
        self.start_gui(gui)
        server = gui.sharer.server
        other = self.path / 'other'
        other.mkdir()
        (other / 'owned.txt').write_text('isolated share', encoding='ascii')
        response = gui.handle_instance_request({'action': 'open_here', 'path': str(other)})
        self.assertTrue(response['ok'])
        self.assertEqual(response['state'], gui.sharer.RUNNING)
        self.assertIs(gui.sharer.server, server)
        self.assertEqual(Path(storage.shared_root_snapshot()[0]), other)
        with urllib.request.urlopen('http://127.0.0.1:%d/api/list' % gui.sharer.port, timeout=3) as response:
            listing = json.load(response)
        self.assertEqual(Path(listing['path']).resolve(), other)
        gui.stop_share()
        self.assertTrue(pump_until(lambda: gui.sharer.wait_stopped(0) and gui.toggle_share_btn.isEnabled(), 5))
        response = gui.handle_instance_request({'action': 'open_here', 'path': str(self.share)})
        self.assertTrue(response['ok'])
        self.assertEqual(response['state'], gui.sharer.STARTING)
        self.assertTrue(pump_until(lambda: gui.sharer.is_running and gui.start_result is not None, 5))
        self.assertIsNot(gui.sharer.server, server)
        self.assertEqual(Path(storage.shared_root_snapshot()[0]), self.share)

    def test_stale_start_completion_cannot_reenable_or_resurrect_window(self):
        gui = self.new_gui()
        old_epoch = gui._start_epoch
        gui._start_epoch += 1
        gui._starting_in_ui = True
        gui._set_transition_state(gui.sharer.STARTING)
        gui._on_start_worker_completed(old_epoch, True, {'port': gui.sharer.port})
        self.assertFalse(gui.toggle_share_btn.isEnabled())
        self.assertTrue(gui._starting_in_ui)
        self.assertIsNone(gui.start_result)
        gui._quitting = True
        gui._on_start_worker_completed(gui._start_epoch, True, {'port': gui.sharer.port})
        self.assertFalse(gui.toggle_share_btn.isEnabled())
        self.assertIsNone(gui.start_result)
        gui._quitting = False
        gui._starting_in_ui = False
        gui._apply_running_state(False)

    def test_cancel_worker_before_it_calls_start(self):
        gui = self.new_gui()
        workers = []

        class PendingThread:
            def __init__(self, target=None, **_kwargs):
                workers.append(target)

            def start(self):
                pass

        with mock.patch('ui.main_window.threading.Thread', PendingThread):
            self.assertTrue(gui.start_share())
            self.assertEqual(len(workers), 1)
            self.assertTrue(gui._starting_in_ui)
            gui.stop_share()
        self.assertTrue(gui._start_cancel.is_set())
        with mock.patch.object(runner, 'FastBindHTTPServer') as create:
            workers[0]()
        APP.processEvents()
        create.assert_not_called()
        self.assertEqual(gui.sharer.state, gui.sharer.STOPPED)
        self.assertTrue(gui.toggle_share_btn.isEnabled())
        self.assertIsNone(gui.start_result)

    def test_password_settings_remain_plaintext_in_owned_ini(self):
        settings = QSettings(str(self.path / 'password-settings.ini'), QSettings.IniFormat)
        settings.setValue('password', 'existing-access-password')
        settings.setValue('admin_password', 'existing-admin-password')
        settings.sync()
        gui = self.new_gui(settings=settings)
        self.assertEqual(gui.password_entry.text(), 'existing-access-password')
        self.assertEqual(gui.admin_password_entry.text(), 'existing-admin-password')
        gui.password_entry.setText('new-access-password')
        gui.admin_password_entry.setText('new-admin-password')
        gui.save_settings()
        gui.settings.sync()
        reloaded = QSettings(str(self.path / 'password-settings.ini'), QSettings.IniFormat)
        self.assertEqual(reloaded.value('password'), 'new-access-password')
        self.assertEqual(reloaded.value('admin_password'), 'new-admin-password')
        content = (self.path / 'password-settings.ini').read_text(encoding='utf-8')
        self.assertIn('new-access-password', content)
        self.assertIn('new-admin-password', content)

    def test_test_mode_never_queries_or_writes_system_integrations(self):
        with mock.patch('ui.main_window.subprocess.run', side_effect=AssertionError('No real process integrations')) as run, \
             mock.patch('ui.context_menu.ContextMenuIntegration.is_installed', side_effect=AssertionError('No real registry queries')) as query:
            gui = self.new_gui(settings=None)
            with self.assertRaises(ValueError):
                self.new_gui(settings=QSettings(str(WORK_PATH / 'outside-fixture.ini'), QSettings.IniFormat))
            self.assertFalse(gui.is_startup_enabled())
            self.assertFalse(gui.is_context_menu_installed())
            gui.enable_startup()
            gui.disable_startup()
            gui.add_context_menu()
            gui.remove_context_menu()
            with self.assertRaises(OSError):
                gui._run_schtasks(['/query'])
            self.assertFalse(gui.startup_btn.isEnabled())
            self.assertFalse(gui.menu_btn.isEnabled())
        run.assert_not_called()
        query.assert_not_called()
        self.assertEqual(Path(gui.settings.fileName()).parent.resolve(), self.path)

    def test_stop_file_only_for_test_profile_waits_for_actual_shutdown(self):
        gui = self.new_gui()
        self.assertIsNone(watch_test_exit(gui, None))
        self.assertFalse(hasattr(gui, '_test_exit_timer'))
        profile = validate_test_profile(str(self.path), {
            'QT_QPA_PLATFORM': 'offscreen', 'LANSHARE_DATA_DIR': str(self.path / 'chat_data')})
        self.start_gui(gui)
        entered, release = threading.Event(), threading.Event()
        shutdown = gui.sharer.server.shutdown

        def delayed_shutdown():
            entered.set()
            release.wait(5)
            shutdown()

        with mock.patch.object(gui.sharer.server, 'shutdown', delayed_shutdown), mock.patch.object(QApplication, 'quit') as quit_app:
            try:
                timer = watch_test_exit(gui, profile)
                self.assertTrue(timer.isActive())
                (profile / 'stop.request').write_text('stop', encoding='ascii')
                self.assertTrue(pump_until(lambda: gui._quitting and entered.is_set(), 3))
                self.assertFalse(timer.isActive())
                self.assertFalse(gui.sharer.wait_stopped(0))
                quit_app.assert_not_called()
                release.set()
                self.assertTrue(pump_until(lambda: quit_app.called, 5))
                self.assertTrue(gui.sharer.wait_stopped(0))
                quit_app.assert_called_once()
            finally:
                release.set()


class ProfileMainTests(unittest.TestCase):
    @unittest.skipUnless((ROOT / 'webassets.py').is_file(), 'Build web assets before CLI integration test')
    def test_main_profile_stop_file_exits_and_releases_listener_and_owner(self):
        profile = Path(tempfile.mkdtemp(prefix='main-profile-', dir=WORK.name)).resolve()
        share = profile / 'share'
        share.mkdir()
        port = unused_port()
        environment = dict(os.environ, QT_QPA_PLATFORM='offscreen',
                           LANSHARE_DATA_DIR=str(profile / 'chat_data'), PYTHONDONTWRITEBYTECODE='1')
        proc = subprocess.Popen([sys.executable, '-B', str(ROOT / 'lanshare.py'), str(share),
                                 '-p', str(port), '--startup', '--test-profile', str(profile)],
                                cwd=str(ROOT), env=environment, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
        try:
            deadline = time.monotonic() + 10
            ready = False
            while time.monotonic() < deadline and proc.poll() is None:
                try:
                    with urllib.request.urlopen('http://127.0.0.1:%d/api/list' % port, timeout=0.5) as response:
                        listing = json.load(response)
                    ready = Path(listing.get('path', '')).resolve() == share.resolve()
                    if ready:
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(0.05)
            self.assertTrue(ready, 'Owned main process did not serve its isolated share')
            (profile / 'stop.request').write_text('stop', encoding='ascii')
            stdout, stderr = proc.communicate(timeout=8)
            self.assertEqual(proc.returncode, 0, stdout + stderr)
            with socket.socket() as sock:
                sock.settimeout(0.5)
                self.assertNotEqual(sock.connect_ex(('127.0.0.1', port)), 0)
            replacement = SingleInstance(profile=profile)
            try:
                self.assertTrue(replacement.acquire())
            finally:
                replacement.close()
            self.assertTrue((profile / 'settings.ini').is_file())
        finally:
            if proc.poll() is None:
                (profile / 'stop.request').write_text('stop', encoding='ascii')
                try:
                    proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    proc.communicate(timeout=5)


if __name__ == '__main__':
    check_cleanup_target(WORK_PATH)
    try:
        unittest.main(verbosity=2)
    finally:
        APP.processEvents()
        check_cleanup_target(WORK_PATH)
        WORK.cleanup()
        if not MODULE_PATH.is_file():
            raise RuntimeError('Test source disappeared during execution; cleanup did not target tools/')
        if hashlib.sha256(MODULE_PATH.read_bytes()).hexdigest() != MODULE_HASH:
            raise RuntimeError('Test source unexpectedly changed during execution')
        print('Test source preserved: %s SHA256=%s' % (MODULE_PATH, MODULE_HASH))
