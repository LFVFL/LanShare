  /* ───────────── 模块：聊天室（Lan.chat） ───────────── */

  (function (Lan) {
    'use strict';

    /* 依赖：显式从命名空间取，缺一个就报 ReferenceError */
    var icon = Lan.icons.icon;
    var toast = Lan.ui.toast;
    var openModal = Lan.ui.openModal;
    var openMenu = Lan.ui.openMenu;
    var apiFetch = Lan.api.fetch;
    var jsonOf = Lan.api.json;
    var writeFetch = Lan.api.write;
    var esc = Lan.util.esc;
    var copyText = Lan.util.copyText;
    var downloadUrl = Lan.util.downloadUrl;
    var readJson = Lan.util.readJson;
    var openLightbox = Lan.lightbox.open;

    /* ───────────── 聊天室 ───────────── */

    var chat = {
      room: '', me: '', msgs: [], lastId: '', pending: 0, timer: null, es: null,
      image: '', thumb: '', filter: ''
    };

    function chatEl(sel) { return document.querySelector(sel); }

    function linkify(text) {
      return esc(text).replace(/(https?:\/\/[^\s<]+)/g, function (u) {
        return '<a href="' + u + '" target="_blank" rel="noopener">' + u + '</a>';
      });
    }

    function chatRender() {
      var body = chatEl('[data-chat-body]');
      if (!body) return;
      // 渲染前按 id 再兜一次底：不管从哪条路进来的，画面上都不会重复
      var seen = {}, uniq = [];
      chat.msgs.forEach(function (m) {
        if (!m || seen[m.id]) return;
        seen[m.id] = 1;
        uniq.push(m);
      });
      chat.msgs = uniq;
      // 顶部"N 条消息"跟着变，否则发完消息还显示旧数字；
      // 搜索时显示命中条数
      var list = chat.msgs;
      if (chat.filter) {
        var needle = chat.filter.toLowerCase();
        list = chat.msgs.filter(function (m) {
          return (m.text || '').toLowerCase().indexOf(needle) >= 0;
        });
      }
      var meta = chatEl('[data-room-meta]');
      if (meta) {
        meta.textContent = chat.filter
          ? ('找到 ' + list.length + ' 条')
          : (chat.msgs.length + ' 条消息');
      }
      var html = '';
      var lastDay = '', lastWho = '', lastTime = '';

      list.forEach(function (m, idx) {
        if ((m.date || '') !== lastDay) {
          html += '<div class="day-chip"><span>' + esc(m.date || '') + '</span></div>';
          lastDay = m.date || '';
          lastWho = '';
        }
        var mine = m.sender === chat.me;
        var first = m.sender !== lastWho || m.time !== lastTime;
        lastWho = m.sender;
        lastTime = m.time;

        var leading = mine ? '' : (first
          ? '<span class="avatar avatar--sm" style="background:' + colorFor(m.sender) + '">' +
            esc((m.sender || '?').slice(0, 1)) + '</span>'
          : '<span class="msg-spacer"></span>');

        var head = first
          ? (mine
            ? '<div class="msg-head"><span class="tnum">' + esc(m.time || '') + '</span></div>'
            : '<div class="msg-head"><span class="msg-who">' + esc(m.sender || '') + '</span>' +
              '<span class="tnum">' + esc(m.time || '') + '</span></div>')
          : '';

        var inner = '';
        if (m.type === 'image' && m.image) {
          // 列表里只加载缩略图，点开再看原图（data-img 存的始终是原图）
          var src = m.thumb || m.image;
          inner += '<img class="msg-img" alt="图片" src="' + esc(src) + '" ' +
            'data-img="' + esc(m.image) + '" loading="lazy">';
        }
        if (m.text) inner += '<div class="msg-text">' + linkify(m.text) + '</div>';

        html += '<div class="msg' + (mine ? ' msg--mine' : '') +
          '" data-id="' + esc(m.id) + '" data-idx="' + idx + '">' +
          leading +
          '<div class="msg-main">' + head +
            '<div class="msg-bubble">' + inner +
              '<div class="msg-tools">' +
                (m.text ? '<button data-act="copy" title="复制">' + icon('copy', 'icon--sm') + '</button>' : '') +
                (m.type === 'image' && m.image
                  ? '<button data-act="save" title="保存">' + icon('download', 'icon--sm') + '</button>' : '') +
                '<button data-act="del" class="danger" title="删除">' + icon('trash', 'icon--sm') + '</button>' +
              '</div>' +
            '</div>' +
          '</div>' +
        '</div>';
      });

      body.innerHTML = html;
    }

    var AVATAR_COLORS = ['#2563eb', '#0891b2', '#7c3aed', '#db2777', '#d97706', '#059669', '#475569'];

    function colorFor(seed) {
      var s = String(seed || '?'), h = 0;
      for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 9973;
      return AVATAR_COLORS[h % AVATAR_COLORS.length];
    }

    function chatNearBottom() {
      var body = chatEl('[data-chat-body]');
      if (!body) return true;
      return body.scrollHeight - body.scrollTop - body.clientHeight < 80;
    }

    function chatScrollBottom() {
      var body = chatEl('[data-chat-body]');
      if (body) body.scrollTop = body.scrollHeight;
    }

    function chatUpdateJump() {
      var jump = chatEl('[data-jump]');
      if (!jump) return;
      var show = !chatNearBottom();
      jump.setAttribute('data-open', show ? 'true' : 'false');
      jump.innerHTML = icon('arrow', 'icon--sm') +
        (chat.pending ? '<span><b>' + chat.pending + '</b> 条新消息</span>' : '<span>回到底部</span>');
    }

    function chatLoad() {
      return apiFetch('/chat/history?room=' + encodeURIComponent(chat.room))
        .then(jsonOf)
        .then(function (list) {
          if (!Array.isArray(list)) return;
          chat.msgs = list;
          chat.lastId = list.length ? list[list.length - 1].id : '';
          chatRender();
          chatScrollBottom();
        })
        .catch(function () { });
    }

    function chatPoll() {
      if (document.hidden) return;
      if (!chat.lastId) { chatLoad(); return; }
      apiFetch('/chat/history?room=' + encodeURIComponent(chat.room) +
        '&after=' + encodeURIComponent(chat.lastId))
        .then(jsonOf)
        .then(function (list) {
          if (!Array.isArray(list) || !list.length) return;
          // 同样走 chatAppend：万一 SSE 和轮询短暂并存，也不会插出重复消息
          var atBottom = chatNearBottom();
          list.forEach(function (m) { chatAppend(m); });
          chat.lastId = list[list.length - 1].id;
          chatRender();
          if (atBottom) chatScrollBottom(); else chatUpdateJump();
        })
        .catch(function () { });
    }

    /* ── 实时推送：优先 SSE，连不上就退回轮询 ───────────────────────────── */

    function msgById(id) {
      for (var i = 0; i < chat.msgs.length; i++) {
        if (chat.msgs[i].id === id) return chat.msgs[i];
      }
      return null;
    }

    function chatAppend(m, mine) {    // 同一条消息可能从两条路到达：SSE 推送 + 自己的 POST 响应（或重连后的补发）。
      // 按 id 去重；重复了就只补一次滚动，绝不再插一条。
      for (var i = 0; i < chat.msgs.length; i++) {
        if (chat.msgs[i].id === m.id) {
          if (mine) chatScrollBottom();
          return;
        }
      }
      var atBottom = chatNearBottom();
      chat.msgs.push(m);
      chat.lastId = m.id;
      if (!atBottom && !mine) chat.pending += 1;
      chatRender();
      if (atBottom || mine) chatScrollBottom(); else chatUpdateJump();
    }

    function chatStreamStop() {
      if (chat.es) { chat.es.close(); chat.es = null; }
      if (chat.timer) { clearInterval(chat.timer); chat.timer = null; }
    }

    function chatStreamStart() {
      chatStreamStop();
      if (document.hidden) return;

      // 老浏览器没有 EventSource，直接走轮询
      if (typeof window.EventSource !== 'function') {
        chat.timer = setInterval(chatPoll, 2000);
        return;
      }

      var url = '/chat/stream?room=' + encodeURIComponent(chat.room) +
        (chat.lastId ? '&after=' + encodeURIComponent(chat.lastId) : '');
      var es;
      try {
        es = new EventSource(url);
      } catch (e) {
        chat.timer = setInterval(chatPoll, 2000);
        return;
      }
      chat.es = es;

      var failStreak = 0;
      es.onopen = function () { failStreak = 0; };

      es.onmessage = function (ev) {
        var m;
        try { m = JSON.parse(ev.data); } catch (e) { return; }
        if (m && m.id) chatAppend(m);
      };

      // 服务端 25 秒主动断一次属于正常情况，浏览器会自己重连（同一条 URL，带旧 after，
      // 靠 chatAppend 的 id 去重兜底）。连续失败 3 次才认为流不可用，退回轮询。
      es.onerror = function () {
        if (es.readyState === 2) failStreak = 3;   // 被彻底关掉
        else failStreak += 1;
        if (failStreak < 3) return;
        es.close();
        if (chat.es === es) chat.es = null;
        if (!chat.timer) chat.timer = setInterval(chatPoll, 2000);
      };
    }

    function chatClearImage() {
      chat.image = '';
      chat.thumb = '';
      var slot = chatEl('[data-image-preview]');
      if (slot) { slot.hidden = true; slot.innerHTML = ''; }
    }

    function chatShowImage(dataUrl) {
      var slot = chatEl('[data-image-preview]');
      if (!slot) return;
      slot.hidden = false;
      slot.innerHTML = '<img src="' + dataUrl + '" alt="待发送图片" ' +
        'style="height:56px;border-radius:8px;vertical-align:middle">' +
        '<button class="btn btn--sm btn--ghost" type="button" data-img-remove style="margin-left:8px">移除</button>';
    }

    /* 缩略图：最长边 320px 的 JPEG。聊天里只加载它，点开才取原图。
       图片解不开（比如某些 HEIC）就退回空串，服务端只存原图。 */
    var THUMB_MAX = 320;
    var THUMB_QUALITY = 0.72;

    function makeThumb(dataUrl, cb) {
      var img = new Image();
      img.onload = function () {
        try {
          var scale = Math.min(1, THUMB_MAX / Math.max(img.width, img.height));
          var w = Math.max(1, Math.round(img.width * scale));
          var h = Math.max(1, Math.round(img.height * scale));
          var canvas = document.createElement('canvas');
          canvas.width = w;
          canvas.height = h;
          canvas.getContext('2d').drawImage(img, 0, 0, w, h);
          cb(canvas.toDataURL('image/jpeg', THUMB_QUALITY));
        } catch (e) {
          cb('');
        }
      };
      img.onerror = function () { cb(''); };
      img.src = dataUrl;
    }

    function chatSend() {
      var input = chatEl('[data-chat-input]');
      if (!input) return;
      var text = input.value.trim();
      if (!text && !chat.image) return;

      var payload = { room: chat.room, text: text };
      if (chat.image) {
        payload.image = chat.image;
        if (chat.thumb) payload.thumb = chat.thumb;
      }
      var backup = chat.image;
      var backupThumb = chat.thumb;

      input.value = '';
      chatAutoGrow();
      chatClearImage();

      apiFetch('/chat/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      }).then(jsonOf).then(function (d) {
        if (d && d.success && d.msg) {
          // 走 chatAppend 而不是直接 push：这条消息可能已经被 SSE 先推回来了
          chatAppend(d.msg, true);
        } else {
          if (backup) {
            chat.image = backup;
            chat.thumb = backupThumb || '';
            chatShowImage(backup);
          }
          toast((d && d.message) || '发送失败', 'err');
        }
      }).catch(function () { toast('网络中断', 'err'); });
    }

    function chatDeleteMsg(id) {
      openModal({
        title: '删除消息', tone: 'danger', icon: 'trash', confirmText: '删除',
        desc: '删除后其他人也看不到了。',
        onConfirm: function () {
          // 走 writeFetch：设了管理密码时会先弹密码框再自动重试，
          // 直接用 apiFetch 只会拿到 401，然后干巴巴地说一句"删除失败"
          writeFetch('/chat/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: id, room: chat.room })
          }).then(jsonOf).then(function (d) {
            if (d && d.success) {
              chat.msgs = chat.msgs.filter(function (m) { return m.id !== id; });
              chat.lastId = chat.msgs.length ? chat.msgs[chat.msgs.length - 1].id : '';
              chatRender();
              toast('已删除', 'ok');
            } else {
              toast(Lan.api.explain(d, '删除失败'), 'err');
            }
          }).catch(function () { toast('网络中断', 'err'); });
        }
      });
    }

    function chatAutoGrow() {
      var input = chatEl('[data-chat-input]');
      var btn = chatEl('[data-chat-send]');
      if (!input) return;
      input.style.height = 'auto';
      input.style.height = Math.min(132, input.scrollHeight) + 'px';
      if (btn) btn.disabled = !input.value.trim() && !chat.image;
    }

    function initChatRoom() {
      var data = readJson('[data-room-json]');
      chat.room = (data && data.id) || '';
      chat.me = (data && data.me) || '';
      if (!chat.room) return;

      var body = chatEl('[data-chat-body]');
      var input = chatEl('[data-chat-input]');
      var sendBtn = chatEl('[data-chat-send]');
      var jump = chatEl('[data-jump]');
      var imgInput = document.getElementById('chatImage');
      var slot = chatEl('[data-image-preview]');
      var searchInput = chatEl('[data-chat-search]');

      if (searchInput) {
        searchInput.addEventListener('input', function () {
          chat.filter = searchInput.value.trim();
          chat.pending = 0;
          chatRender();
          if (!chat.filter) chatScrollBottom();
        });
        searchInput.addEventListener('keydown', function (e) {
          if (e.key === 'Escape') {
            searchInput.value = '';
            chat.filter = '';
            chatRender();
            chatScrollBottom();
          }
        });
      }

      if (body) {
        body.addEventListener('scroll', function () {
          if (chatNearBottom()) { chat.pending = 0; }
          chatUpdateJump();
        });
        // 图片文件被删掉时 <img> 会 404，控制台会报警。
        // error 事件不冒泡，只能在捕获阶段接住，然后换成占位提示。
        body.addEventListener('error', function (e) {
          var img = e.target;
          if (!img || img.tagName !== 'IMG' || !img.classList.contains('msg-img')) return;
          var broken = document.createElement('span');
          broken.className = 'msg-img-broken';
          broken.textContent = '图片已失效';
          if (img.parentNode) img.parentNode.replaceChild(broken, img);
        }, true);
        body.addEventListener('click', function (e) {
          var act = e.target.closest('[data-act]');
          var img = e.target.closest('[data-img]');
          if (img) {
            var row0 = img.closest('[data-id]');
            var m0 = row0 ? msgById(row0.getAttribute('data-id')) : null;
            if (m0) openLightbox(m0.image, '图片', m0.image);
            return;
          }
          if (!act) return;
          var row = act.closest('[data-id]');
          var m = row ? msgById(row.getAttribute('data-id')) : null;
          if (!m) return;
          var kind = act.getAttribute('data-act');
          if (kind === 'copy') {
            copyText(m.text || '');
          } else if (kind === 'save') {
            downloadUrl(m.image, '图片_' + (m.time || '').replace(':', '') + '.png');
          } else if (kind === 'del') {
            chatDeleteMsg(m.id);
          }
        });

        /* 双击复制（电脑双击 / 手机双击都走这里） */
        body.addEventListener('dblclick', function (e) {
          var bubble = e.target.closest('.msg-bubble');
          if (!bubble) return;
          var m = msgOf(bubble);
          if (m && m.text) copyText(m.text);
        });

        function msgOf(el) {
          var row = el.closest('[data-id]');
          return row ? msgById(row.getAttribute('data-id')) : null;
        }

        /* 手机长按 / 电脑右键 → 消息菜单 */
        var lpTimer = null, lpPoint = null;
        function clearLp() {
          if (lpTimer) { clearTimeout(lpTimer); lpTimer = null; }
        }
        body.addEventListener('touchstart', function (e) {
          var bubble = e.target.closest('.msg-bubble');
          if (!bubble) return;
          var t = e.touches[0];
          lpPoint = { x: t.clientX, y: t.clientY };
          lpTimer = setTimeout(function () {
            lpTimer = null;
            if (navigator.vibrate) navigator.vibrate(12);
            openMsgMenu(bubble, lpPoint);
          }, 520);
        }, { passive: true });
        body.addEventListener('touchmove', function (e) {
          if (!lpTimer || !lpPoint) return;
          var t = e.touches[0];
          if (Math.abs(t.clientX - lpPoint.x) > 12 ||
              Math.abs(t.clientY - lpPoint.y) > 12) clearLp();
        }, { passive: true });
        body.addEventListener('touchend', clearLp);
        body.addEventListener('touchcancel', clearLp);
        body.addEventListener('contextmenu', function (e) {
          var bubble = e.target.closest('.msg-bubble');
          if (!bubble) return;
          e.preventDefault();
          openMsgMenu(bubble, { x: e.clientX, y: e.clientY });
        });

        function openMsgMenu(bubble, point) {
          var m = msgOf(bubble);
          if (!m) return;
          var items = [];
          if (m.text) {
            items.push({
              key: 'copy', label: '复制文字', icon: 'copy',
              onSelect: function () { copyText(m.text); }
            });
          }
          if (m.type === 'image' && m.image) {
            items.push({
              key: 'save', label: '保存图片', icon: 'download',
              onSelect: function () { downloadUrl(m.image, '图片.png'); }
            });
          }
          if (items.length) items.push({ sep: true });
          items.push({
            key: 'del', label: '删除消息', icon: 'trash', danger: true,
            onSelect: function () { chatDeleteMsg(m.id); }
          });
          openMenu(bubble, items, point);
        }
      }

      if (jump) {
        jump.addEventListener('click', function () {
          chat.pending = 0;
          chatScrollBottom();
          chatUpdateJump();
        });
      }

      if (input) {
        input.addEventListener('input', chatAutoGrow);
        input.addEventListener('keydown', function (e) {
          if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
            e.preventDefault();
            chatSend();
          }
        });
        input.addEventListener('paste', function (e) {
          var items = e.clipboardData && e.clipboardData.items;
          if (!items) return;
          for (var i = 0; i < items.length; i++) {
            if (items[i].type.indexOf('image') !== -1) {
              var f = items[i].getAsFile();
              if (f) { readImage(f); e.preventDefault(); }
              break;
            }
          }
        });
      }

      if (sendBtn) sendBtn.addEventListener('click', chatSend);
      if (slot) slot.addEventListener('click', function (e) {
        if (e.target.closest('[data-img-remove]')) chatClearImage();
      });

      function readImage(file) {
        if (file.size > 5 * 1024 * 1024) { toast('图片不能超过 5MB', 'err'); return; }
        var reader = new FileReader();
        reader.onload = function (ev) {
          chat.image = ev.target.result;
          chat.thumb = '';
          chatShowImage(chat.image);
          chatAutoGrow();
          // 顺手做一张缩略图：聊天列表只加载它，点开才取原图
          makeThumb(chat.image, function (thumb) {
            if (chat.image === ev.target.result) chat.thumb = thumb || '';
          });
        };
        reader.readAsDataURL(file);
      }

      var imgBtn = document.querySelector('[data-chat-image]');
      if (imgBtn && imgInput) imgBtn.addEventListener('click', function () { imgInput.click(); });
      if (imgInput) imgInput.addEventListener('change', function (e) {
        if (e.target.files[0]) readImage(e.target.files[0]);
        e.target.value = '';
      });

      var clearBtn = document.querySelector('[data-chat-clear]');
      if (clearBtn) {
        clearBtn.addEventListener('click', function () {
          openModal({
            title: '清空聊天记录', tone: 'danger', icon: 'trash', confirmText: '清空',
            desc: '将删除本聊天室的<strong>全部消息</strong>，此操作不可撤销。',
            onConfirm: function () {
              writeFetch('/chat/clear', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ room: chat.room })
              }).then(jsonOf).then(function (d) {
                if (d && d.success) { chat.msgs = []; chat.lastId = ''; chatRender(); toast('已清空', 'ok'); }
                else toast(Lan.api.explain(d, '清空失败'), 'err');
              }).catch(function () { toast('网络中断', 'err'); });
            }
          });
        });
      }

      chatAutoGrow();
      chatLoad().then(function () {
        chatStreamStart();
      });
      // 切到后台就断开，省一条长连接；回来先补一次历史，再重连
      document.addEventListener('visibilitychange', function () {
        if (document.hidden) {
          chatStreamStop();
        } else {
          chatPoll();
          chatStreamStart();
        }
      });
      window.addEventListener('pagehide', chatStreamStop);
    }


    /* 对外只暴露这些 */
    Object.assign(Lan.chat, {
      state: chat, init: initChatRoom, render: chatRender, append: chatAppend,
      send: chatSend, load: chatLoad, poll: chatPoll, deleteMsg: chatDeleteMsg,
      streamStart: chatStreamStart, streamStop: chatStreamStop
    });
  })(window.Lan = window.Lan || {});
