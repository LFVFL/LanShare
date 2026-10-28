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
      image: '', thumb: '', filter: '', revision: '', name: '', media: null,
      sending: false, deleted: false, client: '', draftId: ''
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

    function setRoomName(name) {
      chat.name = name || chat.name;
      document.querySelectorAll('[data-chat-name],.mobile-bar-title').forEach(function (el) {
        el.textContent = chat.name;
      });
      if (chat.name) document.title = chat.name + ' · 局域网共享';
    }

    function revisionIsOld(revision) {
      if (!revision || !chat.revision) return false;
      var a = revision.split(':'), b = chat.revision.split(':');
      return a[0] === b[0] && Number(a[1]) <= Number(b[1]);
    }

    function applySnapshot(data) {
      if (!data || !data.success) return;
      chat.msgs = data.messages || [];
      chat.lastId = chat.msgs.length ? chat.msgs[chat.msgs.length - 1].id : '';
      chat.revision = data.revision || '';
      chat.deleted = !!data.deleted;
      if (data.room && data.room.name) setRoomName(data.room.name);
      if (chat.deleted) {
        chatStreamStop();
        toast('聊天室已删除，请返回列表', 'err');
      }
      chatRender();
      chatAutoGrow();
    }

    function applyChatEvent(type, data, revision) {
      if (revisionIsOld(revision)) return;
      var atBottom = chatNearBottom();
      if (type === 'reset') {
        applySnapshot(data);
      } else if (type === 'message') {
        if (data && data.id) chatAppend(data);
      } else if (type === 'delete') {
        chat.msgs = chat.msgs.filter(function (m) { return m.id !== data.id; });
        chatRender();
      } else if (type === 'clear') {
        chat.msgs = []; chat.lastId = ''; chat.pending = 0; chatRender();
      } else if (type === 'room_updated') {
        setRoomName(data.name);
      } else if (type === 'room_deleted') {
        applySnapshot({ success: true, deleted: true, messages: [], revision: revision });
      }
      if (revision) chat.revision = revision;
      if (atBottom) chatScrollBottom(); else chatUpdateJump();
    }

    function readSync(data) {
      if (!data || !data.success) return;
      if (data.reset) {
        if (!revisionIsOld(data.revision) || !chat.revision) applySnapshot(data);
      } else {
        (data.events || []).forEach(function (event) {
          applyChatEvent(event.type, event.data, event.revision);
        });
      }
    }

    function chatLoad() {
      return apiFetch('/chat/history?sync=1&room=' + encodeURIComponent(chat.room))
        .then(jsonOf).then(function (data) { readSync(data); chatScrollBottom(); })
        .catch(function () { toast('加载聊天记录失败，请检查连接', 'err'); });
    }

    function chatPoll() {
      if (document.hidden || chat.deleted) return Promise.resolve();
      if (!chat.revision) return chatLoad();
      return apiFetch('/chat/history?sync=1&room=' + encodeURIComponent(chat.room) +
        '&since=' + encodeURIComponent(chat.revision)).then(jsonOf).then(readSync).catch(function () { });
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
      if (document.hidden || chat.deleted) return;

      // 老浏览器没有 EventSource，直接走轮询
      if (typeof window.EventSource !== 'function') {
        chat.timer = setInterval(chatPoll, 2000);
        return;
      }

      var url = '/chat/stream?sync=1&room=' + encodeURIComponent(chat.room) +
        (chat.revision ? '&since=' + encodeURIComponent(chat.revision) : '');
      var es;
      try {
        es = new EventSource(url);
      } catch (e) {
        chat.timer = setInterval(chatPoll, 2000);
        return;
      }
      chat.es = es;

      var failStreak = 0;

      // SSE 一旦重新连上，就把之前为兜底开的轮询停掉。
      // 服务端每 25 秒主动断一次是正常设计，偶然凑满 3 次失败会启动轮询；
      // 这里不清的话，SSE 恢复后两条链路会一直并存，消息被拉两遍（流量、耗电都翻倍）。
      es.onopen = function () {
        failStreak = 0;
        if (chat.timer) { clearInterval(chat.timer); chat.timer = null; }
      };

      es.onmessage = function (ev) {
        var m;
        try { m = JSON.parse(ev.data); } catch (e) { return; }
        if (m && m.id) applyChatEvent('message', m, ev.lastEventId);
      };

      ['reset', 'delete', 'clear', 'room_updated', 'room_deleted'].forEach(function (type) {
        es.addEventListener(type, function (event) {
          try { applyChatEvent(type, JSON.parse(event.data), event.lastEventId); } catch (e) { }
        });
      });

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
      var media = chat.media;
      chat.media = null;
      chat.image = '';
      chat.thumb = '';
      if (media) {
        media.cancelled = true;
        if (media.xhr) media.xhr.abort();
        if (media.url) URL.revokeObjectURL(media.url);
        if (media.upload) mediaJson('/chat/media/cancel', { upload: media.upload }).catch(function () { });
      }
      var slot = chatEl('[data-image-preview]');
      if (slot) { slot.hidden = true; slot.innerHTML = ''; }
      chatAutoGrow();
    }

    function chatShowImage() {
      var slot = chatEl('[data-image-preview]');
      var media = chat.media;
      if (!slot || !media) return;
      slot.hidden = false;
      var label = media.status === 'ready' ? '上传完成，待发送（原图保留）' :
        media.status === 'failed' ? media.error : media.progress >= 100 ? '正在安全解码/生成预览…' : '上传 ' + media.progress + '%';
      slot.innerHTML = '<img class="chat-media-preview" src="' + esc(media.thumb || media.url) + '" alt="待发送图片">' +
        '<div class="chat-media-info"><span class="chat-media-name">' + esc(media.file.name) + '</span>' +
        '<span class="chat-media-status" role="status">' + esc(label) + '</span>' +
        '<progress max="100" value="' + media.progress + '" aria-label="图片上传进度"></progress></div>' +
        (media.status === 'failed' ? '<button class="btn btn--sm btn--ghost" type="button" data-img-retry>重试</button>' : '') +
        '<button class="btn btn--sm btn--ghost" type="button" data-img-remove>' +
        (media.status === 'uploading' ? '取消上传' : '移除') + '</button>';
    }

    function randomId() {
      var bytes = new Uint8Array(16);
      window.crypto.getRandomValues(bytes);
      return Array.from(bytes).map(function (value) { return ('0' + value.toString(16)).slice(-2); }).join('');
    }

    function mediaJson(url, body) {
      return apiFetch(url, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Chat-Client': chat.client },
        body: JSON.stringify(body)
      }).then(function (response) {
        return jsonOf(response).then(function (data) {
          if (!response.ok || !data || !data.success) throw new Error((data && data.message) || '图片上传失败');
          return data;
        });
      });
    }

    function uploadChunk(media, blob, offset) {
      return new Promise(function (resolve, reject) {
        var xhr = new XMLHttpRequest();
        media.xhr = xhr;
        xhr.open('POST', '/chat/media/chunk?upload=' + encodeURIComponent(media.upload) + '&offset=' + offset);
        xhr.setRequestHeader('Content-Type', 'application/octet-stream');
        xhr.setRequestHeader('X-Chat-Client', chat.client);
        xhr.timeout = 120000;
        xhr.upload.onprogress = function (event) {
          if (chat.media !== media) return;
          media.progress = Math.min(100, Math.floor(100 * (offset + event.loaded) / media.file.size));
          chatShowImage();
        };
        xhr.onload = function () {
          var data;
          try { data = JSON.parse(xhr.responseText); } catch (e) { }
          media.xhr = null;
          if (xhr.status >= 200 && xhr.status < 300 && data && data.success) resolve(data);
          else reject(new Error((data && data.message) || '图片上传失败，请重试'));
        };
        xhr.onerror = xhr.ontimeout = function () { media.xhr = null; reject(new Error('图片上传网络中断，可重试')); };
        xhr.onabort = function () { media.xhr = null; reject(new Error('已取消图片上传')); };
        xhr.send(blob);
      });
    }

    function uploadImage(media) {
      media.status = 'uploading'; media.error = ''; media.progress = 0;
      chatShowImage(); chatAutoGrow();
      mediaJson('/chat/media/start', { room: chat.room, name: media.file.name, size: media.file.size })
        .then(function (data) {
          media.upload = data.upload;
          function next(offset) {
            if (media.cancelled || chat.media !== media) throw new Error('已取消图片上传');
            if (offset >= media.file.size) return mediaJson('/chat/media/complete', { upload: media.upload });
            return uploadChunk(media, media.file.slice(offset, offset + data.chunk_bytes), offset)
              .then(function (chunk) { return next(chunk.offset); });
          }
          return next(0);
        }).then(function (data) {
          if (media.cancelled || chat.media !== media) {
            mediaJson('/chat/media/cancel', { upload: media.upload }).catch(function () { });
            return;
          }
          media.id = data.media; media.thumb = data.thumb; media.status = 'ready'; media.progress = 100;
          chatShowImage(); chatAutoGrow();
        }).catch(function (error) {
          if (media.upload) mediaJson('/chat/media/cancel', { upload: media.upload }).catch(function () { });
          media.upload = '';
          if (media.cancelled || chat.media !== media) return;
          media.status = 'failed'; media.error = error.message || '图片上传失败，可重试';
          chatShowImage(); chatAutoGrow();
          toast(media.error, 'err');
        });
    }

    function chatSend() {
      var input = chatEl('[data-chat-input]');
      if (!input) return;
      var text = input.value;
      var media = chat.media;
      if (chat.sending || chat.deleted || (!text.trim() && !media)) return;
      if (media && media.status !== 'ready') { toast('请等待图片上传完成，或移除/重试图片', 'info'); return; }
      if (!chat.pendingDraft || chat.pendingDraft.text !== text || chat.pendingDraft.media !== media) {
        chat.pendingDraft = { text: text, media: media, id: randomId() };
      }
      var payload = { room: chat.room, text: text, client_id: chat.pendingDraft.id };
      if (media) payload.media = media.id;
      chat.sending = true;
      chatAutoGrow();

      apiFetch('/chat/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Chat-Client': chat.client },
        body: JSON.stringify(payload)
      }).then(jsonOf).then(function (d) {
        if (d && d.success && d.msg) {
          // 走 chatAppend 而不是直接 push：这条消息可能已经被 SSE 先推回来了
          chatAppend(d.msg, true);
          if (input.value === text) input.value = '';
          if (chat.media === media) {
            if (media) media.upload = '';
            chatClearImage();
          }
          chat.pendingDraft = null;
        } else {
          toast((d && d.message) || '发送失败，草稿已保留，可重试', 'err');
        }
      }).catch(function () { toast('网络中断，草稿和图片已保留，可重试', 'err'); })
        .then(function () { chat.sending = false; chatAutoGrow(); });
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
      if (btn) {
        btn.disabled = chat.sending || chat.deleted || (chat.media && chat.media.status !== 'ready') ||
          (!input.value.trim() && !chat.media);
        btn.textContent = chat.sending ? '发送中…' : '发送';
      }
    }

    function initChatRoom() {
      var data = readJson('[data-room-json]');
      chat.room = (data && data.id) || '';
      chat.me = (data && data.me) || '';
      chat.name = (data && data.name) || '';
      if (!chat.room) return;
      try {
        chat.client = sessionStorage.getItem('lan-chat-client') || randomId();
        sessionStorage.setItem('lan-chat-client', chat.client);
      } catch (e) { chat.client = randomId(); }

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
            if (m0) openLightbox(m0.preview || m0.image, m0.name || '图片', m0.image + '?download=1');
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
            downloadUrl(m.image + '?download=1', m.name || ('图片_' + (m.time || '').replace(':', '') + '.png'));
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
              onSelect: function () { downloadUrl(m.image + '?download=1', m.name || '图片.png'); }
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
          if (!e.clipboardData) return;
          items = items || [];
          for (var i = 0; i < items.length; i++) {
            if (items[i].type.indexOf('image') !== -1) {
              var f = items[i].getAsFile();
              if (f) { readImage(f); e.preventDefault(); }
              return;
            }
          }
          var text = e.clipboardData.getData('text/plain');
          if (text) {
            e.preventDefault();
            input.setRangeText(text.replace(/\r\n?/g, '\n'), input.selectionStart, input.selectionEnd, 'end');
            chatAutoGrow();
          }
        });
      }

      if (sendBtn) sendBtn.addEventListener('click', chatSend);
      if (slot) slot.addEventListener('click', function (e) {
        if (chat.sending) return;
        if (e.target.closest('[data-img-remove]')) chatClearImage();
        if (e.target.closest('[data-img-retry]') && chat.media) uploadImage(chat.media);
      });

      function readImage(file) {
        if (chat.sending) { toast('消息正在发送，请稍后选择图片', 'info'); return; }
        if (/\.svgz?$/i.test(file.name) || file.type === 'image/svg+xml') {
          toast('SVG 不能作为聊天图片发送，请先转为 PNG/JPEG', 'err'); return;
        }
        chatClearImage();
        var media = { file: file, url: URL.createObjectURL(file), status: 'uploading', progress: 0, cancelled: false };
        chat.media = media; chat.image = media.url;
        uploadImage(media);
      }

      var imgBtn = document.querySelector('[data-chat-image]');
      if (imgBtn && imgInput) imgBtn.addEventListener('click', function () { imgInput.click(); });
      if (imgInput) imgInput.addEventListener('change', function (e) {
        if (e.target.files[0]) readImage(e.target.files[0]);
        e.target.value = '';
      });

      apiFetch('/chat/media/config').then(jsonOf).then(function (config) {
        if (!config || !config.success) return;
        if (imgBtn) {
          imgBtn.disabled = !config.available;
          imgBtn.title = config.available ? '' : '服务器暂不支持图片上传';
        }
      }).catch(function () { });

      var mobileActions = document.querySelector('.mobile-bar-actions');
      var mobileRename = chatEl('.chat-mobile-rename');
      if (mobileActions && mobileRename) mobileActions.appendChild(mobileRename);

      document.querySelectorAll('[data-chat-rename]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          openModal({
            title: '修改群名', icon: 'edit', confirmText: '保存',
            desc: '默认群聊也可以改名；需要管理员权限。',
            field: { label: '群名', value: chat.name },
            validate: function (v) { return !v ? '请输入群名' : v.length > 50 ? '群名最长 50 个字' : ''; },
            onConfirm: function (name) {
              writeFetch('/chat/rename_room', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: chat.room, name: name })
              }).then(jsonOf).then(function (result) {
                if (result && result.success) { setRoomName(result.name); toast('群名已更新', 'ok'); }
                else toast(Lan.api.explain(result, '修改群名失败'), 'err');
              }).catch(function () { toast('网络中断，群名未修改', 'err'); });
            }
          });
        });
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
          chatPoll().then(chatStreamStart);
        }
      });
      window.addEventListener('pagehide', chatStreamStop);
    }


    /* 对外只暴露这些 */
    Object.assign(Lan.chat, {
      state: chat, init: initChatRoom, render: chatRender, append: chatAppend,
      send: chatSend, load: chatLoad, poll: chatPoll, deleteMsg: chatDeleteMsg,
      streamStart: chatStreamStart, streamStop: chatStreamStop,
      applyEvent: applyChatEvent, applySnapshot: applySnapshot, clearImage: chatClearImage
    });
  })(window.Lan = window.Lan || {});
