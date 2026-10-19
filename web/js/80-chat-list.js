  /* ───────────── 模块：聊天室列表页（Lan.pages.chatList） ───────────── */

  (function (Lan) {
    'use strict';

    /* 依赖：显式从命名空间取，缺一个就报 ReferenceError */
    var esc = Lan.util.esc;
    var readJson = Lan.util.readJson;
    var icon = Lan.icons.icon;
    var toast = Lan.ui.toast;
    var openModal = Lan.ui.openModal;
    var openMenu = Lan.ui.openMenu;
    var apiFetch = Lan.api.fetch;
    var jsonOf = Lan.api.json;
    var writeFetch = Lan.api.write;

    /* ───────────── 聊天室列表 ───────────── */

    function initChatList() {
      var data = readJson('[data-room-json]');
      var rooms = (data && data.rooms) || [];
      var list = document.querySelector('[data-room-list]');
      var emptyEl = document.querySelector('[data-room-empty]');
      var countEl = document.querySelector('[data-room-count]');
      if (!list) return;

      list.innerHTML = rooms.map(function (r) {
        return '<div class="row" data-room="' + esc(r.id) + '" data-name="' + esc(r.name) + '" tabindex="0">' +
          '<span class="row-icon" data-tone="doc">' + icon('chat') + '</span>' +
          '<div class="row-main">' +
            '<div class="row-name">' + esc(r.name) + '</div>' +
            '<div class="row-sub">' + r.count + ' 条消息 · ' + esc(r.createdText) + '</div>' +
          '</div>' +
          '<div class="row-size tnum">' + r.count + ' 条</div>' +
          '<div class="row-time tnum">' + esc(r.createdText) + '</div>' +
          '<button class="row-more" data-menu-trigger aria-label="更多操作" aria-expanded="false">' +
            icon('more') + '</button>' +
          '</div>';
      }).join('');

      if (emptyEl) emptyEl.hidden = rooms.length > 0;
      list.hidden = rooms.length === 0;
      if (countEl) countEl.textContent = rooms.length + ' 个聊天室';

      function roomOf(el) {
        var id = el.getAttribute('data-room');
        return rooms.find(function (r) { return r.id === id; });
      }

      list.addEventListener('click', function (e) {
        var more = e.target.closest('[data-menu-trigger]');
        var row = e.target.closest('[data-room]');
        if (!row) return;
        var room = roomOf(row);
        if (more) {
          openMenu(more, [
            {
              key: 'open', label: '进入', icon: 'chat',
              onSelect: function () { location.href = '/chat/room?id=' + encodeURIComponent(room.id); }
            },
            { sep: true },
            {
              key: 'del', label: '删除', icon: 'trash', danger: true,
              onSelect: function () { askDeleteRoom(room); }
            }
          ]);
          return;
        }
        location.href = '/chat/room?id=' + encodeURIComponent(room.id);
      });

      list.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter') return;
        var row = e.target.closest('[data-room]');
        if (row) location.href = '/chat/room?id=' + encodeURIComponent(row.getAttribute('data-room'));
      });

      function askDeleteRoom(room) {
        openModal({
          title: '删除聊天室', tone: 'danger', icon: 'trash', confirmText: '删除',
          desc: '确定删除 <strong>' + esc(room.name) + '</strong> 吗？里面的 ' + room.count + ' 条消息会一起删除。',
          onConfirm: function () {
            writeFetch('/chat/delete_room', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ id: room.id })
            }).then(jsonOf).then(function (d) {
              if (d && d.success) { toast('已删除', 'ok'); location.reload(); }
              else toast((d && d.message) || '删除失败', 'err');
            }).catch(function () { toast('网络中断', 'err'); });
          }
        });
      }

      document.querySelectorAll('[data-new-room]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          openModal({
            title: '新建聊天室', icon: 'plus', confirmText: '创建',
            desc: '创建后同一局域网的人都能进来。',
            field: { label: '名称', value: '', placeholder: '例如：运维值班' },
            validate: function (v) {
              if (!v) return '请输入名称';
              if (v.length > 50) return '名称最长 50 个字';
              return '';
            },
            onConfirm: function (v) {
              apiFetch('/chat/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: v })
              }).then(jsonOf).then(function (d) {
                if (d && d.success) location.reload();
                else toast('创建失败', 'err');
              }).catch(function () { toast('网络中断', 'err'); });
            }
          });
        });
      });
    }


    /* 对外只暴露这些 */
    Lan.pages.chatList = initChatList;
  })(window.Lan = window.Lan || {});
