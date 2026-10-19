  /* ───────────── 模块：00 核心：命名空间与通用工具 ───────────── */

  /* 每个分片都是独立 IIFE，只能通过 window.Lan 互相访问：
     谁依赖谁，写在各自文件头的 var x = Lan.y.x 里，不再共享闭包。 */
  (function (Lan) {
    'use strict';

    Lan.version = '6.1';

    /* 根节点统一由 core 建，其余分片只往里挂成员 */
    Lan.util = Lan.util || {};
    Lan.icons = Lan.icons || {};
    Lan.ui = Lan.ui || {};
    Lan.api = Lan.api || {};
    Lan.appearance = Lan.appearance || {};
    Lan.files = Lan.files || {};
    Lan.upload = Lan.upload || {};
    Lan.lightbox = Lan.lightbox || {};
    Lan.chat = Lan.chat || {};
    Lan.pages = Lan.pages || {};

    function esc(s) {
      return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function readJson(sel) {
      var el = document.querySelector(sel);
      if (!el) return null;
      try { return JSON.parse(el.textContent || '{}'); } catch (e) { return null; }
    }

    /* 用 <a download> 触发真正的下载；直接 window.open 只会在新标签里"预览" */
    function downloadUrl(url, filename) {
      var a = document.createElement('a');
      a.href = url;
      if (filename) a.download = filename;
      a.rel = 'noopener';
      document.body.appendChild(a);
      a.click();
      a.remove();
    }

    /* 局域网是 http，不属于安全上下文，navigator.clipboard 往往不可用，
       所以必须有 execCommand 兜底（手机上也能用） */
    function legacyCopy(text) {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.cssText = 'position:fixed;top:0;left:-9999px;opacity:0';
      document.body.appendChild(ta);
      ta.select();
      ta.setSelectionRange(0, ta.value.length);
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      ta.remove();
      Lan.ui.toast(ok ? '已复制' : '复制失败，请手动选择', ok ? 'ok' : 'err');
    }

    function copyText(text) {
      if (!text) return;
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(function () {
          Lan.ui.toast('已复制', 'ok');
        }).catch(function () { legacyCopy(text); });
        return;
      }
      legacyCopy(text);
    }

    Lan.util.esc = esc;
    Lan.util.readJson = readJson;
    Lan.util.downloadUrl = downloadUrl;
    Lan.util.copyText = copyText;
    Lan.util.legacyCopy = legacyCopy;

  })(window.Lan = window.Lan || {});
