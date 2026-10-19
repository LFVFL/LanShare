  /* ───────────── 模块：灯箱（Lan.lightbox，文件页与聊天页共用） ───────────── */

  (function (Lan) {
    'use strict';

    /* 依赖：显式从命名空间取，缺一个就报 ReferenceError */
    var downloadUrl = Lan.util.downloadUrl;

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

    /* 对外只暴露这些 */
    Lan.lightbox.open = openLightbox;
    Lan.lightbox.init = initLightbox;
  })(window.Lan = window.Lan || {});
