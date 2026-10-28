/* No browser/process state: execute the real chat module against a minimal DOM. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'web/js/85-chat-room.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'web/app.css'), 'utf8');
const roomTemplate = fs.readFileSync(path.join(root, 'web/pages/chat_room.html'), 'utf8');
const ticks = () => new Promise(resolve => setImmediate(resolve));
let passed = 0;

function harness(fetchImpl, options = {}) {
  const listeners = {};
  const toasts = [];
  const timers = new Map();
  const streams = [];
  const modals = [];
  const renameEvents = [];
  function renameButton(name) {
    return { listeners: {}, parentNode: null,
      addEventListener(type, listener) {
        this.listeners[type] = listener; renameEvents.push('bind-' + name);
      }
    };
  }
  const desktopRename = renameButton('desktop');
  const mobileRename = renameButton('mobile');
  const mobileActions = { children: [], appendChild(child) {
    child.parentNode = this; this.children.push(child); renameEvents.push('move-mobile');
  } };
  let nextTimer = 0;
  const input = {
    value: '', style: {}, scrollHeight: 20, selectionStart: 0, selectionEnd: 0,
    addEventListener(type, listener) { listeners[type] = listener; },
    setRangeText(value, start, end) {
      this.value = this.value.slice(0, start) + value + this.value.slice(end);
      this.selectionStart = this.selectionEnd = start + value.length;
    }
  };
  const body = { innerHTML: '', scrollHeight: 100, scrollTop: 100, clientHeight: 100,
    addEventListener() {} };
  const send = { disabled: false, textContent: '', addEventListener() {} };
  const imageButton = { disabled: false, title: '', addEventListener() {} };
  const document = {
    hidden: false, title: '',
    querySelector(selector) {
      return { '[data-chat-input]': input, '[data-chat-body]': body,
        '[data-chat-send]': send, '[data-chat-image]': imageButton,
        '.chat-mobile-rename': options.mobileHeader ? mobileRename : null,
        '.mobile-bar-actions': options.mobileHeader ? mobileActions : null }[selector] || null;
    },
    querySelectorAll(selector) {
      return options.mobileHeader && selector === '[data-chat-rename]' ? [desktopRename, mobileRename] : [];
    }, getElementById() { return null; },
    addEventListener() {}
  };
  const Lan = {
    util: {
      esc: value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
      readJson: () => ({ id: 'room', me: '127.0.0.1', name: 'test-room' }),
      downloadUrl() {}, copyText() {}
    },
    icons: { icon: () => '' },
    ui: { toast: (...args) => toasts.push(args), openModal: modal => modals.push(modal), openMenu() {} },
    api: { fetch: fetchImpl, json: response => Promise.resolve(response.data), write() {} },
    lightbox: { open() {} }, chat: {}
  };
  const window = { Lan, crypto: crypto.webcrypto, addEventListener() {} };
  class EventSource {
    constructor(url) { this.url = url; this.readyState = 0; this.closed = false; streams.push(this); }
    addEventListener() {}
    close() { this.closed = true; this.readyState = 2; }
  }
  if (options.eventSource) window.EventSource = EventSource;
  vm.runInNewContext(source, {
    window, document, console, Promise, Uint8Array, URL,
    EventSource,
    sessionStorage: { getItem: () => null, setItem() {} },
    setInterval(callback, interval) { const id = ++nextTimer; timers.set(id, { callback, interval }); return id; },
    clearInterval(id) { timers.delete(id); }, setTimeout, clearTimeout
  });
  return { Lan, input, body, send, imageButton, toasts, listeners, timers, streams,
    modals, renameEvents, desktopRename, mobileRename, mobileActions };
}

async function test(name, callback) {
  await callback(); passed++;
  console.log('PASS ' + name);
}

async function main() {
  await test('network failure preserves the exact text and prepared image', async () => {
    const h = harness(() => Promise.reject(new Error('offline')));
    h.input.value = '  important draft\n\tstill here  \n';
    const image = { status: 'ready', id: 'prepared', upload: 'prepared' };
    h.Lan.chat.state.media = image;
    h.Lan.chat.send(); await ticks();
    assert.equal(h.input.value, '  important draft\n\tstill here  \n');
    assert.equal(h.Lan.chat.state.media, image);
    assert.equal(h.Lan.chat.state.sending, false);
    assert.equal(h.send.disabled, false);
    assert.match(h.toasts[0][0], /草稿和图片已保留/);
  });

  await test('HTTP error preserves draft and reuses idempotency key on retry', async () => {
    const requests = [];
    const h = harness((url, options) => {
      requests.push(JSON.parse(options.body));
      return Promise.resolve({ ok: false, status: 403, data: { success: false, message: 'refused' } });
    });
    h.input.value = '  refused\n\tvalue';
    h.Lan.chat.send(); await ticks();
    h.Lan.chat.send(); await ticks();
    assert.equal(h.input.value, '  refused\n\tvalue');
    assert.equal(requests[0].text, h.input.value);
    assert.equal(requests[0].client_id, requests[1].client_id);
  });

  await test('success clears only the sent draft, not newly typed text', async () => {
    let resolve;
    const h = harness(() => new Promise(done => { resolve = done; }));
    h.input.value = 'sent';
    h.Lan.chat.send();
    h.Lan.chat.send(); // disabled action cannot issue a second request
    h.input.value = 'new text while waiting';
    resolve({ ok: true, data: { success: true, msg: { id: 'ok', text: 'sent', sender: 'me' } } });
    await ticks();
    assert.equal(h.input.value, 'new text while waiting');
    assert.equal(h.Lan.chat.state.msgs.length, 1);
    assert.equal(h.Lan.chat.state.sending, false);
  });

  await test('paste inserts plain text with tabs, spaces and normalized line breaks', async () => {
    const h = harness(url => Promise.resolve({ data: url.includes('/chat/history') ?
      { success: true, reset: true, messages: [], room: { name: 'room' }, revision: 'epoch:0' } :
      { success: true, formats: ['PNG'], available: true } }));
    h.Lan.chat.init(); await ticks();
    h.input.value = 'beforeafter'; h.input.selectionStart = h.input.selectionEnd = 6;
    let prevented = false;
    h.listeners.paste({
      preventDefault() { prevented = true; },
      clipboardData: { items: [], getData(type) {
        return type === 'text/plain' ? '  copied\r\n\t  indented\n<script>not HTML</script>' : '<b>ignored HTML</b>';
      } }
    });
    assert.equal(prevented, true);
    assert.equal(h.input.value, 'before  copied\n\t  indented\n<script>not HTML</script>after');
    h.Lan.chat.state.msgs = [{ id: 'text', sender: 'other', text: h.input.value }];
    h.Lan.chat.render();
    assert.match(h.body.innerHTML, /  copied\n\t  indented/);
    assert.match(h.body.innerHTML, /&lt;script&gt;not HTML&lt;\/script&gt;/);
    assert.doesNotMatch(h.body.innerHTML, /<script>/);
  });

  await test('render contract uses pre-wrap and tab-size without the 5MB gate', () => {
    assert.match(css, /\.msg-text\s*\{[^}]*white-space:\s*pre-wrap;[^}]*tab-size:\s*4;/);
    assert.match(css, /\.composer textarea\s*\{[^}]*white-space:\s*pre-wrap;[^}]*tab-size:\s*4;/);
    assert.doesNotMatch(source, /file\.size\s*>\s*5\s*\*/);
    assert.doesNotMatch(source, /readAsDataURL/);
  });

  await test('delete, clear, reset and stale revisions apply consistently', () => {
    const h = harness(() => Promise.resolve({ data: {} }));
    h.Lan.chat.applySnapshot({ success: true, messages: [{ id: 'one' }], revision: 'e:0', room: { name: 'test' } });
    h.Lan.chat.applyEvent('delete', { id: 'one' }, 'e:1');
    assert.equal(h.Lan.chat.state.msgs.length, 0);
    h.Lan.chat.applyEvent('message', { id: 'old' }, 'e:0');
    assert.equal(h.Lan.chat.state.msgs.length, 0);
    h.Lan.chat.applyEvent('message', { id: 'two' }, 'e:2');
    h.Lan.chat.applyEvent('clear', {}, 'e:3');
    assert.equal(h.Lan.chat.state.msgs.length, 0);
    h.Lan.chat.applyEvent('reset', { success: true, messages: [{ id: 'current' }], revision: 'e:5' }, 'e:5');
    assert.equal(h.Lan.chat.state.msgs[0].id, 'current');
  });

  await test('composer removes the permanent hints rather than hiding functional controls', () => {
    assert.doesNotMatch(roomTemplate, /composer-hint|chat-media-help|data-online|data-media-help/);
    assert.match(roomTemplate, /data-chat-input/);
    assert.match(roomTemplate, /data-chat-image/);
    assert.match(roomTemplate, /data-chat-send/);
    assert.match(roomTemplate, /data-image-preview hidden/);
    const composer = roomTemplate.slice(roomTemplate.indexOf('<div class="composer-wrap">'),
      roomTemplate.indexOf('<script type="application/json"'));
    assert.doesNotMatch(composer, /data-chat-rename/);
    assert.doesNotMatch(source, /data-online|data-media-help/);
  });

  await test('mobile rename moves to the top bar before binding the shared desktop action', async () => {
    const h = harness(url => Promise.resolve({ data: url.includes('/chat/history') ?
      { success: true, reset: true, messages: [], room: { name: 'test-room' }, revision: 'epoch:0' } :
      { success: true, available: true, formats: ['PNG'] } }), { mobileHeader: true });
    h.Lan.chat.init(); await ticks();
    assert.equal(h.mobileRename.parentNode, h.mobileActions);
    assert.deepEqual(h.mobileActions.children, [h.mobileRename]);
    assert.deepEqual(h.renameEvents, ['move-mobile', 'bind-desktop', 'bind-mobile']);
    const header = roomTemplate.slice(roomTemplate.indexOf('<header class="chat-head'),
      roomTemplate.indexOf('</header>'));
    assert.equal((header.match(/data-chat-rename/g) || []).length, 2);
    for (const button of [h.desktopRename, h.mobileRename]) {
      button.listeners.click();
      const modal = h.modals.at(-1);
      assert.equal(modal.title, '\u4fee\u6539\u7fa4\u540d');
      assert.equal(modal.field.value, 'test-room');
      assert.equal(modal.validate('valid room'), '');
      assert.ok(modal.validate(''));
      assert.equal(typeof modal.onConfirm, 'function');
    }
    assert.equal(h.modals.length, 2);
    assert.equal(h.Lan.chat.state.room, 'room');
  });

  await test('missing image decoder still disables uploads without a help element', async () => {
    for (const available of [false, true]) {
      const h = harness(url => Promise.resolve({ data: url.includes('/chat/history') ?
        { success: true, reset: true, messages: [], revision: 'epoch:0' } :
        { success: true, available, formats: available ? ['PNG'] : [] } }));
      h.Lan.chat.init(); await ticks();
      assert.equal(h.imageButton.disabled, !available);
      if (!available) assert.match(h.imageButton.title, /服务器暂不支持图片上传/);
      assert.equal(h.toasts.length, 0);
    }
  });

  await test('SSE reconnect and polling fallback do not need a visible status element', async () => {
    const h = harness(url => Promise.resolve({ data: url.includes('/chat/history') ?
      { success: true, reset: true, messages: [], revision: 'epoch:0' } :
      { success: true, available: true, formats: ['PNG'] } }), { eventSource: true });
    h.Lan.chat.init(); await ticks();
    assert.equal(h.streams.length, 1);
    const first = h.streams[0];
    first.readyState = 1; first.onopen();
    assert.equal(h.timers.size, 0);
    first.onerror(); first.onerror(); first.onerror();
    assert.equal(first.closed, true);
    assert.equal(h.Lan.chat.state.es, null);
    assert.equal(h.timers.size, 1);
    assert.equal([...h.timers.values()][0].interval, 2000);
    h.Lan.chat.streamStart();
    const second = h.streams[1];
    second.readyState = 1; second.onopen();
    assert.equal(h.Lan.chat.state.es, second);
    assert.equal(h.timers.size, 0);
    h.Lan.chat.streamStop();
    assert.equal(second.closed, true);
  });
  console.log('\n' + passed + ' frontend regression tests passed.');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
