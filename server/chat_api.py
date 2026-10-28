"""Chat pages, versioned realtime events and binary image upload routes."""
import json
import mimetypes
import queue
import re
import shutil
import threading
import time
import urllib.parse
import uuid
from collections import deque
from pathlib import Path

from .config import *
from .paths import is_path_within
from .storage import chat_lock
from .chat_store import (
    CHAT_ROOMS, chat_event, chat_room_saver, notify_chat_event,
    save_chat_base64_image, delete_chat_image_file, ensure_thumb, cleanup_chat_images,
)
from . import chat_media

_subscribers = {}
_sub_lock = threading.Lock()
_broadcaster_started = False
_broadcaster_guard = threading.Lock()
_room_sync = {}


def _sync_locked(room_id):
    if room_id not in _room_sync:
        _room_sync[room_id] = {'epoch': uuid.uuid4().hex, 'seq': 0, 'events': deque(maxlen=1024)}
    return _room_sync[room_id]


def _revision_locked(room_id):
    state = _sync_locked(room_id)
    return state['epoch'] + ':' + str(state['seq'])


def _snapshot_locked(room_id):
    room = CHAT_ROOMS.get(room_id)
    return {
        'success': True, 'reset': True, 'deleted': room is None,
        'room': {'id': room_id, 'name': room.get('name', '')} if room else {'id': room_id},
        'messages': [dict(m) for m in room.get('messages', [])] if room else [],
        'revision': _revision_locked(room_id),
    }


def _record_locked(room_id, kind, data):
    """Call while chat_lock is held so snapshots and their cursors are atomic."""
    state = _sync_locked(room_id)
    state['seq'] += 1
    state['events'].append({'type': kind, 'data': data, 'revision': _revision_locked(room_id)})


def _changes_locked(room_id, since):
    state = _sync_locked(room_id)
    current = _revision_locked(room_id)
    if since == current:
        return []
    prefix = state['epoch'] + ':'
    try:
        seq = int(since[len(prefix):]) if since.startswith(prefix) else -1
    except (ValueError, TypeError):
        seq = -1
    events = state['events']
    earliest = int(events[0]['revision'].rsplit(':', 1)[1]) if events else state['seq'] + 1
    if seq < earliest - 1 or seq < 0 or seq > state['seq']:
        return [{'type': 'reset', 'data': _snapshot_locked(room_id), 'revision': current}]
    fresh = [event for event in events if int(event['revision'].rsplit(':', 1)[1]) > seq]
    if len(fresh) > 200 or any(event['type'] == 'reset' for event in fresh):
        return [{'type': 'reset', 'data': _snapshot_locked(room_id), 'revision': current}]
    return fresh


def _ensure_broadcaster():
    global _broadcaster_started
    with _broadcaster_guard:
        if not _broadcaster_started:
            _broadcaster_started = True
            threading.Thread(target=_broadcast_loop, daemon=True, name='sse-broadcast').start()


def _broadcast_loop():
    while True:
        with chat_event:
            chat_event.wait(0.5)
        with _sub_lock:
            subscribers = list(_subscribers.items())
        for sub_id, sub in subscribers:
            with chat_lock:
                events = _changes_locked(sub['room'], sub['revision'])
            for event in events:
                try:
                    sub['queue'].put_nowait(event)
                except queue.Full:
                    sub['overflow'].set()
                    break
                sub['revision'] = event['revision']


