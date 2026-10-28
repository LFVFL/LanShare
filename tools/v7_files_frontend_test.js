'use strict';

// Behavioral VM tests execute the real modules; this is not a browser/layout test.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..');
const args = process.argv.slice(2);
if (args.length && (args.length !== 2 || args[0] !== '--results' || !args[1])) {
  throw new Error('Usage: node tools/v7_files_frontend_test.js [--results DIRECTORY]');
}
const RESULTS = args.length ? path.resolve(args[1]) :
  path.join(ROOT, 'docs', 'changes', '2026-10-28-path-and-chat', 'test-results');
const MODULES = ['40-api.js', '50-files.js', '72-files-page.js'];
const SOURCES = MODULES.map(name => ({
  name,
  source: fs.readFileSync(path.join(ROOT, 'web', 'js', name), 'utf8')
}));
const PAGE = fs.readFileSync(path.join(ROOT, 'web', 'pages', 'files.html'), 'utf8');
const SEARCH_CSS = fs.readFileSync(path.join(ROOT, 'web', 'files-search.css'), 'utf8');
const SCOPE_MARKUP = Array.from(PAGE.matchAll(/<select\b[^>]*data-files-search-scope[^>]*>([\s\S]*?)<\/select>/g), match => match[1]);
const SHARE = '/synthetic/shared';
const TEXT = {
  item: ' \u9879',
  deleted: '\u5df2\u5220\u9664 ',
  failed: '\uff0c\u5931\u8d25 ',
  remaining: '\uff0c\u672a\u6267\u884c ',
  searchFailed: '\u641c\u7d22\u5931\u8d25\uff0c\u8bf7\u91cd\u8bd5',
  inputHint: '\u8f93\u5165\u540e\u81ea\u52a8\u641c\u7d22\uff0c\u4e5f\u53ef\u4ee5\u6309\u56de\u8f66',
  rootChanged: '\u5171\u4eab\u76ee\u5f55\u5df2\u53d8\u66f4\uff0c\u5df2\u56de\u5230\u65b0\u76ee\u5f55',
  currentScope: '\u4ec5\u5f53\u524d\u76ee\u5f55',
  subtreeScope: '\u5f53\u524d\u76ee\u5f55\u53ca\u5b50\u76ee\u5f55',
  shareScope: '\u6574\u4e2a\u5171\u4eab\u76ee\u5f55'
};

class Element {
  constructor(document, attributes = {}, tagName = 'div') {
    this.document = document;
    this.tagName = tagName.toUpperCase();
    this.attributes = { ...attributes };
    this.children = new Map();
    this.options = [];
    this.listeners = new Map();
    this.textContent = '';
    this.value = '';
    this.className = '';
    this.hidden = false;
    this.checked = false;
    this.disabled = false;
    this.indeterminate = false;
    this.parentNode = null;
    this._html = '';
  }

  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return Object.hasOwn(this.attributes, name) ? this.attributes[name] : null; }
  removeAttribute(name) { delete this.attributes[name]; }
  querySelector(selector) {
    const option = /^option\[value="([^"]+)"\]$/.exec(selector);
    return option ? this.options.find(node => node.value === option[1]) || null :
      this.children.get(selector) || null;
  }
  querySelectorAll(selector) { const child = this.querySelector(selector); return child ? [child] : []; }
  set value(value) {
    this._value = this.tagName !== 'SELECT' || this.options.some(option => option.value === String(value))
      ? String(value) : '';
  }
  get value() { return this._value; }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }
  dispatch(type, values = {}) {
    const event = { target: this, preventDefault() {}, stopPropagation() {}, ...values };
    for (const listener of this.listeners.get(type) || []) listener(event);
  }
  insertBefore(child) {
    child.parentNode = this;
    if (child.getAttribute('data-search-note') !== null) {
      this.document.register('[data-search-note]', child);
    }
  }
  appendChild(child) {
    child.parentNode = this;
    if (child.tagName === 'OPTION') this.options.push(child);
    return child;
  }
  remove() {
    if (this.parentNode) {
      this.parentNode.options = this.parentNode.options.filter(option => option !== this);
    }
    this.document.unregister(this);
    this.parentNode = null;
  }
  set innerHTML(value) {
    this._html = String(value);
    if (this._html.includes('data-search-exit')) {
      this.children.set('[data-search-exit]', new Element(this.document));
    }
  }
  get innerHTML() { return this._html; }
}

