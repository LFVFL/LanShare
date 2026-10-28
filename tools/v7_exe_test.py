"""Verify a copied onefile EXE with default extraction in an owned Windows temp."""
import argparse
import hashlib
import io
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path

from test_support import isolate_test_environment
from windows_job import OwnedProcessJob


TEST_ENV = isolate_test_environment()
ROOT = Path(__file__).resolve().parent.parent
from PIL import Image  # noqa: E402
from PyInstaller.archive.readers import CArchiveReader  # noqa: E402
import pillow_heif  # noqa: E402
import pillow_avif  # noqa: E402,F401

pillow_heif.register_heif_opener()
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')


def unused_port():
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if os.name == 'nt':
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class ExeTests(unittest.TestCase):
    exe = None
    process = None
    job = None
    portable = None
    evidence = {}

    @classmethod
    def setUpClass(cls):
        cls.evidence['launches'] = 0
        cls.workspace = TEST_ENV.temporary_dir(prefix='portable-exe-')
        cls.portable = cls.workspace / 'portable'
        cls.portable.mkdir()
        cls.copied_exe = cls.portable / 'lanshare.exe'
        shutil.copy2(cls.exe, cls.copied_exe)
        cls.profile = cls.workspace / 'profile'
        cls.profile.mkdir()
        cls.local_app_data = cls.workspace / 'local-\u7528\u6237 \u914d\u7f6e'
        cls.local_app_data.mkdir()
        cls.legacy_runtime_base = cls.local_app_data / 'LanShare' / 'Temp'
        cls.runtime_base = cls.workspace / 'temp-\u4e34\u65f6 \u7a7a\u95f4'
        cls.runtime_base.mkdir()
        if (cls.local_app_data / 'LanShare').exists() or list(cls.runtime_base.iterdir()):
            raise RuntimeError('First-run LOCALAPPDATA and temporary-directory fixtures are not clean')
        cls.runtime_dir = None
        cls.share = cls.workspace / 'share'
        cls.share.mkdir()
        cls.text = b'  LanShare portable preview\n\tindented line\n'
        (cls.share / 'preview.txt').write_bytes(cls.text)
        (cls.share / 'active.html').write_text('<h1>attachment only</h1>', encoding='ascii')
        image = io.BytesIO()
        Image.new('RGBA', (24, 24), (30, 80, 160, 64)).save(image, 'PNG')
        cls.png = image.getvalue()
        (cls.share / 'preview.png').write_bytes(cls.png)
        cls.port = unused_port()
        environment = dict(os.environ)
        for key in tuple(environment):
            name = key.upper()
            if (name in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'CONDA_PREFIX',
                         'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QTDIR',
                         'LOCALAPPDATA', 'TEMP', 'TMP') or
                    name.startswith(('_PYI_', 'PYINSTALLER_'))):
                environment.pop(key)
        windows = Path(os.environ.get('SystemRoot', r'C:\Windows'))
        environment.update(
            QT_QPA_PLATFORM='offscreen',
            LANSHARE_DATA_DIR=str(cls.profile / 'chat_data'),
            LANSHARE_CHAT_IMAGE_MAX_MB='0',
            LANSHARE_CHAT_MIN_FREE_MB='0',
            LOCALAPPDATA=str(cls.local_app_data),
            PYINSTALLER_RESET_ENVIRONMENT='1',
            PATH=os.pathsep.join((str(windows / 'System32'), str(windows))),
            TEMP=str(cls.runtime_base), TMP=str(cls.runtime_base),
        )
        cls.evidence.update(local_app_data=str(cls.local_app_data),
                            local_app_data_exists_before_launch=cls.local_app_data.is_dir(),
                            lanshare_parent_exists_before_launch=(cls.local_app_data / 'LanShare').exists(),
                            legacy_runtime_base_exists_before_launch=cls.legacy_runtime_base.exists(),
                            runtime_base=str(cls.runtime_base),
                            runtime_base_empty_before_launch=not list(cls.runtime_base.iterdir()),
                            temp=environment['TEMP'], tmp=environment['TMP'],
                            bootloader_environment_reset=True)
        cls.job = OwnedProcessJob()
        try:
            cls.process = subprocess.Popen(
                [str(cls.copied_exe), str(cls.share), '-p', str(cls.port),
                 '--startup', '--test-profile', str(cls.profile)],
                cwd=cls.portable, env=environment,
            )
            cls.evidence['launches'] = 1
            cls.job.assign(cls.process)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if cls.process.poll() is not None:
                    raise RuntimeError('Owned EXE exited before startup: %s' % cls.process.returncode)
                try:
                    status, _, body = cls.request('GET', '/api/list?limit=1')
                    if status == 200:
                        listing = json.loads(body)
                        if Path(listing.get('path', '')).resolve() != cls.share.resolve():
                            raise RuntimeError('Port does not belong to the owned test share')
                        if Path(listing.get('root', '')).resolve() != cls.share.resolve():
                            raise RuntimeError('Unexpected shared root; aborting before write requests')
                        break
                except (OSError, ValueError):
                    time.sleep(0.1)
            else:
                raise RuntimeError('Owned EXE did not start its HTTP listener')
            directories = sorted(cls.runtime_base.glob('_MEI*'))
            if len(directories) != 1:
                raise RuntimeError('Expected one extraction directory in the owned Unicode TEMP/TMP')
            cls.runtime_dir = directories[0]
            if (not cls.runtime_dir.is_dir() or cls.runtime_dir.is_symlink() or
                    cls.runtime_dir.resolve().parent != cls.runtime_base.resolve()):
                raise RuntimeError('Extraction directory is outside its owned runtime base')
            if (cls.local_app_data / 'LanShare').exists():
                raise RuntimeError('Default extraction unexpectedly created the legacy LOCALAPPDATA directory')
            python_dlls = sorted(path for path in cls.runtime_dir.glob('python*.dll')
                                 if re.fullmatch(r'python\d{2,3}\.dll', path.name, re.IGNORECASE))
            if not python_dlls:
                raise RuntimeError('Extraction directory does not contain a Python runtime DLL')
            payload_files = python_dlls + [cls.runtime_dir / 'PyQt5/QtCore.pyd',
                                          cls.runtime_dir / 'PyQt5/Qt5/bin/Qt5Core.dll']
            if any(not path.is_file() or not path.stat().st_size for path in payload_files):
                raise RuntimeError('Expected extracted Python/Qt payload files are missing')
            cls.evidence.update(
                exe=str(cls.exe), copied_exe=str(cls.copied_exe),
                size_bytes=cls.exe.stat().st_size,
                sha256=hashlib.sha256(cls.exe.read_bytes()).hexdigest(),
                environment='offscreen; owned Unicode TEMP/TMP and LOCALAPPDATA/profile; reset bootloader environment; Windows-only PATH; no source beside EXE',
                local_app_data=str(cls.local_app_data), runtime_base=str(cls.runtime_base),
                runtime_extraction_directory=str(cls.runtime_dir),
                runtime_payload_files=[{'file': path.relative_to(cls.runtime_dir).as_posix(),
                                        'bytes': path.stat().st_size} for path in payload_files],
            )
        except Exception:
            cls.close_process()
            raise

    @classmethod
    def close_process(cls, require_running=False):
        try:
            if cls.process is not None:
                existing_code = cls.process.poll()
                cls.evidence['exit_code_before_shutdown'] = existing_code
                if existing_code is not None:
                    if require_running:
                        raise RuntimeError('Owned EXE exited before controlled shutdown: %s' % existing_code)
                    return
                (cls.profile / 'stop.request').write_text('stop\n', encoding='ascii')
                try:
                    code = cls.process.wait(timeout=20)
                    cls.evidence['graceful_exit_code'] = code
                    if code:
                        raise RuntimeError('Owned EXE returned %d during normal shutdown' % code)
                except subprocess.TimeoutExpired as exc:
                    TEST_ENV.stop_process(cls.process)
                    raise RuntimeError('Owned EXE did not exit gracefully') from exc
        finally:
            if cls.job is not None:
                cls.job.close()

    @classmethod
    def tearDownClass(cls):
        cls.close_process(require_running=True)
        deadline = time.monotonic() + 5
        while cls.runtime_dir.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        cls.evidence['runtime_directory_removed_before_fixture_cleanup'] = not cls.runtime_dir.exists()
        cls.evidence['runtime_base_remaining_entries'] = (
            sorted(path.name for path in cls.runtime_base.iterdir()) if cls.runtime_base.exists() else [])
        if (not cls.evidence['runtime_directory_removed_before_fixture_cleanup'] or
                cls.evidence['runtime_base_remaining_entries']):
            raise AssertionError('Owned bootloader extraction files remain before fixture cleanup')
        cls.evidence['legacy_runtime_base_exists_after_exit'] = cls.legacy_runtime_base.exists()
        if (cls.local_app_data / 'LanShare').exists():
            raise AssertionError('Default extraction changed the legacy LOCALAPPDATA directory state')
        try:
            cls.request('GET', '/api/list?limit=1')
        except OSError:
            pass
        else:
            raise AssertionError('Owned EXE listener is still open after shutdown')
        cls.evidence['listener_closed'] = True
        cls.evidence['portable_directory_files'] = sorted(p.name for p in cls.portable.iterdir())
        if cls.evidence['portable_directory_files'] != ['lanshare.exe']:
            raise AssertionError('EXE required or wrote unexpected files beside itself')
        cls.evidence['settings_ini_isolated'] = (cls.profile / 'settings.ini').is_file()
        if not cls.evidence['settings_ini_isolated']:
            raise AssertionError('Dedicated INI settings were not written')

    @classmethod
    def request(cls, method, path, data=None, headers=None):
        body = json.dumps(data).encode('utf-8') if isinstance(data, dict) else data
        merged = {'Content-Type': 'application/json', 'X-Chat-Client': 'e' * 32,
                  'Connection': 'close', **(headers or {})}
        connection = HTTPConnection('127.0.0.1', cls.port, timeout=12)
        try:
            connection.request(method, path, body=body, headers=merged)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def json_request(self, method, path, data=None):
        status, _, payload = self.request(method, path, data)
        self.assertEqual(status, 200, payload[:1000])
        value = json.loads(payload)
        self.assertTrue(value.get('success', True), value)
        return value

    def room(self):
        return self.json_request('POST', '/chat/create', {'name': 'Portable synthetic room'})['room_id']

    def test_01_sole_exe_and_current_embedded_assets(self):
        self.assertEqual(sorted(p.name for p in self.portable.iterdir()), ['lanshare.exe'])
        self.assertEqual(self.copied_exe.read_bytes(), self.exe.read_bytes())
        _, _, page = self.request('GET', '/')
        self.assertIn(b'data-files-search-scope', page)
        version = re.search(rb'/static/app.css\?v=([0-9a-f]+)', page)
        self.assertIsNotNone(version)
        self.evidence['asset_version'] = version.group(1).decode('ascii')
        assets = runpy.run_path(str(ROOT / 'webassets.py'))
        for path, key in (('/static/app-files.js', 'JS_FILES'),
                          ('/static/app-chat.js', 'JS_CHAT'), ('/static/app.css', 'CSS')):
            status, _, payload = self.request('GET', path)
            self.assertEqual(status, 200, path)
            self.assertEqual(payload, assets[key].encode('utf-8'), path)
        self.assertEqual(self.request('GET', '/favicon.ico')[0], 200)

    def test_02_preview_is_inline_and_download_is_attachment(self):
        for name, expected, kind in (('preview.txt', self.text, 'text/plain'),
                                     ('preview.png', self.png, 'image/png')):
            status, headers, body = self.request('GET', '/download?file=' + name + '&preview=1')
            self.assertEqual(status, 200)
            self.assertEqual(body, expected)
            self.assertTrue(headers['Content-Disposition'].startswith('inline;'))
            self.assertTrue(headers['Content-Type'].startswith(kind))
            self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
            _, download_headers, downloaded = self.request('GET', '/download?file=' + name)
            self.assertTrue(download_headers['Content-Disposition'].startswith('attachment;'))
            self.assertEqual(downloaded, expected)
        status, headers, _ = self.request('GET', '/download?file=active.html&preview=1')
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(self.request('GET', '/open?file=active.html')[0], 410)

    def test_03_range_preview_is_preserved(self):
        status, headers, body = self.request('GET', '/download?file=preview.txt&preview=1',
                                             headers={'Range': 'bytes=2-9'})
        self.assertEqual(status, 206)
        self.assertEqual(body, self.text[2:10])
        self.assertTrue(headers['Content-Disposition'].startswith('inline;'))
        self.assertEqual(headers['Content-Range'], 'bytes 2-9/%d' % len(self.text))

    def test_04_decoder_capabilities_and_unlimited_product_size(self):
        config = self.json_request('GET', '/chat/media/config')
        self.assertTrue(config['available'])
        self.assertEqual(config['max_bytes'], 0)
        for format_name in ('PNG', 'JPEG', 'GIF', 'WEBP', 'BMP', 'TIFF', 'ICO', 'HEIF', 'AVIF'):
            self.assertIn(format_name, config['formats'])
        self.evidence['formats'] = config['formats']

    def image_roundtrip(self, format_name, size=(40, 40)):
        room = self.room()
        output = io.BytesIO()
        Image.new('RGB', size, (30, 80, 160)).save(output, format_name)
        original = output.getvalue()
        start = self.json_request('POST', '/chat/media/start',
                                  {'room': room, 'name': 'synthetic.' + format_name.lower(),
                                   'size': len(original)})
        token = start['upload']
        for offset in range(0, len(original), start['chunk_bytes']):
            block = original[offset:offset + start['chunk_bytes']]
            status, _, response = self.request('POST', '/chat/media/chunk?upload=' + token +
                                               '&offset=' + str(offset), block,
                                               {'Content-Type': 'application/octet-stream'})
            self.assertEqual(status, 200, response[:1000])
            self.assertEqual(json.loads(response)['offset'], offset + len(block))
        media = self.json_request('POST', '/chat/media/complete', {'upload': token})
        self.assertEqual(media['size'], len(original))
        sent = self.json_request('POST', '/chat/send', {'room': room, 'text': '', 'media': token})['msg']
        status, headers, downloaded = self.request('GET', sent['image'] + '?download=1')
        self.assertEqual(status, 200)
        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertEqual(downloaded, original)
        for key in ('thumb', 'preview'):
            status, _, preview = self.request('GET', sent[key])
            self.assertEqual(status, 200)
            with Image.open(io.BytesIO(preview)) as decoded:
                decoded.load()
        self.json_request('POST', '/chat/clear', {'room': room})
        for key in ('image', 'thumb', 'preview'):
            self.assertEqual(self.request('GET', sent[key])[0], 404)
        return {'format': format_name, 'original_bytes': len(original),
                'sha256': hashlib.sha256(original).hexdigest()}

    def test_05_seven_common_formats_roundtrip(self):
        self.evidence['common_format_roundtrips'] = []
        for format_name in ('PNG', 'JPEG', 'GIF', 'WEBP', 'BMP', 'TIFF', 'ICO'):
            with self.subTest(format=format_name):
                self.evidence['common_format_roundtrips'].append(self.image_roundtrip(format_name))

    def test_06_heic_and_avif_roundtrip_in_packaged_exe(self):
        self.evidence['optional_format_roundtrips'] = []
        for format_name in ('HEIF', 'AVIF'):
            with self.subTest(format=format_name):
                self.evidence['optional_format_roundtrips'].append(self.image_roundtrip(format_name))

    def test_07_twenty_mebibyte_original(self):
        result = self.image_roundtrip('BMP', (2700, 2700))
        self.assertGreaterEqual(result['original_bytes'], 20 * 1024 ** 2)
        self.evidence['large_image_roundtrip'] = result

    def test_08_default_rename_text_whitespace_and_retry(self):
        text = '  portable\n\tindented\n    trailing  \n'
        payload = {'room': 'default', 'text': text, 'client_id': 'portable-whitespace'}
        first = self.json_request('POST', '/chat/send', payload)['msg']
        retried = self.json_request('POST', '/chat/send', payload)['msg']
        self.assertEqual(first['id'], retried['id'])
        self.assertEqual(first['text'], text)
        self.json_request('POST', '/chat/rename_room', {'id': 'default', 'name': 'Portable renamed default'})
        snapshot = self.json_request('GET', '/chat/history?sync=1&room=default')
        self.assertEqual(snapshot['room']['name'], 'Portable renamed default')
        self.assertEqual(len(snapshot['messages']), 1)
        self.assertEqual(snapshot['messages'][0]['text'], text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', required=True, type=Path)
    parser.add_argument('--results', required=True, type=Path,
                        help='New JSON evidence file; existing files are never overwritten')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('This regression requires Windows')
    ExeTests.exe = args.exe.resolve(strict=True)
    if not ExeTests.exe.is_file() or ExeTests.exe.suffix.lower() != '.exe':
        parser.error('--exe must be a standalone Windows EXE')
    try:
        reader = CArchiveReader(str(ExeTests.exe))
        options = getattr(reader, 'options', None)
        if options is None:
            options = [name for name, entry in reader.toc.items() if entry[-1] == 'OPTION']
        runtime_options = [option for option in options if option.startswith('pyi-runtime-tmpdir')]
        if runtime_options:
            raise ValueError('Custom runtime temp options are forbidden; expected the Windows default')
    except Exception as exc:
        parser.error('Refusing to launch an unverified runtime-temp EXE: %s' % exc)
    ExeTests.evidence['runtime_directory_policy'] = 'pyinstaller-default-windows-temp'
    ExeTests.evidence['runtime_tmpdir_archive_options'] = runtime_options
    try:
        args.results.parent.mkdir(parents=True, exist_ok=True)
        output = args.results.open('x', encoding='utf-8')
    except OSError as exc:
        parser.error('Cannot reserve new --results file; no EXE was launched: %s' % exc)
    with output:
        started = time.monotonic()
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(ExeTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        evidence = dict(ExeTests.evidence, success=result.wasSuccessful(), tests=result.testsRun,
                        failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
                        planned_launches=1,
                        seconds=round(time.monotonic() - started, 2),
                        timestamp=time.strftime('%Y-%m-%dT%H:%M:%S%z'))
        if not result.wasSuccessful():
            TEST_ENV.keep = True
            evidence['retained_owned_test_workspace'] = str(TEST_ENV.root)
        output.write(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    print('EXE evidence: %s' % args.results)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
