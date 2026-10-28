"""Owned headless Chrome integration, never a user's browser/profile/service."""
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
import time
import unittest
import urllib.parse
import urllib.request
from http.client import HTTPConnection
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from test_support import isolate_test_environment

TEST_ENV = isolate_test_environment()
os.environ['LANSHARE_CHAT_MIN_FREE_MB'] = '0'
from PIL import Image
from server.chat_store import initialize_chat_store, chat_room_saver
from server.handler import FileShareHandler
from server.runner import FastBindHTTPServer
from server.storage import SHARE_STATE
from ui_test import CDP, CHROME

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

RESULTS = ROOT / 'docs' / 'changes' / '2026-10-27' / 'test-results'


def minimal_pdf():
    stream = b'BT /F1 12 Tf 20 100 Td (LanShare synthetic PDF preview) Tj ET'
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
        b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    document = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for index, content in enumerate(objects, 1):
        offsets.append(len(document))
        document.extend(str(index).encode() + b' 0 obj\n' + content + b'\nendobj\n')
    start = len(document)
    document.extend(b'xref\n0 6\n0000000000 65535 f \n')
    for offset in offsets[1:]:
        document.extend(('%010d 00000 n \n' % offset).encode())
    document.extend(('trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % start).encode())
    return bytes(document)


class BrowserHandler(FileShareHandler):
    reject_send = False
    send_delay = 0
    chunk_delay = 0
    requests = []
    requests_lock = threading.Lock()

    def log_message(self, format, *args):
        if not format.startswith('code '):
            with self.requests_lock:
                self.requests.append({'method': getattr(self, 'command', ''), 'path': self.path})

    def api_chat_send(self):
        if type(self).send_delay:
            time.sleep(type(self).send_delay)
        if type(self).reject_send:
            self._read_json()
            self.send_api_error('Synthetic test rejection; draft must survive', 503)
        else:
            super().api_chat_send()

    def api_chat_media_chunk(self):
        if type(self).chunk_delay:
            time.sleep(type(self).chunk_delay)
        super().api_chat_media_chunk()


class BrowserServer(FastBindHTTPServer):
    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


class BrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Path(CHROME).is_file():
            raise unittest.SkipTest('Chrome is not installed at the configured test path')
        RESULTS.mkdir(parents=True, exist_ok=True)
        cls.share = TEST_ENV.temporary_dir('browser-share-')
        cls.area = cls.share / 'current'
        (cls.area / 'sub').mkdir(parents=True)
        (cls.share / 'other').mkdir()
        (cls.area / 'report-current.txt').write_text('current fixture', encoding='utf-8')
        (cls.area / 'sub' / 'report-sub.txt').write_text('sub fixture', encoding='utf-8')
        (cls.share / 'other' / 'report-other.txt').write_text('other fixture', encoding='utf-8')
        (cls.share / 'alpha.txt').write_text('alpha', encoding='utf-8')
        (cls.share / 'beta.txt').write_text('beta', encoding='utf-8')
        (cls.share / 'notes.txt').write_text('LanShare synthetic text preview\n  indentation\n', encoding='utf-8')
        (cls.share / 'preview.pdf').write_bytes(minimal_pdf())
        (cls.share / 'unsupported.bin').write_bytes(b'synthetic unknown format')
        (cls.share / 'payload.js').write_text('window.__roomInjected = true;', encoding='utf-8')
        Image.new('RGB', (160, 90), (40, 140, 190)).save(cls.share / 'preview.png', 'PNG')
        cls.original_image = TEST_ENV.temporary_dir('browser-media-') / 'browser-20mib.bmp'
        Image.new('RGB', (2700, 2700), (35, 80, 115)).save(cls.original_image, 'BMP')
        SHARE_STATE['path'] = str(cls.share)
        initialize_chat_store()
        cls.server = BrowserServer(('127.0.0.1', 0),
            lambda *a, **kw: BrowserHandler(*a, shared_path=str(cls.share), **kw))
        cls.port = cls.server.server_address[1]
        cls.base = 'http://127.0.0.1:%d' % cls.port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.profile = TEST_ENV.temporary_dir('chrome-profile-')
        cls.downloads = TEST_ENV.temporary_dir('chrome-downloads-')
        cls.chrome = subprocess.Popen([
            CHROME, '--headless=new', '--disable-gpu', '--no-sandbox',
            '--no-first-run', '--no-default-browser-check', '--disable-component-update',
            '--disable-background-networking', '--mute-audio', '--window-size=1280,900',
            '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
            '--remote-debugging-port=0', '--user-data-dir=' + str(cls.profile), 'about:blank',
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.cdp_port = TEST_ENV.wait_browser(cls.chrome, cls.profile)
        cls.connections = []
        cls.runtime_errors = []
        targets = cls.targets()
        first = next(target for target in targets if target.get('type') == 'page')
        cls.main = cls.attach(first['id'])
        cls.main.call('Browser.setDownloadBehavior', {
            'behavior': 'allow', 'downloadPath': str(cls.downloads), 'eventsEnabled': True,
        })
        cls.main_id = first['id']

    @classmethod
    def tearDownClass(cls):
        for cdp in getattr(cls, 'connections', []):
            cdp.ws.close()
        if getattr(cls, 'chrome', None):
            TEST_ENV.stop_process(cls.chrome)
        if getattr(cls, 'server', None):
            cls.server.shutdown(); cls.server.server_close()
        chat_room_saver.flush()

    @classmethod
    def targets(cls):
        with urllib.request.urlopen('http://127.0.0.1:%d/json/list' % cls.cdp_port, timeout=5) as response:
            return json.load(response)

    @classmethod
    def attach(cls, target_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for target in cls.targets():
                if target['id'] == target_id and target.get('webSocketDebuggerUrl'):
                    cdp = CDP(target['webSocketDebuggerUrl'])
                    cdp.target_id = target_id
                    cdp.parent_id = target.get('parentId')
                    cdp.closed = False
                    cls.connections.append(cdp)
                    cdp.call('Runtime.enable'); cdp.call('Network.enable'); cdp.call('Page.enable')
                    cdp.call('Log.enable')
                    return cdp
            time.sleep(0.05)
        raise RuntimeError('Owned target did not publish a debug endpoint')

    @classmethod
    def collect_errors(cls, cdp):
        for event in cdp.events:
            if event.get('method') == 'Runtime.exceptionThrown':
                cls.runtime_errors.append(event['params'].get('exceptionDetails'))
            if event.get('method') == 'Log.entryAdded':
                entry = event['params']['entry']
                if entry.get('level') == 'error' and entry.get('source') == 'javascript':
                    cls.runtime_errors.append(entry)
        cdp.events.clear()

    @classmethod
    def close_target(cls, cdp):
        if cdp.closed:
            return
        closing = [cdp]
        for connection in cls.connections:
            if not connection.closed and any(connection.parent_id == parent.target_id for parent in closing):
                closing.append(connection)
        try:
            for connection in closing:
                connection.js('document.readyState')
        finally:
            for connection in closing:
                cls.collect_errors(connection)
            cls.main.call('Target.closeTarget', {'targetId': cdp.target_id})
            for connection in closing:
                connection.closed = True
                connection.ws.close()
            cls.main.call('Page.bringToFront')

    @classmethod
    def request(cls, method, route, data=None):
        connection = HTTPConnection('127.0.0.1', cls.port, timeout=10)
        body = json.dumps(data).encode('utf-8') if data is not None else None
        connection.request(method, route, body=body, headers={
            'Content-Type': 'application/json', 'Connection': 'close'})
        response = connection.getresponse()
        payload = response.read(); connection.close()
        if response.status >= 400:
            raise RuntimeError('Fixture request failed: %d %r' % (response.status, payload))
        return json.loads(payload)

    def navigate(self, cdp, route, selector=None):
        url = self.base + route
        cdp.call('Page.bringToFront')
        cdp.call('Page.navigate', {'url': url})
        ready = 'location.href === %s && document.readyState === "complete"' % json.dumps(url)
        if selector:
            ready += ' && !!document.querySelector(%s)' % json.dumps(selector)
        self.assertTrue(cdp.wait_js(ready), 'Page did not become ready: ' + route)
        self.assertTrue(cdp.wait_js('!document.fonts || document.fonts.status === "loaded"'))

    @staticmethod
    def gesture(cdp, expression):
        result = cdp.call('Runtime.evaluate', {'expression': expression, 'returnByValue': True,
            'awaitPromise': True, 'userGesture': True})
        if result.get('exceptionDetails'):
            raise RuntimeError(str(result['exceptionDetails']))
        return result.get('result', {}).get('value')

    def width(self, cdp, width):
        cdp.call('Emulation.setDeviceMetricsOverride', {
            'width': width, 'height': 844 if width < 900 else 900,
            'deviceScaleFactor': 1, 'mobile': width < 900,
        })

    def assert_no_overflow(self, cdp):
        dimensions = cdp.js('''(() => ({width: innerWidth, html: document.documentElement.scrollWidth,
          body: document.body.scrollWidth, offenders: Array.from(document.querySelectorAll('*')).filter(e =>
          e.offsetWidth && e.getBoundingClientRect().right > innerWidth + 1).slice(0,8).map(e => e.tagName+'.'+e.className)}))()''')
        self.assertLessEqual(max(dimensions['html'], dimensions['body']), dimensions['width'] + 1, dimensions)

    def screenshot(self, cdp, name):
        cdp.call('Page.bringToFront')
        self.gesture(cdp, 'new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        result = cdp.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})
        (RESULTS / name).write_bytes(base64.b64decode(result['data']))

    @staticmethod
    def choose_image(cdp, image):
        root = cdp.call('DOM.getDocument')['root']['nodeId']
        node = cdp.call('DOM.querySelector', {'nodeId': root, 'selector': '#chatImage'})['nodeId']
        cdp.call('DOM.setFileInputFiles', {'nodeId': node, 'files': [str(image)]})

    @classmethod
    def pdf_ready(cls, cdp):
        def has_viewer(node):
            name = node.get('nodeName', '').lower()
            attributes = dict(zip(node.get('attributes', [])[::2], node.get('attributes', [])[1::2]))
            if name == 'pdf-viewer' or name == 'embed' and attributes.get('type') in (
                    'application/pdf', 'application/x-google-chrome-pdf'):
                return True
            children = node.get('children', []) + node.get('shadowRoots', [])
            if node.get('contentDocument'):
                children.append(node['contentDocument'])
            return any(has_viewer(child) for child in children)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            target = next((target for target in cls.targets() if target.get('type') == 'iframe'
                and target.get('parentId') == cdp.target_id and target.get('url', '').startswith('chrome-extension://')), None)
            if target:
                viewer = cls.attach(target['id'])
                ready = viewer.wait_js('''(() => {let v=document.querySelector('pdf-viewer');
                  return !!v && v.loadState_==='success' && !!v.documentDimensions &&
                    v.documentDimensions.pageDimensions.length===1;})()''')
                evidence = viewer.js('''(() => {let v=document.querySelector('pdf-viewer');
                  return {url:location.href,source:v&&v.originalUrl,loadState:v&&v.loadState_,
                    dimensions:v&&v.documentDimensions};})()''')
                (RESULTS / 'v7-pdf-browser-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
                return ready
            document = cdp.call('DOM.getDocument', {'depth': -1, 'pierce': True})
            if has_viewer(document['root']):
                return True
            time.sleep(0.15)
        return False

    def test_01_preview_download_are_adjacent_and_real_previews(self):
        cdp = self.main
        self.width(cdp, 1280)
        self.navigate(cdp, '/', '[data-files-list] .row')
        for filename, expected in [('preview.png', 'image'), ('notes.txt', 'text'), ('preview.pdf', 'pdf')]:
            row = '.row[data-name=%s]' % json.dumps(filename)
            adjacent = cdp.js('''(() => {let row=document.querySelector(%s), p=row.querySelector('.row-preview');
                return p.tagName==='A' && p.nextElementSibling.matches('a.row-dl') &&
                p.href.includes('preview=1') && !p.nextElementSibling.href.includes('preview=1');})()''' % json.dumps(row))
            self.assertTrue(adjacent, filename)
            before_targets = {target['id'] for target in self.targets()}
            before_files = set(self.downloads.iterdir())
            start = len(BrowserHandler.requests)
            self.gesture(cdp, 'document.querySelector(%s).click()' % json.dumps(row + ' .row-preview'))
            target = None
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and target is None:
                target = next((t for t in self.targets() if t.get('type') == 'page' and t['id'] not in before_targets
                    and '/download?' in t.get('url', '')), None)
                if target is None: time.sleep(0.05)
            self.assertIsNotNone(target, filename + ' did not open a preview tab')
            preview = self.attach(target['id'])
            expressions = {
                'image': '!!document.querySelector("img") && document.querySelector("img").naturalWidth === 160',
                'text': '!!document.querySelector("pre") && document.querySelector("pre").textContent.includes("synthetic text preview")',
            }
            try:
                preview.call('Page.bringToFront')
                ready = self.pdf_ready(preview) if expected == 'pdf' else preview.wait_js(expressions[expected])
                if not ready:
                    diagnostic = {'targets': self.targets(), 'document': preview.call('DOM.getDocument', {'depth': -1, 'pierce': True}),
                        'frames': preview.call('Page.getFrameTree')}
                    (RESULTS / 'v7-preview-failure.json').write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding='utf-8')
                self.assertTrue(ready, (filename, preview.js('document.body.innerHTML.slice(0,2000)')))
                if expected == 'pdf':
                    self.screenshot(preview, 'v7-preview-pdf.png')
                self.assertEqual(set(self.downloads.iterdir()), before_files, filename + ' triggered a download')
                downloads = [request['path'] for request in BrowserHandler.requests[start:] if request['path'].startswith('/download?')]
                self.assertTrue(downloads, filename)
                self.assertTrue(all('preview=1' in route for route in downloads), downloads)
            finally:
                self.close_target(preview)
        self.assertTrue(cdp.js('document.querySelector(".row[data-name=\\"unsupported.bin\\"] .row-preview").disabled'))
        self.gesture(cdp, 'document.querySelector(".row[data-name=\\"notes.txt\\"] .row-dl").click()')
        downloaded = self.downloads / 'notes.txt'
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not downloaded.is_file():
            cdp.js('document.readyState'); time.sleep(0.05)
        self.assertTrue(downloaded.is_file(), 'Download button did not download')
        self.assertEqual(downloaded.read_bytes(), (self.share / 'notes.txt').read_bytes())

    def test_02_search_scopes_and_stale_response_guard(self):
        cdp = self.main
        self.width(cdp, 1280)
        self.navigate(cdp, '/dir?path=' + urllib.parse.quote(str(self.area)), '[data-files-list] .row')
        def run(scope, text):
            self.gesture(cdp, '''(() => {let scope=Array.from(document.querySelectorAll('[data-files-search-scope]')).find(e=>e.offsetWidth);
              scope.value=%s; scope.dispatchEvent(new Event('change',{bubbles:true}));
              let input=Array.from(document.querySelectorAll('[data-files-search]')).find(e=>e.offsetWidth);
              input.value=%s; input.dispatchEvent(new Event('input',{bubbles:true}));
              Array.from(document.querySelectorAll('[data-files-search-submit]')).find(e=>e.offsetWidth).click();})()''' %
              (json.dumps(scope), json.dumps(text)))
        run('current', 'report')
        self.assertTrue(cdp.wait_js("Lan.files.state.query==='report' && Lan.files.state.total===1 && Lan.files.state.entries.length===1"))
        run('subtree', 'report')
        self.assertTrue(cdp.wait_js("Lan.files.search.active && Lan.files.search.entries.length===2"))
        run('share', 'report')
        self.assertTrue(cdp.wait_js("Lan.files.search.active && Lan.files.search.entries.length===3"))
        cdp.js('''window.__realFetch=window.fetch; window.fetch=function(url,opts){
          if(String(url).startsWith('/api/search?') && String(url).includes('q=alpha')) {
            let copied=Object.assign({},opts); delete copied.signal;
            return window.__realFetch(url,copied).then(r=>new Promise(resolve=>setTimeout(()=>resolve(r),600)));
          } return window.__realFetch(url,opts); };''')
        run('share', 'alpha'); run('share', 'beta')
        self.assertTrue(cdp.wait_js("Lan.files.search.query==='beta' && Lan.files.search.entries.length===1 && Lan.files.search.entries[0].name==='beta.txt'"))
        time.sleep(0.8)
        self.assertEqual(cdp.js('Lan.files.search.entries[0].name'), 'beta.txt')
        cdp.js('window.fetch=window.__realFetch; delete window.__realFetch;')

    def test_03_safe_group_names_without_extra_script_or_style(self):
        cdp = self.main
        names = ['</script><style>body{display:none}</style>', '</script><script src=/download?file=payload.js>']
        for name in names:
            self.request('POST', '/chat/create', {'name': name})
        self.navigate(cdp, '/chat', '[data-room-list] .row')
        for name in names:
            self.assertIn(name, cdp.js("Array.from(document.querySelectorAll('.row-name')).map(e=>e.textContent)"))
        self.assertTrue(cdp.js("!window.__roomInjected && !document.querySelector('style') && document.querySelectorAll('script:not([src]):not([type=\\\"application/json\\\"])').length===0"))
        self.assertEqual(cdp.js('getComputedStyle(document.body).display'), 'block')

    def test_04_chat_paste_render_draft_rename_and_sse(self):
        cdp = self.main
        room = self.request('POST', '/chat/create', {'name': 'Synthetic Browser Chat'})['room_id']
        type(self).room_id = room
        route = '/chat/room?id=' + room
        self.navigate(cdp, route, '[data-chat-input]')
        self.assertTrue(cdp.wait_js('!!Lan.chat.state.revision'))
        other_id = cdp.call('Target.createTarget', {'url': 'about:blank'})['targetId']
        other = self.attach(other_id)
        self.addCleanup(self.close_target, other)
        self.width(other, 1280)
        self.navigate(other, route, '[data-chat-input]')
        self.assertTrue(other.wait_js('!!Lan.chat.state.revision'))
        text = '  copied first\n\t    indented second\nlast  \n'
        self.gesture(cdp, '''(() => {let i=document.querySelector('[data-chat-input]'); i.focus();
          let data=new DataTransfer(); data.setData('text/plain',%s); data.setData('text/html','<b>Do not paste HTML</b>');
          i.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));})()''' % json.dumps(text))
        self.assertEqual(cdp.js("document.querySelector('[data-chat-input]').value"), text)
        BrowserHandler.reject_send = True
        try:
            self.gesture(cdp, "document.querySelector('[data-chat-send]').click()")
            self.assertTrue(cdp.wait_js("!Lan.chat.state.sending && !!document.querySelector('.toast[data-tone=err]')"))
            self.assertEqual(cdp.js("document.querySelector('[data-chat-input]').value"), text)
            self.assertEqual(cdp.js('Lan.chat.state.msgs.length'), 0)
        finally:
            BrowserHandler.reject_send = False
        self.gesture(cdp, "document.querySelector('[data-chat-send]').click()")
        self.assertTrue(cdp.wait_js("document.querySelectorAll('.msg').length===1 && !Lan.chat.state.sending"))
        self.assertTrue(other.wait_js("document.querySelectorAll('.msg').length===1"), 'Empty room SSE missed first message')
        self.assertEqual(cdp.js("document.querySelector('.msg-text').textContent"), text)
        styles = cdp.js("(() => {let e=document.querySelector('.msg-text'),s=getComputedStyle(e);return {whiteSpace:s.whiteSpace,tabSize:s.tabSize,height:e.getBoundingClientRect().height,lineHeight:parseFloat(s.lineHeight)};})()")
        self.assertEqual(styles['whiteSpace'], 'pre-wrap'); self.assertEqual(styles['tabSize'], '4')
        self.assertGreaterEqual(styles['height'], styles['lineHeight'] * 3 - 1)
        self.assertEqual(self.request('GET', '/chat/history?room=' + room)[0]['text'], text)
        self.gesture(cdp, "document.querySelector('[data-chat-rename]').click()")
        self.assertTrue(cdp.wait_js("!!document.querySelector('.scrim[data-open=true] input')"))
        self.gesture(cdp, "document.querySelector('.scrim[data-open=true] input').value='Browser Renamed'; document.querySelector('.scrim[data-open=true] [data-modal-ok]').click()")
        self.assertTrue(cdp.wait_js("document.querySelector('[data-chat-name]').textContent==='Browser Renamed'"))
        self.assertTrue(other.wait_js("document.querySelector('[data-chat-name]').textContent==='Browser Renamed'"))
        msg_id = cdp.js('Lan.chat.state.msgs[0].id')
        self.request('POST', '/chat/delete', {'room': room, 'id': msg_id})
        self.assertTrue(other.wait_js('Lan.chat.state.msgs.length===0'))
        self.request('POST', '/chat/send', {'room': room, 'text': text})
        self.assertTrue(other.wait_js('Lan.chat.state.msgs.length===1'))
        self.request('POST', '/chat/clear', {'room': room})
        self.assertTrue(other.wait_js('Lan.chat.state.msgs.length===0'))
        self.request('POST', '/chat/send', {'room': room, 'text': text})
        self.close_target(other)

    def test_05_desktop_and_mobile_layout_no_js_errors(self):
        cdp = self.main
        room = getattr(self, 'room_id', None)
        if not room:
            room = self.request('POST', '/chat/create', {'name': 'Synthetic Layout Room'})['room_id']
            self.request('POST', '/chat/send', {'room': room, 'text': '  first line\n\tindented line\nlast line'})
        for width in (320, 390, 1280):
            self.width(cdp, width)
            for page, route, selector in [('files', '/', '[data-files-list] .row'),
                ('chat', '/chat/room?id=' + room, '[data-chat-input]')]:
                self.navigate(cdp, route, selector)
                self.assert_no_overflow(cdp)
                self.screenshot(cdp, 'v7-%s-%d.png' % (page, width))
        for connection in self.connections:
            if not connection.closed:
                connection.js('document.readyState')
                self.collect_errors(connection)
        self.assertFalse(self.runtime_errors, self.runtime_errors)

    def test_06_large_binary_image_draft_retry_lightbox_download_and_cancel(self):
        from server import chat_media
        cdp = self.main
        self.width(cdp, 1280)
        room = self.request('POST', '/chat/create', {'name': 'Synthetic Large Image'})['room_id']
        self.navigate(cdp, '/chat/room?id=' + room, '[data-chat-input]')
        self.assertTrue(cdp.wait_js('!!Lan.chat.state.revision'))
        request_start = len(BrowserHandler.requests)
        self.choose_image(cdp, self.original_image)
        self.assertTrue(cdp.wait_js("!!Lan.chat.state.media && Lan.chat.state.media.status==='ready'", timeout=25))
        self.assertEqual(cdp.js('Lan.chat.state.media.file.size'), self.original_image.stat().st_size)
        self.assertGreaterEqual(self.original_image.stat().st_size, 20 * 1024 ** 2)
        self.assertEqual(cdp.js("document.querySelector('[data-image-preview] progress').value"), 100)
        token = cdp.js('Lan.chat.state.media.id')
        chunks = [item['path'] for item in BrowserHandler.requests[request_start:] if item['path'].startswith('/chat/media/chunk?')]
        self.assertEqual(len(chunks), 21)
        caption = '  large image caption\n\tindented second line\n'
        self.gesture(cdp, "let input=document.querySelector('[data-chat-input]'); input.value=%s; input.dispatchEvent(new Event('input',{bubbles:true}));" % json.dumps(caption))
        BrowserHandler.reject_send = True
        try:
            self.gesture(cdp, "document.querySelector('[data-chat-send]').click()")
            self.assertTrue(cdp.wait_js("!Lan.chat.state.sending && !!document.querySelector('.toast[data-tone=err]')"))
            self.assertEqual(cdp.js("document.querySelector('[data-chat-input]').value"), caption)
            self.assertEqual(cdp.js('Lan.chat.state.media.id'), token)
            self.assertEqual(cdp.js('Lan.chat.state.media.status'), 'ready')
            self.assertEqual(cdp.js('Lan.chat.state.msgs.length'), 0)
        finally:
            BrowserHandler.reject_send = False
        draft_id = cdp.js('Lan.chat.state.pendingDraft.id')
        new_draft = '  new text typed while sending\n'
        BrowserHandler.send_delay = 0.3
        try:
            self.gesture(cdp, "document.querySelector('[data-chat-send]').click()")
            self.assertTrue(cdp.js('Lan.chat.state.sending'))
            self.gesture(cdp, "document.querySelector('[data-chat-input]').value=%s; document.querySelector('[data-chat-input]').dispatchEvent(new Event('input',{bubbles:true}));" % json.dumps(new_draft))
            self.assertTrue(cdp.wait_js("!Lan.chat.state.sending && Lan.chat.state.msgs.length===1 && !Lan.chat.state.media"))
        finally:
            BrowserHandler.send_delay = 0
        self.assertEqual(cdp.js("document.querySelector('[data-chat-input]').value"), new_draft)
        self.assertTrue(cdp.js("document.querySelector('[data-image-preview]').hidden"))
        message = cdp.js('Lan.chat.state.msgs[0]')
        self.assertEqual(message['text'], caption)
        self.assertTrue(message['image'].endswith('.bmp'), message)
        self.assertTrue(message['preview'].endswith('_p.jpg'), message)
        self.assertTrue(cdp.wait_js("!!document.querySelector('.msg-img') && document.querySelector('.msg-img').naturalWidth===320"))
        self.assertTrue(cdp.js("document.querySelector('.msg-img').src.endsWith('_t.jpg')"))
        sends = [json.loads(event['params']['request']['postData']) for event in cdp.events
            if event.get('method') == 'Network.requestWillBeSent' and event['params']['request']['url'].endswith('/chat/send')
            and event['params']['request'].get('postData')]
        self.assertEqual(len(sends), 2, sends)
        self.assertTrue(all(payload['client_id'] == draft_id and payload.get('media') == token and
            'image' not in payload and len(json.dumps(payload)) < 1024 for payload in sends), sends)
        self.assertEqual(len([item for item in BrowserHandler.requests[request_start:] if item['path'].startswith('/chat/media/chunk?')]), 21)
        self.assert_no_overflow(cdp)
        self.assertTrue(cdp.wait_js("!document.querySelector('.toast[data-tone=err]')"))
        self.screenshot(cdp, 'v7-chat-large-image.png')
        self.gesture(cdp, "document.querySelector('.msg-img').click()")
        self.assertTrue(cdp.wait_js("document.querySelector('[data-lightbox]').getAttribute('data-open')==='true' && document.querySelector('[data-lightbox] img').naturalWidth===1920"))
        self.assertTrue(cdp.js("document.querySelector('[data-lightbox] img').src.endsWith('_p.jpg')"))
        self.screenshot(cdp, 'v7-chat-bmp-lightbox.png')
        before_files = set(self.downloads.iterdir())
        self.gesture(cdp, "document.querySelector('[data-lightbox-download]').click()")
        deadline = time.monotonic() + 15
        downloads = []
        while time.monotonic() < deadline:
            downloads = [path for path in set(self.downloads.iterdir()) - before_files
                if path.is_file() and path.suffix != '.crdownload']
            if downloads:
                break
            cdp.js('document.readyState'); time.sleep(0.1)
        self.assertEqual(len(downloads), 1, downloads)
        downloaded = downloads[0].read_bytes()
        self.assertEqual(downloaded, self.original_image.read_bytes())
        evidence = {'source_bytes': len(downloaded), 'chunks': len(chunks), 'sent_messages': 1,
            'preview_width': 1920, 'original_download_sha256': hashlib.sha256(downloaded).hexdigest(),
            'failed_send_reused_media': True, 'new_draft_preserved': True}
        (RESULTS / 'v7-browser-large-image-evidence.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
        self.gesture(cdp, "document.querySelector('[data-lightbox-close]').click()")
        self.request('POST', '/chat/clear', {'room': room})
        self.assertTrue(cdp.wait_js('Lan.chat.state.msgs.length===0'))
        BrowserHandler.chunk_delay = 0.35
        try:
            self.choose_image(cdp, self.original_image)
            self.assertTrue(cdp.wait_js("!!Lan.chat.state.media && !!Lan.chat.state.media.upload && Lan.chat.state.media.status==='uploading'"))
            cancel_token = cdp.js('Lan.chat.state.media.upload')
            self.gesture(cdp, "document.querySelector('[data-img-remove]').click()")
            self.assertTrue(cdp.wait_js('!Lan.chat.state.media'))
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and cancel_token in chat_media._uploads:
                time.sleep(0.1)
            self.assertNotIn(cancel_token, chat_media._uploads)
            self.assertFalse((chat_media.CHAT_UPLOAD_DIR / (cancel_token + '.part')).exists())
            self.assertEqual(cdp.js("document.querySelector('[data-chat-input]').value"), new_draft)
        finally:
            BrowserHandler.chunk_delay = 0
        cdp.js('document.readyState')
        self.collect_errors(cdp)
        self.assertFalse(self.runtime_errors, self.runtime_errors)


if __name__ == '__main__':
    unittest.main(verbosity=2)