class Document {
  constructor() { this.selectors = new Map(); }
  register(selector, element) {
    if (!this.selectors.has(selector)) this.selectors.set(selector, []);
    this.selectors.get(selector).push(element);
    return element;
  }
  unregister(element) {
    for (const [selector, values] of this.selectors) {
      this.selectors.set(selector, values.filter(value => value !== element));
    }
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) { return this.selectors.get(selector) || []; }
  createElement(tagName = 'div') { return new Element(this, {}, tagName); }
  add(selector, attributes = {}) { return this.register(selector, new Element(this, attributes)); }
}

class Timers {
  constructor() { this.now = 0; this.next = 1; this.tasks = new Map(); }
  setTimeout(fn, delay) {
    const id = this.next++;
    this.tasks.set(id, { time: this.now + Number(delay || 0), fn });
    return id;
  }
  clearTimeout(id) { this.tasks.delete(id); }
  advance(ms) {
    this.now += ms;
    for (;;) {
      const next = Array.from(this.tasks.entries())
        .filter(([, task]) => task.time <= this.now)
        .sort((left, right) => left[1].time - right[1].time)[0];
      if (!next) break;
      this.tasks.delete(next[0]);
      next[1].fn();
    }
  }
}

function entry(name, directory = SHARE) {
  return {
    name, enc: encodeURIComponent(name), kind: 'doc', size: 12, mtime: 1700000000000,
    sizeText: '12 B', timeText: 'synthetic time', previewable: true,
    path: directory, dir: '', dirHref: '/dir?path=' + encodeURIComponent(directory),
    href: '/download?file=' + encodeURIComponent(name) + '&path=' + encodeURIComponent(directory)
  };
}

function listData(entries, changes = {}) {
  return {
    path: SHARE, root: SHARE, entries, total: entries.length,
    hasMore: false, offset: 0, sort: 'name', order: 'asc', query: '',
    breadcrumb: '<span>synthetic root</span>', ...changes
  };
}

function searchData(name, scope = 'subtree') {
  return { success: true, entries: [entry(name)], count: 1, scanned: 5, scope, truncated: false };
}

function response(status, data) {
  return { status, ok: status >= 200 && status < 300, json: () => Promise.resolve(data) };
}

async function flush(turns = 32) {
  for (let index = 0; index < turns; index++) await Promise.resolve();
}

async function until(predicate, description) {
  for (let index = 0; index < 40; index++) {
    if (predicate()) { await flush(); return; }
    await flush();
  }
  throw new Error('Unsettled VM behavior: ' + description);
}

