"""Chat layout regressions using fresh assets and the owned Chrome test harness."""
import argparse
import hashlib
import importlib.util
import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from test_support import isolate_test_environment

TEST_ENV = isolate_test_environment()
import build_assets

# Do not overwrite a developer's embedded resources while checking current web sources.
build_assets.OUT = TEST_ENV.temporary_dir('chat-layout-assets-') / 'webassets.py'
build_assets.main()
spec = importlib.util.spec_from_file_location('webassets', build_assets.OUT)
assets = importlib.util.module_from_spec(spec)
sys.modules['webassets'] = assets
spec.loader.exec_module(assets)

import v7_browser_test as browser


class ChatLayoutTests(browser.BrowserTest):
    evidence = {'viewports': [], 'screenshots': []}
    shot_prefix = 'chat-layout'

    def capture(self, name):
        name = self.shot_prefix + '-' + name + '.png'
        self.screenshot(self.main, name)
        path = browser.RESULTS / name
        self.evidence['screenshots'].append({
            'file': name, 'bytes': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        })

    def assert_clean_composer(self):
        result = self.main.js('''(() => {
          const wrap = document.querySelector('.composer-wrap');
          return {text: wrap.innerText, hintCount: wrap.querySelectorAll(
            '.composer-hint,.chat-media-help,[data-online],[data-media-help]').length,
            renameCount: wrap.querySelectorAll('[data-chat-rename]').length,
            previewHidden: wrap.querySelector('[data-image-preview]').hidden,
            imageEnabled: !wrap.querySelector('[data-chat-image]').disabled};
        })()''')
        self.assertEqual(result['hintCount'], 0, result)
        self.assertEqual(result['renameCount'], 0, result)
        for text in ('Enter ', 'Shift+Enter', '\u5b9e\u65f6\u8fde\u63a5', '\u53ef\u7528\u683c\u5f0f'):
            self.assertNotIn(text, result['text'])
        self.assertTrue(result['previewHidden'])
        self.assertTrue(result['imageEnabled'])
        return result

    def test_chat_list_headers_align_and_mobile_metadata_remains(self):
        room = self.request('POST', '/chat/create', {
            'name': '\u4ea7\u54c1\u534f\u4f5c\u7fa4 - \u5e03\u5c40\u9a8c\u8bc1',
        })['room_id']
        for index in range(12):
            self.request('POST', '/chat/send', {'room': room, 'text': 'Synthetic note %d' % index})
        self.request('POST', '/chat/create', {
            'name': '\u957f\u7fa4\u540d' * 12,
        })
        for width in (1280, 1024, 901, 390, 320):
            with self.subTest(width=width):
                self.width(self.main, width)
                self.navigate(self.main, '/chat', '[data-room-list] .row')
                measurements = self.main.js('''(() => {
                  const list = document.querySelector('[data-room-list]');
                  const section = list.closest('.files');
                  const head = section.querySelector('.list-head');
                  const rect = el => {const r=el.getBoundingClientRect();
                    return {left:r.left,right:r.right,width:r.width,visible:!!el.offsetWidth};};
                  return {width:innerWidth, headerVisible:!!head.offsetHeight,
                    countHeader:rect(head.querySelector('.col-size')),
                    timeHeader:rect(head.querySelector('.col-time')),
                    actionHeader:rect(head.querySelector('.col-more')),
                    rows:Array.from(list.children).map(row => ({
                      count:rect(row.querySelector('.row-size')),
                      time:rect(row.querySelector('.row-time')),
                      action:rect(row.querySelector('.row-more')),
                      sub:rect(row.querySelector('.row-sub')),
                      subText:row.querySelector('.row-sub').textContent}))};
                })()''')
                self.evidence['viewports'].append({'page': 'chat-list', **measurements})
                self.capture('list-%d' % width)
                self.assert_no_overflow(self.main)
                if width > 900:
                    self.assertTrue(measurements['headerVisible'])
                    for row in measurements['rows']:
                        for column in ('count', 'time', 'action'):
                            header = measurements[column + 'Header']
                            self.assertAlmostEqual(header['right'], row[column]['right'], delta=1,
                                                   msg=(width, column, measurements))
                else:
                    self.assertFalse(measurements['headerVisible'])
                    for row in measurements['rows']:
                        self.assertTrue(row['sub']['visible'])
                        self.assertIn('\u6761\u6d88\u606f', row['subText'])
                        self.assertFalse(row['count']['visible'])
                        self.assertFalse(row['time']['visible'])
                        self.assertTrue(row['action']['visible'])

    def test_composer_has_no_persistent_footer_desktop_or_mobile(self):
        room = self.request('POST', '/chat/create', {'name': '\u7b80\u6d01\u8f93\u5165\u533a'})['room_id']
        self.request('POST', '/chat/send', {
            'room': room, 'text': '\u8f93\u5165\u533a\u4fdd\u7559\u6d88\u606f\u548c\u56fe\u7247\u64cd\u4f5c\u3002',
        })
        for width in (1280, 390, 320):
            with self.subTest(width=width):
                self.width(self.main, width)
                self.navigate(self.main, '/chat/room?id=' + room, '[data-chat-input]')
                self.assertTrue(self.main.wait_js('Lan.chat.state.es && Lan.chat.state.es.readyState===1'))
                self.capture('room-%d' % width)
                composer = self.assert_clean_composer()
                self.assert_no_overflow(self.main)
                self.evidence['viewports'].append({'page': 'chat-room', 'width': width, 'composer': composer})

    def test_rename_stays_in_desktop_or_mobile_header_and_preserves_room(self):
        long_name = '\u957f\u7fa4\u540d' * 10
        room = self.request('POST', '/chat/create', {'name': long_name})['room_id']
        self.request('POST', '/chat/send', {'room': room, 'text': 'Rename preserves room history.'})
        history = self.request('GET', '/chat/history?room=' + room)
        for width in (1280, 390, 320):
            with self.subTest(width=width):
                self.width(self.main, width)
                self.navigate(self.main, '/chat/room?id=' + room, '[data-chat-input]')
                self.assertTrue(self.main.wait_js('Lan.chat.state.es && Lan.chat.state.es.readyState===1'))
                self.assert_clean_composer()
                measurements = self.main.js('''(() => {
                  const buttons=Array.from(document.querySelectorAll('[data-chat-rename]'));
                  const active=buttons.filter(el=>el.offsetWidth && el.offsetHeight);
                  const header=document.querySelector(innerWidth<=900 ? '.mobile-bar' : '.chat-head');
                  const rect=el=>{const r=el.getBoundingClientRect();
                    return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width};};
                  return {width:innerWidth,totalButtons:buttons.length,visibleButtons:active.length,
                    header:rect(header),buttons:active.map(el=>({rect:rect(el),
                      inDesktop:!!el.closest('.chat-head'),inMobile:!!el.closest('.mobile-bar-actions')})),
                    title:rect(header.querySelector('.mobile-bar-title,.chat-title')),
                    actions:rect(header.querySelector('.mobile-bar-actions,.chat-head-actions'))};
                })()''')
                self.assertEqual(measurements['totalButtons'], 2, measurements)
                self.assertEqual(measurements['visibleButtons'], 1, measurements)
                button = measurements['buttons'][0]
                self.assertEqual(button['inMobile'], width <= 900, measurements)
                self.assertEqual(button['inDesktop'], width > 900, measurements)
                for axis in ('left', 'top'):
                    self.assertGreaterEqual(button['rect'][axis], measurements['header'][axis] - 1, measurements)
                for axis in ('right', 'bottom'):
                    self.assertLessEqual(button['rect'][axis], measurements['header'][axis] + 1, measurements)
                self.assertGreater(measurements['title']['width'], 0, measurements)
                self.assertLessEqual(measurements['title']['right'], measurements['actions']['left'] + 1,
                                     measurements)
                self.assert_no_overflow(self.main)
                self.capture('rename-%d' % width)
                self.gesture(self.main, "Array.from(document.querySelectorAll('[data-chat-rename]')).find(e=>e.offsetWidth).click()")
                self.assertTrue(self.main.wait_js("!!document.querySelector('.scrim[data-open=true] input')"))
                renamed = long_name + '-' + str(width)
                self.gesture(self.main, "document.querySelector('.scrim[data-open=true] input').value=%s; "
                    "document.querySelector('.scrim[data-open=true] [data-modal-ok]').click()" % json.dumps(renamed))
                self.assertTrue(self.main.wait_js('Lan.chat.state.name===%s' % json.dumps(renamed)))
                self.assertEqual(self.main.js('Lan.chat.state.room'), room)
                self.assertEqual(self.main.js("document.querySelector('[data-chat-name]').textContent"), renamed)
                self.assertEqual(self.main.js("document.querySelector('.mobile-bar-title').textContent"), renamed)
                self.assertEqual(self.request('GET', '/chat/history?room=' + room), history)
                self.evidence['viewports'].append({'page': 'chat-rename', **measurements,
                    'rename_confirmed': True, 'room_and_history_preserved': True})
        self.collect_errors(self.main)
        self.assertFalse(self.runtime_errors, self.runtime_errors)

    def test_sse_keyboard_send_and_image_upload_work_without_footer(self):
        room = self.request('POST', '/chat/create', {'name': 'Quiet composer behavior'})['room_id']
        self.width(self.main, 1280)
        self.navigate(self.main, '/chat/room?id=' + room, '[data-chat-input]')
        self.assertTrue(self.main.wait_js('Lan.chat.state.es && Lan.chat.state.es.readyState===1'))
        self.assert_clean_composer()
        self.request('POST', '/chat/send', {'room': room, 'text': 'Remote SSE note'})
        self.assertTrue(self.main.wait_js("Lan.chat.state.msgs.some(m=>m.text==='Remote SSE note')"))

        self.gesture(self.main, '''(() => {
          const input=document.querySelector('[data-chat-input]');
          input.value='  first line'; input.focus(); input.setSelectionRange(input.value.length,input.value.length);
          input.dispatchEvent(new Event('input',{bubbles:true}));
        })()''')
        self.assertTrue(self.main.js("document.activeElement===document.querySelector('[data-chat-input]')"))
        self.main.call('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': 'Enter', 'code': 'Enter',
            'windowsVirtualKeyCode': 13, 'nativeVirtualKeyCode': 13, 'modifiers': 8,
            'text': '\r', 'unmodifiedText': '\r'})
        self.main.call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': 'Enter', 'code': 'Enter',
            'windowsVirtualKeyCode': 13, 'nativeVirtualKeyCode': 13, 'modifiers': 8})
        self.assertEqual(self.main.js("document.querySelector('[data-chat-input]').value"), '  first line\n')
        self.assertEqual(self.main.js('Lan.chat.state.msgs.length'), 1)
        self.main.call('Input.insertText', {'text': '\tsecond line  '})
        expected = '  first line\n\tsecond line  '
        self.main.call('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': 'Enter', 'code': 'Enter',
            'windowsVirtualKeyCode': 13, 'nativeVirtualKeyCode': 13})
        self.main.call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': 'Enter', 'code': 'Enter',
            'windowsVirtualKeyCode': 13, 'nativeVirtualKeyCode': 13})
        self.assertTrue(self.main.wait_js('Lan.chat.state.msgs.length===2 && !Lan.chat.state.sending'))
        self.assertEqual(self.main.js('Lan.chat.state.msgs[1].text'), expected)
        self.assertEqual(self.main.js("document.querySelector('[data-chat-input]').value"), '')

        self.choose_image(self.main, self.share / 'preview.png')
        self.assertTrue(self.main.wait_js("Lan.chat.state.media && Lan.chat.state.media.status==='ready'"))
        self.assertFalse(self.main.js("document.querySelector('[data-image-preview]').hidden"))
        self.gesture(self.main, "document.querySelector('[data-chat-send]').click()")
        self.assertTrue(self.main.wait_js("Lan.chat.state.msgs.length===3 && !Lan.chat.state.media"))
        self.assertEqual(self.main.js('Lan.chat.state.msgs[2].type'), 'image')
        self.assert_clean_composer()
        self.collect_errors(self.main)
        self.assertFalse(self.runtime_errors, self.runtime_errors)
        self.evidence['behavior'] = {'sse': True, 'shift_enter_newline': True, 'enter_send': True,
            'preserved_whitespace': True, 'image_upload_and_send': True, 'javascript_errors': self.runtime_errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path,
        default=ROOT / 'docs/changes/2026-10-28-release-v7/test-results/chat-layout.json')
    parser.add_argument('--shot-prefix', default='chat-layout')
    args = parser.parse_args()
    browser.RESULTS = args.results.resolve().parent
    browser.RESULTS.mkdir(parents=True, exist_ok=True)
    ChatLayoutTests.shot_prefix = args.shot_prefix
    # The base class provides isolation helpers; only this change's four methods are run.
    names = ('test_chat_list_headers_align_and_mobile_metadata_remains',
             'test_composer_has_no_persistent_footer_desktop_or_mobile',
             'test_rename_stays_in_desktop_or_mobile_header_and_preserves_room',
             'test_sse_keyboard_send_and_image_upload_work_without_footer')
    suite = unittest.TestSuite(ChatLayoutTests(name) for name in names)
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = dict(ChatLayoutTests.evidence, tests=result.testsRun, success=result.wasSuccessful(),
        failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
        seconds=round(time.monotonic() - started, 3), timestamp=time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        source_css_sha256=hashlib.sha256(assets.CSS.encode('utf-8')).hexdigest(),
        source_chat_js_sha256=hashlib.sha256(assets.JS_CHAT.encode('utf-8')).hexdigest(),
        isolation='Owned dynamic-port server and Chrome profile; fresh temporary webassets; synthetic rooms/images only',
        owned_test_root=str(TEST_ENV.root))
    args.results.write_text(json.dumps(report, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')
    print('Chat layout evidence: %s' % args.results)
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == '__main__':
    sys.exit(main())
