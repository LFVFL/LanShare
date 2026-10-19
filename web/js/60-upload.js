  /* ───────────── 模块：上传队列（Lan.upload） ───────────── */

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
    var ensureAdmin = Lan.api.ensureAdmin;
    var esc = Lan.util.esc;
    var q = Lan.files.q;
    var refreshFiles = Lan.files.refresh;
    var fsState = Lan.files.state;

    /* ───────────── 上传队列 ───────────── */

    var upQueue = [];
    var upRunning = 0;
    var UP_PARALLEL = 2;
    var CHUNK = 2 * 1024 * 1024;

    function upPanel() { return document.querySelector('[data-uploader]'); }

    function upSummary() {
      var panel = upPanel();
      if (!panel) return;
      var sum = panel.querySelector('[data-up-sum]');
      var done = upQueue.filter(function (i) { return i.state === 'done'; }).length;
      var failed = upQueue.filter(function (i) { return i.state === 'error'; }).length;
      if (!upQueue.length) { if (sum) sum.textContent = '—'; return; }
      if (sum) {
        sum.textContent = done === upQueue.length
          ? '全部完成 · ' + upQueue.length + ' 个文件'
          : done + ' / ' + upQueue.length + ' 完成' + (failed ? ' · ' + failed + ' 失败' : '');
      }
    }

    function upRender() {
      var panel = upPanel();
      if (!panel) return;
      var list = panel.querySelector('[data-up-list]');
      list.innerHTML = upQueue.map(function (item, idx) {
        var meta = item.state === 'done' ? '已完成'
          : item.state === 'error' ? (item.err || '失败')
          : item.state === 'paused' ? '已暂停 · ' + Math.round(item.pct) + '%'
          : item.state === 'skipped' ? '已跳过'
          : Math.round(item.pct) + '%';
        // 正在传 → 暂停；暂停/失败 → 继续/重试
        var action = '';
        if (item.state === 'run') {
          action = '<button class="btn btn--icon btn--ghost" data-up-pause aria-label="暂停">' +
            icon('pause', 'icon--sm') + '</button>';
        } else if (item.state === 'paused') {
          action = '<button class="btn btn--icon btn--ghost" data-up-resume aria-label="继续">' +
            icon('play', 'icon--sm') + '</button>';
        } else if (item.state === 'error') {
          action = '<button class="btn btn--icon btn--ghost" data-up-retry aria-label="重试">' +
            icon('refresh', 'icon--sm') + '</button>';
        }
        return '<div class="up-item" data-state="' + item.state + '" data-idx="' + idx + '">' +
          '<span class="row-icon" data-tone="archive">' + icon('upload') + '</span>' +
          '<div class="up-main"><div class="up-name">' + esc(item.rel) + '</div>' +
          '<div class="up-bar"><i style="width:' + Math.round(item.pct) + '%"></i></div>' +
          '<div class="up-meta tnum">' + esc(meta) + '</div></div>' +
          action +
          '<button class="btn btn--icon btn--ghost" data-up-remove aria-label="移除">' +
            icon('close', 'icon--sm') + '</button></div>';
      }).join('');
      upSummary();
    }

    function addUploads(files) {
      var panel = upPanel();
      if (panel) panel.setAttribute('data-open', 'true');
      Array.prototype.forEach.call(files, function (f) {
        var rel = f.webkitRelativePath || f.name;
        upQueue.push({
          file: f, rel: rel, size: f.size, state: 'wait', pct: 0, offset: 0
        });
      });
      upRender();
      pump();
    }

    function pump() {
      while (upRunning < UP_PARALLEL) {
        var next = upQueue.find(function (i) { return i.state === 'wait'; });
        if (!next) break;
        upRunning++;
        next.state = 'run';
        upRender();
        runUpload(next).then(function () {
          upRunning--;
          upRender();
          pump();
        });
      }
      // 队列跑空且没有正在传的 → 刷新一次列表（不整页重载）
      var busy = upRunning > 0 || upQueue.some(function (i) {
        return i.state === 'wait' || i.state === 'run';
      });
      if (!busy && upQueue.length && !upRefreshing) {
        upRefreshing = true;
        setTimeout(function () {
          upRefreshing = false;
          refreshFiles();
        }, 300);
      }
    }

    var upRefreshing = false;

    function upUrl(item) {
      return 'name=' + encodeURIComponent(item.rel) +
        '&path=' + encodeURIComponent(fsState.path) +
        '&size=' + item.file.size + '&mtime=' + item.file.lastModified;
    }

    function sendRange(item, start) {
      var file = item.file;

      function step(offset) {
        // 每块之间检查一次暂停：已发出去的块服务端已经记了偏移，恢复时能接着传
        if (item.paused) { item.state = 'paused'; return Promise.resolve('paused'); }
        if (offset >= file.size) return Promise.resolve();
        var end = Math.min(offset + CHUNK, file.size);
        var url = '/upload?offset=' + offset + '&' + upUrl(item);
        return writeFetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/octet-stream' },
          body: file.slice(offset, end)
        }).then(function (r) {
          if (!r.ok) throw new Error('上传失败 HTTP ' + r.status);
          item.offset = end;
          item.pct = file.size ? end / file.size * 100 : 100;
          upRender();
          return step(end);
        });
      }
      return step(start);
    }

    function askConflict(item) {
      return new Promise(function (resolve) {
        var answered = false;
        function done(v) { if (!answered) { answered = true; resolve(v); } }
        openModal({
          title: '同名文件已存在', icon: 'warning',
          desc: '<strong>' + esc(item.rel) + '</strong> 已经在目标目录里了，要覆盖还是留一份副本？',
          confirmText: '覆盖', cancelText: '跳过',
          extra: [{ label: '另存副本', onClick: function () { done('copy'); } }],
          onConfirm: function () { done('overwrite'); },
          onCancel: function () { done('skip'); }
        });
      });
    }

    function uniqueName(item) {
      // 这个接口也要管理权限：用 writeFetch，401 时先要密码再重试，
      // 否则改名失败会静默落回原文件名，覆盖掉别人的文件
      return writeFetch('/upload/unique?name=' + encodeURIComponent(item.rel) +
        '&path=' + encodeURIComponent(fsState.path))
        .then(jsonOf)
        .then(function (d) {
          if (d && d.name) item.rel = d.name;
        })
        .catch(function () { });
    }

    function runUpload(item) {
      var file = item.file;
      var checkUrl = '/check_exists?file=' + encodeURIComponent(item.rel) +
        '&path=' + encodeURIComponent(fsState.path) +
        '&size=' + file.size + '&mtime=' + file.lastModified;

      var start = 0;

      return apiFetch(checkUrl).then(jsonOf).then(function (d) {
        if (d && d.resume && d.offset > 0) {
          start = d.offset;
          return 'go';
        }
        if (d && d.exists) {
          return askConflict(item).then(function (choice) {
            if (choice === 'skip') return 'skip';
            if (choice === 'copy') {
              return uniqueName(item).then(function () { return 'go'; });
            }
            return 'go';
          });
        }
        return 'go';
      }).then(function (verdict) {
        if (verdict === 'skip') {
          item.state = 'skipped';
          item.err = '已跳过';
          return;
        }
        if (file.size === 0) {
          return writeFetch('/upload?offset=0&' + upUrl(item), {
            method: 'POST',
            headers: { 'Content-Type': 'application/octet-stream' },
            body: new Blob([])
          });
        }
        return sendRange(item, start);
      }).then(function () {
        // 跳过 / 暂停都不要调 /upload/done：done 会清掉服务端的续传偏移
        if (item.state === 'skipped' || item.state === 'paused') return;
        return writeFetch('/upload/done?' + upUrl(item), { method: 'POST' });
      }).then(function () {
        if (item.state === 'skipped' || item.state === 'paused') return;
        item.state = 'done';
        item.pct = 100;
      }).catch(function (err) {
        if (item.state === 'paused') return;
        item.state = 'error';
        item.err = (err && err.message) || '失败';
      });
    }

    function initUploads() {
      var fileInput = document.getElementById('fileInput');
      var folderInput = document.getElementById('folderInput');
      var panel = upPanel();

      document.querySelectorAll('[data-upload-pick]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          openMenu(btn, [
            {
              key: 'file', label: '上传文件', icon: 'file',
              onSelect: function () { pickFiles(fileInput); }
            },
            {
              key: 'folder', label: '上传文件夹', icon: 'folder',
              onSelect: function () { pickFiles(folderInput); }
            }
          ]);
        });
      });

      /* 选文件必须在用户点击的同一个"用户激活"里完成：
         一旦先 await 一个网络请求（比如去弹管理密码），浏览器就认为
         已经脱离手势，input.click() 会被静默忽略——表现就是"点了没反应"。
         所以这里同步打开选择框，等真的选了文件再要管理密码。 */
      function pickFiles(input) {
        if (!input) { toast('这个浏览器不支持选择文件', 'err'); return; }
        input.click();
      }

      function onPicked(e) {
        var picked = Array.prototype.slice.call(e.target.files || []);
        e.target.value = '';
        if (!picked.length) return;
        ensureAdmin(function () { addUploads(picked); });
      }

      if (fileInput) fileInput.addEventListener('change', onPicked);
      if (folderInput) folderInput.addEventListener('change', onPicked);

      if (panel) {
        panel.querySelector('[data-up-close]').addEventListener('click', function () {
          panel.setAttribute('data-open', 'false');
        });
        panel.addEventListener('click', function (e) {
          var host = e.target.closest('[data-idx]');
          if (!host) return;
          var idx = Number(host.getAttribute('data-idx'));
          var item = upQueue[idx];
          if (!item) return;

          if (e.target.closest('[data-up-pause]')) {
            item.paused = true;           // 当前这一块传完就停
            return;
          }
          if (e.target.closest('[data-up-resume]') || e.target.closest('[data-up-retry]')) {
            // 继续/重试都走同一套：重新入队，check_exists 会告诉前端从哪个偏移接着传
            item.paused = false;
            item.err = '';
            item.state = 'wait';
            upRender();
            pump();
            return;
          }
          var rm = e.target.closest('[data-up-remove]');
          if (!rm) return;
          if (item.state === 'run') return;
          upQueue.splice(idx, 1);
          upRender();
        });
      }

      var dragDepth = 0;
      window.addEventListener('dragenter', function (e) {
        if (!e.dataTransfer || Array.from(e.dataTransfer.types).indexOf('Files') === -1) return;
        dragDepth++;
        document.body.setAttribute('data-dragging', 'true');
      });
      window.addEventListener('dragleave', function () {
        dragDepth = Math.max(0, dragDepth - 1);
        if (!dragDepth) document.body.removeAttribute('data-dragging');
      });
      window.addEventListener('dragover', function (e) { e.preventDefault(); });
      window.addEventListener('drop', function (e) {
        if (!e.dataTransfer || !e.dataTransfer.files.length) return;
        e.preventDefault();
        dragDepth = 0;
        document.body.removeAttribute('data-dragging');
        // 先把文件列表抓成数组：等异步回调时 dataTransfer 已经失效了
        var dropped = Array.prototype.slice.call(e.dataTransfer.files);
        ensureAdmin(function () { addUploads(dropped); });
      });
    }

    function initNewFolder() {
      document.querySelectorAll('[data-new-folder]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          openModal({
            title: '新建文件夹', icon: 'folderPlus', confirmText: '创建',
            desc: '在当前目录下创建一个新文件夹。',
            field: { label: '文件夹名称', value: '', placeholder: '例如：2026 年季度报告' },
            validate: function (v) {
              if (!v) return '请输入文件夹名称';
              if (/[\\/:*?"<>|]/.test(v)) return '名称不能包含 \\ / : * ? " < > |';
              if (fsState.entries.some(function (e) { return e.name === v; })) return '已存在同名文件或文件夹';
              return '';
            },
            onConfirm: function (v) {
              writeFetch('/mkdir?name=' + encodeURIComponent(v) + '&' + q(''), { method: 'POST' })
                .then(function (r) { return r.ok ? jsonOf(r) : null; })
                .then(function (d) {
                  if (d && d.success) { toast('已创建 ' + v, 'ok'); refreshFiles(); }
                  else toast('创建失败', 'err');
                })
                .catch(function () { toast('网络中断', 'err'); });
            }
          });
        });
      });
    }


    /* 对外只暴露这些 */
    Object.assign(Lan.upload, { queue: upQueue, pump: pump, render: upRender, init: initUploads, initFolder: initNewFolder });
  })(window.Lan = window.Lan || {});
