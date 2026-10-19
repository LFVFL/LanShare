"""聊天接口：页面、房间列表、历史、图片、增删改、SSE 推送。

原来这些逻辑散在 do_GET / do_POST 里，一个分支三四十行；
现在每个接口一个方法，路由表只写「路径 → 方法名」。
"""
import json
import mimetypes
import queue
import shutil
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

from .config import *
from .paths import *
from .storage import *
from .chat_store import *
from .pages import *
from .paths import _password_ok
from .storage import _upload_key


# ══════════════════════════════════════════════════════════════════════════
# SSE 广播：所有连接挂在同一张订阅表上，由一个线程统一扫描房间
# ══════════════════════════════════════════════════════════════════════════
_subscribers = {}                  # sub_id -> {'queue': Queue, 'room': str, 'after': str}
_sub_lock = threading.Lock()
_broadcaster_started = False
_broadcaster_guard = threading.Lock()


def _ensure_broadcaster():
    """第一次有人连流时启动广播线程（全局只有一个）。"""
    global _broadcaster_started
    with _broadcaster_guard:
        if _broadcaster_started:
            return
        _broadcaster_started = True
        threading.Thread(target=_broadcast_loop, daemon=True,
                         name="sse-broadcast").start()


def _broadcast_loop():
    """等 chat_event（有人发消息）或超时，然后把新消息分发给各自的队列。"""
    while True:
        try:
            with chat_event:
                chat_event.wait(0.5)
        except Exception:
            time.sleep(0.5)

        with _sub_lock:
            subs = list(_subscribers.items())
        if not subs:
            continue

        rooms = {}
        with chat_lock:
            for sub_id, sub in subs:
                room_id = sub['room']
                if room_id not in rooms:
                    rooms[room_id] = CHAT_ROOMS.get(room_id, {}).get('messages', [])[:]
                msgs = rooms[room_id]

                after = sub['after']
                fresh = []
                if after:
                    idx = next((i for i, m in enumerate(msgs)
                                if m.get('id') == after), None)
                    if idx is not None:
                        fresh = msgs[idx + 1:]
                if not fresh:
                    continue
                sub['after'] = fresh[-1].get('id') or after
                for msg in fresh:
                    try:
                        sub['queue'].put_nowait(msg)
                    except queue.Full:
                        # 客户端读得太慢：丢连接，让它重连（重连会带 after 补齐）
                        _subscribers.pop(sub_id, None)
                        break


