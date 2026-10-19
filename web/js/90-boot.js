  /* ───────────── 模块：90 启动：只做编排，不碰实现 ───────────── */

  (function (Lan) {
    'use strict';

    var icons = Lan.icons;
    var api = Lan.api;

    /* 按 body[data-page] 决定装哪些页面模块；各模块自己管自己的 DOM */
    var PAGES = {
      files: function () { Lan.pages.files(); Lan.lightbox.init(); },
      chatlist: function () { Lan.pages.chatList(); },
      chatroom: function () { Lan.chat.init(); Lan.lightbox.init(); },
      login: function () { Lan.pages.login(); }
    };

    function boot() {
      icons.injectSprite();
      Lan.appearance.init();
      icons.hydrateIcons(document);

      var page = document.body.getAttribute('data-page');
      if (PAGES[page]) PAGES[page]();

      document.querySelectorAll('[data-admin-open]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          if (api.ctx.isAdmin) { Lan.ui.toast('当前已是管理员', 'ok'); return; }
          if (api.ctx.needsAdmin === false) {
            // 明确知道"没设密码"才拦；还没拿到 /is_admin 结果时按需要密码处理
            Lan.ui.toast('没有设置管理密码，不需要解锁', 'info');
            return;
          }
          api.askAdmin().then(function (ok) {
            if (ok) {
              // 这里必须同步把状态记下来，否则界面显示"已解锁"、实际还当自己是游客，
              // 下一次点上传又会弹密码框
              api.ctx.isAdmin = true;
              refreshAdminUi();
              Lan.ui.toast('已验证管理员权限', 'ok');
            }
          });
        });
      });

      api.fetch('/is_admin').then(api.json).then(function (d) {
        api.ctx.isAdmin = !!(d && d.is_admin);
        api.ctx.needsAdmin = !!(d && d.needs_admin);
        refreshAdminUi();
      }).catch(function () { });
    }

    /* 管理员入口的显隐：
       - 没设管理密码 → 整条藏起来（点了也没用）
       - 设了但没解锁 → 显示锁头（手机端在顶栏，桌面端在侧栏）
       - 已解锁 → 换成"已解锁"的样式，点一下只提示 */
    function refreshAdminUi() {
      var buttons = document.querySelectorAll('[data-admin-open]');
      buttons.forEach(function (btn) {
        if (!api.ctx.needsAdmin) { btn.hidden = true; return; }
        btn.hidden = false;
        btn.setAttribute('data-unlocked', api.ctx.isAdmin ? 'true' : 'false');
        var tail = btn.querySelector('span');
        if (tail) tail.textContent = api.ctx.isAdmin ? '已解锁' : '管理员权限';
        btn.title = api.ctx.isAdmin ? '已是管理员' : '输入管理密码';
      });
    }

    Lan.api.refreshAdminUi = refreshAdminUi;

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', boot);
    } else {
      boot();
    }

  })(window.Lan = window.Lan || {});
