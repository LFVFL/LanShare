  /* ───────────── 模块：请求与管理员（Lan.api） ───────────── */

  (function (Lan) {
    'use strict';

    /* 依赖：显式从命名空间取，缺一个就报 ReferenceError */
    var toast = Lan.ui.toast;
    var openModal = Lan.ui.openModal;

    /* ───────────── 请求与管理员 ───────────── */

    var CTX = { isAdmin: false };

    function apiFetch(url, opts) {
      opts = opts || {};
      opts.credentials = 'same-origin';
      return fetch(url, opts);
    }

    function jsonOf(r) {
      return r.json().catch(function () { return {}; });
    }

    /* 要管理密码。解析 true/false；调用方拿到 true 之后才能做下一步
       ——比如打开文件选择框（那一下必须由用户自己点，不能替用户点）。 */
    function askAdmin(desc) {
      return new Promise(function (resolve) {
        var answered = false;
        openModal({
          title: '需要管理密码', icon: 'lock', confirmText: '解锁',
          desc: desc || '上传、重命名、删除等操作需要管理密码，验证后本次会话有效。',
          field: { label: '管理密码', type: 'password', placeholder: '输入管理密码' },
          validate: function (v) { return v ? '' : '请输入密码'; },
          onCancel: function () { if (!answered) { answered = true; resolve(false); } },
          onConfirm: function (pwd) {
            apiFetch('/verify_admin', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ password: pwd })
            }).then(jsonOf).then(function (d) {
              if (!answered) { answered = true; resolve(!!(d && d.success)); }
              if (!(d && d.success)) toast('管理密码不对', 'err');
            }).catch(function () {
              if (!answered) { answered = true; resolve(false); }
              toast('网络异常', 'err');
            });
          }
        });
      });
    }

    // 写操作：401 时先要管理员密码，再重试一次
    function writeFetch(url, opts) {
      return apiFetch(url, opts).then(function (r) {
        if (r.status !== 401) return r;
        return askAdmin().then(function (ok) {
          if (!ok) return r;
          CTX.isAdmin = true;
          return apiFetch(url, opts);
        });
      });
    }

    function ensureAdmin(action) {
      if (CTX.isAdmin) { action(); return true; }
      askAdmin().then(function (ok) {
        if (ok) { CTX.isAdmin = true; action(); }
        else toast('需要管理密码才能操作，已取消', 'err');
      });
      return false;
    }

    /* 401 的响应体里通常带 message（"Admin Required"），
       把它翻译成一句人话，别让界面只显示"失败"。 */
    function explainFailure(data, fallback) {
      if (!data) return fallback;
      var msg = data.message || data.error || '';
      if (msg === 'Admin Required' || msg === 'admin_required') {
        return '需要管理密码：点右上角「管理员权限」解锁后再试';
      }
      return msg || fallback;
    }


    /* 对外只暴露这些 */
    Object.assign(Lan.api, { fetch: apiFetch, json: jsonOf, write: writeFetch, askAdmin: askAdmin, ensureAdmin: ensureAdmin, explain: explainFailure, ctx: CTX });
  })(window.Lan = window.Lan || {});
