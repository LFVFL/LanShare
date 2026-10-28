"""Isolated file-list/search HTTP regressions on an owned loopback server."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone
from functools import cmp_to_key
from html.parser import HTMLParser
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from test_support import isolate_test_environment

TEST_ENV = isolate_test_environment()
from server import listing
from server.chat_store import initialize_chat_store, chat_room_saver
from server.handler import FileShareHandler
from server.runner import FastBindHTTPServer
from server.storage import set_shared_root

RESULTS = ROOT / 'docs' / 'changes' / '2026-10-27' / 'test-results'


class EmbeddedJSON(HTMLParser):
    def __init__(self):
        super().__init__()
        self.collecting = False
        self.text = ''
        self.files = None

    def handle_starttag(self, tag, attributes):
        if tag == 'script' and 'data-fs-json' in dict(attributes):
            self.collecting = True
            self.text = ''

    def handle_data(self, value):
        if self.collecting:
            self.text += value

    def handle_endtag(self, tag):
        if tag == 'script' and self.collecting:
            self.files = json.loads(self.text)
            self.collecting = False


class FilesHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initialize_chat_store()
        cls.share = TEST_ENV.temporary_dir('files-share-')
        cls.folders = ['Dir-00', 'Dir-10', '\u4e2d\u6587\u76ee\u5f55']
        for index, name in enumerate(cls.folders):
            (cls.share / name).mkdir()
            os.utime(cls.share / name, (1700000000 + index, 1700000000 + index))
        for index in range(1103):
            suffix = '-needle' if index >= 950 else ''
            item = cls.share / ('file-%04d%s.txt' % (index, suffix))
            item.write_bytes(b'x' * ((index * 37) % 127 + 1))
            os.utime(item, (1700000100 + index % 101, 1700000100 + index % 101))
        (cls.share / 'z-picture.png').write_bytes(b'\x89PNG\r\n\x1a\n')
        (cls.share / 'z-package.zip').write_bytes(b'PK\x03\x04')
        cls.deep = cls.share / cls.folders[-1] / 'nested'
        cls.deep.mkdir()
        (cls.deep / '\u6df1\u5ea6\u5173\u952e\u5b57.txt').write_text('synthetic', encoding='utf-8')
        for name in ('.git', '.hidden', 'node_modules', '__pycache__'):
            directory = cls.deep / name
            directory.mkdir()
            (directory / '\u7981\u6b62\u7d22\u5f15.txt').write_text('skip', encoding='utf-8')
        set_shared_root(cls.share)

        class Handler(FileShareHandler):
            def log_message(self, format, *args):
                pass

        cls.server = FastBindHTTPServer(('127.0.0.1', 0),
            lambda *args, **kwargs: Handler(*args, shared_path=str(cls.share), **kwargs))
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        chat_room_saver.flush()

    def request(self, route, **query):
        connection = HTTPConnection('127.0.0.1', self.port, timeout=20)
        try:
            route += ('?' + urllib.parse.urlencode(query)) if query else ''
            connection.request('GET', route, headers={'Connection': 'close'})
            response = connection.getresponse()
            payload = response.read()
            return response.status, payload
        finally:
            connection.close()

    def data(self, route='/api/list', **query):
        status, payload = self.request(route, **query)
        self.assertEqual(status, 200, payload[:200])
        return json.loads(payload)

    def expected(self, sort='name', order='asc', query=''):
        values = []
        for item in self.share.iterdir():
            if query and query.casefold() not in item.name.casefold():
                continue
            stat = item.stat()
            values.append({
                'name': item.name, 'folder': item.is_dir(),
                'size': 0 if item.is_dir() else stat.st_size,
                'mtime': int(stat.st_mtime * 1000),
            })

        def compare(left, right):
            if left['folder'] != right['folder']:
                return -1 if left['folder'] else 1
            left_key = ((left['name'].casefold(), left['name']) if sort == 'name'
                        else left[sort])
            right_key = ((right['name'].casefold(), right['name']) if sort == 'name'
                         else right[sort])
            primary = (left_key > right_key) - (left_key < right_key)
            if primary:
                return primary if order == 'asc' else -primary
            return ((left['name'].casefold(), left['name'])
                    > (right['name'].casefold(), right['name'])) - (
                    (left['name'].casefold(), left['name'])
                    < (right['name'].casefold(), right['name']))

        return [value['name'] for value in sorted(values, key=cmp_to_key(compare))]

    def test_initial_page_embeds_400_with_complete_contract(self):
        status, payload = self.request('/')
        self.assertEqual(status, 200)
        parser = EmbeddedJSON()
        parser.feed(payload.decode('utf-8'))
        data = parser.files
        self.assertIsNotNone(data)
        self.assertEqual(len(data['entries']), 400)
        self.assertEqual(data['total'], 1108)
        self.assertTrue(data['hasMore'])
        self.assertEqual(data['offset'], 0)
        self.assertEqual(Path(data['root']), self.share)
        self.assertEqual(Path(data['path']), self.share)
        self.assertEqual((data['sort'], data['order'], data['query']), ('name', 'asc', ''))
        self.assertEqual([entry['name'] for entry in data['entries']], self.expected()[:400])

    def test_all_global_sort_pages_have_no_gaps_or_duplicates(self):
        for sort in ('name', 'size', 'mtime'):
            for order in ('asc', 'desc'):
                with self.subTest(sort=sort, order=order):
                    collected = []
                    for offset in (0, 400, 800):
                        data = self.data(limit=400, offset=offset, sort=sort, order=order)
                        self.assertEqual(data['total'], 1108)
                        self.assertEqual(data['offset'], offset)
                        self.assertEqual((data['sort'], data['order']), (sort, order))
                        self.assertEqual(data['hasMore'], offset < 800)
                        collected.extend(entry['name'] for entry in data['entries'])
                    self.assertEqual(collected, self.expected(sort, order))
                    self.assertEqual(len(set(collected)), 1108)
                    self.assertEqual(set(collected[:3]), set(self.folders))

    def test_current_filter_finds_files_beyond_unfiltered_first_page(self):
        first = self.data(limit=400)
        self.assertFalse(any('needle' in entry['name'] for entry in first['entries']))
        data = self.data(limit=400, q='needle')
        self.assertEqual(data['total'], 153)
        self.assertEqual([entry['name'] for entry in data['entries']], self.expected(query='needle'))
        self.assertEqual(data['query'], 'needle')
        self.assertFalse(data['hasMore'])

    def test_url_query_first_page_and_followup_pages_use_same_filter(self):
        status, payload = self.request('/', q='file-', sort='size', order='desc')
        self.assertEqual(status, 200)
        parser = EmbeddedJSON()
        parser.feed(payload.decode('utf-8'))
        initial = parser.files
        collected = [entry['name'] for entry in initial['entries']]
        self.assertEqual((initial['query'], initial['sort'], initial['order']),
                         ('file-', 'size', 'desc'))
        for offset in (400, 800):
            data = self.data(path=initial['path'], q=initial['query'],
                             sort=initial['sort'], order=initial['order'],
                             offset=offset, limit=400)
            collected.extend(entry['name'] for entry in data['entries'])
            self.assertEqual(data['query'], initial['query'])
        self.assertEqual(collected, self.expected('size', 'desc', 'file-'))
        self.assertEqual(len(set(collected)), 1103)

    def test_preview_metadata_agrees_with_file_types(self):
        data = self.data(q='z-')
        values = {entry['name']: entry['previewable'] for entry in data['entries']}
        self.assertTrue(values['z-picture.png'])
        self.assertFalse(values['z-package.zip'])
        folders = self.data(q='Dir-')['entries']
        self.assertTrue(all(not entry['previewable'] for entry in folders))

    def test_scopes_find_chinese_subfolders_and_skip_private_dependencies(self):
        keyword = '\u6df1\u5ea6\u5173\u952e\u5b57'
        current = self.data('/api/search', q=keyword, scope='current', path=str(self.share))
        self.assertEqual(current['count'], 0)
        subtree = self.data('/api/search', q=keyword, scope='subtree', path=str(self.share))
        self.assertEqual(subtree['count'], 1)
        hit = subtree['entries'][0]
        self.assertEqual(hit['name'], keyword + '.txt')
        self.assertEqual(Path(hit['path']), self.deep)
        self.assertEqual(hit['dir'], self.folders[-1] + '/nested')
        outside_current = self.share / self.folders[0]
        whole = self.data('/api/search', q=keyword, scope='share', path=str(outside_current))
        self.assertEqual(whole['count'], 1)
        self.assertEqual(Path(whole['root']), self.share)
        skipped = self.data('/api/search', q='\u7981\u6b62\u7d22\u5f15', scope='share')
        self.assertEqual(skipped['count'], 0)

    def test_invalid_scope_outside_root_and_old_root_do_not_leak(self):
        private = TEST_ENV.temporary_dir('outside-share-')
        (private / 'private-only.txt').write_text('synthetic marker')
        self.assertEqual(self.request('/api/search', q='private', scope='invalid')[0], 400)
        self.assertEqual(self.request('/api/search', q='private', path=str(private))[0], 403)
        fallback = self.data(path=str(private), limit=10)
        self.assertEqual(Path(fallback['root']), self.share)
        self.assertFalse(any(entry['name'] == 'private-only.txt' for entry in fallback['entries']))

    def test_result_scan_and_time_truncation_are_explicit(self):
        data = self.data('/api/search', q='needle', scope='share')
        self.assertEqual(data['count'], 80)
        self.assertTrue(data['truncated'])
        self.assertEqual(data['truncatedReason'], 'result_limit')
        with patch.object(listing.ListingMixin, 'SEARCH_MAX_SCAN', 12):
            data = self.data('/api/search', q='no-such-keyword', scope='share')
            self.assertEqual(data['scanned'], 12)
            self.assertTrue(data['truncated'])
            self.assertEqual(data['truncatedReason'], 'scan_limit')
        counter = iter([0, 100])
        clock = SimpleNamespace(monotonic=lambda: next(counter, 100),
                                strftime=time.strftime, localtime=time.localtime)
        with patch.object(listing, 'time', clock):
            data = self.data('/api/search', q='no-such-keyword', scope='share')
            self.assertTrue(data['truncated'])
            self.assertEqual(data['scanned'], 0)
            self.assertEqual(data['truncatedReason'], 'time_limit')

    def test_junction_is_hidden_and_never_descended(self):
        private = TEST_ENV.temporary_dir('junction-target-')
        (private / 'junction-private-marker.txt').write_text('synthetic')
        link = self.share / 'linked-private-directory'
        if os.name == 'nt':
            executable = shutil.which('powershell.exe') or shutil.which('pwsh.exe')
            if not executable:
                self.skipTest('No PowerShell for the junction fixture')
            environment = dict(os.environ, LANSHARE_TEST_LINK=str(link),
                               LANSHARE_TEST_TARGET=str(private))
            subprocess.run([executable, '-NoProfile', '-NonInteractive', '-Command',
                "$ErrorActionPreference='Stop'; New-Item -ItemType Junction "
                '-Path $env:LANSHARE_TEST_LINK -Target $env:LANSHARE_TEST_TARGET | Out-Null'],
                env=environment, check=True, capture_output=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.addCleanup(os.rmdir, link)
        else:
            try:
                link.symlink_to(private, target_is_directory=True)
            except OSError as error:
                self.skipTest(str(error))
            self.addCleanup(link.unlink)
        data = self.data(q='linked-private')
        self.assertEqual(data['entries'], [])
        data = self.data('/api/search', q='junction-private', scope='share')
        self.assertEqual(data['count'], 0)


class RecordedResults(unittest.TextTestResult):
    def __init__(self, *arguments, **keywords):
        super().__init__(*arguments, **keywords)
        self.cases = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.cases.append({'name': test.id(), 'status': 'passed'})

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.cases.append({'name': test.id(), 'status': 'failed'})

    def addError(self, test, error):
        super().addError(test, error)
        self.cases.append({'name': test.id(), 'status': 'error'})

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.cases.append({'name': test.id(), 'status': 'skipped', 'reason': reason})


if __name__ == '__main__':
    RESULTS.mkdir(parents=True, exist_ok=True)
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        result = unittest.TextTestRunner(stream=output, verbosity=2,
            resultclass=RecordedResults).run(unittest.defaultTestLoader.loadTestsFromTestCase(FilesHTTPTests))
    log = output.getvalue().replace(str(TEST_ENV.root), '<test-root>').replace(str(ROOT), '<checkout>')
    (RESULTS / 'files-http.log').write_text(log, encoding='utf-8')
    report = {
        'timestamp': datetime.now(timezone(timedelta(hours=8))).isoformat(),
        'runtime': sys.version.split()[0],
        'environment': 'virtualenv' if sys.prefix != sys.base_prefix else 'system',
        'tests': result.testsRun, 'success': result.wasSuccessful(),
        'failures': len(result.failures), 'errors': len(result.errors),
        'skipped': len(result.skipped), 'cases': result.cases,
    }
    (RESULTS / 'files-http.json').write_text(json.dumps(report, ensure_ascii=True, indent=2) + '\n',
                                          encoding='utf-8')
    print(log)
    raise SystemExit(0 if result.wasSuccessful() else 1)