class ChatApiMixin:
    """聊天相关路由的处理函数，鉴权方式与原实现逐条对齐。"""
    # ── SSE 推送的限额 ──────────────────────────────────────────────────
    # 每条流会占住一个处理线程，所以既限条数也限寿命：
    # 满了回 503、到点主动断，前端两种情况都会自动退回轮询。
    STREAM_MAX = 8
    STREAM_LIFETIME = 25.0      # 秒；到期让浏览器重连，顺便回收线程
    STREAM_TICK = 0.5           # 秒；队列取不到消息时的等待上限（不走网络）
    STREAM_BEAT = 10.0          # 秒；没消息时的心跳，用来探活
    _stream_slots = threading.BoundedSemaphore(STREAM_MAX)

    # ── 页面 ────────────────────────────────────────────────────────────
    def page_chat_list(self):
        if not self.check_auth():
            self.redirect('/login')
            return
        self.send_chat_list_page()

    def page_chat_room(self):
        if not self.check_auth():
            self.redirect('/login')
            return
        room_id = self.q('id')
        if room_id and room_id in CHAT_ROOMS:
            self.send_chat_room_page(room_id)
        else:
            self.send_error(404, "Room not found")

    # ── 读接口 ──────────────────────────────────────────────────────────
    def api_chat_rooms(self):
        if not self.check_auth():
            self.send_error(403, "Forbidden")
            return
        with chat_lock:
            rooms_list = [
                {
                    'id': rid,
                    'name': room['name'],
                    'created': room['created'],
                    'message_count': len(room.get('messages', [])),
                    'owner': room.get('owner', '')
                }
                for rid, room in CHAT_ROOMS.items()
            ]
        rooms_list.sort(key=lambda r: r['created'])
        self.send_json_response(rooms_list)

    def api_chat_history(self):
        if not self.check_auth():
            self.send_error(403, "Forbidden")
            return
        room_id = self.q('room', 'default')
        after_id = self.q('after')
        with chat_lock:
            if room_id not in CHAT_ROOMS:
                self.send_error(404, "Room not found")
                return
            messages = CHAT_ROOMS[room_id].get('messages', [])[:]
        if after_id:
            result = []
            found = False
            for msg in messages:
                if found:
                    result.append(msg)
                elif msg.get('id') == after_id:
                    found = True
            if not found:
                result = []
        else:
            result = messages

        # 老消息只有原图：顺手补缩略图（每次最多 20 张，别让首屏卡住）
        backfilled = 0
        for msg in result:
            if backfilled >= 20:
                break
            if (msg.get('type') == 'image' and msg.get('image')
                    and not msg.get('thumb')):
                thumb = ensure_thumb(msg['image'])
                if thumb:
                    msg['thumb'] = thumb
                    backfilled += 1
        if backfilled:
            chat_room_saver.mark_dirty()
            print(f"[Chat] 已为 {backfilled} 条老消息补出缩略图")

        self.send_json_response(result)

    def serve_chat_image(self, path):
        if not self.check_auth():
            self.send_error(403, "Forbidden")
            return

        filename = urllib.parse.unquote(path.replace('/chat/image/', '', 1))
        filename = Path(filename).name
        if not filename:
            self.send_error(404, "Image not found")
            return

        image_path = (CHAT_IMAGE_DIR / filename).resolve()
        if not is_path_within(image_path, CHAT_IMAGE_DIR):
            self.send_error(403, "Access denied")
            return
        if not image_path.exists() or not image_path.is_file():
            self.send_error(404, "Image not found")
            return

        content_type = mimetypes.guess_type(str(image_path))[0] or 'application/octet-stream'
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(image_path.stat().st_size))
        self.send_header('Cache-Control', 'private, max-age=86400')
        self.end_headers()
        with open(image_path, 'rb') as f:
            shutil.copyfileobj(f, self.wfile)

    # ── SSE：新消息直接推给浏览器，省掉轮询 ─────────────────────────────
    def _sse_send(self, text):
        """写一帧并立刻 flush；连不上就是抛异常，由调用方收尾。"""
        self.wfile.write(text.encode('utf-8'))
        self.wfile.flush()

    def api_chat_stream(self):
        """GET /chat/stream?room=<id>&after=<最后一条消息 id>

        只推 after 之后的新消息；客户端按 id 去重，所以断线重连不会重复上屏。
        没有 Content-Length —— 流式响应靠关连接结束，所以显式 Connection: close。

        实现上不再让每条连接自己扫房间：所有连接挂到一个订阅表上，
        由一个广播线程统一扫描、把新消息塞进各自的队列。N 个观众只有 1 份扫描，
        而且新消息仍然由 `chat_event` 立刻叫醒，延迟不变。
        """
        if not self.check_auth():
            self.send_error(403, "Forbidden")
            return

        room_id = self.q('room', 'default')
        after = self.q('after')

        with chat_lock:
            if room_id not in CHAT_ROOMS:
                self.send_error(404, "Room not found")
                return
            if not after:
                msgs = CHAT_ROOMS[room_id].get('messages', [])
                after = msgs[-1].get('id', '') if msgs else ''

        if not self._stream_slots.acquire(blocking=False):
            # 名额满了：让前端退回轮询，别把线程堆在这里
            self.send_error(503, "Too many streams")
            return

        sub_id = uuid.uuid4().hex
        inbox = queue.Queue(maxsize=256)
        with _sub_lock:
            _subscribers[sub_id] = {'queue': inbox, 'room': room_id, 'after': after}
        _ensure_broadcaster()

        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.send_header('X-Accel-Buffering', 'no')
            self.end_headers()
            self.close_connection = True

            # 断线后 3 秒再连；这条注释同时也是"连上了"的凭据
            self._sse_send('retry: 3000\n\n')

            deadline = time.time() + self.STREAM_LIFETIME
            last_beat = time.time()
            while time.time() < deadline:
                try:
                    msg = inbox.get(timeout=self.STREAM_TICK)
                except queue.Empty:
                    msg = None
                if msg is not None:
                    self._sse_send('data: ' +
                                   json.dumps(msg, ensure_ascii=False) + '\n\n')

                if time.time() - last_beat >= self.STREAM_BEAT:
                    # 心跳顺便探活：对端没了，这里就会抛
                    self._sse_send(': ping\n\n')
                    last_beat = time.time()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass                          # 浏览器关页面 / 断网，正常收场
        finally:
            with _sub_lock:
                _subscribers.pop(sub_id, None)
            self._stream_slots.release()

    # ── 写接口 ──────────────────────────────────────────────────────────
    def api_chat_create(self):
        if not self.check_auth():
            self.send_error(403, "Forbidden")
            return
        data = self._read_json()
        if data is None:
            return
        room_name = data.get('name', '').strip()
        if not room_name or len(room_name) > 50:
            self.send_error(400, "Bad Request")
            return
        room_id = str(uuid.uuid4())
        with chat_lock:
            CHAT_ROOMS[room_id] = {
                'id': room_id, 'name': room_name,
                'messages': [], 'created': time.time()
            }
        chat_room_saver.mark_dirty()
        self.send_json_response({'success': True, 'room_id': room_id})
        print(f"[Chat] 创建新聊天室: {room_name} (ID: {room_id})")

    def api_chat_delete_room(self):
        if not self.check_admin_auth():
            self.send_api_error("Admin Required", 401)
            return

        data = self._read_json()
        if data is None:
            return

        room_id = data.get('id', '')
        if room_id == 'default':
            self.send_json_response({
                'success': False,
                'message': 'Cannot delete default room'
            })
            return

        with chat_lock:
            room = CHAT_ROOMS.pop(room_id, None)

        if not room:
            self.send_api_error("Room not found", 404)
            return

        for msg in room.get('messages', []):
            delete_chat_image_file(msg.get('image', ''))

        chat_room_saver.flush()
        print(f"[Chat] 删除聊天室: {room.get('name', room_id)}")
        self.send_json_response({'success': True})

    def api_chat_send(self):
        if not self.check_auth():
            self.send_error(403, "Forbidden")
            return

        data = self._read_json()
        if data is None:
            return

        room_id = data.get('room', 'default')
        text = data.get('text', '').strip()
        image = data.get('image', '')
        thumb = data.get('thumb', '')

        # 先检查房间是否存在，再保存图片。
        # 避免 room_id 无效时图片已经落盘，产生孤儿图片。
        with chat_lock:
            if room_id not in CHAT_ROOMS:
                self.send_error(404, "Room not found")
                return

        if image and len(image) > MAX_CHAT_IMAGE_BASE64_LEN:
            self.send_json_response({
                'success': False,
                'message': '图片太大'
            }, 400)
            return

        if not text and not image:
            self.send_error(400, "Bad Request")
            return

        image_url = ""
        thumb_url = ""
        if image:
            image_url = save_chat_base64_image(image)
            if not image_url:
                self.send_json_response({
                    'success': False,
                    'message': '图片保存失败，可能是格式不支持或图片过大'
                }, 400)
                return
            # 缩略图是可选的：前端用 canvas 生成，老客户端没有就只存原图。
            # 用原图的文件名主体，这样删消息时缩略图能跟着删掉。
            if thumb:
                stem = Path(image_url).stem
                thumb_url = save_chat_base64_image(thumb, thumb=True, stem=stem) or ""

        msg = {
            'id': str(uuid.uuid4()),
            'sender': self.client_address[0],
            'text': text,
            'image': image_url,
            'thumb': thumb_url,
            'type': 'image' if image_url else 'text',
            'time': time.strftime('%H:%M'),
            'date': time.strftime('%Y-%m-%d'),
            'timestamp': time.time()
        }

        with chat_lock:
            room_msgs = CHAT_ROOMS[room_id]['messages']
            room_msgs.append(msg)

            if len(room_msgs) > MAX_CHAT_LEN:
                removed_msgs = room_msgs[:-MAX_CHAT_LEN]
                for old_msg in removed_msgs:
                    delete_chat_image_file(old_msg.get('image', ''))
                CHAT_ROOMS[room_id]['messages'] = room_msgs[-MAX_CHAT_LEN:]

        chat_room_saver.mark_dirty()
        notify_chat_event()          # 叫醒 SSE 连接，立刻把新消息推出去
        self.send_json_response({
            'success': True,
            'msg': msg
        })

    def api_chat_delete_msg(self):
        if not self.check_auth():
            self.send_error(403, "Forbidden")
            return
        data = self._read_json()
        if data is None:
            return
        room_id = data.get('room', 'default')
        msg_id = data.get('id', '')
        with chat_lock:
            if room_id not in CHAT_ROOMS or not msg_id:
                self.send_error(400, "Bad Request")
                return
            messages = CHAT_ROOMS[room_id]['messages']
            target_msg = next((m for m in messages if m.get('id') == msg_id), None)
            if target_msg is None:
                self.send_error(404, "Message not found")
                return
            is_sender = target_msg.get('sender') == self.client_address[0]
            if not is_sender and not self.check_admin_auth():
                self.send_error(401, "Not authorized")
                return
            delete_chat_image_file(target_msg.get('image', ''))
            CHAT_ROOMS[room_id]['messages'] = [m for m in messages if m.get('id') != msg_id]
        chat_room_saver.mark_dirty()
        notify_chat_event()
        self.send_json_response({'success': True})

    def api_chat_clear(self):
        if not self.check_admin_auth():
            self.send_error(401, "Admin Required")
            return
        data = self._read_json()
        if data is None:
            return
        room_id = data.get('room', 'default')
        with chat_lock:
            if room_id not in CHAT_ROOMS:
                self.send_error(404, "Room not found")
                return
            for msg in CHAT_ROOMS[room_id].get('messages', []):
                delete_chat_image_file(msg.get('image', ''))
            CHAT_ROOMS[room_id]['messages'] = []
        chat_room_saver.flush()
        self.send_json_response({'success': True})
        print(f"[Chat] 清空聊天室: {room_id}")

    def api_chat_cleanup(self):
        """手动清一次聊天图片目录里的孤儿文件（管理员）。

        ?grace=0 可以把刚生成的文件也一起清掉（测试用），默认保留 10 分钟内的。
        """
        if not self.check_admin_auth():
            self.send_api_error("Admin Required", 401)
            return
        try:
            grace = int(self.q('grace', '600'))
        except ValueError:
            grace = 600
        result = cleanup_chat_images(grace_seconds=max(0, grace))
        self.send_json_response({'success': True, **result})
