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
      path: '', root: '', entries: [], sortKey: 'name', sortDir: 'asc',
      view: 'list', query: '', sel: new Set(),
      total: 0, hasMore: false, scope: 'subtree'
    };

    /* Pagination, filtering and sorting share the server's global ordering. */
    var searchState = { active: false, query: '', entries: [], truncated: false, scanned: 0 };
    var searchTimer = null;
    var searchController = null;
    var searchRequestId = 0;
    var listRequestId = 0;

    function visibleEntries() {
      return fsState.entries.slice();
    }

    function fileActionsHtml(e, path) {
      if (e.kind === 'folder') {
        return '<span class="row-actions row-dl--empty" aria-hidden="true"></span>';
      }
      var dl = '/download?file=' + encodeURIComponent(e.name) + '&path=' + encodeURIComponent(path);
      var preview = e.previewable
        ? '<a class="row-preview" href="' + esc(dl + '&preview=1') + '" target="_blank" rel="noopener"' +
          ' title="预览" aria-label="预览 ' + esc(e.name) + '">' + icon('eye', 'icon--sm') + '</a>'
        : '<button class="row-preview" type="button" disabled title="此格式不支持浏览器直接预览，请下载后打开"' +
          ' aria-label="无法直接预览 ' + esc(e.name) + '">' + icon('eye', 'icon--sm') + '</button>';
      return '<span class="row-actions">' + preview +
        '<a class="row-dl" href="' + esc(dl) + '" download title="下载" aria-label="下载 ' + esc(e.name) + '">' +
        icon('download', 'icon--sm') + '</a></span>';
    }

    function rowHtml(e) {
      var tone = TONE[e.kind] || 'other';
      var sel = fsState.sel.has(e.enc) ? ' data-selected="true"' : '';
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
        fileActionsHtml(e, fsState.path) +
        '<button class="row-more" data-menu-trigger aria-label="更多操作" aria-expanded="false">' +
          icon('more') + '</button>' +
        '</div>';
    }

    function tileHtml(e) {
      var tone = TONE[e.kind] || 'other';
      var sel = fsState.sel.has(e.enc) ? ' data-selected="true"' : '';
      var hit = e.kind === 'folder'
        ? '<a class="tile-hit" href="' + esc(e.href) + '" aria-label="打开文件夹 ' + esc(e.name) + '"></a>'
        : '';
      return '<div class="tile" data-name="' + esc(e.name) + '" data-enc="' + esc(e.enc) +
        '" data-kind="' + e.kind + '" data-tone="' + tone + '"' + sel + '>' +
        '<input type="checkbox" class="cbx" aria-label="选择 ' + esc(e.name) + '"' +
          (sel ? ' checked' : '') + '>' +
        hit +
        '<div class="tile-thumb">' + icon(TONE[tone] || 'file') + '</div>' +
        '<div class="tile-name" title="' + esc(e.name) + '">' + esc(e.name) + '</div>' +
        '<div class="tile-sub"><span>' + esc(e.kind === 'folder' ? '文件夹' : e.sizeText) +
          '</span><span class="tile-time">' + esc(e.timeText) + '</span></div>' +
        '<div class="tile-actions">' + fileActionsHtml(e, fsState.path) +
          '<button class="row-more" data-menu-trigger aria-label="更多操作" aria-expanded="false">' +
          icon('more') + '</button></div>' +
        '</div>';
    }

    function renderFiles() {
      if (typeof Lan.files.syncSearchScopes === 'function') Lan.files.syncSearchScopes();
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
          ? '当前目录匹配 ' + fsState.total + ' 项 · 已加载 ' + rows.length + ' 项'
          : fsState.total + ' 项 · 已加载 ' + fsState.entries.length + ' 项（' + folders + ' 个文件夹）';
      }

      updateHead();
      updateSelbar();
      syncSelectAll();
      updateLoadMore();
    }

    /* ───────────── 共享内搜索 ───────────── */

    function searchNote() {
      return document.querySelector('[data-search-note]');
    }

    function searchExit() {
      clearTimeout(searchTimer);
      searchRequestId++;
      if (searchController) searchController.abort();
      searchController = null;
      searchState.active = false;
      searchState.entries = [];
      searchState.query = '';
      fsState.query = '';
      fsState.sel.clear();
      document.querySelectorAll('[data-files-search]').forEach(function (input) { input.value = ''; });
      var note = searchNote();
      if (note) note.remove();
      var list = document.querySelector('[data-files-list]');
      if (list) list.className = fsState.view === 'grid' ? 'grid' : 'list';
      setSearchStatus('');
      return refreshFiles();
    }

    function setSearchStatus(message) {
      document.querySelectorAll('[data-files-search-status]').forEach(function (node) {
        node.textContent = message;
      });
    }

    function scopeLabel(scope) {
      return { current: '当前目录', subtree: '当前目录及子目录', share: '全部共享' }[scope] || '当前目录及子目录';
    }

    function searchRun(raw) {
      clearTimeout(searchTimer);
      var query = (raw || '').trim();
      if (!query) { searchExit(); return; }
      fsState.query = query;
      fsState.sel.clear();
      searchState.query = query;
      var requestId = ++searchRequestId;
      if (searchController) searchController.abort();
      searchController = null;
      listRequestId++;
      if (fsState.scope === 'current') {
        searchState.active = false;
        var note = searchNote();
        if (note) note.remove();
        setSearchStatus('正在搜索当前目录…');
        return loadPage(0, false).then(function () {
          if (requestId === searchRequestId) setSearchStatus('当前目录 · ' + fsState.total + ' 个匹配');
        }).catch(function (error) {
          if (requestId !== searchRequestId) return;
          setSearchStatus('搜索失败，请重试');
          toast(error.message || '网络中断', 'err');
        });
      }
      searchController = typeof AbortController !== 'undefined' ? new AbortController() : null;
      var opts = searchController ? { signal: searchController.signal } : {};
      setSearchStatus('正在搜索' + scopeLabel(fsState.scope) + '…');

      return apiFetch('/api/search?q=' + encodeURIComponent(query) +
        '&path=' + encodeURIComponent(fsState.path) + '&scope=' + fsState.scope +
        '&sort=' + fsState.sortKey + '&order=' + fsState.sortDir, opts)
        .then(function (response) {
          return jsonOf(response).then(function (data) {
            if (!response.ok) throw new Error((data && data.message) || '搜索失败');
            return data;
          });
        })
        .then(function (d) {
          if (requestId !== searchRequestId) return;
          if (!d || !d.success) {
            toast((d && d.message) || '搜索失败', 'err');
            return;
          }
          searchState.active = true;
          searchState.entries = d.entries || [];
          searchState.truncated = !!d.truncated;
          searchState.scanned = d.scanned || 0;
          searchState.scope = d.scope || fsState.scope;
          setSearchStatus(scopeLabel(searchState.scope) + ' · ' + searchState.entries.length +
            ' 个结果' + (d.truncated ? '（未搜索完毕）' : ''));
          renderSearchResults();
        })
        .catch(function (error) {
          if (requestId !== searchRequestId || error.name === 'AbortError') return;
          setSearchStatus('搜索失败，请重试');
          toast(error.message || '网络中断', 'err');
        });
    }

    function searchRowHtml(e) {
      var tone = TONE[e.kind] || 'other';
      var where = e.dir ? e.dir : '根目录';
      return '<div class="row row--hit">' +
        '<span class="row-icon" data-tone="' + tone + '">' + icon(TONE[tone] || 'file') + '</span>' +
        '<a class="row-main row-hit-main" href="' + esc(e.dirHref) + '" title="打开所在目录">' +
          '<div class="row-name">' + esc(e.name) + '</div>' +
          '<div class="row-sub">' + esc(where) + (e.kind === 'folder' ? ' · 文件夹' : '') + '</div>' +
        '</a>' +
        '<div class="row-size tnum">' + esc(e.sizeText) + '</div>' +
        fileActionsHtml(e, e.path || fsState.root) + '</div>';
    }

    function renderSearchResults() {
      var list = document.querySelector('[data-files-list]');
      var emptyEl = document.querySelector('[data-files-empty]');
      if (!list) return;

      var hits = searchState.entries;
      var countEl = document.querySelector('[data-files-count]');
      if (countEl) countEl.textContent = hits.length + ' 个结果（' + scopeLabel(searchState.scope) + '）';
      updateHead();
      syncSelectAll();
      updateLoadMore();
      var note = searchNote();
      if (!note) {
        note = document.createElement('div');
        note.className = 'search-note';
        note.setAttribute('data-search-note', '');
        list.parentNode.insertBefore(note, list);
      }
      note.innerHTML = scopeLabel(searchState.scope) + '搜索「' + esc(searchState.query) + '」：' +
        hits.length + ' 条' +
        (searchState.truncated ? '（已达搜索上限，请缩小范围或细化关键词）' : '') +
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
          if (t) t.textContent = scopeLabel(searchState.scope) + '没有找到「' + searchState.query + '」';
          if (d) d.textContent = '换个关键词试试，或点上面的「回到当前目录」';
        }
        return;
      }

      if (emptyEl) emptyEl.hidden = true;
      list.hidden = false;
      list.className = 'list';
      list.innerHTML = hits.map(searchRowHtml).join('');
      updateSelbar();
      updateLoadMore();
    }

    function updateHead() {
      // 表头（桌面）与排序条（手机）都要同步当前排序状态
      document.querySelectorAll('[data-sort]').forEach(function (btn) {
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
      all.disabled = searchState.active;
      if (searchState.active) { all.checked = false; all.indeterminate = false; return; }
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
      var requestId = ++listRequestId;
      var requestedPath = fsState.path;
      var query = fsState.scope === 'current' ? fsState.query.trim() : '';
      var url = '/api/list?path=' + encodeURIComponent(fsState.path) +
        '&limit=' + PAGE_SIZE + '&offset=' + offset +
        '&sort=' + fsState.sortKey + '&order=' + fsState.sortDir +
        '&q=' + encodeURIComponent(query);
      return apiFetch(url).then(function (response) {
        return jsonOf(response).then(function (data) {
          if (!response.ok) throw new Error((data && data.message) || '列表加载失败');
          return data;
        });
      }).then(function (d) {
        if (requestId !== listRequestId) return null;
        if (!d || !d.entries) return null;
        // A root switch invalidates an old page offset as well as its entries.
        if (append && d.path && d.path !== requestedPath) {
          fsState.path = d.path;
          fsState.sel.clear();
          toast('共享目录已变更，已回到新目录', 'info');
          return loadPage(0, false);
        }
        // 桌面端可能在运行中换了共享目录：服务端会把越界的 path 归到新根，
        // 这里跟着回到新目录，别让页面一直指着一个已经不存在的旧路径
        if (!append && d.path && d.path !== fsState.path) {
          fsState.path = d.path;
          fsState.sel.clear();
          toast('共享目录已变更，已回到新目录', 'info');
        }
        fsState.entries = append ? fsState.entries.concat(d.entries) : d.entries;
        fsState.root = d.root || fsState.root;
        fsState.total = typeof d.total === 'number' ? d.total : fsState.entries.length;
        fsState.hasMore = !!d.hasMore;
        fsState.sortKey = d.sort || fsState.sortKey;
        fsState.sortDir = d.order || fsState.sortDir;
        if (d.breadcrumb) {
          var crumbs = document.querySelector('.topbar .crumbs');
          if (crumbs) crumbs.innerHTML = d.breadcrumb;
        }
        renderFiles();
        updateLoadMore();
        return d;
      });
    }

    function updateLoadMore() {
      var bar = document.querySelector('[data-load-more]');
      if (!bar) return;
      var rest = (fsState.total || 0) - fsState.entries.length;
      bar.hidden = searchState.active || !(fsState.hasMore && rest > 0);
      var label = bar.querySelector('[data-load-more-text]');
      if (label) label.textContent = '还有 ' + rest + ' 项，点这里继续加载';
    }

    function refreshFiles() {
      if (searchState.active && fsState.query.trim()) return searchRun(fsState.query);
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
        if (entry.previewable) items.push({
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
        // Grid folder cards use a native link above; this fallback is for list rows.
        var host = e.target.closest('.row[data-enc]');
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
      // 排序：表头（桌面）和排序条（手机）共用一套 data-sort，用文档级委托统一处理
      document.querySelectorAll('[data-sort]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          var key = btn.getAttribute('data-sort');
          if (fsState.sortKey === key) {
            fsState.sortDir = fsState.sortDir === 'asc' ? 'desc' : 'asc';
          } else {
            fsState.sortKey = key;
            fsState.sortDir = 'asc';
          }
          if (fsState.query.trim()) searchRun(fsState.query);
          else refreshFiles();
        });
      });

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
          clearTimeout(searchTimer);
          searchRequestId++;
          listRequestId++;
          if (searchController) searchController.abort();
          fsState.query = input.value;
          fsState.sel.clear();
          document.querySelectorAll('[data-files-search]').forEach(function (o) {
            if (o !== input) o.value = input.value;
          });
          if (!input.value.trim()) { searchExit(); return; }
          setSearchStatus('输入后自动搜索，也可以按回车');
          searchTimer = setTimeout(function () { searchRun(input.value); }, 320);
        });
        // Enter and the visible button share the selected scope on mobile too.
        input.addEventListener('keydown', function (e) {
          if (e.key === 'Enter') {
            e.preventDefault();
            searchRun(input.value);
          } else if (e.key === 'Escape') {
            searchExit();
          }
        });
      });

      document.querySelectorAll('[data-files-search-submit]').forEach(function (button) {
        button.addEventListener('click', function () { searchRun(fsState.query); });
      });
      document.querySelectorAll('[data-files-search-scope]').forEach(function (select) {
        select.value = fsState.scope;
        select.addEventListener('change', function () {
          fsState.scope = select.value;
          document.querySelectorAll('[data-files-search-scope]').forEach(function (other) {
            other.value = fsState.scope;
          });
          if (fsState.query.trim()) searchRun(fsState.query);
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
              var succeeded = 0;
              var failed = 0;
              var cancelled = false;
              encs.forEach(function (enc) {
                chain = chain.then(function () {
                  if (cancelled) return;
                  return writeFetch('/delete?file=' + encodeURIComponent(enc) + '&' + q(''), { method: 'POST' })
                    .then(function (response) {
                      return jsonOf(response).then(function (data) {
                        if (response.ok && data && data.success) {
                          succeeded++;
                          fsState.sel.delete(enc);
                        } else {
                          failed++;
                          if (response.status === 401 || response.status === 403) cancelled = true;
                        }
                      });
                    }).catch(function () { failed++; cancelled = true; });
                });
              });
              chain.then(function () {
                var remaining = n - succeeded - failed;
                toast('已删除 ' + succeeded + ' 项' + (failed ? '，失败 ' + failed + ' 项' : '') +
                  (remaining ? '，未执行 ' + remaining + ' 项' : ''), failed || remaining ? 'err' : 'ok');
                refreshFiles();
              });
            }
          });
        });
      }
    }


    /* 对外只暴露这些 */
    Object.assign(Lan.files, { state: fsState, search: searchState, render: renderFiles, refresh: refreshFiles, run: searchRun, exit: searchExit, zip: doZip, relocate: askRelocate, visible: visibleEntries, bindList: bindFilesList, bindToolbar: bindToolbar, q: q });
  })(window.Lan = window.Lan || {});