class ChatApiMixin:
    STREAM_MAX = 8
    STREAM_LIFETIME = 25.0
    STREAM_TICK = 0.5
    STREAM_BEAT = 10.0
    _stream_slots = threading.BoundedSemaphore(STREAM_MAX)

    def _chat_json(self):
        data = self._read_json()
        if data is not None and not isinstance(data, dict):
            self.send_api_error('请求必须是 JSON 对象', 400)
            return None
        return data

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
        with chat_lock:
            exists = room_id in CHAT_ROOMS
        if exists:
            self.send_chat_room_page(room_id)
        else:
            self.send_error(404, 'Room not found')

    def api_chat_rooms(self):
        if not self.check_auth():
            self.send_error(403, 'Forbidden')
            return
        with chat_lock:
            rooms = [{'id': rid, 'name': room['name'], 'created': room['created'],
                      'message_count': len(room.get('messages', [])), 'owner': room.get('owner', '')}
                     for rid, room in CHAT_ROOMS.items()]
        rooms.sort(key=lambda room: room['created'])
        self.send_json_response(rooms)

    def api_chat_history(self):
        if not self.check_auth():
            self.send_error(403, 'Forbidden')
            return
        room_id = self.q('room', 'default')
        sync_result = None
        with chat_lock:
            if self.q('sync') == '1':
                if self.q('since'):
                    result = {'success': True, 'events': _changes_locked(room_id, self.q('since')),
                              'revision': _revision_locked(room_id)}
                else:
                    result = _snapshot_locked(room_id)
                sync_result = result
            elif room_id not in CHAT_ROOMS:
                self.send_error(404, 'Room not found')
                return
            else:
                messages = CHAT_ROOMS[room_id].get('messages', [])[:]
        if sync_result is not None:
            self.send_json_response(sync_result)
            return
        after = self.q('after')
        index = next((i for i, msg in enumerate(messages) if msg.get('id') == after), None)
        result = messages[index + 1:] if after and index is not None else messages
        backfilled = 0
        for msg in result:
            if backfilled >= 20:
                break
            if msg.get('type') == 'image' and msg.get('image') and not msg.get('thumb'):
                thumb = ensure_thumb(msg['image'])
                if thumb:
                    msg['thumb'] = thumb
                    backfilled += 1
        if backfilled:
            chat_room_saver.mark_dirty()
        self.send_json_response(result)

    def serve_chat_image(self, path):
        if not self.check_auth():
            self.send_error(403, 'Forbidden')
            return
        filename = Path(urllib.parse.unquote(path.replace('/chat/image/', '', 1))).name
        image = (CHAT_IMAGE_DIR / filename).resolve()
        if not filename or not is_path_within(image, CHAT_IMAGE_DIR) or not image.is_file():
            self.send_error(404, 'Image not found')
            return
        content_type = mimetypes.guess_type(str(image))[0] or 'application/octet-stream'
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(image.stat().st_size))
        self.send_header('Cache-Control', 'private, no-cache')
        self.send_header('Content-Security-Policy', "default-src 'none'; sandbox")
        if self.q('download') == '1' or image.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
            self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + urllib.parse.quote(filename))
        self.end_headers()
        with image.open('rb') as source:
            shutil.copyfileobj(source, self.wfile)

    def _sse_send(self, text):
        self.wfile.write(text.encode('utf-8'))
        self.wfile.flush()

    def api_chat_stream(self):
        if not self.check_auth():
            self.send_error(403, 'Forbidden')
            return
        room_id = self.q('room', 'default')
        inbox = queue.Queue(maxsize=256)
        with chat_lock:
            if room_id not in CHAT_ROOMS:
                self.send_error(404, 'Room not found')
                return
            revision = _revision_locked(room_id)
            since = self.headers.get('Last-Event-ID') or self.q('since')
            if self.q('sync') == '1' or since:
                initial = _changes_locked(room_id, since or '')
            else:
                messages = CHAT_ROOMS[room_id].get('messages', [])
                after = self.q('after')
                index = next((i for i, msg in enumerate(messages) if msg.get('id') == after), None)
                fresh = messages[index + 1:] if after and index is not None else messages
                initial = [{'type': 'message', 'data': msg, 'revision': revision} for msg in fresh]
                if len(initial) > 200:
                    initial = [{'type': 'reset', 'data': _snapshot_locked(room_id), 'revision': revision}]
            for event in initial:
                inbox.put_nowait(event)
            # Register under the snapshot lock so no message falls in between.
            sub_id = uuid.uuid4().hex
            sub = {'queue': inbox, 'room': room_id, 'revision': revision, 'overflow': threading.Event()}
            if not self._stream_slots.acquire(blocking=False):
                self.send_error(503, 'Too many streams')
                return
            with _sub_lock:
                _subscribers[sub_id] = sub
        _ensure_broadcaster()
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.send_header('X-Accel-Buffering', 'no')
            self.end_headers()
            self.close_connection = True
            self._sse_send('retry: 3000\n\n')
            deadline = time.monotonic() + self.STREAM_LIFETIME
            last_beat = time.monotonic()
            while time.monotonic() < deadline and not sub['overflow'].is_set():
                try:
                    event = inbox.get(timeout=self.STREAM_TICK)
                except queue.Empty:
                    event = None
                if event:
                    frame = 'id: ' + event['revision'] + '\n'
                    if event['type'] != 'message':
                        frame += 'event: ' + event['type'] + '\n'
                    frame += 'data: ' + json.dumps(event['data'], ensure_ascii=False) + '\n\n'
                    self._sse_send(frame)
                if time.monotonic() - last_beat >= self.STREAM_BEAT:
                    self._sse_send(': ping\n\n')
                    last_beat = time.monotonic()
        except (ConnectionError, OSError):
            pass
        finally:
            with _sub_lock:
                _subscribers.pop(sub_id, None)
            self._stream_slots.release()

    def api_chat_create(self):
        if not self.check_auth():
            self.send_error(403, 'Forbidden')
            return
        data = self._chat_json()
        if data is None:
            return
        name = data.get('name', '')
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 50:
            self.send_api_error('群名需为 1-50 个字', 400)
            return
        room_id = str(uuid.uuid4())
        with chat_lock:
            CHAT_ROOMS[room_id] = {'id': room_id, 'name': name.strip(), 'messages': [], 'created': time.time()}
        chat_room_saver.mark_dirty()
        self.send_json_response({'success': True, 'room_id': room_id})

    def api_chat_rename_room(self):
        if not self.check_auth():
            self.send_error(403, 'Forbidden')
            return
        if not self.check_admin_auth():
            self.send_api_error('Admin Required', 401)
            return
        data = self._chat_json()
        if data is None:
            return
        name, room_id = data.get('name', ''), data.get('id', '')
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 50 or not isinstance(room_id, str):
            self.send_api_error('群名需为 1-50 个字', 400)
            return
        with chat_lock:
            room = CHAT_ROOMS.get(room_id)
            if not room:
                self.send_api_error('Room not found', 404)
                return
            room['name'] = name.strip()
            _record_locked(room_id, 'room_updated', {'id': room_id, 'name': room['name']})
        chat_room_saver.flush()
        notify_chat_event()
        self.send_json_response({'success': True, 'name': name.strip()})

    def api_chat_delete_room(self):
        if not self.check_admin_auth():
            self.send_api_error('Admin Required', 401)
            return
        data = self._chat_json()
        if data is None:
            return
        room_id = data.get('id', '')
        if not isinstance(room_id, str):
            self.send_api_error('Room not found', 404)
            return
        if room_id == 'default':
            self.send_json_response({'success': False, 'message': 'Cannot delete default room'})
            return
        with chat_lock:
            room = CHAT_ROOMS.pop(room_id, None)
            if room:
                _record_locked(room_id, 'room_deleted', {'id': room_id})
        if not room:
            self.send_api_error('Room not found', 404)
            return
        for msg in room.get('messages', []):
            delete_chat_image_file(msg.get('image', ''))
        chat_room_saver.flush()
        notify_chat_event()
        self.send_json_response({'success': True})

    def api_chat_send(self):
        if not self.check_auth():
            self.send_error(403, 'Forbidden')
            return
        data = self._chat_json()
        if data is None:
            return
        room_id, text = data.get('room', 'default'), data.get('text', '')
        image, thumb, media = data.get('image', ''), data.get('thumb', ''), data.get('media', '')
        request_id = data.get('client_id', '')
        if not all(isinstance(value, str) for value in (room_id, text, image, thumb, media, request_id)):
            self.send_api_error('消息字段类型无效', 400)
            return
        if not text.strip() and not image and not media:
            self.send_api_error('消息不能为空', 400)
            return
        if media and image:
            self.send_api_error('图片来源冲突', 400)
            return
        if image and len(image) > MAX_CHAT_IMAGE_BASE64_LEN:
            self.send_api_error('旧版 Base64 上传超过兼容上限，请刷新页面使用二进制图片上传', 413)
            return
        with chat_lock:
            room = CHAT_ROOMS.get(room_id)
            if not room:
                self.send_api_error('Room not found', 404)
                return
            prior = next((msg for msg in room['messages'] if request_id and msg.get('client_id') == request_id
                          and msg.get('sender') == self.client_address[0]), None)
            if prior:
                self.send_json_response({'success': True, 'msg': prior})
                return
        attachment = {}
        if image:
            image_url = save_chat_base64_image(image)
            if not image_url:
                self.send_api_error('图片保存失败，格式不支持或图片过大', 400)
                return
            thumb_url = save_chat_base64_image(thumb, thumb=True, stem=Path(image_url).stem) if thumb else ''
            attachment = {'image': image_url, 'thumb': thumb_url or ''}
        msg = {'id': str(uuid.uuid4()), 'sender': self.client_address[0], 'text': text,
               'image': '', 'thumb': '', 'type': 'text', 'time': time.strftime('%H:%M'),
               'date': time.strftime('%Y-%m-%d'), 'timestamp': time.time()}
        if request_id:
            msg['client_id'] = request_id
        removed = []
        owner = None
        if media:
            try:
                owner = self._media_owner()
            except chat_media.MediaError as error:
                self.send_api_error(str(error), error.status)
                return
        with chat_lock:
            room = CHAT_ROOMS.get(room_id)
            if not room:
                if attachment:
                    delete_chat_image_file(attachment['image'])
                self.send_api_error('Room not found', 404)
                return
            duplicate = next((old for old in room['messages'] if request_id and old.get('client_id') == request_id
                              and old.get('sender') == self.client_address[0]), None)
            if duplicate:
                if attachment:
                    delete_chat_image_file(attachment['image'])
                self.send_json_response({'success': True, 'msg': duplicate})
                return
            if media:
                try:
                    attachment = chat_media.take_media(media, owner, room_id)
                except chat_media.MediaError as error:
                    self.send_api_error(str(error), error.status)
                    return
            msg.update(attachment)
            msg['type'] = 'image' if msg['image'] else 'text'
            room['messages'].append(msg)
            if len(room['messages']) > MAX_CHAT_LEN:
                removed = room['messages'][:-MAX_CHAT_LEN]
                room['messages'] = room['messages'][-MAX_CHAT_LEN:]
                _record_locked(room_id, 'reset', None)
            else:
                _record_locked(room_id, 'message', msg)
        for old in removed:
            delete_chat_image_file(old.get('image', ''))
        if media:
            chat_media.finish_media(media, owner)
        chat_room_saver.mark_dirty()
        notify_chat_event()
        self.send_json_response({'success': True, 'msg': msg})

    def api_chat_delete_msg(self):
        if not self.check_auth():
            self.send_error(403, 'Forbidden')
            return
        data = self._chat_json()
        if data is None:
            return
        room_id, msg_id = data.get('room', 'default'), data.get('id', '')
        if not isinstance(room_id, str) or not isinstance(msg_id, str):
            self.send_api_error('消息不存在', 400)
            return
        with chat_lock:
            room = CHAT_ROOMS.get(room_id)
            msg = next((m for m in room.get('messages', []) if m.get('id') == msg_id), None) if room else None
            if not msg:
                self.send_api_error('Message not found', 404)
                return
            if msg.get('sender') != self.client_address[0] and not self.check_admin_auth():
                self.send_api_error('Admin Required', 401)
                return
            room['messages'] = [m for m in room['messages'] if m.get('id') != msg_id]
            _record_locked(room_id, 'delete', {'id': msg_id})
        delete_chat_image_file(msg.get('image', ''))
        chat_room_saver.mark_dirty()
        notify_chat_event()
        self.send_json_response({'success': True})

    def api_chat_clear(self):
        if not self.check_admin_auth():
            self.send_api_error('Admin Required', 401)
            return
        data = self._chat_json()
        if data is None:
            return
        room_id = data.get('room', 'default')
        if not isinstance(room_id, str):
            self.send_api_error('Room not found', 404)
            return
        with chat_lock:
            room = CHAT_ROOMS.get(room_id)
            if not room:
                self.send_api_error('Room not found', 404)
                return
            messages, room['messages'] = room['messages'], []
            _record_locked(room_id, 'clear', {})
        for msg in messages:
            delete_chat_image_file(msg.get('image', ''))
        chat_room_saver.flush()
        notify_chat_event()
        self.send_json_response({'success': True})

    def api_chat_cleanup(self):
        if not self.check_admin_auth():
            self.send_api_error('Admin Required', 401)
            return
        try:
            grace = max(0, int(self.q('grace', '600')))
        except ValueError:
            grace = 600
        result = cleanup_chat_images(grace_seconds=grace)
        chat_media.cleanup_uploads()
        self.send_json_response({'success': True, **result})

    def _media_owner(self):
        client = self.headers.get('X-Chat-Client', '')
        if not re.fullmatch(r'[0-9a-f]{32}', client):
            raise chat_media.MediaError('缺少图片上传会话，请刷新页面重试', 400)
        return (self.get_token_from_cookie() or '', client)

    def _media_call(self, operation):
        if not self.check_auth():
            self.send_error(403, 'Forbidden')
            return
        try:
            result = operation()
        except chat_media.MediaError as error:
            self.close_connection = True
            self.send_api_error(str(error), error.status)
            return
        except OSError:
            self.close_connection = True
            self.send_api_error('服务器无法保存图片，请检查磁盘空间和写入权限', 507)
            return
        if result is not None:
            self.send_json_response(result)

    def api_chat_media_config(self):
        self._media_call(chat_media.capabilities)

    def api_chat_media_start(self):
        def start():
            owner = self._media_owner()
            data = self._chat_json()
            if data is None:
                return None
            room = data.get('room', 'default')
            with chat_lock:
                if not isinstance(room, str) or room not in CHAT_ROOMS:
                    raise chat_media.MediaError('Room not found', 404)
            return chat_media.start_upload(owner, room, data.get('name', ''), data.get('size'))
        self._media_call(start)

    def api_chat_media_chunk(self):
        def chunk():
            try:
                length = int(self.headers.get('Content-Length', 0))
                offset = int(self.q('offset', '-1'))
            except ValueError:
                raise chat_media.MediaError('图片分片参数无效')
            if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/octet-stream':
                raise chat_media.MediaError('图片分片必须使用二进制请求', 415)
            return chat_media.append_chunk(self.q('upload'), self._media_owner(), offset, self.rfile, length)
        self._media_call(chunk)

    def api_chat_media_complete(self):
        def complete():
            data = self._chat_json()
            return chat_media.complete_upload(data.get('upload'), self._media_owner()) if data is not None else None
        self._media_call(complete)

    def api_chat_media_cancel(self):
        def cancel():
            data = self._chat_json()
            return chat_media.cancel_upload(data.get('upload'), self._media_owner()) if data is not None else None
        self._media_call(cancel)
