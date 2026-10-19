  /* ───────────── 模块：通用交互：提示 / 弹窗 / 菜单（Lan.ui） ───────────── */

  (function (Lan) {
    'use strict';

    /* 依赖：显式从命名空间取，缺一个就报 ReferenceError */
    var icon = Lan.icons.icon;
    var hydrateIcons = Lan.icons.hydrateIcons;

    /* ───────────── Toast ───────────── */

    function toast(message, tone) {
      var host = document.querySelector('.toasts');
      if (!host) { host = document.createElement('div'); host.className = 'toasts'; document.body.appendChild(host); }
      var map = { ok: 'check', err: 'warning', info: 'info' };
      var el = document.createElement('div');
      el.className = 'toast glass';
      el.setAttribute('data-tone', tone || 'info');
      el.innerHTML = icon(map[tone || 'info'] || 'info', 'icon--sm') + '<span>' + message + '</span>';
      host.appendChild(el);
      setTimeout(function () {
        el.setAttribute('data-leaving', 'true');
        setTimeout(function () { el.remove(); }, 200);
      }, 2600);
    }

    /* ───────────── 弹窗 ───────────── */

    var activeModal = null;

    function closeModal() {
      if (!activeModal) return;
      var node = activeModal;
      activeModal = null;
      node.removeAttribute('data-open');
      setTimeout(function () { node.remove(); }, 200);
      document.removeEventListener('keydown', onModalKey);
    }

    function onModalKey(e) { if (e.key === 'Escape') closeModal(); }

    function openModal(opts) {
      closeModal();
      var scrim = document.createElement('div');
      scrim.className = 'scrim';
      var isDanger = opts.danger || opts.tone === 'danger';

      var bodyHtml = '';
      if (opts.field) {
        bodyHtml = '<div class="modal-body"><div class="field"><label>' +
          (opts.field.label || '') + '</label><input class="input" type="' +
          (opts.field.type || 'text') + '" value="' +
          String(opts.field.value || '').replace(/"/g, '&quot;') + '" placeholder="' +
          (opts.field.placeholder || '') + '"></div><div class="modal-err"></div></div>';
      } else if (opts.body) {
        bodyHtml = '<div class="modal-body">' + opts.body + '</div>';
      }

      scrim.innerHTML =
        '<div class="modal glass" role="dialog" aria-modal="true" aria-labelledby="modal-title">' +
          '<div class="modal-icon" data-tone="' + (opts.tone || 'brand') + '">' +
            icon(opts.icon || (isDanger ? 'warning' : 'info')) + '</div>' +
          '<h3 class="modal-title" id="modal-title">' + (opts.title || '') + '</h3>' +
          '<p class="modal-desc">' + (opts.desc || '') + '</p>' + bodyHtml +
          '<div class="modal-foot">' +
            (opts.extra || []).map(function (b, i) {
              return '<button class="btn btn--outline" data-extra="' + i + '">' + b.label + '</button>';
            }).join('') +
            '<button class="btn btn--outline" data-modal-cancel>' + (opts.cancelText || '取消') + '</button>' +
            '<button class="btn ' + (isDanger ? 'btn--danger-solid' : 'btn--primary') + '" data-modal-ok>' +
              (opts.confirmText || '确定') + '</button>' +
          '</div></div>';

      document.body.appendChild(scrim);
      activeModal = scrim;
      hydrateIcons(scrim);
      void scrim.offsetWidth;
      scrim.setAttribute('data-open', 'true');
      document.addEventListener('keydown', onModalKey);

      var input = scrim.querySelector('input');
      if (input) { input.focus(); input.select(); }
      else { scrim.querySelector(isDanger ? '[data-modal-cancel]' : '[data-modal-ok]').focus(); }

      var closed = false;
      function finish() { closed = true; closeModal(); }
      function cancel() {
        if (closed) return;
        finish();
        if (opts.onCancel) opts.onCancel();
      }

      function submit() {
        var val = input ? input.value.trim() : undefined;
        var errEl = scrim.querySelector('.modal-err');
        if (opts.validate) {
          var msg = opts.validate(val);
          if (msg) {
            if (errEl) errEl.textContent = msg;
            if (input) input.setAttribute('aria-invalid', 'true');
            scrim.querySelector('.modal').classList.remove('shake');
            void scrim.querySelector('.modal').offsetWidth;
            scrim.querySelector('.modal').classList.add('shake');
            return;
          }
        }
        finish();
        if (opts.onConfirm) opts.onConfirm(val);
      }

      scrim.querySelector('[data-modal-ok]').addEventListener('click', submit);
      scrim.querySelector('[data-modal-cancel]').addEventListener('click', cancel);
      scrim.querySelectorAll('[data-extra]').forEach(function (btn, i) {
        btn.addEventListener('click', function () {
          var def = (opts.extra || [])[i];
          finish();
          if (def && def.onClick) def.onClick();
        });
      });
      scrim.addEventListener('click', function (e) { if (e.target === scrim) cancel(); });
      if (input) {
        input.addEventListener('keydown', function (e) {
          if (e.key === 'Enter') { e.preventDefault(); submit(); }
        });
      }
    }

    /* ───────────── 菜单 ───────────── */

    var openMenuEl = null;
    var menuOpenedAt = 0;

    function closeMenu() {
      if (!openMenuEl) return;
      // 直接移除节点：菜单的可见性靠入场动画，改属性关不掉它
      openMenuEl.remove();
      openMenuEl = null;
    }

    function openMenu(anchor, items, atPoint) {
      closeMenu();
      var el = document.createElement('div');
      el.className = 'menu glass';
      el.innerHTML = items.map(function (it) {
        if (it.sep) return '<div class="menu-sep"></div>';
        return '<button class="menu-item' + (it.danger ? ' danger' : '') + '" data-key="' + it.key + '">' +
          icon(it.icon || 'file', 'icon--sm') + '<span>' + it.label + '</span></button>';
      }).join('');
      document.body.appendChild(el);
      hydrateIcons(el);

      var r = atPoint
        ? { left: atPoint.x, right: atPoint.x, top: atPoint.y, bottom: atPoint.y }
        : anchor.getBoundingClientRect();
      var w = el.offsetWidth, h = el.offsetHeight;
      var left = Math.min(r.right - w, window.innerWidth - w - 8);
      var top = r.bottom + 6;
      if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 6);
      el.style.left = Math.max(8, left) + 'px';
      el.style.top = top + 'px';
      menuOpenedAt = Date.now();
      void el.offsetWidth;
      el.setAttribute('data-open', 'true');
      anchor.setAttribute('aria-expanded', 'true');
      openMenuEl = el;

      el.addEventListener('click', function (e) {
        var btn = e.target.closest('[data-key]');
        if (!btn) return;
        var key = btn.getAttribute('data-key');
        var item = items.find(function (i) { return i.key === key; });
        closeMenu();
        anchor.setAttribute('aria-expanded', 'false');
        if (item && item.onSelect) item.onSelect();
      });
    }

    document.addEventListener('click', function (e) {
      if (!openMenuEl) return;
      if (Date.now() - menuOpenedAt < 200) return;   // 打开它的那一下不算"点外面"
      if (e.target.closest('.menu') || e.target.closest('[data-menu-trigger]')) return;
      closeMenu();
    });
    /* pointerdown 比 click 更可靠：手机上长按/拖动后浏览器可能不再派发 click */
    document.addEventListener('pointerdown', function (e) {
      if (!openMenuEl) return;
      if (e.target.closest('.menu') || e.target.closest('[data-menu-trigger]')) return;
      if (Date.now() - menuOpenedAt < 120) return;   // 别被打开它的那一下关掉
      closeMenu();
    }, true);
    document.addEventListener('scroll', function () {
      if (Date.now() - menuOpenedAt > 250) closeMenu();
    }, true);


    /* 对外只暴露这些 */
    Object.assign(Lan.ui, { toast: toast, openModal: openModal, closeModal: closeModal, openMenu: openMenu, closeMenu: closeMenu });
  })(window.Lan = window.Lan || {});
