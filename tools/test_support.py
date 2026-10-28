"""Owned test data/profile directories, configured before importing the app."""
import atexit
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


_environment = None


class TestEnvironment:
    def __init__(self):
        if any(name == 'server.config' or name.startswith('server.chat_') for name in sys.modules):
            raise RuntimeError('Test isolation must be enabled before importing server modules')
        self.root = Path(tempfile.mkdtemp(prefix='lanshare-test-')).resolve()
        self._token = uuid.uuid4().hex
        self._marker = self.root / '.lanshare-test-owner'
        self._marker.write_text(self._token, encoding='ascii')
        self.data = self.root / 'chat_data'
        self.data.mkdir()
        self.keep = False
        os.environ['LANSHARE_DATA_DIR'] = str(self.data)
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        os.environ['LANSHARE_TEST_PROFILE'] = str(self.root)
        atexit.register(self.cleanup)

    def temporary_dir(self, prefix='fixture-'):
        return Path(tempfile.mkdtemp(prefix=prefix, dir=str(self.root))).resolve()

    def _check_target(self, target, allow_root=False):
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError('Test root no longer exists or is a link')
        if self._marker.read_text(encoding='ascii') != self._token:
            raise ValueError('Test directory ownership marker does not match')
        raw = Path(target).absolute()
        resolved = raw.resolve()
        if raw.is_symlink() or resolved == Path(resolved.anchor):
            raise ValueError('Refusing to recursively delete a link or a volume root')
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError('Refusing to delete outside the owned test directory') from exc
        if resolved == self.root and not allow_root:
            raise ValueError('Only cleanup() can remove the test root')
        return resolved

    def remove_test_dir(self, target):
        target = self._check_target(target)
        if target.exists():
            shutil.rmtree(target)

    def gui(self, **kwargs):
        from PyQt5.QtCore import QSettings
        from ui.context_menu import ContextMenuIntegration
        from ui.main_window import FileShareGUI

        if kwargs.get('settings') is None:
            kwargs['settings'] = QSettings(str(self.root / 'settings.ini'), QSettings.IniFormat)
        else:
            settings_path = Path(kwargs['settings'].fileName()).resolve()
            try:
                settings_path.relative_to(self.root)
            except ValueError as exc:
                raise ValueError('GUI test settings must be inside the owned test profile') from exc
        kwargs['context_menu'] = ContextMenuIntegration(enabled=False)
        kwargs['system_integration_enabled'] = False
        return FileShareGUI(**kwargs)

    def wait_server(self, process, port, share, timeout=25):
        """Do not mistake a live user's service on a fixed port for our child."""
        ready = queue.Queue()

        def read_startup():
            while True:
                line = process.stdout.readline()
                if not line:
                    ready.put(False)
                    return
                if line.strip() == b'READY':
                    ready.put(True)
                    return
                if line.startswith(b'FAILED'):
                    ready.put(False)
                    return

        reader = threading.Thread(target=read_startup, daemon=True)
        reader.start()
        try:
            started = ready.get(timeout=timeout)
        except queue.Empty:
            return False
        if not started or process.poll() is not None:
            return False
        expected = Path(share).resolve()
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d/api/list?limit=1' % port,
                                        timeout=5) as response:
                listing = json.load(response)
            return (process.poll() is None and Path(listing.get('path', '')).resolve() == expected
                    and Path(listing.get('root', listing.get('path', ''))).resolve() == expected)
        except urllib.error.HTTPError as exc:
            # Protected tests cannot read /api/list before login. The owned child's
            # READY is emitted only after its exclusive bind has actually succeeded.
            return exc.code in (401, 403) and process.poll() is None
        except (OSError, ValueError):
            return False

    def wait_browser(self, process, profile, timeout=25):
        profile = Path(profile).resolve()
        try:
            profile.relative_to(self.root)
        except ValueError as exc:
            raise ValueError('Browser profile must be inside the owned test directory') from exc
        active = profile / 'DevToolsActivePort'
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('Owned Chrome exited before creating its debug endpoint')
            try:
                lines = active.read_text(encoding='ascii').splitlines()
                port = int(lines[0])
                if 0 < port < 65536 and lines[1].startswith('/devtools/browser/'):
                    return port
            except (OSError, ValueError, IndexError):
                pass
            time.sleep(0.05)
        raise RuntimeError('Owned Chrome did not create DevToolsActivePort')

    @staticmethod
    def stop_process(process):
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def cleanup(self):
        if self.keep:
            print('Owned test data retained at %s' % self.root, file=sys.stderr)
            return
        if not self.root.exists():
            return
        try:
            target = self._check_target(self.root, allow_root=True)
            shutil.rmtree(target)
        except (OSError, ValueError) as exc:
            print('Test cleanup skipped; owned data retained at %s: %s' % (self.root, exc),
                  file=sys.stderr)


def isolate_test_environment():
    global _environment
    if _environment is None:
        _environment = TestEnvironment()
    return _environment
