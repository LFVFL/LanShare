  /* ───────────── 模块：灯箱与文件页启动（Lan.lightbox / Lan.pages.files） ───────────── */

  (function (Lan) {
    'use strict';

    /* 依赖：显式从命名空间取，缺一个就报 ReferenceError */
    var downloadUrl = Lan.util.downloadUrl;
    var readJson = Lan.util.readJson;
    var fsState = Lan.files.state;
    var renderFiles = Lan.files.render;
    var bindFilesList = Lan.files.bindList;
    var bindToolbar = Lan.files.bindToolbar;
    var initUploads = Lan.upload.init;
    var initNewFolder = Lan.upload.initFolder;

    /* ───────────── 灯箱（文件预览与聊天图片共用） ───────────── */

    function openLightbox(src, name, dlUrl) {
      var box = document.querySelector('[data-lightbox]');
      if (!box) { window.open(src, '_blank'); return; }
      var img = box.querySelector('img');
      if (img) img.src = src;
      var label = box.querySelector('[data-lightbox-name]');
      if (label) label.textContent = name || '';
      var dl = box.querySelector('[data-lightbox-download]');
      if (dl) {
        dl.onclick = function () {
          if (dlUrl) downloadUrl(dlUrl, name || 'download');
          else downloadUrl(src, name || 'image');
        };
      }
      box.setAttribute('data-open', 'true');
    }

    function initLightbox() {
      var box = document.querySelector('[data-lightbox]');
      if (!box) return;
      box.addEventListener('click', function (e) {
        if (e.target === box || e.target.closest('[data-lightbox-close]')) {
          box.removeAttribute('data-open');
          var img = box.querySelector('img');
          if (img) img.src = '';
        }
      });
    }

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
    Lan.lightbox.open = openLightbox;
    Lan.lightbox.init = initLightbox;
    Lan.pages.files = initFilesPage;
  })(window.Lan = window.Lan || {});
