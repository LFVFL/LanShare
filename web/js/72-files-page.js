  /* ───────────── 模块：文件页启动（Lan.pages.files） ───────────── */

  /* 从 70-lightbox.js 里拆出来的：灯箱要和聊天页共用（放核心包），
     文件页的启动只需要在文件页加载，所以单独成包。 */
  (function (Lan) {
    'use strict';

    /* 依赖：显式从命名空间取，缺一个就报 ReferenceError */
    var readJson = Lan.util.readJson;
    var fsState = Lan.files.state;
    var renderFiles = Lan.files.render;
    var bindFilesList = Lan.files.bindList;
    var bindToolbar = Lan.files.bindToolbar;
    var initUploads = Lan.upload.init;
    var initNewFolder = Lan.upload.initFolder;
    var searchExit = Lan.files.exit;

    function isShareRoot() {
      var path = String(fsState.path || '');
      var root = String(fsState.root || '');
      if (!path || !root) return false;
      // The server resolves both paths; match pathlib's Windows case/separator semantics.
      if (/^(?:[a-z]:[\\/]|\\\\|\/\/)/i.test(root)) {
        path = path.replace(/\\/g, '/').toLowerCase();
        root = root.replace(/\\/g, '/').toLowerCase();
      }
      return path.replace(/\/+$/, '') === root.replace(/\/+$/, '');
    }

    function syncSearchScopes() {
      var atRoot = isShareRoot();
      if (atRoot && fsState.scope === 'share') fsState.scope = 'subtree';
      document.querySelectorAll('[data-files-search-scope]').forEach(function (select) {
        var shareOption = select.querySelector('option[value="share"]');
        if (atRoot && shareOption) shareOption.remove();
        else if (!atRoot && !shareOption) {
          shareOption = document.createElement('option');
          shareOption.value = 'share';
          shareOption.textContent = '整个共享目录';
          select.appendChild(shareOption);
        }
        select.value = fsState.scope;
      });
    }

    function initFilesPage() {
      var data = readJson('[data-fs-json]');
      fsState.path = (data && data.path) || '';
      fsState.root = (data && data.root) || fsState.path;
      fsState.entries = (data && data.entries) || [];
      fsState.total = data && typeof data.total === 'number' ? data.total : fsState.entries.length;
      fsState.hasMore = !!(data && data.hasMore);
      fsState.sortKey = (data && data.sort) || 'name';
      fsState.sortDir = (data && data.order) || 'asc';
      fsState.query = (data && data.query) || '';
      if (fsState.query) fsState.scope = 'current';
      document.querySelectorAll('[data-files-search]').forEach(function (input) {
        input.value = fsState.query;
      });

      bindFilesList();
      bindToolbar();
      initUploads();
      initNewFolder();

      var clear = document.querySelector('[data-empty-clear]');
      if (clear) {
        clear.addEventListener('click', function () {
          searchExit();
        });
      }

      renderFiles();
    }


    /* 对外只暴露这些 */
    Lan.files.syncSearchScopes = syncSearchScopes;
    Lan.pages.files = initFilesPage;
  })(window.Lan = window.Lan || {});
