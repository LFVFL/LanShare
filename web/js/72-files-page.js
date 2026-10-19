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

    function initFilesPage() {
      var data = readJson('[data-fs-json]');
      fsState.path = (data && data.path) || '';
      fsState.entries = (data && data.entries) || [];

      bindFilesList();
      bindToolbar();
      initUploads();
      initNewFolder();

      var clear = document.querySelector('[data-empty-clear]');
      if (clear) {
        clear.addEventListener('click', function () {
          fsState.query = '';
          document.querySelectorAll('[data-files-search]').forEach(function (i) { i.value = ''; });
          renderFiles();
        });
      }

      renderFiles();
    }


    /* 对外只暴露这些 */
    Lan.pages.files = initFilesPage;
  })(window.Lan = window.Lan || {});
