"""V7 backend regressions; all data is isolated, native file launching is mocked."""
import http.client
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
import uuid
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if not (ROOT / 'webassets.py').is_file():
    raise SystemExit('Run python tools/build_assets.py before this test.')
sys.path.insert(0, str(ROOT))
WORK = Path(tempfile.mkdtemp(dir=str(ROOT), prefix='lan6-v7-security-')).resolve()
assert WORK.parent == ROOT.resolve()
os.environ['LANSHARE_DATA_DIR'] = str(WORK / 'chat-data')
tempfile.tempdir = str(WORK)

from server.chat_store import CHAT_ROOMS, initialize_chat_store, chat_room_saver
from server.archive import ArchiveMixin
from server.config import CHAT_DATA_DIR, SECURITY_HEADERS
from server.handler import FileShareHandler
from server.fs_ops import FsOpsMixin
from server.pages import safe_json
from server.paths import (
    checked_shared_path, is_link_or_reparse, can_preview, preview_mime_type,
)
from server.runner import FastBindHTTPServer
from server.storage import (
    ZIP_DOWNLOAD_STORE, ZIP_STORE_LOCK, chat_lock, set_shared_root,
    shared_root_snapshot,
)


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_data = False
        self.current = ''
        self.data = []
        self.styles = []

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == 'script' and attributes.get('type') == 'application/json':
            self.in_data = True
            self.current = ''
        if tag == 'style':
            self.styles.append(attributes)

    def handle_data(self, data):
        if self.in_data:
            self.current += data

    def handle_endtag(self, tag):
        if tag == 'script' and self.in_data:
            self.data.append(json.loads(self.current))
            self.in_data = False


def multipart(payload, boundary='ReviewBoundary00112233', filename='multipart.bin',
              prefix=b'', suffix=b'', trailing=True):
    head = (
        '--' + boundary + '\r\n'
        + 'Content-Disposition: form-data; name="file"; filename="'
        + filename + '"\r\nContent-Type: application/octet-stream\r\n\r\n'
    ).encode()
    body = prefix + head + payload
    if trailing:
        body += b'\r\n--' + boundary.encode() + b'--\r\n' + suffix
    return body


class BackendSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initialize_chat_store()
        initial = WORK / 'initial'
        initial.mkdir()
        set_shared_root(initial)

        def handler(*args, **kwargs):
            return FileShareHandler(*args, shared_path=str(initial), **kwargs)

        cls.server = FastBindHTTPServer(('127.0.0.1', 0), handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        chat_room_saver.flush()

    def setUp(self):
        self.share = WORK / ('share-' + uuid.uuid4().hex[:8])
        self.share.mkdir()
        set_shared_root(self.share)

    def request(self, method, path, body=None, headers=None, connection=None):
        own = connection is None
        connection = connection or http.client.HTTPConnection(
            '127.0.0.1', self.port, timeout=10
        )
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        if own:
            connection.close()
        return result

    def query(self, **values):
        return urllib.parse.urlencode(values)

    def zip_task(self, name):
        return self.request('POST', '/zip', self.query(
            path=str(self.share), **{'files[]': name}
        ), {'Content-Type': 'application/x-www-form-urlencoded'})

    def make_link(self, target, link, directory=True):
        if os.name == 'nt' and directory:
            executable = shutil.which('powershell.exe') or shutil.which('pwsh.exe')
            if not executable:
                self.skipTest('PowerShell is needed for the Windows junction fixture')
            environment = dict(os.environ, LANSHARE_TEST_LINK=str(link),
                               LANSHARE_TEST_TARGET=str(target))
            subprocess.run([
                executable, '-NoProfile', '-NonInteractive', '-Command',
                "$ErrorActionPreference='Stop'; New-Item -ItemType Junction "
                '-Path $env:LANSHARE_TEST_LINK -Target $env:LANSHARE_TEST_TARGET '
                '| Out-Null',
            ], env=environment, check=True, capture_output=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.addCleanup(os.rmdir, link)
        else:
            try:
                link.symlink_to(target, target_is_directory=directory)
            except OSError as error:
                self.skipTest('Symlink fixture unavailable: ' + str(error))
            self.addCleanup(link.unlink)
        self.assertTrue(is_link_or_reparse(link))

    def test_open_never_launches_native_files(self):
        (self.share / 'inert.exe').write_bytes(b'not-an-executable')
        with patch('os.startfile', create=True) as start, patch('subprocess.Popen') as popen:
            status, _, _ = self.request('GET', '/open?' + self.query(file='inert.exe'))
            self.assertEqual(status, 410)
            start.assert_not_called()
            popen.assert_not_called()

    def test_keep_alive_security_headers_on_every_response(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        self.addCleanup(connection.close)
        for path in ('/api/list', '/api/list', '/open?file=missing'):
            _, headers, _ = self.request('GET', path, connection=connection)
            for name, value in SECURITY_HEADERS:
                self.assertEqual(headers.get(name), value)

    def test_preview_uses_stable_mime_not_windows_registry(self):
        samples = {
            'picture.PNG': b'\x89PNG\r\n\x1a\n',
            'document.pdf': b'%PDF-1.4\n%%EOF',
            'readme.txt': '\u4e2d\u6587 text'.encode('utf-8'),
            'video.mp4': b'video-bytes',
            'audio.mp3': b'audio-bytes',
        }
        with patch('mimetypes.guess_type', return_value=('application/octet-stream', None)):
            for name, content in samples.items():
                with self.subTest(name=name):
                    (self.share / name).write_bytes(content)
                    status, headers, result = self.request('GET', '/download?' + self.query(
                        file=name, preview=1
                    ))
                    self.assertEqual(status, 200)
                    self.assertEqual(result, content)
                    self.assertTrue(headers['Content-Disposition'].startswith('inline;'))
                    self.assertEqual(headers['Content-Type'], preview_mime_type(name))
                    self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                    self.assertIn("default-src 'none'", headers['Content-Security-Policy'])
                    self.assertTrue(can_preview(name))

    def test_preview_does_not_disable_media_range_requests(self):
        (self.share / 'video.mp4').write_bytes(b'0123456789')
        status, headers, result = self.request('GET', '/download?file=video.mp4&preview=1',
                                             headers={'Range': 'bytes=2-5'})
        self.assertEqual(status, 206)
        self.assertEqual(result, b'2345')
        self.assertEqual(headers['Content-Type'], 'video/mp4')
        self.assertEqual(headers['Content-Range'], 'bytes 2-5/10')
        self.assertTrue(headers['Content-Disposition'].startswith('inline;'))

    def test_active_unknown_office_and_archive_types_never_inline(self):
        for name in ('active.svg', 'active.html', 'active.js', 'book.xlsx', 'document.docx',
                     'package.zip', 'unknown.bin', 'picture.heic', 'movie.mkv'):
            with self.subTest(name=name):
                (self.share / name).write_bytes(b'inert-test-content')
                status, headers, result = self.request('GET', '/download?' + self.query(
                    file=name, preview=1
                ))
                self.assertEqual(status, 200)
                self.assertEqual(result, b'inert-test-content')
                self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))
                self.assertFalse(can_preview(name))

    def test_regular_download_still_downloads_previewable_files(self):
        (self.share / 'picture.png').write_bytes(b'picture-bytes')
        status, headers, _ = self.request('GET', '/download?file=picture.png')
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))

    def test_safe_json_round_trips_html_delimiters(self):
        value = {'name': '</script><style>*{display:none}</style>&\u2028\u2029'}
        encoded = safe_json(value)
        self.assertNotIn('<', encoded)
        self.assertNotIn('&', encoded)
        self.assertEqual(json.loads(encoded), value)

    def test_chat_pages_keep_user_names_inside_json(self):
        name = '</script><style>*{display:none}</style>'
        room_id = 'security-room'
        with chat_lock:
            CHAT_ROOMS[room_id] = {'name': name, 'messages': [], 'created': 0}
        try:
            for path in ('/chat', '/chat/room?id=' + room_id):
                status, _, page = self.request('GET', path)
                self.assertEqual(status, 200)
                parser = Scripts()
                parser.feed(page.decode('utf-8'))
                self.assertFalse(parser.styles)
                self.assertTrue(parser.data)
                self.assertIn(name, json.dumps(parser.data, ensure_ascii=False))
        finally:
            with chat_lock:
                CHAT_ROOMS.pop(room_id, None)

    def test_first_page_only_embeds_400_of_450_entries(self):
        for index in range(450):
            (self.share / ('file-%03d.txt' % index)).write_text('x')
        status, _, page = self.request('GET', '/')
        self.assertEqual(status, 200)
        parser = Scripts()
        parser.feed(page.decode('utf-8'))
        data = parser.data[0]
        self.assertEqual(len(data['entries']), 400)
        self.assertEqual(data['total'], 450)
        self.assertTrue(data['hasMore'])
        self.assertEqual(data['offset'], 0)
        self.assertEqual(Path(data['root']), self.share.resolve())
        self.assertEqual((data['sort'], data['order']), ('name', 'asc'))

    def test_multipart_boundary_across_chunk_end(self):
        payload = b'X' * 65524
        body = multipart(payload)
        status, _, _ = self.request('POST', '/upload', body, {
            'Content-Type': 'multipart/form-data; boundary=ReviewBoundary00112233'
        })
        self.assertEqual(status, 200)
        self.assertEqual((self.share / 'multipart.bin').read_bytes(), payload)

    def test_multipart_preserves_false_delimiter_and_unicode_name(self):
        name = '\u4e2d\u6587.txt'
        payload = (b'\r\n--ReviewBoundary00112233NOT-A-DELIMITER\x00tail'
                   b'\r\n--ReviewBoundary00112233--NOT-A-FINAL-DELIMITER')
        body = multipart(payload, filename=name)
        status, _, _ = self.request('POST', '/upload', body, {
            'Content-Type': 'multipart/form-data; boundary="ReviewBoundary00112233"'
        })
        self.assertEqual(status, 200)
        self.assertEqual((self.share / name).read_bytes(), payload)

    def test_multipart_fields_epilogue_and_keep_alive(self):
        boundary = 'ReviewBoundary00112233'
        prefix = (
            '--' + boundary + '\r\nContent-Disposition: form-data; '
            'name="note"\r\n\r\nhello\r\n'
        ).encode()
        body = multipart(b'', prefix=prefix, suffix=b'epilogue')
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        self.addCleanup(connection.close)
        status, _, _ = self.request('POST', '/upload', body, {
            'Content-Type': 'multipart/form-data; boundary=' + boundary
        }, connection=connection)
        self.assertEqual(status, 200)
        self.assertEqual((self.share / 'multipart.bin').stat().st_size, 0)
        self.assertEqual(self.request('GET', '/api/list', connection=connection)[0], 200)

    def test_multipart_missing_closing_boundary_has_no_residue(self):
        body = multipart(b'X' * 70000, trailing=False)
        status, _, _ = self.request('POST', '/upload', body, {
            'Content-Type': 'multipart/form-data; boundary=ReviewBoundary00112233'
        })
        self.assertEqual(status, 400)
        self.assertFalse((self.share / 'multipart.bin').exists())

    def test_multipart_shorter_than_content_length_has_no_residue(self):
        body = multipart(b'X' * 70000, trailing=False)
        client = socket.create_connection(('127.0.0.1', self.port), timeout=10)
        self.addCleanup(client.close)
        head = (
            'POST /upload HTTP/1.1\r\nHost: 127.0.0.1\r\n'
            'Content-Type: multipart/form-data; boundary=ReviewBoundary00112233\r\n'
            'Content-Length: %d\r\nConnection: close\r\n\r\n' % (len(body) + 100)
        ).encode()
        client.sendall(head + body)
        client.shutdown(socket.SHUT_WR)
        response = bytearray()
        while True:
            data = client.recv(65536)
            if not data:
                break
            response.extend(data)
        self.assertTrue(bytes(response).startswith(b'HTTP/1.1 400'))
        self.assertFalse((self.share / 'multipart.bin').exists())

    def test_multipart_rejects_multiple_files_without_residue(self):
        boundary = b'ReviewBoundary00112233'
        first = multipart(b'first', filename='first.bin', trailing=False)
        body = first + b'\r\n' + multipart(b'second', filename='second.bin')
        status, _, _ = self.request('POST', '/upload', body, {
            'Content-Type': 'multipart/form-data; boundary=' + boundary.decode()
        })
        self.assertEqual(status, 400)
        self.assertFalse((self.share / 'first.bin').exists())
        self.assertFalse((self.share / 'second.bin').exists())

    def test_multipart_rejects_outside_destination(self):
        outside = WORK / ('outside-' + uuid.uuid4().hex[:8])
        outside.mkdir()
        status, _, _ = self.request('POST', '/upload?' + self.query(path=str(outside)),
                                    multipart(b'no'), {
            'Content-Type': 'multipart/form-data; boundary=ReviewBoundary00112233'
        })
        self.assertEqual(status, 403)
        self.assertFalse((outside / 'multipart.bin').exists())

    def test_multipart_does_not_overwrite_existing_file(self):
        (self.share / 'multipart.bin').write_bytes(b'original')
        status, _, _ = self.request('POST', '/upload', multipart(b'new'), {
            'Content-Type': 'multipart/form-data; boundary=ReviewBoundary00112233'
        })
        self.assertEqual(status, 200)
        self.assertEqual((self.share / 'multipart.bin').read_bytes(), b'original')
        self.assertEqual((self.share / 'multipart_1.bin').read_bytes(), b'new')

    def test_zip_links_are_revoked_on_root_switch(self):
        (self.share / 'private.txt').write_text('old-share-content')
        status, _, data = self.zip_task('private.txt')
        self.assertEqual(status, 200)
        url = json.loads(data)['download_url']
        status, _, package = self.request('GET', url)
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(package)) as package:
            self.assertEqual(package.read('private.txt'), b'old-share-content')
        with ZIP_STORE_LOCK:
            cached = Path(next(iter(ZIP_DOWNLOAD_STORE.values()))['path'])
        new = WORK / ('new-' + uuid.uuid4().hex[:8])
        new.mkdir()
        old_generation = shared_root_snapshot()[1]
        self.assertGreater(set_shared_root(new), old_generation)
        self.assertFalse(cached.exists())
        self.assertIn(self.request('GET', url)[0], (404, 410))
        self.assertIn(self.request('GET', url + '&stream=1')[0], (404, 410))

    def test_zip_undownloaded_task_is_also_revoked(self):
        (self.share / 'private.txt').write_text('old-share-content')
        _, _, data = self.zip_task('private.txt')
        url = json.loads(data)['download_url']
        new = WORK / ('new-' + uuid.uuid4().hex[:8])
        new.mkdir()
        set_shared_root(new)
        self.assertIn(self.request('GET', url)[0], (404, 410))

    def test_zip_registration_during_root_switch_does_not_publish_old_task(self):
        (self.share / 'a.txt').write_text('a')
        new = WORK / ('new-' + uuid.uuid4().hex[:8])
        new.mkdir()
        original = ArchiveMixin._collect_zip_entries

        def collect_then_switch(handler, *arguments, **keywords):
            result = original(handler, *arguments, **keywords)
            set_shared_root(new)
            return result

        with patch.object(ArchiveMixin, '_collect_zip_entries', collect_then_switch):
            self.assertEqual(self.zip_task('a.txt')[0], 409)
        with ZIP_STORE_LOCK:
            self.assertFalse(ZIP_DOWNLOAD_STORE)

    def test_root_switch_during_zip_build_removes_cache(self):
        (self.share / 'a.txt').write_text('a')
        _, _, data = self.zip_task('a.txt')
        url = json.loads(data)['download_url']
        new = WORK / ('new-' + uuid.uuid4().hex[:8])
        new.mkdir()
        original = ArchiveMixin._build_zip_file
        caches = []

        def build_then_switch(handler, *arguments, **keywords):
            path = original(handler, *arguments, **keywords)
            caches.append(path)
            set_shared_root(new)
            return path

        with patch.object(ArchiveMixin, '_build_zip_file', build_then_switch):
            self.assertEqual(self.request('GET', url)[0], 410)
        self.assertTrue(caches)
        self.assertTrue(all(not path.exists() for path in caches))

    def test_parallel_request_for_same_zip_does_not_build_two_caches(self):
        (self.share / 'a.txt').write_text('a')
        _, _, data = self.zip_task('a.txt')
        url = json.loads(data)['download_url']
        original = ArchiveMixin._build_zip_file
        entered, resume = threading.Event(), threading.Event()
        first = []

        def delayed_build(handler, *arguments, **keywords):
            entered.set()
            if not resume.wait(5):
                raise TimeoutError('Concurrent test did not resume')
            return original(handler, *arguments, **keywords)

        def request_first():
            try:
                first.append(self.request('GET', url)[0])
            except Exception as error:
                first.append(error)

        with patch.object(ArchiveMixin, '_build_zip_file', delayed_build):
            thread = threading.Thread(target=request_first)
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.request('GET', url)[0], 503)
            finally:
                resume.set()
                thread.join(timeout=10)
        self.assertEqual(first, [200])

    def test_shared_generation_change_during_active_zip_stops_output(self):
        (self.share / 'a.bin').write_bytes(os.urandom(700000))
        _, _, data = self.zip_task('a.bin')
        zip_id = urllib.parse.parse_qs(urllib.parse.urlsplit(
            json.loads(data)['download_url']).query)['id'][0]
        with ZIP_STORE_LOCK:
            info = ZIP_DOWNLOAD_STORE[zip_id]
        new = WORK / ('new-' + uuid.uuid4().hex[:8])
        new.mkdir()

        class Output(io.BytesIO):
            def write(output, content):
                result = super().write(content)
                set_shared_root(new)
                return result

        class Probe(ArchiveMixin):
            shared_path = str(self.share)
            wfile = Output()

            def q(probe, name, default=''):
                return zip_id if name == 'id' else default

            def send_response(probe, status):
                probe.status = status

            def send_header(probe, key, value):
                probe.declared = int(value) if key == 'Content-Length' else getattr(
                    probe, 'declared', None)

            def end_headers(probe):
                pass

            headers = {}

        probe = Probe()
        probe.api_zip_download()
        self.assertEqual(probe.status, 200)
        self.assertTrue(probe.close_connection)
        self.assertLess(len(probe.wfile.getvalue()), probe.declared)
        self.assertFalse(Path(info['path']).exists())

    def test_setting_same_root_preserves_generation_and_zip(self):
        (self.share / 'a.txt').write_text('a')
        _, _, data = self.zip_task('a.txt')
        generation = shared_root_snapshot()[1]
        self.assertEqual(set_shared_root(self.share), generation)
        self.assertEqual(self.request('GET', json.loads(data)['download_url'])[0], 200)

    def test_nested_external_junction_is_rejected_by_copy_move_and_zip(self):
        private = WORK / ('private-' + uuid.uuid4().hex[:8])
        private.mkdir()
        (private / 'secret.txt').write_text('review-marker-not-real-secret')
        source = self.share / 'source'
        source.mkdir()
        self.make_link(private, source / 'nested-link')
        with self.assertRaises(PermissionError):
            checked_shared_path(source / 'nested-link' / 'secret.txt', self.share)
        for operation in ('copy', 'move'):
            status, _, result = self.request('POST', '/' + operation, self.query(
                path=str(self.share), dst='destination-' + operation, name='source'
            ), {'Content-Type': 'application/x-www-form-urlencoded'})
            data = json.loads(result)
            self.assertEqual(status, 200)
            self.assertFalse(data['moved'])
            self.assertTrue(data['skipped'])
            self.assertFalse((self.share / ('destination-' + operation) / 'source').exists())
            self.assertTrue((private / 'secret.txt').is_file())
        self.assertEqual(self.zip_task('source')[0], 403)

    def test_internal_junction_is_not_followed_either(self):
        target = self.share / 'target'
        target.mkdir()
        (target / 'a.txt').write_text('a')
        link = self.share / 'link'
        self.make_link(target, link)
        self.assertEqual(self.zip_task('link')[0], 403)

    def test_link_destination_is_rejected_before_copy(self):
        (self.share / 'a.txt').write_text('a')
        outside = WORK / ('outside-' + uuid.uuid4().hex[:8])
        outside.mkdir()
        self.make_link(outside, self.share / 'linked-target')
        status, _, _ = self.request('POST', '/copy', self.query(
            path=str(self.share), dst='linked-target', name='a.txt'
        ), {'Content-Type': 'application/x-www-form-urlencoded'})
        self.assertEqual(status, 403)
        self.assertFalse((outside / 'a.txt').exists())

    def test_failed_copy_removes_only_its_created_files(self):
        source = self.share / 'source'
        source.mkdir()
        (source / 'a.txt').write_text('a')
        (self.share / 'keep.txt').write_text('keep')
        original = FsOpsMixin._copy_shared_entry

        def copy_then_fail(handler, src, dst, root, created):
            original(handler, src, dst, root, created)
            if Path(src) == source:
                raise PermissionError('Simulated source change')

        with patch.object(FsOpsMixin, '_copy_shared_entry', copy_then_fail):
            status, _, result = self.request('POST', '/copy', self.query(
                path=str(self.share), dst='destination', name='source'
            ), {'Content-Type': 'application/x-www-form-urlencoded'})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(result)['moved'])
        self.assertFalse((self.share / 'destination' / 'source').exists())
        self.assertEqual((self.share / 'keep.txt').read_text(), 'keep')
        self.assertEqual((source / 'a.txt').read_text(), 'a')

    def test_zip_rechecks_path_after_task_registration(self):
        source = self.share / 'source'
        source.mkdir()
        (source / 'a.txt').write_text('safe-original')
        _, _, result = self.zip_task('source')
        url = json.loads(result)['download_url']
        source.rename(self.share / 'original-source')
        private = WORK / ('private-' + uuid.uuid4().hex[:8])
        private.mkdir()
        (private / 'a.txt').write_text('review-marker-not-real-secret')
        self.make_link(private, source)
        self.assertEqual(self.request('GET', url)[0], 403)


if __name__ == '__main__':
    try:
        unittest.main(verbosity=2)
    finally:
        # The root is generated under this checkout; links are removed by cleanups.
        assert WORK.parent == ROOT.resolve()
        shutil.rmtree(WORK)
