"""Isolated HTTP regression tests for v7 chat, SSE and binary media."""
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from collections import namedtuple
from http.client import HTTPConnection
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
TEMP = tempfile.TemporaryDirectory(prefix='lanshare-v7-chat-')
os.environ['LANSHARE_DATA_DIR'] = str(Path(TEMP.name) / 'data')
os.environ['LANSHARE_CHAT_MIN_FREE_MB'] = '0'
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from server import chat_store, chat_api, chat_media  # noqa: E402
from server.handler import FileShareHandler  # noqa: E402
from server.runner import FastBindHTTPServer  # noqa: E402
from server.storage import SHARE_STATE, SESSIONS_STORE, chat_lock  # noqa: E402


class ChatTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        chat_store.initialize_chat_store()
        cls.share = Path(TEMP.name) / 'share'
        cls.share.mkdir()
        SHARE_STATE['path'] = str(cls.share)
        handler = lambda *a, **kw: FileShareHandler(*a, shared_path=str(cls.share), **kw)
        cls.server = FastBindHTTPServer(('127.0.0.1', 0), handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.headers = {'Content-Type': 'application/json', 'X-Chat-Client': 'a' * 32}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        chat_store.chat_room_saver.flush()

    def request(self, method, path, data=None, headers=None):
        body = json.dumps(data).encode('utf-8') if isinstance(data, (dict, list)) else data
        conn = HTTPConnection('127.0.0.1', self.port, timeout=10)
        merged = {**self.headers, 'Connection': 'close', **(headers or {})}
        conn.request(method, path, body=body, headers=merged)
        response = conn.getresponse()
        payload = response.read()
        result = (response.status, dict(response.getheaders()), payload)
        conn.close()
        return result

    def json_request(self, method, path, data=None, headers=None):
        status, _, payload = self.request(method, path, data, headers)
        try:
            value = json.loads(payload)
        except ValueError:
            value = {'raw': payload.decode('utf-8', 'replace')}
        return status, value

    def room(self):
        status, data = self.json_request('POST', '/chat/create', {'name': 'v7-test'})
        self.assertEqual(status, 200)
        return data['room_id']

    def snapshot(self, room):
        return self.json_request('GET', '/chat/history?sync=1&room=' + room)[1]

    def stream(self, room, query='', headers=None):
        sock = socket.create_connection(('127.0.0.1', self.port), timeout=8)
        sock.settimeout(8)
        head = ('GET /chat/stream?room=%s%s HTTP/1.1\r\nHost: 127.0.0.1:%s\r\nConnection: close\r\n' %
                (room, query, self.port))
        for key, value in (headers or {}).items():
            head += '%s: %s\r\n' % (key, value)
        sock.sendall((head + '\r\n').encode())
        stream = sock.makefile('rb')
        self.assertIn(b'200', stream.readline())
        while stream.readline() != b'\r\n':
            pass
        return sock, stream

    @staticmethod
    def frame(stream):
        fields = {}
        while True:
            line = stream.readline()
            if not line:
                return fields
            if line == b'\n':
                if 'data' in fields:
                    return fields
                fields = {}
                continue
            key, sep, value = line.decode().rstrip('\n').partition(': ')
            if sep:
                fields[key] = json.loads(value) if key == 'data' else value

    def test_01_import_has_no_data_side_effects(self):
        target = Path(TEMP.name) / 'import-only'
        env = {**os.environ, 'LANSHARE_DATA_DIR': str(target), 'PYTHONPATH': str(ROOT)}
        proc = subprocess.run([sys.executable, '-B', '-c', 'import server.chat_store,server.chat_api'],
                              env=env, cwd=ROOT, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(target.exists())
        alias = chat_api.CHAT_ROOMS
        chat_store.load_chat_rooms()
        self.assertIs(alias, chat_store.CHAT_ROOMS)

    def test_02_empty_room_live_first_message(self):
        room = self.room()
        sock, stream = self.stream(room)
        try:
            status, data = self.json_request('POST', '/chat/send', {'room': room, 'text': 'first-from-other-client'})
            self.assertEqual(status, 200)
            event = self.frame(stream)
            self.assertEqual(event['data']['id'], data['msg']['id'])
        finally:
            stream.close(); sock.close()

    def test_03_mutations_replay_and_last_event_id(self):
        room = self.room()
        baseline = self.snapshot(room)['revision']
        sock, stream = self.stream(room, '&sync=1&since=' + urllib.parse.quote(baseline))
        try:
            _, sent = self.json_request('POST', '/chat/send', {'room': room, 'text': 'live'})
            first = self.frame(stream)
            self.assertEqual(first['data']['id'], sent['msg']['id'])
            self.json_request('POST', '/chat/delete', {'room': room, 'id': sent['msg']['id']})
            deleted = self.frame(stream)
            self.assertEqual(deleted['event'], 'delete')
            self.json_request('POST', '/chat/rename_room', {'id': room, 'name': 'renamed'})
            renamed = self.frame(stream)
            self.assertEqual(renamed['event'], 'room_updated')
            self.assertEqual(renamed['data']['name'], 'renamed')
            self.json_request('POST', '/chat/clear', {'room': room})
            cleared = self.frame(stream)
            self.assertEqual(cleared['event'], 'clear')
        finally:
            stream.close(); sock.close()
        sock, stream = self.stream(room, '&sync=1&since=' + urllib.parse.quote(baseline),
                                   {'Last-Event-ID': deleted['id']})
        try:
            replay = self.frame(stream)
            self.assertEqual(replay['event'], 'room_updated')
        finally:
            stream.close(); sock.close()
        events = self.json_request('GET', '/chat/history?sync=1&room=' + room + '&since=' +
                                   urllib.parse.quote(first['id']))[1]['events']
        self.assertEqual([event['type'] for event in events], ['delete', 'room_updated', 'clear'])

    def test_04_invalid_cursor_reset(self):
        room = self.room()
        self.json_request('POST', '/chat/send', {'room': room, 'text': 'retained'})
        legacy = self.json_request('GET', '/chat/history?room=' + room + '&after=missing')[1]
        self.assertEqual(len(legacy), 1)
        sock, stream = self.stream(room, '&sync=1&since=old-epoch%3A5')
        try:
            event = self.frame(stream)
            self.assertEqual(event['event'], 'reset')
            self.assertEqual(event['data']['messages'][0]['text'], 'retained')
        finally:
            stream.close(); sock.close()
        state = chat_api._room_sync[room]
        with chat_lock:
            for i in range(1030):
                chat_api._record_locked(room, 'room_updated', {'name': str(i)})
        events = self.json_request('GET', '/chat/history?sync=1&room=' + room + '&since=' +
                                   state['epoch'] + '%3A0')[1]['events']
        self.assertEqual(events[0]['type'], 'reset')

    def test_05_default_rename_persistence_and_auth(self):
        self.json_request('POST', '/chat/rename_room', {'id': 'default', 'name': '测试默认群'})
        chat_store.load_chat_rooms()
        chat_store.migrate_old_chat()
        self.assertEqual(chat_store.CHAT_ROOMS['default']['name'], '测试默认群')
        status, data = self.json_request('POST', '/chat/delete_room', {'id': 'default'})
        self.assertEqual(status, 200); self.assertFalse(data['success'])
        original = FileShareHandler.check_admin_auth
        FileShareHandler.check_admin_auth = lambda handler: False
        try:
            self.assertEqual(self.json_request('POST', '/chat/rename_room', {'id': 'default', 'name': 'no'})[0], 401)
        finally:
            FileShareHandler.check_admin_auth = original

    def test_06_text_whitespace_and_idempotent_retry(self):
        room = self.room()
        text = '  first\n\tsecond\n    third  \n'
        payload = {'room': room, 'text': text, 'client_id': 'retry-test'}
        _, first = self.json_request('POST', '/chat/send', payload)
        _, retried = self.json_request('POST', '/chat/send', payload)
        self.assertEqual(first['msg']['text'], text)
        self.assertEqual(first['msg']['id'], retried['msg']['id'])
        self.assertEqual(len(self.snapshot(room)['messages']), 1)
        self.assertEqual(self.json_request('POST', '/chat/send', {'room': room, 'text': ['bad']})[0], 400)

    def upload(self, room, data, name='image.bmp', headers=None):
        status, start = self.json_request('POST', '/chat/media/start',
                                          {'room': room, 'name': name, 'size': len(data)}, headers)
        self.assertEqual(status, 200, start)
        token = start['upload']
        for offset in range(0, len(data), start['chunk_bytes']):
            block = data[offset:offset + start['chunk_bytes']]
            status, chunk = self.json_request('POST', '/chat/media/chunk?upload=' + token + '&offset=' + str(offset),
                                               block, {'Content-Type': 'application/octet-stream', **(headers or {})})
            self.assertEqual(status, 200, chunk)
        return token

    def test_07_large_binary_original_preview_and_cancel(self):
        room = self.room()
        data_io = io.BytesIO()
        Image.new('RGB', (1500, 1500), 'green').save(data_io, 'BMP')
        data = data_io.getvalue()
        self.assertGreater(len(data), 5 * 1024 ** 2)
        token = self.upload(room, data)
        status, completed = self.json_request('POST', '/chat/media/complete', {'upload': token})
        self.assertEqual(status, 200, completed)
        status, repeated = self.json_request('POST', '/chat/media/complete', {'upload': token})
        self.assertEqual(repeated['image'], completed['image'])
        _, sent = self.json_request('POST', '/chat/send', {'room': room, 'text': '', 'media': token})
        msg = sent['msg']
        self.assertTrue(msg['preview'].endswith('_p.jpg'))
        self.assertEqual(self.request('GET', msg['image'])[2], data)
        self.assertIn('attachment', self.request('GET', msg['image'])[1]['Content-Disposition'])
        self.assertEqual(self.request('GET', msg['preview'])[0], 200)
        self.json_request('POST', '/chat/delete', {'room': room, 'id': msg['id']})
        for key in ('image', 'preview', 'thumb'):
            self.assertEqual(self.request('GET', msg[key])[0], 404)
        tiny = io.BytesIO(); Image.new('RGB', (10, 10)).save(tiny, 'PNG')
        cancel = self.upload(room, tiny.getvalue(), 'tiny.png')
        _, ready = self.json_request('POST', '/chat/media/complete', {'upload': cancel})
        self.assertEqual(self.json_request('POST', '/chat/media/cancel', {'upload': cancel})[0], 200)
        self.assertEqual(self.request('GET', ready['image'])[0], 404)

    def test_08_format_support_validation_and_session_binding(self):
        room = self.room()
        for fmt in ('PNG', 'JPEG', 'GIF', 'WEBP', 'BMP', 'TIFF', 'ICO'):
            buf = io.BytesIO(); Image.new('RGB', (32, 32), 'red').save(buf, fmt)
            token = self.upload(room, buf.getvalue(), 'safe.' + fmt.lower())
            status, data = self.json_request('POST', '/chat/media/complete', {'upload': token})
            self.assertEqual(status, 200, (fmt, data))
            self.json_request('POST', '/chat/media/cancel', {'upload': token})
        self.assertEqual(self.json_request('POST', '/chat/media/start', {'room': room, 'name': 'evil.svg', 'size': 100})[0], 415)
        token = self.upload(room, b'not-an-image', 'bad.png')
        self.assertEqual(self.json_request('POST', '/chat/media/complete', {'upload': token})[0], 415)
        self.json_request('POST', '/chat/media/cancel', {'upload': token})
        buf = io.BytesIO(); Image.new('RGB', (10, 10)).save(buf, 'PNG')
        token = self.upload(room, buf.getvalue(), 'tiny.png')
        self.assertEqual(self.json_request('POST', '/chat/media/complete', {'upload': token},
                                           {'X-Chat-Client': 'b' * 32})[0], 404)
        SESSIONS_STORE['different'] = {'create_time': time.time(), 'is_admin': False}
        self.assertEqual(self.json_request('POST', '/chat/media/complete', {'upload': token},
                                           {'Cookie': 'auth_token=different'})[0], 404)
        self.json_request('POST', '/chat/media/cancel', {'upload': token})

    def test_09_chunk_offset_and_resource_guards(self):
        room = self.room()
        _, session = self.json_request('POST', '/chat/media/start', {'room': room, 'name': 'x.png', 'size': 100})
        token = session['upload']
        self.assertEqual(self.json_request('POST', '/chat/media/chunk?upload=' + token + '&offset=1', b'x',
                                           {'Content-Type': 'application/octet-stream'})[0], 409)
        self.assertEqual(self.json_request('POST', '/chat/media/complete', {'upload': token})[0], 409)
        self.json_request('POST', '/chat/media/cancel', {'upload': token})
        old = chat_media.CHAT_IMAGE_MAX_BYTES
        chat_media.CHAT_IMAGE_MAX_BYTES = 50
        try:
            self.assertEqual(self.json_request('POST', '/chat/media/start', {'room': room, 'name': 'x.png', 'size': 100})[0], 413)
        finally:
            chat_media.CHAT_IMAGE_MAX_BYTES = old

    def test_10_twenty_mebibyte_original(self):
        room = self.room()
        buffer = io.BytesIO()
        Image.new('RGB', (2700, 2700), (35, 80, 115)).save(buffer, 'BMP')
        original = buffer.getvalue()
        self.assertGreaterEqual(len(original), 20 * 1024 ** 2)
        token = self.upload(room, original, 'original-20mib.bmp')
        status, ready = self.json_request('POST', '/chat/media/complete', {'upload': token})
        self.assertEqual(status, 200, ready)
        self.assertEqual(ready['size'], len(original))
        self.assertEqual(self.request('GET', ready['image'])[2], original)
        self.assertLess(len(self.request('GET', ready['thumb'])[2]), 100 * 1024)
        self.json_request('POST', '/chat/media/cancel', {'upload': token})

    def test_11_prepared_images_survive_cleanup_then_cancel_removes_all(self):
        room = self.room()
        buffer = io.BytesIO(); Image.new('RGBA', (32, 32), (15, 80, 200, 20)).save(buffer, 'PNG')
        token = self.upload(room, buffer.getvalue(), 'alpha.png')
        _, ready = self.json_request('POST', '/chat/media/complete', {'upload': token})
        files = list(chat_store.CHAT_IMAGE_DIR.glob(token + '*'))
        self.assertEqual(len(files), 3)
        for file in files:
            os.utime(file, (time.time() - 7200, time.time() - 7200))
        chat_store.cleanup_chat_images(grace_seconds=0)
        self.assertTrue(all(file.exists() for file in files))
        self.json_request('POST', '/chat/media/cancel', {'upload': token})
        self.assertFalse(any(file.exists() for file in files))
        self.assertEqual(self.json_request('POST', '/chat/send', {'room': room, 'media': token})[0], 404)

    def test_12_message_ownership_and_retry_lifecycle(self):
        room, other_room = self.room(), self.room()
        buffer = io.BytesIO(); Image.new('RGB', (16, 16), 'blue').save(buffer, 'PNG')
        token = self.upload(room, buffer.getvalue(), 'life.png')
        _, ready = self.json_request('POST', '/chat/media/complete', {'upload': token})
        self.assertEqual(self.json_request('POST', '/chat/send', {'room': other_room, 'media': token})[0], 409)
        payload = {'room': room, 'media': token, 'client_id': 'image-retry'}
        _, first = self.json_request('POST', '/chat/send', payload)
        _, retry = self.json_request('POST', '/chat/send', payload)
        self.assertEqual(first['msg']['id'], retry['msg']['id'])
        self.assertEqual(self.json_request('POST', '/chat/media/cancel', {'upload': token})[0], 404)
        self.assertEqual(self.request('GET', ready['image'])[0], 200)
        chat_store.cleanup_chat_images(grace_seconds=0)
        self.assertEqual(self.request('GET', ready['image'])[0], 200)
        self.json_request('POST', '/chat/clear', {'room': room})
        self.assertEqual(self.request('GET', ready['image'])[0], 404)

    def test_13_expired_partial_upload_and_bad_path_tokens(self):
        room = self.room()
        _, started = self.json_request('POST', '/chat/media/start', {'room': room, 'name': '..\\..\\escape.png', 'size': 100})
        token = started['upload']
        part = chat_media._uploads[token]['path']
        self.assertEqual(part.parent, chat_media.CHAT_UPLOAD_DIR)
        owner = ('', 'a' * 32)
        with self.assertRaises(chat_media.MediaError):
            chat_media.append_chunk(token, owner, 0, io.BytesIO(b'abc'), 10)
        self.assertEqual(part.stat().st_size, 0)
        self.assertEqual(chat_media._uploads[token]['offset'], 0)
        chat_media._uploads[token]['touched'] = time.time() - chat_media.CHAT_MEDIA_EXPIRE_SECONDS - 1
        self.assertEqual(self.json_request('POST', '/chat/media/complete', {'upload': token})[0], 410)
        chat_media.cleanup_uploads()
        self.assertFalse(part.exists())
        self.assertNotIn(token, chat_media._uploads)
        self.assertEqual(self.json_request('POST', '/chat/media/complete', {'upload': '../rooms.json'})[0], 404)

    def test_14_decode_pixels_and_slots_temp_space_and_disk_guards(self):
        room = self.room()
        buffer = io.BytesIO(); Image.new('RGB', (32, 32)).save(buffer, 'PNG')
        token = self.upload(room, buffer.getvalue(), 'pixel.png')
        old_pixels = chat_media.CHAT_MEDIA_MAX_PIXELS
        chat_media.CHAT_MEDIA_MAX_PIXELS = 10
        try:
            self.assertEqual(self.json_request('POST', '/chat/media/complete', {'upload': token})[0], 413)
        finally:
            chat_media.CHAT_MEDIA_MAX_PIXELS = old_pixels
        self.assertFalse(list(chat_store.CHAT_IMAGE_DIR.glob(token + '*')))
        chat_media._decode_slots.acquire(); chat_media._decode_slots.acquire()
        try:
            self.assertEqual(self.json_request('POST', '/chat/media/complete', {'upload': token})[0], 503)
        finally:
            chat_media._decode_slots.release(); chat_media._decode_slots.release()
        self.assertEqual(self.json_request('POST', '/chat/media/complete', {'upload': token})[0], 200)
        self.json_request('POST', '/chat/media/cancel', {'upload': token})
        old_temp = chat_media.CHAT_MEDIA_TEMP_BYTES
        chat_media.CHAT_MEDIA_TEMP_BYTES = 50
        try:
            self.assertEqual(self.json_request('POST', '/chat/media/start', {'room': room, 'name': 'x.png', 'size': 100})[0], 507)
        finally:
            chat_media.CHAT_MEDIA_TEMP_BYTES = old_temp
        disk_usage = chat_media.shutil.disk_usage
        usage = namedtuple('usage', 'total used free')
        chat_media.shutil.disk_usage = lambda path: usage(100, 75, 25)
        try:
            self.assertEqual(self.json_request('POST', '/chat/media/start', {'room': room, 'name': 'x.png', 'size': 100})[0], 507)
        finally:
            chat_media.shutil.disk_usage = disk_usage

    def optional_codec_round_trip(self, fmt, extension):
        Image.init()
        if fmt not in Image.OPEN or fmt not in Image.SAVE:
            self.skipTest(fmt + ' encoder/decoder is not installed in this interpreter')
        room = self.room()
        buffer = io.BytesIO()
        Image.new('RGB', (64, 40), (25, 95, 180)).save(buffer, fmt, quality=90)
        original = buffer.getvalue()
        self.assertGreater(len(original), 100)
        token = self.upload(room, original, 'optional.' + extension)
        status, ready = self.json_request('POST', '/chat/media/complete', {'upload': token})
        self.assertEqual(status, 200, (fmt, ready))
        self.assertIn(fmt, self.json_request('GET', '/chat/media/config')[1]['formats'])
        self.assertTrue(ready['image'].endswith('.' + extension), ready)
        self.assertTrue(ready['preview'].endswith('_p.jpg'), ready)
        status, headers, downloaded = self.request('GET', ready['image'])
        self.assertEqual(status, 200)
        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertEqual(downloaded, original)
        for kind in ('thumb', 'preview'):
            status, headers, data = self.request('GET', ready[kind])
            self.assertEqual(status, 200)
            self.assertEqual(headers['Content-Type'], 'image/jpeg')
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                self.assertEqual(image.format, 'JPEG')
                self.assertEqual(image.size, (64, 40))
        _, sent = self.json_request('POST', '/chat/send', {'room': room, 'media': token})
        self.assertEqual(sent['msg']['image'], ready['image'])
        self.json_request('POST', '/chat/clear', {'room': room})
        for kind in ('image', 'thumb', 'preview'):
            self.assertEqual(self.request('GET', ready[kind])[0], 404)

    def test_15_optional_heif_original_and_safe_preview(self):
        self.optional_codec_round_trip('HEIF', 'heic')

    def test_16_optional_avif_original_and_safe_preview(self):
        self.optional_codec_round_trip('AVIF', 'avif')


if __name__ == '__main__':
    try:
        unittest.main(verbosity=2)
    finally:
        chat_store.chat_room_saver.flush()
        TEMP.cleanup()
