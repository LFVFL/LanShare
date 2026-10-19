  /* ───────────── 模块：文件页（Lan.files） ───────────── */

  (function (Lan) {
    'use strict';

    /* 依赖：显式从命名空间取，缺一个就报 ReferenceError */
    var icon = Lan.icons.icon;
    var toast = Lan.ui.toast;
    var openModal = Lan.ui.openModal;
    var openMenu = Lan.ui.openMenu;
    var apiFetch = Lan.api.fetch;
    var jsonOf = Lan.api.json;
    var writeFetch = Lan.api.write;
    var esc = Lan.util.esc;
    var copyText = Lan.util.copyText;
    var downloadUrl = Lan.util.downloadUrl;
    var readJson = Lan.util.readJson;

    /* ───────────── 文件页 ───────────── */

    var TONE = {
      folder: 'folder', image: 'image', video: 'video', audio: 'audio',
      archive: 'archive', doc: 'doc', code: 'code'
    };
    var fsState = {
      path: '', entries: [], sortKey: 'name', sortDir: 'asc',
      view: 'list', query: '', sel: new Set(),
      total: 0, hasMore: false
    };

    /* 共享内搜索（服务端递归）的状态。和上面的 query 不是一回事：
       query 是当前目录的内存过滤，search 是"整个共享找一遍"。 */
    var searchState = { active: false, query: '', entries: [], truncated: false, scanned: 0 };

    function visibleEntries() {
      var q = fsState.query.trim().toLowerCase();
      var rows = fsState.entries.filter(function (e) {
        return !q || e.name.toLowerCase().indexOf(q) !== -1;
      });
      rows.sort(function (a, b) {
        var r;
        if (fsState.sortKey === 'name') r = a.name.localeCompare(b.name, 'zh-CN');
        else if (fsState.sortKey === 'size') r = a.size - b.size;
        else r = a.mtime - b.mtime;
        return fsState.sortDir === 'asc' ? r : -r;
      });
      rows.sort(function (a, b) {
        return (a.kind === 'folder' ? 0 : 1) - (b.kind === 'folder' ? 0 : 1);
      });
      return rows;
    }

    function rowHtml(e) {
      var tone = TONE[e.kind] || 'other';
      var sel = fsState.sel.has(e.enc) ? ' data-selected="true"' : '';
      // 文件夹不放下载按钮（点一下压大半个目录太容易误触），只留占位保持对齐
      var dl = e.kind === 'folder'
        ? '<span class="row-dl row-dl--empty" aria-hidden="true"></span>'
        : '<a class="row-dl" href="/download?file=' + esc(e.enc) +
          '&path=' + encodeURIComponent(fsState.path) +
          '" download title="下载" aria-label="下载 ' + esc(e.name) + '">' +
          icon('download', 'icon--sm') + '</a>';
      return '<div class="row" data-name="' + esc(e.name) + '" data-enc="' + esc(e.enc) +
        '" data-kind="' + e.kind + '"' + sel + ' tabindex="0">' +
        '<input type="checkbox" class="cbx" aria-label="选择 ' + esc(e.name) + '"' +
          (sel ? ' checked' : '') + '>' +
        '<span class="row-icon" data-tone="' + tone + '">' + icon(TONE[tone] || 'file') + '</span>' +
        '<div class="row-main">' +
          '<div class="row-name" title="' + esc(e.name) + '">' + esc(e.name) + '</div>' +
          '<div class="row-sub">' + esc(e.sizeText) + ' · ' + esc(e.timeText) + '</div>' +
        '</div>' +
        '<div class="row-size tnum">' + esc(e.sizeText) + '</div>' +
        '<div class="row-time tnum">' + esc(e.timeText) + '</div>' +
        dl +
        '<button class="row-more" data-menu-trigger aria-label="更多操作" aria-expanded="false">' +
          icon('more') + '</button>' +
        '</div>';
    }

    function tileHtml(e) {
      var tone = TONE[e.kind] || 'other';
      var sel = fsState.sel.has(e.enc) ? ' data-selected="true"' : '';
      return '<div class="tile" data-name="' + esc(e.name) + '" data-enc="' + esc(e.enc) +
        '" data-kind="' + e.kind + '" data-tone="' + tone + '"' + sel + '>' +
        '<input type="checkbox" class="cbx"' + (sel ? ' checked' : '') + '>' +
        '<div class="tile-thumb">' + icon(TONE[tone] || 'file') + '</div>' +
        '<div class="tile-name">' + esc(e.name) + '</div>' +
        '<div class="tile-sub">' + esc(e.kind === 'folder' ? e.sizeText : e.sizeText) + '</div>' +
        '</div>';
    }

    function renderFiles() {
      if (searchState.active) { return; }
      var list = document.querySelector('[data-files-list]');
      var emptyEl = document.querySelector('[data-files-empty]');
      var head = document.querySelector('[data-files-head]');
      var countEl = document.querySelector('[data-files-count]');
      if (!list) return;

      var rows = visibleEntries();

      if (!rows.length) {
        list.innerHTML = '';
        list.hidden = true;
        if (emptyEl) {
          emptyEl.hidden = false;
          var t = emptyEl.querySelector('[data-empty-title]');
          var d = emptyEl.querySelector('[data-empty-desc]');
          if (t) t.textContent = fsState.query ? '没有找到匹配的文件' : '这个文件夹是空的';
          if (d) d.textContent = fsState.query
            ? '换个关键词，或清除搜索条件'
            : '把文件拖进这个页面，或点右上角的「上传」';
        }
      } else {
        if (emptyEl) emptyEl.hidden = true;
        list.hidden = false;
        if (fsState.view === 'grid') {
          list.className = 'grid';
          list.innerHTML = rows.map(tileHtml).join('');
          if (head) head.hidden = true;
        } else {
          list.className = 'list';
          list.innerHTML = rows.map(rowHtml).join('');
          if (head) head.hidden = false;
        }
      }

      if (countEl) {
        var folders = fsState.entries.filter(function (e) { return e.kind === 'folder'; }).length;
        countEl.textContent = fsState.query
          ? rows.length + ' 个匹配'
          : fsState.entries.length + ' 项 · ' + folders + ' 个文件夹';
      }

      updateHead();
      updateSelbar();
      syncSelectAll();
    }

    /* ───────────── 共享内搜索 ───────────── */

    function searchNote() {
      return document.querySelector('[data-search-note]');
    }

    function searchExit() {
      searchState.active = false;
      searchState.entries = [];
      var note = searchNote();
      if (note) note.remove();
      var list = document.querySelector('[data-files-list]');
      if (list) list.className = fsState.view === 'grid' ? 'grid' : 'list';
      renderFiles();
    }

    function searchRun(raw) {
      var query = (raw || '').trim();
      if (!query) { searchExit(); return; }
      searchState.query = query;

      apiFetch('/api/search?q=' + encodeURIComponent(query) +
        '&path=' + encodeURIComponent(fsState.path))
        .then(jsonOf)
        .then(function (d) {
          if (!d || !d.success) {
            toast((d && d.message) || '搜索失败', 'err');
            return;
          }
          searchState.active = true;
          searchState.entries = d.entries || [];
          searchState.truncated = !!d.truncated;
          searchState.scanned = d.scanned || 0;
          renderSearchResults();
        })
        .catch(function () { toast('网络中断', 'err'); });
    }

    function searchRowHtml(e) {
      var tone = TONE[e.kind] || 'other';
      var where = e.dir ? e.dir : '根目录';
      return '<a class="row row--hit" href="' + esc(e.dirHref) + '">' +
        '<span class="row-icon" data-tone="' + tone + '">' + icon(TONE[tone] || 'file') + '</span>' +
        '<div class="row-main">' +
          '<div class="row-name">' + esc(e.name) + '</div>' +
          '<div class="row-sub">' + esc(where) + (e.kind === 'folder' ? ' · 文件夹' : '') + '</div>' +
        '</div>' +
        '<div class="row-size tnum">' + esc(e.sizeText) + '</div>' +
        '</a>';
    }

    function renderSearchResults() {
      var list = document.querySelector('[data-files-list]');
      var emptyEl = document.querySelector('[data-files-empty]');
      if (!list) return;

      var hits = searchState.entries;
      var note = searchNote();
      if (!note) {
        note = document.createElement('div');
        note.className = 'search-note';
        note.setAttribute('data-search-note', '');
        list.parentNode.insertBefore(note, list);
      }
      note.innerHTML = '共享内搜索「' + esc(searchState.query) + '」：' +
        hits.length + ' 条' +
        (searchState.truncated ? '（结果过多已截断）' : '') +
        ' · 扫描 ' + searchState.scanned + ' 项' +
        ' <button class="btn btn--sm btn--ghost" type="button" data-search-exit>回到当前目录</button>';
      note.querySelector('[data-search-exit]').addEventListener('click', function () {
        var input = document.querySelector('[data-files-search]');
        if (input) input.value = '';
        fsState.query = '';
        searchExit();
      });

      if (!hits.length) {
        list.innerHTML = '';
        list.hidden = true;
        if (emptyEl) {
          emptyEl.hidden = false;
          var t = emptyEl.querySelector('[data-empty-title]');
          var d = emptyEl.querySelector('[data-empty-desc]');
          if (t) t.textContent = '共享里没有找到「' + searchState.query + '」';
          if (d) d.textContent = '换个关键词试试，或点上面的「回到当前目录」';
        }
        return;
      }

      if (emptyEl) emptyEl.hidden = true;
      list.hidden = false;
      list.className = 'list';
      list.innerHTML = hits.map(searchRowHtml).join('');
      var countEl = document.querySelector('[data-files-count]');
      if (countEl) countEl.textContent = hits.length + ' 个结果（共享内搜索）';
    }

    function updateHead() {
      var head = document.querySelector('[data-files-head]');
      if (!head) return;
      head.querySelectorAll('[data-sort]').forEach(function (btn) {
        if (btn.getAttribute('data-sort') === fsState.sortKey) {
          btn.setAttribute('data-dir', fsState.sortDir);
        } else {
          btn.removeAttribute('data-dir');
        }
      });
    }

    function updateSelbar() {
      var bar = document.querySelector('[data-selbar]');
      if (!bar) return;
      var n = fsState.sel.size;
      bar.setAttribute('data-open', n ? 'true' : 'false');
      var c = bar.querySelector('.selbar-count');
      if (c) c.innerHTML = '已选 <b>' + n + '</b> 项';
    }

    function syncSelectAll() {
      var all = document.querySelector('[data-select-all]');
      if (!all) return;
      var rows = visibleEntries();
      var n = rows.filter(function (e) { return fsState.sel.has(e.enc); }).length;
      all.checked = n > 0 && n === rows.length;
      all.indeterminate = n > 0 && n < rows.length;
    }

    function entryByEnc(enc) {
      return fsState.entries.find(function (e) { return e.enc === enc; });
    }

    function q(extra) {
      return 'path=' + encodeURIComponent(fsState.path) + extra;
    }

    /* 分页拉取：超大目录（几万项）一次性塞进 DOM 会把手机卡死，
       所以服务端按 limit 切片，前端只保留"已加载"的部分。 */
    var PAGE_SIZE = 400;

    function loadPage(offset, append) {
      var url = '/api/list?path=' + encodeURIComponent(fsState.path) +
        '&limit=' + PAGE_SIZE + '&offset=' + offset;
      return apiFetch(url).then(jsonOf).then(function (d) {
        if (!d || !d.entries) return null;
        fsState.entries = append ? fsState.entries.concat(d.entries) : d.entries;
        fsState.total = d.total || fsState.entries.length;
        fsState.hasMore = !!d.hasMore;
        renderFiles();
        updateLoadMore();
        return d;
      });
    }

    function updateLoadMore() {
      var bar = document.querySelector('[data-load-more]');
      if (!bar) return;
      var rest = (fsState.total || 0) - fsState.entries.length;
      bar.hidden = !(fsState.hasMore && rest > 0);
      var label = bar.querySelector('[data-load-more-text]');
      if (label) label.textContent = '还有 ' + rest + ' 项，点这里继续加载';
    }

    function refreshFiles() {
      return loadPage(0, false).catch(function () {
        toast('刷新列表失败', 'err');
      });
    }

    function doZip(names) {
      if (!names || !names.length) { toast('请先勾选', 'err'); return; }
      var body = new URLSearchParams();
      body.append('path', fsState.path);
      names.forEach(function (n) { body.append('files[]', n); });
      toast('正在打包…', 'info');
      apiFetch('/zip', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: body.toString()
      }).then(jsonOf).then(function (d) {
        if (d && d.success) location.href = d.download_url;
        else toast('打包失败', 'err');
      }).catch(function () { toast('网络中断', 'err'); });
    }

    /* 批量移动 / 复制：目标目录用相对当前目录的路径（可以写 docs、../ 之类） */
    function askRelocate(copy) {
      var encs = Array.from(fsState.sel);
      if (!encs.length) { toast('请先勾选', 'err'); return; }
      var names = encs.map(function (enc) {
        var e = entryByEnc(enc);
        return e ? e.name : null;
      }).filter(Boolean);

      openModal({
        title: copy ? '复制到' : '移动到',
        icon: copy ? 'copy' : 'folder',
        confirmText: copy ? '复制 ' + names.length + ' 项' : '移动 ' + names.length + ' 项',
        desc: '目标文件夹（相对当前目录，例如 <code>docs</code>、<code>docs/2024</code>）' +
              '<br>不存在的目录会自动创建；不支持 <code>..</code>，要移到上级请先返回上级再操作。',
        field: { label: '目标文件夹', value: '', placeholder: 'docs/2024' },
        validate: function (v) {
          if (!v) return '请填写目标文件夹';
          if (v.split(/[\\/]/).indexOf('..') >= 0) return '目标路径不能包含 ..';
          return '';
        },
        onConfirm: function (v) {
          var dst = v.trim();
          var body = new URLSearchParams();
          body.append('path', fsState.path);
          body.append('dst', dst);
          names.forEach(function (n) { body.append('name', n); });
          writeFetch('/' + (copy ? 'copy' : 'move'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: body.toString()
          }).then(function (r) { return r.ok ? jsonOf(r) : null; })
            .then(function (d) {
              if (d && d.success) {
                var ok = (d.moved || []).length;
                var bad = (d.skipped || []).length;
                toast((copy ? '已复制 ' : '已移动 ') + ok + ' 项' +
                      (bad ? '，跳过 ' + bad + ' 项' : ''), bad ? 'info' : 'ok');
                fsState.sel.clear();
                refreshFiles();
              } else {
                toast((d && d.message) || '操作失败', 'err');
              }
            })
            .catch(function () { toast('网络中断', 'err'); });
        }
      });
    }

    function askDelete(entry, after) {
      openModal({
        title: entry.kind === 'folder' ? '删除文件夹' : '删除文件',
        tone: 'danger', icon: 'trash', confirmText: '删除',
        desc: '确定删除 <strong>' + esc(entry.name) + '</strong> 吗？' +
          (entry.kind === 'folder' ? '<br>文件夹里的内容会一起删除。' : '') + '此操作不可撤销。',
        onConfirm: function () {
          writeFetch('/delete?file=' + encodeURIComponent(entry.enc) + '&' + q(''), { method: 'POST' })
            .then(function (r) { return r.ok ? jsonOf(r) : null; })
            .then(function (d) {
              if (d && d.success) { toast('已删除 ' + entry.name, 'ok'); after && after(); }
              else toast('删除失败', 'err');
            })
            .catch(function () { toast('网络中断', 'err'); });
        }
      });
    }

    function askRename(entry, after) {
      openModal({
        title: '重命名', icon: 'edit', confirmText: '保存',
        desc: '为 <strong>' + esc(entry.name) + '</strong> 输入新名称。',
        field: { label: '名称', value: entry.name },
        validate: function (v) {
          if (!v) return '名称不能为空';
          if (/[\\/:*?"<>|]/.test(v)) return '名称不能包含 \\ / : * ? " < > |';
          if (v === entry.name) return '名称没有变化';
          return '';
        },
        onConfirm: function (v) {
          writeFetch('/rename?old=' + encodeURIComponent(entry.enc) +
            '&new=' + encodeURIComponent(v) + '&' + q(''), { method: 'POST' })
            .then(function (r) { return r.ok ? jsonOf(r) : null; })
            .then(function (d) {
              if (d && d.success) { toast('已重命名', 'ok'); after && after(); }
              else toast('重命名失败', 'err');
            })
            .catch(function () { toast('网络中断', 'err'); });
        }
      });
    }

    function menuFor(entry) {    var items = [];
      var dl = '/download?file=' + encodeURIComponent(entry.enc) + '&path=' + encodeURIComponent(fsState.path);

      if (entry.kind === 'folder') {
        items.push({
          key: 'open', label: '打开', icon: 'folder',
          onSelect: function () { location.href = entry.href; }
        });
        items.push({
          key: 'zip', label: '打包下载', icon: 'download',
          onSelect: function () { doZip([entry.name]); }
        });
      } else {
        items.push({
          key: 'preview', label: '预览', icon: 'eye',
          onSelect: function () { window.open(dl + '&preview=1', '_blank'); }
        });
        items.push({
          key: 'download', label: '下载', icon: 'download',
          onSelect: function () { location.href = dl; }
        });
      }
      items.push({ sep: true });
      items.push({
        key: 'rename', label: '重命名', icon: 'edit',
        onSelect: function () { askRename(entry, refreshFiles); }
      });
      items.push({
        key: 'delete', label: '删除', icon: 'trash', danger: true,
        onSelect: function () { askDelete(entry, refreshFiles); }
      });
      return items;
    }

    function bindFilesList() {
      var list = document.querySelector('[data-files-list]');
      if (!list) return;

      list.addEventListener('click', function (e) {
        var more = e.target.closest('[data-menu-trigger]');
        if (more) {
          var entry = entryByEnc(more.closest('[data-enc]').getAttribute('data-enc'));
          if (entry) openMenu(more, menuFor(entry));
          return;
        }
        var nameEl = e.target.closest('.row-name');
        if (nameEl) {
          var e2 = entryByEnc(nameEl.closest('[data-enc]').getAttribute('data-enc'));
          if (e2 && e2.kind === 'folder') location.href = e2.href;
        }
      });

      list.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter') return;
        var host = e.target.closest('[data-enc]');
        if (!host) return;
        var entry = entryByEnc(host.getAttribute('data-enc'));
        if (entry && entry.kind === 'folder') location.href = entry.href;
      });

      list.addEventListener('change', function (e) {
        var cb = e.target.closest('.cbx');
        if (!cb) return;
        var host = cb.closest('[data-enc]');
        var enc = host.getAttribute('data-enc');
        if (cb.checked) fsState.sel.add(enc); else fsState.sel.delete(enc);
        host.setAttribute('data-selected', cb.checked ? 'true' : 'false');
        updateSelbar();
        syncSelectAll();
      });
    }

    function bindToolbar() {
      var head = document.querySelector('[data-files-head]');
      if (head) {
        head.addEventListener('click', function (e) {
          var btn = e.target.closest('[data-sort]');
          if (!btn) return;
          var key = btn.getAttribute('data-sort');
          if (fsState.sortKey === key) {
            fsState.sortDir = fsState.sortDir === 'asc' ? 'desc' : 'asc';
          } else {
            fsState.sortKey = key;
            fsState.sortDir = 'asc';
          }
          renderFiles();
        });
      }

      var all = document.querySelector('[data-select-all]');
      if (all) {
        all.addEventListener('change', function () {
          var rows = visibleEntries();
          if (all.checked) rows.forEach(function (e) { fsState.sel.add(e.enc); });
          else fsState.sel.clear();
          renderFiles();
        });
      }

      document.querySelectorAll('[data-files-search]').forEach(function (input) {
        input.addEventListener('input', function () {
          // 正在看共享内搜索结果时，一改输入就退回本地过滤
          if (searchState.active) searchExit();
          fsState.query = input.value;
          fsState.sel.clear();
          document.querySelectorAll('[data-files-search]').forEach(function (o) {
            if (o !== input) o.value = input.value;
          });
          renderFiles();
        });
        // 回车 = 在整个共享里找；Esc = 退出搜索
        input.addEventListener('keydown', function (e) {
          if (e.key === 'Enter') {
            e.preventDefault();
            searchRun(input.value);
          } else if (e.key === 'Escape') {
            input.value = '';
            fsState.query = '';
            searchExit();
          }
        });
      });

      document.querySelectorAll('[data-view]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          fsState.view = btn.getAttribute('data-view');
          document.querySelectorAll('[data-view]').forEach(function (b) {
            b.setAttribute('aria-pressed', b === btn ? 'true' : 'false');
          });
          renderFiles();
        });
      });

      var more = document.querySelector('[data-load-more]');
      if (more) {
        more.addEventListener('click', function () {
          more.hidden = true;
          loadPage(fsState.entries.length, true).catch(function () {
            toast('加载更多失败', 'err');
            updateLoadMore();
          });
        });
      }

      var bar = document.querySelector('[data-selbar]');
      if (bar) {
        bar.querySelector('[data-selbar-clear]').addEventListener('click', function () {
          fsState.sel.clear();
          renderFiles();
        });
        bar.querySelector('[data-selbar-zip]').addEventListener('click', function () {
          var names = Array.from(fsState.sel).map(function (enc) {
            var e = entryByEnc(enc);
            return e ? e.name : null;
          }).filter(Boolean);
          doZip(names);
        });
        bar.querySelector('[data-selbar-move]').addEventListener('click', function () {
          askRelocate(false);
        });
        bar.querySelector('[data-selbar-copy]').addEventListener('click', function () {
          askRelocate(true);
        });
        bar.querySelector('[data-selbar-del]').addEventListener('click', function () {
          var encs = Array.from(fsState.sel);
          var n = encs.length;
          if (!n) return;
          openModal({
            title: '批量删除', tone: 'danger', icon: 'trash', confirmText: '删除 ' + n + ' 项',
            desc: '确定删除选中的 <strong>' + n + '</strong> 项吗？此操作不可撤销。',
            onConfirm: function () {
              var chain = Promise.resolve();
              encs.forEach(function (enc) {
                chain = chain.then(function () {
                  return writeFetch('/delete?file=' + encodeURIComponent(enc) + '&' + q(''), { method: 'POST' });
                });
              });
              chain.then(function () {
                fsState.sel.clear();
                toast('已删除 ' + n + ' 项', 'ok');
                refreshFiles();
              }).catch(function () { toast('部分删除失败', 'err'); refreshFiles(); });
            }
          });
        });
      }
    }


    /* 对外只暴露这些 */
    Object.assign(Lan.files, { state: fsState, search: searchState, render: renderFiles, refresh: refreshFiles, run: searchRun, exit: searchExit, zip: doZip, relocate: askRelocate, visible: visibleEntries, bindList: bindFilesList, bindToolbar: bindToolbar, q: q });
  })(window.Lan = window.Lan || {});
