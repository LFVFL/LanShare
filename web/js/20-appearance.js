  /* ───────────── 模块：外观设置（Lan.appearance） ───────────── */

  (function (Lan) {
    'use strict';

    /* 依赖：显式从命名空间取，缺一个就报 ReferenceError */
    var icon = Lan.icons.icon;

    /* ───────────── 外观：配色 / 明暗 / 玻璃 ───────────── */

    var STORE = { theme: 'lanshare.theme', palette: 'lanshare.palette', glass: 'lanshare.glass' };

    var PALETTES = [
      { id: 'indigo', name: '蓝紫' }, { id: 'ocean', name: '青蓝' },
      { id: 'mint', name: '翠绿' }, { id: 'sunset', name: '蜜橙' },
      { id: 'berry', name: '莓紫' }, { id: 'graphite', name: '石墨' }
    ];

    var appearance = { theme: 'light', palette: 'mint', glass: 'normal' };

    function prefDark() {
      return !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
    }
    function resolvedTheme() {
      return appearance.theme === 'auto' ? (prefDark() ? 'dark' : 'light') : appearance.theme;
    }
    function storeGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
    function storeSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { } }

    function applyAppearance() {
      var root = document.documentElement;
      var t = resolvedTheme();
      root.setAttribute('data-theme', t);
      root.setAttribute('data-palette', appearance.palette);
      root.setAttribute('data-glass', appearance.glass);
      document.querySelectorAll('[data-theme-label]').forEach(function (el) {
        el.textContent = t === 'dark' ? '浅色' : '深色';
      });
      document.querySelectorAll('[data-theme-icon]').forEach(function (el) {
        el.innerHTML = icon(t === 'dark' ? 'sun' : 'moon');
      });
      syncAppearancePanel();
    }

    var panelEl = null;
    var panelOpenedAt = 0;

    function buildPanel() {
      var el = document.createElement('div');
      el.className = 'popover glass';
      el.setAttribute('role', 'dialog');
      el.setAttribute('aria-label', '外观设置');
      el.innerHTML =
        '<div class="popover-title">' + icon('palette', 'icon--sm') + '<span>外观</span></div>' +
        '<div class="popover-row"><span>主题</span><div class="seg" data-seg="theme">' +
          '<button type="button" data-v="light">浅色</button>' +
          '<button type="button" data-v="dark">深色</button>' +
          '<button type="button" data-v="auto">跟随系统</button>' +
        '</div></div>' +
        '<div class="popover-row"><span>配色方案</span><div class="pal-grid">' +
          PALETTES.map(function (p) {
            return '<button type="button" class="pal-swatch" data-pal="' + p.id + '" aria-pressed="false">' +
              '<span class="pal-dot" data-p="' + p.id + '"></span>' + p.name + '</button>';
          }).join('') +
        '</div></div>' +
        '<div class="popover-row"><span>玻璃质感</span><div class="seg" data-seg="glass">' +
          '<button type="button" data-v="strong">强</button>' +
          '<button type="button" data-v="normal">标准</button>' +
          '<button type="button" data-v="off">关闭</button>' +
        '</div></div>' +
        '<p class="popover-hint">玻璃越强越好看，也越吃 GPU。低端手机或远程桌面建议选「关闭」。</p>';

      document.body.appendChild(el);

      el.addEventListener('click', function (e) {
        var segBtn = e.target.closest('.seg button');
        if (segBtn) {
          var key = segBtn.parentElement.getAttribute('data-seg');
          appearance[key] = segBtn.getAttribute('data-v');
          storeSet(STORE[key], appearance[key]);
          applyAppearance();
          return;
        }
        var pal = e.target.closest('[data-pal]');
        if (pal) {
          appearance.palette = pal.getAttribute('data-pal');
          storeSet(STORE.palette, appearance.palette);
          applyAppearance();
        }
      });
      return el;
    }

    function syncAppearancePanel() {
      if (!panelEl) return;
      panelEl.querySelectorAll('.seg button').forEach(function (b) {
        var key = b.parentElement.getAttribute('data-seg');
        b.setAttribute('aria-pressed', String(appearance[key] === b.getAttribute('data-v')));
      });
      panelEl.querySelectorAll('[data-pal]').forEach(function (b) {
        b.setAttribute('aria-pressed', String(appearance.palette === b.getAttribute('data-pal')));
      });
    }

    function closePanel() {
      if (!panelEl) return;
      panelEl.remove();
      panelEl = null;
      document.removeEventListener('keydown', onPanelKey);
    }

    function onPanelKey(e) { if (e.key === 'Escape') closePanel(); }

    function openPanel(anchor) {
      if (panelEl) { closePanel(); return; }
      panelEl = buildPanel();
      syncAppearancePanel();
      panelOpenedAt = Date.now();

      var r = anchor.getBoundingClientRect();
      var w = panelEl.offsetWidth, h = panelEl.offsetHeight, left, top;
      if (r.left < 260 && window.innerWidth > 900) {
        left = r.right + 10;
        top = Math.min(Math.max(8, r.bottom - h), window.innerHeight - h - 8);
      } else {
        left = Math.min(Math.max(8, r.right - w), window.innerWidth - w - 8);
        top = r.bottom + 8;
        if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 8);
      }
      panelEl.style.left = left + 'px';
      panelEl.style.top = top + 'px';
      document.addEventListener('keydown', onPanelKey);
      setTimeout(function () { document.addEventListener('click', onPanelOutside); }, 0);
    }

    function onPanelOutside(e) {
      if (!panelEl) { document.removeEventListener('click', onPanelOutside); return; }
      if (e.target.closest('.popover') || e.target.closest('[data-appearance]')) return;
      closePanel();
      document.removeEventListener('click', onPanelOutside);
    }

    function initAppearance() {
      appearance.theme = storeGet(STORE.theme) || 'light';
      appearance.palette = storeGet(STORE.palette) || 'mint';
      appearance.glass = storeGet(STORE.glass) || 'normal';
      applyAppearance();

      if (window.matchMedia) {
        var mq = window.matchMedia('(prefers-color-scheme: dark)');
        var onMq = function () { if (appearance.theme === 'auto') applyAppearance(); };
        if (mq.addEventListener) mq.addEventListener('change', onMq);
        else if (mq.addListener) mq.addListener(onMq);
      }

      document.addEventListener('click', function (e) {
        var t = e.target.closest('[data-theme-toggle]');
        if (t) {
          appearance.theme = resolvedTheme() === 'dark' ? 'light' : 'dark';
          storeSet(STORE.theme, appearance.theme);
          applyAppearance();
          return;
        }
        var a = e.target.closest('[data-appearance]');
        if (a) openPanel(a);
      });
    }


    /* 对外只暴露这些 */
    Object.assign(Lan.appearance, { state: appearance, palettes: PALETTES, apply: applyAppearance, init: initAppearance, openPanel: openPanel, closePanel: closePanel, prefDark: prefDark });
  })(window.Lan = window.Lan || {});