function environment(initial = listData([entry('initial.txt')])) {
  const document = new Document();
  const timers = new Timers();
  const dom = {};
  for (const [key, selector] of Object.entries({
    list: '[data-files-list]', empty: '[data-files-empty]', head: '[data-files-head]',
    count: '[data-files-count]', selectAll: '[data-select-all]', more: '[data-load-more]',
    selbar: '[data-selbar]', crumbs: '.topbar .crumbs', clear: '[data-empty-clear]'
  })) dom[key] = document.add(selector);
  dom.list.parentNode = new Element(document);
  for (const selector of ['[data-empty-title]', '[data-empty-desc]']) {
    dom.empty.children.set(selector, new Element(document));
  }
  dom.more.children.set('[data-load-more-text]', new Element(document));
  for (const selector of ['.selbar-count', '[data-selbar-clear]', '[data-selbar-zip]',
    '[data-selbar-move]', '[data-selbar-copy]', '[data-selbar-del]']) {
    dom.selbar.children.set(selector, new Element(document));
  }
  dom.inputs = [document.add('[data-files-search]'), document.add('[data-files-search]')];
  dom.statuses = [document.add('[data-files-search-status]'), document.add('[data-files-search-status]')];
  assert.equal(SCOPE_MARKUP.length, 2, 'The real file page must provide desktop and mobile scope selectors');
  dom.scopes = SCOPE_MARKUP.map(markup => {
    const select = document.createElement('select');
    let selected = '';
    for (const match of markup.matchAll(/<option\s+value="([^"]+)"([^>]*)>([^<]*)<\/option>/g)) {
      const option = document.createElement('option');
      option.value = match[1];
      option.textContent = match[3];
      select.appendChild(option);
      if (/\bselected\b/.test(match[2])) selected = option.value;
    }
    select.value = selected;
    return document.register('[data-files-search-scope]', select);
  });
  dom.submit = [document.add('[data-files-search-submit]'), document.add('[data-files-search-submit]')];
  for (const sort of ['name', 'size', 'mtime']) document.add('[data-sort]', { 'data-sort': sort });
  for (const view of ['list', 'grid']) document.add('[data-view]', { 'data-view': view });

  const env = { document, timers, dom, requests: [], toasts: [], modals: [], responder: null,
    modalResponder: null, uploadInitializations: 0, folderInitializations: 0 };
  const fetch = (url, options = {}) => {
    const request = { url, options, method: options.method || 'GET', signal: options.signal };
    env.requests.push(request);
    // Deliberately ignore an aborted signal: request IDs must also reject stale responses.
    if (env.responder) {
      try { return Promise.resolve(env.responder(request)); }
      catch (error) { return Promise.reject(error); }
    }
    return new Promise((resolve, reject) => {
      request.respond = (status, data) => resolve(response(status, data));
      request.reject = error => reject(error);
    });
  };
  const Lan = {
    icons: { icon: name => '<svg data-icon="' + name + '"></svg>' },
    ui: {
      toast: (message, tone) => env.toasts.push({ message, tone }),
      openModal: options => {
        env.modals.push(options);
        if (env.modalResponder) env.modalResponder(options);
      },
      openMenu() {}
    },
    api: {}, files: {}, pages: {},
    util: {
      esc: value => String(value == null ? '' : value).replace(/&/g, '&amp;')
        .replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
      readJson: selector => {
        assert.equal(selector, '[data-fs-json]');
        return JSON.parse(JSON.stringify(initial));
      },
      copyText() {}, downloadUrl() {}
    },
    upload: {
      init: () => env.uploadInitializations++, initFolder: () => env.folderInitializations++
    }
  };
  const window = { Lan, open() {} };
  const context = vm.createContext({
    window, document, fetch, AbortController, URLSearchParams, console,
    location: { href: '' },
    setTimeout: timers.setTimeout.bind(timers), clearTimeout: timers.clearTimeout.bind(timers)
  });
  for (const module of SOURCES) vm.runInContext(module.source, context, { filename: module.name });
  Lan.pages.files();
  env.files = Lan.files;
  env.api = Lan.api;
  env.Lan = Lan;
  return env;
}

function queryOf(request) { return new URL(request.url, 'http://synthetic.invalid').searchParams; }
function assertStatuses(env, message) {
  assert.deepEqual(env.dom.statuses.map(node => node.textContent), [message, message]);
}
function assertScopes(env, expected, message) {
  const options = expected.map(value => [value, TEXT[value + 'Scope']]);
  for (const select of env.dom.scopes) {
    assert.deepEqual(select.options.map(option => [option.value, option.textContent]), options, message);
    assert.equal(select.value, env.files.state.scope, message);
  }
}
function summaryText(successes, failures, remaining) {
  return TEXT.deleted + successes + TEXT.item +
    (failures ? TEXT.failed + failures + TEXT.item : '') +
    (remaining ? TEXT.remaining + remaining + TEXT.item : '');
}

