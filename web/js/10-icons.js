  /* ───────────── 模块：图标雪碧（Lan.icons） ───────────── */

  (function (Lan) {
    'use strict';

    /* ───────────── 图标 ───────────── */

    var ICONS = {
      home: '<path d="M3 10.6 12 3.5l9 7.1V20a1 1 0 0 1-1 1h-5.2v-6.2H9.2V21H4a1 1 0 0 1-1-1Z"/>',
      folder: '<path d="M3 7.2A1.8 1.8 0 0 1 4.8 5.4h3.9l1.9 2.4h6.6A1.8 1.8 0 0 1 19 9.6v7.8A1.8 1.8 0 0 1 17.2 19.2H4.8A1.8 1.8 0 0 1 3 17.4Z"/>',
      file: '<path d="M6.5 3h6.6L19 9v11.4a.6.6 0 0 1-.6.6H6.5a.6.6 0 0 1-.6-.6V3.6a.6.6 0 0 1 .6-.6Z"/><path d="M13 3v6h6"/>',
      image: '<rect x="3" y="4.5" width="18" height="15" rx="2.2"/><circle cx="8.6" cy="9.4" r="1.5"/><path d="M21 15.4 16.4 11 9 18"/>',
      video: '<rect x="3" y="5" width="18" height="14" rx="2.2"/><path d="M10.2 9.4 14.8 12l-4.6 2.6Z"/>',
      audio: '<path d="M9 17.5V6.2l9-1.7v11.3"/><circle cx="7" cy="18" r="2.2"/><circle cx="16" cy="16" r="2.2"/>',
      archive: '<path d="M4 8.2h16v11a1.6 1.6 0 0 1-1.6 1.6H5.6A1.6 1.6 0 0 1 4 19.2Z"/><path d="M3.2 4.4h17.6v3.8H3.2zM10.4 12h3.2"/>',
      doc: '<path d="M6.5 3h6.6L19 9v11.4a.6.6 0 0 1-.6.6H6.5a.6.6 0 0 1-.6-.6V3.6a.6.6 0 0 1 .6-.6Z"/><path d="M13 3v6h6M8.6 13h6.8M8.6 16.4h4.6"/>',
      code: '<path d="M9.4 7.6 4.8 12l4.6 4.4M14.6 7.6 19.2 12l-4.6 4.4"/>',
      chat: '<path d="M20.4 12.4c0 3.9-3.8 7-8.4 7-1 0-2-.1-2.9-.4L4 21l1.2-3.7A6.6 6.6 0 0 1 3.6 12.4c0-3.9 3.8-7 8.4-7s8.4 3.1 8.4 7Z"/>',
      search: '<circle cx="11" cy="11" r="6.3"/><path d="M16 16l4.4 4.4"/>',
      upload: '<path d="M12 16.4V4.6M7.6 9 12 4.6 16.4 9"/><path d="M4.6 15.4v3.2a1.6 1.6 0 0 0 1.6 1.6h11.6a1.6 1.6 0 0 0 1.6-1.6v-3.2"/>',
      download: '<path d="M12 4.6v11.8M16.4 12 12 16.4 7.6 12"/><path d="M4.6 15.4v3.2a1.6 1.6 0 0 0 1.6 1.6h11.6a1.6 1.6 0 0 0 1.6-1.6v-3.2"/>',
      more: '<circle cx="6" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="18" cy="12" r="1.4"/>',
      chevron: '<path d="M8.6 10.2 12 13.6l3.4-3.4"/>',
      chevronLeft: '<path d="M14 6.6 8.6 12 14 17.4"/>',
      arrow: '<path d="M12 5v14M6.6 13.4 12 18.8l5.4-5.4"/>',
      check: '<path d="M5 12.6 9.6 17 19 6.6"/>',
      close: '<path d="M6.4 6.4l11.2 11.2M17.6 6.4 6.4 17.6"/>',
      trash: '<path d="M4.6 7.2h14.8M9.4 7.2V5.4a.8.8 0 0 1 .8-.8h3.6a.8.8 0 0 1 .8.8v1.8"/><path d="M6.4 7.2l.9 12a1.4 1.4 0 0 0 1.4 1.3h6.6a1.4 1.4 0 0 0 1.4-1.3l.9-12"/>',
      edit: '<path d="M15.6 4.9a2.1 2.1 0 0 1 3 3L8.4 18.1l-4 1 1-4Z"/>',
      eye: '<path d="M2.6 12S6 6.4 12 6.4 21.4 12 21.4 12 18 17.6 12 17.6 2.6 12 2.6 12Z"/><circle cx="12" cy="12" r="2.6"/>',
      lock: '<rect x="5" y="10.4" width="14" height="9.6" rx="2"/><path d="M8.4 10.4V8a3.6 3.6 0 0 1 7.2 0v2.4"/>',
      sun: '<circle cx="12" cy="12" r="4"/><path d="M12 3v2.2M12 18.8V21M3 12h2.2M18.8 12H21M5.6 5.6l1.6 1.6M16.8 16.8l1.6 1.6M18.4 5.6l-1.6 1.6M7.2 16.8l-1.6 1.6"/>',
      moon: '<path d="M20 14.6A8.4 8.4 0 0 1 9.4 4a8.6 8.6 0 1 0 10.6 10.6Z"/>',
      plus: '<path d="M12 5.4v13.2M5.4 12h13.2"/>',
      copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M15 6.6V5.4A1.4 1.4 0 0 0 13.6 4H5.4A1.4 1.4 0 0 0 4 5.4v8.2A1.4 1.4 0 0 0 5.4 15h1.2"/>',
      grid: '<rect x="3.6" y="3.6" width="7" height="7" rx="1.6"/><rect x="13.4" y="3.6" width="7" height="7" rx="1.6"/><rect x="3.6" y="13.4" width="7" height="7" rx="1.6"/><rect x="13.4" y="13.4" width="7" height="7" rx="1.6"/>',
      list: '<path d="M8.4 6.6h12M8.4 12h12M8.4 17.4h12"/><circle cx="4.4" cy="6.6" r="1.1"/><circle cx="4.4" cy="12" r="1.1"/><circle cx="4.4" cy="17.4" r="1.1"/>',
      info: '<circle cx="12" cy="12" r="8.4"/><path d="M12 11v5.4M12 8.2v.2"/>',
      warning: '<path d="M12 4.4 21 19.6H3Z"/><path d="M12 10v4.2M12 17v.2"/>',
      folderPlus: '<path d="M3 7.2A1.8 1.8 0 0 1 4.8 5.4h3.9l1.9 2.4h6.6A1.8 1.8 0 0 1 19 9.6v7.8A1.8 1.8 0 0 1 17.2 19.2H4.8A1.8 1.8 0 0 1 3 17.4Z"/><path d="M11 12.4v4M9 14.4h4"/>',
      refresh: '<path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 4v4h-4"/>',
      pause: '<path d="M9.2 5.6v12.8M14.8 5.6v12.8"/>',
      play: '<path d="M8.6 5.4 18 12l-9.4 6.6Z"/>',
      palette: '<path d="M12 3.6a8.4 8.4 0 1 0 0 16.8c1 0 1.7-.7 1.7-1.6 0-.4-.2-.8-.5-1.1-.3-.3-.4-.7-.4-1.1 0-.9.7-1.6 1.6-1.6h1.5a4.6 4.6 0 0 0 4.5-4.6A8.4 8.4 0 0 0 12 3.6Z"/><circle cx="8.6" cy="9.4" r="1.1"/><circle cx="12" cy="7.8" r="1.1"/><circle cx="15.4" cy="9.4" r="1.1"/>'
    };

    function icon(name, cls) {
      return '<svg class="icon' + (cls ? ' ' + cls : '') + '" aria-hidden="true" focusable="false">' +
        '<use href="#i-' + name + '"></use></svg>';
    }

    function injectSprite() {
      var symbols = Object.keys(ICONS).map(function (k) {
        return '<symbol id="i-' + k + '" viewBox="0 0 24 24">' + ICONS[k] + '</symbol>';
      }).join('');
      var holder = document.createElement('div');
      holder.innerHTML = '<svg class="icon-sprite" aria-hidden="true">' + symbols + '</svg>';
      document.body.insertBefore(holder.firstChild, document.body.firstChild);
    }

    function hydrateIcons(root) {
      (root || document).querySelectorAll('[data-icon]').forEach(function (el) {
        el.innerHTML = icon(el.getAttribute('data-icon'), el.getAttribute('data-icon-cls') || '');
        el.removeAttribute('data-icon');
        // data-icon 被摘掉了，但居中样式原本挂在它身上：补一个稳定的 class，
        // 否则容器退回普通 block，<svg> 以 inline+baseline 排版，图标会整体偏上约 2px
        el.classList.add('icon-box');
      });
    }


    /* 对外只暴露这些 */
    Object.assign(Lan.icons, {
      ICONS: ICONS, icon: icon, injectSprite: injectSprite, hydrateIcons: hydrateIcons
    });
  })(window.Lan = window.Lan || {});
