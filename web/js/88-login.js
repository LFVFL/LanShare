  /* ───────────── 模块：登录页（Lan.pages.login） ───────────── */

  (function (Lan) {
    'use strict';

    /* 依赖：显式从命名空间取，缺一个就报 ReferenceError */
    var icon = Lan.icons.icon;

    /* ───────────── 登录页 ───────────── */

    function initLogin() {
      var form = document.querySelector('[data-login-form]');
      var input = document.getElementById('pwd');
      var eye = document.querySelector('[data-eye]');
      var submit = document.querySelector('[data-submit]');
      var err = document.querySelector('.login-error');

      if (eye && input) {
        eye.addEventListener('click', function () {
          var show = input.type === 'password';
          input.type = show ? 'text' : 'password';
          eye.innerHTML = icon(show ? 'eye' : 'lock', 'icon--sm');
          eye.setAttribute('aria-label', show ? '隐藏密码' : '显示密码');
        });
      }

      if (input && err) {
        input.addEventListener('input', function () { err.setAttribute('data-open', 'false'); });
      }

      if (form) {
        form.addEventListener('submit', function () {
          if (submit) {
            submit.disabled = true;
            submit.textContent = '验证中…';
          }
        });
      }
    }


    /* 对外只暴露这些 */
    Lan.pages.login = initLogin;
  })(window.Lan = window.Lan || {});