const tests = [];
function test(name, fn) { tests.push({ name, fn }); }

test('Root shows two accurate scopes on desktop/mobile and keeps recursive search as default', async () => {
  const env = environment();
  assert.equal(env.files.state.scope, 'subtree');
  assertScopes(env, ['current', 'subtree']);
  for (let index = 0; index < 3; index++) env.files.render();
  assertScopes(env, ['current', 'subtree'], 'Repeated renders must not recreate a redundant choice');
  const pending = env.files.run('needle');
  assert.ok(env.requests[0].url.startsWith('/api/search?'));
  assert.equal(queryOf(env.requests[0]).get('scope'), 'subtree');
  assert.equal(queryOf(env.requests[0]).get('path'), SHARE);
  env.requests[0].respond(200, searchData('needle.txt'));
  await pending;
  assertScopes(env, ['current', 'subtree']);
});

test('Nested scope choices stay distinct and mobile whole-share selection preserves the request contract', async () => {
  const nested = SHARE + '/nested';
  const env = environment(listData([entry('nested.txt', nested)], { path: nested }));
  assertScopes(env, ['current', 'subtree', 'share']);
  for (const scope of ['current', 'subtree', 'share']) {
    env.dom.scopes[1].value = scope;
    env.dom.scopes[1].dispatch('change');
    assert.equal(env.files.state.scope, scope);
    assertScopes(env, ['current', 'subtree', 'share']);
  }
  for (let index = 0; index < 3; index++) env.files.render();
  assertScopes(env, ['current', 'subtree', 'share'], 'Repeated renders must not duplicate options');
  const pending = env.files.run('report');
  assert.ok(env.requests[0].url.startsWith('/api/search?'));
  assert.equal(queryOf(env.requests[0]).get('scope'), 'share');
  assert.equal(queryOf(env.requests[0]).get('path'), nested);
  env.requests[0].respond(200, searchData('report-other.txt', 'share'));
  await pending;
  assert.equal(env.files.search.scope, 'share');
  assertScopes(env, ['current', 'subtree', 'share']);
});

test('Root equality follows resolved Windows drive/UNC semantics without prefix or POSIX case guessing', () => {
  const fixtures = [
    { path: 'c:/shared/', root: 'C:\\Shared', atRoot: true },
    { path: 'c:/', root: 'C:\\', atRoot: true },
    { path: '//nas/share/', root: '\\\\NAS\\Share', atRoot: true },
    { path: 'e:/\u5171\u4eab/\u8d44\u6599/', root: 'E:\\\u5171\u4eab\\\u8d44\u6599', atRoot: true },
    { path: 'C:\\Shared\\nested', root: 'C:\\Shared', atRoot: false },
    { path: 'C:\\Shared-more', root: 'C:\\Shared', atRoot: false },
    { path: '\\\\NAS\\Share\\nested', root: '//nas/share', atRoot: false },
    { path: '/', root: '/', atRoot: true },
    { path: '/synthetic/SHARED', root: SHARE, atRoot: false },
    { path: '/synthetic/data/name', root: '/synthetic/data\\name', atRoot: false },
    { path: SHARE + '/nested', root: SHARE, atRoot: false }
  ];
  for (const fixture of fixtures) {
    const env = environment(listData([], fixture));
    assertScopes(env, fixture.atRoot ? ['current', 'subtree'] : ['current', 'subtree', 'share'],
      JSON.stringify(fixture));
  }
});

test('A share selection at root normalizes before rendering even with active search results', () => {
  const env = environment();
  env.files.state.scope = 'share';
  env.files.search.active = true;
  env.files.render();
  assert.equal(env.files.state.scope, 'subtree');
  assertScopes(env, ['current', 'subtree']);
});

test('API root switch normalizes a nested whole-share selection and synchronizes both selectors', async () => {
  const nested = SHARE + '/nested';
  const env = environment(listData([entry('old.txt', nested)], { path: nested }));
  env.dom.scopes[1].value = 'share';
  env.dom.scopes[1].dispatch('change');
  assert.equal(env.files.state.scope, 'share');
  const pending = env.files.refresh();
  const newRoot = '/synthetic/new-root';
  env.requests[0].respond(200, listData([entry('new.txt', newRoot)], { path: newRoot, root: newRoot }));
  await pending;
  assert.equal(env.files.state.path, newRoot);
  assert.equal(env.files.state.root, newRoot);
  assert.equal(env.files.state.scope, 'subtree');
  assertScopes(env, ['current', 'subtree']);
});

test('API expansion of the shared root restores the third choice without changing the current directory', async () => {
  const env = environment();
  assertScopes(env, ['current', 'subtree']);
  const pending = env.files.refresh();
  env.requests[0].respond(200, listData([entry('same.txt')], { root: '/synthetic' }));
  await pending;
  assert.equal(env.files.state.path, SHARE);
  assert.equal(env.files.state.scope, 'subtree');
  assertScopes(env, ['current', 'subtree', 'share']);
  env.dom.scopes[0].value = 'share';
  env.dom.scopes[0].dispatch('change');
  assert.equal(env.files.state.scope, 'share');
  assertScopes(env, ['current', 'subtree', 'share']);
});

test('File rendering still works when the page-specific scope hook is absent', () => {
  const env = environment();
  delete env.files.syncSearchScopes;
  assert.doesNotThrow(() => env.files.render());
  assert.ok(env.dom.list.innerHTML.includes('initial.txt'));
});

test('URL query initializer restores both inputs and preserves query/sort on append', async () => {
  const initial = [entry('needle-a.txt'), entry('needle-b.txt')];
  const env = environment(listData(initial, { query: 'needle', sort: 'size', order: 'desc', total: 3, hasMore: true }));
  assert.equal(env.files.state.query, 'needle');
  assert.equal(env.files.state.scope, 'current');
  assert.deepEqual(env.dom.inputs.map(node => node.value), ['needle', 'needle']);
  assert.deepEqual(env.dom.scopes.map(node => node.value), ['current', 'current']);
  assert.equal(env.uploadInitializations, 1);
  assert.equal(env.folderInitializations, 1);
  env.dom.more.dispatch('click');
  assert.equal(env.requests.length, 1);
  const request = env.requests[0];
  assert.equal(request.options.credentials, 'same-origin');
  assert.equal(queryOf(request).get('q'), 'needle');
  assert.equal(queryOf(request).get('offset'), '2');
  assert.equal(queryOf(request).get('limit'), '400');
  assert.equal(queryOf(request).get('sort'), 'size');
  assert.equal(queryOf(request).get('order'), 'desc');
  request.respond(200, listData([entry('needle-c.txt')], { total: 3, sort: 'size', order: 'desc', query: 'needle', offset: 2 }));
  await until(() => env.files.state.entries.length === 3, 'filtered append');
  assert.deepEqual(Array.from(env.files.state.entries, item => item.name), ['needle-a.txt', 'needle-b.txt', 'needle-c.txt']);
  assert.equal(env.dom.more.hidden, true);
});

test('Older recursive search response cannot overwrite the newest response even when fetch ignores abort', async () => {
  const env = environment();
  const older = env.files.run('older');
  const first = env.requests[0];
  const newer = env.files.run('newer');
  assert.equal(first.signal.aborted, true);
  env.requests[1].respond(200, searchData('newer-hit.txt'));
  await newer;
  first.respond(200, searchData('older-hit.txt'));
  await older;
  assert.equal(env.files.search.query, 'newer');
  assert.equal(env.files.search.entries[0].name, 'newer-hit.txt');
  assert.ok(env.dom.list.innerHTML.includes('newer-hit.txt'));
  assert.ok(!env.dom.list.innerHTML.includes('older-hit.txt'));
});

test('Typing invalidates an in-flight recursive search before the debounce fires', async () => {
  const env = environment();
  const pending = env.files.run('older');
  const first = env.requests[0];
  env.dom.inputs[1].value = 'typed-newer';
  env.dom.inputs[1].dispatch('input');
  assert.equal(first.signal.aborted, true);
  assert.deepEqual(env.dom.inputs.map(node => node.value), ['typed-newer', 'typed-newer']);
  first.respond(200, searchData('older-hit.txt'));
  await pending;
  assert.equal(env.files.search.active, false);
  assert.ok(!env.dom.list.innerHTML.includes('older-hit.txt'));
  assertStatuses(env, TEXT.inputHint);
  env.timers.advance(319);
  assert.equal(env.requests.length, 1);
  env.timers.advance(1);
  assert.equal(env.requests.length, 2);
  assert.equal(queryOf(env.requests[1]).get('q'), 'typed-newer');
  env.requests[1].respond(200, searchData('typed-newer-hit.txt'));
  await until(() => env.files.search.active, 'debounced recursive search');
  assert.equal(env.files.search.entries[0].name, 'typed-newer-hit.txt');
});

test('Typing invalidates an in-flight current-directory list/filter before debounce', async () => {
  const env = environment();
  env.files.state.scope = 'current';
  const pending = env.files.run('older');
  env.dom.inputs[0].value = 'typed-newer';
  env.dom.inputs[0].dispatch('input');
  env.requests[0].respond(200, listData([entry('older-hit.txt')]));
  await pending;
  assert.equal(env.files.state.entries[0].name, 'initial.txt');
  assertStatuses(env, TEXT.inputHint);
  env.timers.advance(320);
  assert.equal(env.requests.length, 2);
  assert.equal(queryOf(env.requests[1]).get('q'), 'typed-newer');
  env.requests[1].respond(200, listData([entry('typed-newer-hit.txt')], { query: 'typed-newer' }));
  await until(() => env.files.state.entries[0].name === 'typed-newer-hit.txt', 'debounced current-directory filter');
  assert.equal(env.files.state.query, 'typed-newer');
});

for (const status of [422, 503]) {
  test('Current-directory HTTP ' + status + ' renders failure instead of a successful match count', async () => {
    const env = environment();
    env.files.state.scope = 'current';
    const pending = env.files.run('needle');
    assert.equal(queryOf(env.requests[0]).get('q'), 'needle');
    env.requests[0].respond(status, { success: false, message: 'synthetic-http-' + status });
    await pending;
    assertStatuses(env, TEXT.searchFailed);
    assert.equal(env.files.state.entries[0].name, 'initial.txt');
    assert.deepEqual(env.toasts, [{ message: 'synthetic-http-' + status, tone: 'err' }]);
  });
}

test('Root changed during append discards old offset and refetches the new root from zero', async () => {
  const original = [entry('old-first.txt'), entry('old-second.txt')];
  const env = environment(listData(original, { total: 5, hasMore: true }));
  env.files.state.sel.add(original[0].enc);
  env.dom.more.dispatch('click');
  assert.equal(queryOf(env.requests[0]).get('offset'), '2');
  const newRoot = '/synthetic/new-shared';
  env.requests[0].respond(200, listData([entry('new-tail-from-wrong-offset.txt', newRoot)], {
    path: newRoot, root: newRoot, offset: 2, total: 5, hasMore: true
  }));
  await until(() => env.requests.length === 2, 'root switch refetch');
  assert.equal(queryOf(env.requests[1]).get('path'), newRoot);
  assert.equal(queryOf(env.requests[1]).get('offset'), '0');
  assert.equal(env.files.state.sel.size, 0);
  env.requests[1].respond(200, listData([entry('new-first.txt', newRoot), entry('new-second.txt', newRoot)], {
    path: newRoot, root: newRoot, breadcrumb: '<span>new-root</span>'
  }));
  await until(() => env.files.state.root === newRoot, 'new root application');
  assert.deepEqual(Array.from(env.files.state.entries, item => item.name), ['new-first.txt', 'new-second.txt']);
  assert.ok(!env.dom.list.innerHTML.includes('old-first.txt'));
  assert.ok(!env.dom.list.innerHTML.includes('new-tail-from-wrong-offset.txt'));
  assert.equal(env.dom.crumbs.innerHTML, '<span>new-root</span>');
  assertScopes(env, ['current', 'subtree']);
  assert.deepEqual(env.toasts, [{ message: TEXT.rootChanged, tone: 'info' }]);
});

test('Mobile scope and Enter submit use the same current-directory server search contract', async () => {
  const env = environment();
  env.dom.scopes[1].value = 'current';
  env.dom.scopes[1].dispatch('change');
  assert.deepEqual(env.dom.scopes.map(node => node.value), ['current', 'current']);
  env.dom.inputs[1].value = '\u4e2d\u6587';
  env.dom.inputs[1].dispatch('input');
  env.dom.inputs[1].dispatch('keydown', { key: 'Enter' });
  assert.equal(env.requests.length, 1);
  assert.ok(env.requests[0].url.startsWith('/api/list?'));
  assert.equal(queryOf(env.requests[0]).get('q'), '\u4e2d\u6587');
  env.requests[0].respond(200, listData([entry('\u4e2d\u6587.txt')], { query: '\u4e2d\u6587' }));
  await until(() => env.files.state.entries[0].name === '\u4e2d\u6587.txt', 'mobile Enter search');
  env.timers.advance(320);
  assert.equal(env.requests.length, 1, 'Enter cancels the outstanding debounce');
});

async function batch(sequence, options = {}) {
  const original = ['alpha.txt', '\u4e2d\u6587.txt', 'gamma.txt', 'delta.txt'].map(name => entry(name));
  const env = environment(listData(original));
  const deleted = new Set();
  const outcomes = sequence.slice();
  env.responder = request => {
    if (request.method === 'POST' && request.url.startsWith('/delete?')) {
      assert.ok(outcomes.length, 'Unexpected extra delete request');
      const outcome = outcomes.shift();
      if (outcome === 'network') throw new Error('synthetic network loss');
      const [status, success] = outcome;
      if (status >= 200 && status < 300 && success) deleted.add(queryOf(request).get('file'));
      return response(status, { success, message: success ? '' : 'synthetic failure' });
    }
    if (request.method === 'GET' && request.url.startsWith('/api/list?')) {
      return response(200, listData(original.filter(item => !deleted.has(item.enc))));
    }
    throw new Error('Unexpected request: ' + request.method + ' ' + request.url);
  };
  env.modalResponder = modal => {
    if (options.cancelAdmin && modal.field && modal.field.type === 'password') {
      queueMicrotask(() => modal.onCancel());
    }
  };
  env.dom.selectAll.checked = true;
  env.dom.selectAll.dispatch('change');
  assert.equal(env.files.state.sel.size, 4);
  env.dom.selbar.querySelector('[data-selbar-del]').dispatch('click');
  assert.equal(env.modals.length, 1);
  if (options.cancelInitial) {
    const modal = env.modals[0];
    if (modal.onCancel) modal.onCancel();
    await flush();
  } else {
    env.modals[0].onConfirm();
    await until(() => env.toasts.some(toast => toast.message.startsWith(TEXT.deleted)), 'batch-delete result');
  }
  return { env, original, deleted };
}

function assertBatch(result, successes, failures, remaining, requests, selectedIndices) {
  const { env, original } = result;
  assert.deepEqual(env.toasts.filter(toast => toast.message.startsWith(TEXT.deleted)), [{
    message: summaryText(successes, failures, remaining), tone: failures || remaining ? 'err' : 'ok'
  }]);
  const writes = env.requests.filter(request => request.method === 'POST');
  assert.equal(writes.length, requests);
  assert.ok(writes.every(request => request.url.startsWith('/delete?')));
  assert.ok(writes.every(request => request.options.credentials === 'same-origin'));
  assert.equal(env.requests.filter(request => request.method === 'GET').length, 1);
  assert.deepEqual(Array.from(env.files.state.sel), selectedIndices.map(index => original[index].enc));
  assert.equal(env.files.state.entries.length, 4 - successes);
}

test('Batch all-success reports four actual deletions and clears only successful selections', async () => {
  assertBatch(await batch([[200, true], [200, true], [200, true], [200, true]]), 4, 0, 0, 4, []);
});

test('Batch first 403 stops writes and truthfully reports failed and unexecuted items', async () => {
  assertBatch(await batch([[403, false]]), 0, 1, 3, 1, [0, 1, 2, 3]);
});

test('Batch 500 retains its failed selection but continues later successful deletions', async () => {
  assertBatch(await batch([[500, false], [200, true], [200, true], [200, true]]), 3, 1, 0, 4, [0]);
});

test('Batch network interruption after one success stops and retains failed/unexecuted selections', async () => {
  assertBatch(await batch([[200, true], 'network']), 1, 1, 2, 2, [1, 2, 3]);
});

test('Batch mixed 200 success:false and HTTP 500 are not counted as deleted', async () => {
  assertBatch(await batch([[200, false], [500, false], [200, true], [200, true]]), 2, 2, 0, 4, [0, 1]);
});

test('Cancelling the original delete confirmation performs no request and keeps all selections', async () => {
  const { env, original } = await batch([], { cancelInitial: true });
  assert.equal(env.requests.length, 0);
  assert.equal(env.toasts.length, 0);
  assert.deepEqual(Array.from(env.files.state.sel), original.map(item => item.enc));
});

test('Real Lan.api.write 401 administrator cancellation stops the batch without false successes', async () => {
  const result = await batch([[401, false]], { cancelAdmin: true });
  assertBatch(result, 0, 1, 3, 1, [0, 1, 2, 3]);
  assert.equal(result.env.modals.length, 2);
  assert.equal(result.env.modals[1].field.type, 'password');
  assert.equal(result.env.api.ctx.isAdmin, false);
});

async function main() {
  const started = Date.now();
  const cases = [];
  const lines = [];
  for (const item of tests) {
    try {
      await item.fn();
      cases.push({ name: item.name, status: 'passed' });
      lines.push('PASS ' + item.name);
    } catch (error) {
      const detail = String(error.stack || error).split(ROOT).join('<checkout>');
      cases.push({ name: item.name, status: 'failed', detail });
      lines.push('FAIL ' + item.name + '\n' + detail);
    }
  }
  const failures = cases.filter(item => item.status === 'failed').length;
  const report = {
    timestamp: new Date().toISOString(), runtime: process.version,
    scope: 'Actual API/files/initializer modules in an isolated Node VM with a minimal fake DOM; not browser rendering.',
    tests: cases.length, passed: cases.length - failures, failures, success: failures === 0,
    durationMs: Date.now() - started,
    modules: SOURCES.map(module => ({ name: module.name,
      sha256: crypto.createHash('sha256').update(module.source).digest('hex') })),
    assets: [{ name: 'web/pages/files.html', source: PAGE }, { name: 'web/files-search.css', source: SEARCH_CSS }]
      .map(asset => ({ name: asset.name, sha256: crypto.createHash('sha256').update(asset.source).digest('hex') })),
    cases
  };
  lines.push('\n' + report.passed + '/' + report.tests + ' passed; ' + failures + ' failed.');
  fs.mkdirSync(RESULTS, { recursive: true });
  fs.writeFileSync(path.join(RESULTS, 'files-frontend.log'), lines.join('\n') + '\n', 'utf8');
  fs.writeFileSync(path.join(RESULTS, 'files-frontend.json'), JSON.stringify(report, null, 2) + '\n', 'utf8');
  console.log(lines.join('\n'));
  process.exitCode = failures ? 1 : 0;
}

main().catch(error => { console.error(error); process.exitCode = 1; });
