"""HTTP 基础：响应输出、页面渲染、静态资源、日志。"""
import base64
import gzip
import hashlib
import html
import json
import mimetypes
import os
import re
import shutil
import tempfile
import threading
import time
import urllib.parse
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from .config import *
from .paths import *
from .storage import *
from .chat_store import *
from .pages import *
from .paths import _password_ok
from .storage import _upload_key

# brotli 是可选的：装了就用（比 gzip 再省 15~20%），没装就退回 gzip。
# 纯 Python 包，pip install brotli 即可，不影响没装的人。
try:
    import brotli
except ImportError:
    brotli = None


def _status_of(args):
    """从 log_message 的参数里抠出 HTTP 状态码。

    log_request 传的是 ('"%s" %s %s', 请求行, '200', 响应体大小)，
    log_error 传的是 ('code %d, message %s', 404, 'Not Found')，
    所以第一个能转成整数的参数就是状态码。
    """
    for arg in args[:2]:
        try:
            return int(arg)
        except (TypeError, ValueError):
            continue
    return None


class HttpUtilMixin:
    """HTTP 基础：响应输出、页面渲染、静态资源、日志。"""

    def end_headers(self):
        """所有响应统一补安全头（每个响应只补一次）"""
        if not self._sec_headers_sent:
            self._sec_headers_sent = True
            for key, value in SECURITY_HEADERS:
                self.send_header(key, value)
        super().end_headers()

    def version_string(self):
        """默认会拼上 Python 版本号，这里只报产品名"""
        return self.server_version

    def log_message(self, format, *args):
        """控制台访问日志 + 丢给桌面端一份（带状态码，用于着色）

        注意：不能加 @staticmethod——加了以后 self 会变成 format 字符串，
        整个方法静默炸掉（异常被下面的兜底吞了），日志就再也不出现。
        """
        try:
            # send_error 会先 log_error 再 send_response，两次都会走到这里；
            # "code %d, message %s" 这条是错误详情，真正的状态码由 send_response 报，跳过它
            if isinstance(format, str) and format.startswith('code %d'):
                return

            now = time.strftime('%H:%M:%S')
            client_ip = self.client_address[0] if self.client_address else '-'
            method = getattr(self, 'command', '-')
            raw_path = getattr(self, 'path', '-')

            parsed = urllib.parse.urlparse(raw_path)
            path = urllib.parse.unquote(parsed.path)
            query = urllib.parse.parse_qs(parsed.query)

            def q(name, default=''):
                value = query.get(name, [default])[0]
                return urllib.parse.unquote(value)

            status = _status_of(args)
            status_text = str(status) if status is not None else '-'

            # 这些接口太频繁，可以不打印
            silent_paths = {
                '/chat/history',
                '/is_admin',
                '/favicon.ico'
            }

            if path in silent_paths:
                return

            if path == '/upload':
                folder = q('path')
                name = q('name')
                offset = q('offset', '0')

                try:
                    offset_num = int(offset)
                    offset_text = self.format_size(offset_num)
                except Exception:
                    offset_text = offset

                msg = f"上传分片 | 文件: {name} | 位置: {offset_text} | 目录: {folder}"

            elif path == '/check_exists':
                folder = q('path')
                name = q('file')
                msg = f"检查文件 | 文件: {name} | 目录: {folder}"

            elif path == '/download':
                folder = q('path')
                name = q('file')
                preview = q('preview', '0')
                action = "预览文件" if preview == '1' else "下载文件"
                msg = f"{action} | 文件: {name} | 目录: {folder}"

            elif path == '/dir':
                folder = q('path')
                msg = f"浏览目录 | 目录: {folder}"

            elif path == '/zip':
                msg = "创建 ZIP 下载任务"

            elif path == '/zip_download':
                msg = "下载 ZIP 文件"

            elif path == '/':
                msg = "访问首页"

            else:
                decoded_query_parts = []
                for key, values in query.items():
                    for value in values:
                        decoded_query_parts.append(
                            f"{urllib.parse.unquote(key)}={urllib.parse.unquote(value)}"
                        )

                if decoded_query_parts:
                    msg = f"{path}?{'&'.join(decoded_query_parts)}"
                else:
                    msg = path

            print(f"[{now}] {client_ip} {method:<4} {status_text:<3} {msg}")

            # 桌面端的访问日志走同一条人话消息，但要多带一个状态码用于着色。
            # 这里由 send_response 触发，所以 status 是真实的响应码。
            logger = getattr(self, 'access_logger', None)
            if logger:
                try:
                    logger(client_ip, self.requestline, msg, status)
                except Exception:
                    pass

        except Exception:
            try:
                print(f"[{time.strftime('%H:%M:%S')}] {self.client_address[0]} - {format % args}")
            except Exception:
                pass

    def _encode_body(self, body):
        """按客户端偏好挑压缩方式：br > gzip > 不压。

        返回 (要发出去的字节, 额外响应头)。
        不压的情形：太小（压了反而变大）、客户端什么都没带、或者算法不可用。
        """
        if len(body) < 1024:
            return body, ()
        accept = (self.headers.get('Accept-Encoding') or '').lower()

        if brotli is not None and 'br' in accept:
            # quality=5：比 q=11 快一个数量级，体积只差几个百分点
            return (brotli.compress(body, quality=5),
                    (('Content-Encoding', 'br'), ('Vary', 'Accept-Encoding')))

        if 'gzip' in accept:
            return (gzip.compress(body, 6),
                    (('Content-Encoding', 'gzip'), ('Vary', 'Accept-Encoding')))

        return body, ()

    def send_html(self, body_text, status=200, cache="no-store"):
        body = body_text.encode('utf-8')
        payload, extra = self._encode_body(body)
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', cache)
        # 页面才需要的 CSP：脚本全同源、不允许内联、不许被外站嵌
        self.send_header('Content-Security-Policy', PAGE_CSP)
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def send_json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        payload, extra = self._encode_body(body)
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def send_api_error(self, message="Error", status=400):
        self.send_json_response({
            'success': False,
            'message': message
        }, status)

    def _read_json(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))

            if content_length <= 0:
                self.send_error(400, "Empty Body")
                return None

            if content_length > MAX_JSON_BODY_LEN:
                self.send_error(413, "Payload Too Large")
                return None

            body = self.rfile.read(content_length).decode('utf-8')
            return json.loads(body)

        except Exception as e:
            print(f"[HTTP] JSON 解析失败: {e}")
            self.send_error(400, "Bad Request")
            return None

    def serve_asset(self, path):
        """内置的 CSS / JS：带 ETag，命中就回 304；文本够大就压缩。

        页面里引用的是 /static/app.css?v=<内容摘要>：
        - 带了正确的 v → 一年长缓存 + immutable，浏览器以后直接不发请求；
        - 没带或对不上（手敲地址、旧页面留着的老 URL）→ no-cache，
          每次用 ETag 校验，保证不会给出过期内容。
        """
        from .pages import ASSET_VERSION

        if webassets is None:
            self.send_error(500, "webassets missing")
            return
        if path.endswith('theme-boot.js'):
            text = getattr(webassets, 'THEME_BOOT', '') or ''
        elif path.endswith('.css'):
            text = webassets.CSS
        elif path.endswith('app-files.js'):
            text = getattr(webassets, 'JS_FILES', '') or ''
        elif path.endswith('app-chat.js'):
            text = getattr(webassets, 'JS_CHAT', '') or ''
        elif path.endswith('app-login.js'):
            text = getattr(webassets, 'JS_LOGIN', '') or ''
        else:
            text = webassets.JS
        body = text.encode('utf-8')
        # ETag 用未压缩内容的摘要：同一份源码无论压不压都是同一个版本号
        etag = '"' + hashlib.sha1(body).hexdigest()[:16] + '"'

        cache_control = ('public, max-age=31536000, immutable'
                         if self.q('v') == ASSET_VERSION else 'no-cache')

        if self.headers.get('If-None-Match') == etag:
            self.send_response(304)
            self.send_header('ETag', etag)
            self.send_header('Cache-Control', cache_control)
            self.send_header('Vary', 'Accept-Encoding')
            self.end_headers()
            return
        payload, extra = self._encode_body(body)
        self.send_response(200)
        self.send_header('Content-Type',
                         'text/css; charset=utf-8' if path.endswith('.css')
                         else 'application/javascript; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('ETag', etag)
        self.send_header('Cache-Control', cache_control)
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def serve_icon(self):
        """把 icon.ico 直接当 favicon 用（不再内嵌 base64）"""
        data = load_icon_bytes()
        if not data:
            self.send_error(404, "icon not found")
            return
        self.send_response(200)
        self.send_header('Content-Type', 'image/x-icon')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'public, max-age=86400')
        self.end_headers()
        self.wfile.write(data)

    def host_header(self):
        return (self.headers.get('Host') or 'localhost').strip()

    def q(self, name, default=''):
        """取一条 query 参数（do_GET/do_POST 已把解析结果放进 self._query）。"""
        return (self._query or {}).get(name, [default])[0]

    def redirect(self, location):
        """302 跳转，省掉到处重复的四行。

        HTTP/1.1 长连接下没有 Content-Length 会让浏览器一直等，所以显式给 0。
        """
        self.send_response(302)
        self.send_header('Location', location)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def send_login_page(self, error_msg=""):
        html_text = render_template(
            webassets.LOGIN,
            HOST=html.escape(self.host_header()),
            ERROR_MSG=html.escape(error_msg),
            ERROR_OPEN="true" if error_msg else "false",
            ASSET_V=ASSET_VERSION,
        )
        self.send_html(html_text)

    def send_main_page(self):
        try:
            listing = self.build_listing(self.current_path or self.shared_path)

            payload = {
                'path': listing['path'],
                'entries': listing['entries'],
            }
            mobile_actions = (
                '<button class="btn btn--primary btn--icon" data-upload-pick type="button" '
                'aria-label="上传"><i data-icon="upload"></i></button>'
            )
            content = render_template(
                webassets.FILES,
                BREADCRUMB=listing['breadcrumb'],
                FILES_JSON=json.dumps(payload, ensure_ascii=False),
            )
            page = page_shell(
                'files', '文件 · 局域网共享', self.host_header(), content,
                mobile_title='文件', mobile_actions=mobile_actions, active='files',
            )
            self.send_html(page)

        except Exception as e:
            print(f"[HTTP] 渲染文件页失败: {e}")
            try:
                self.send_error(500, "Page error")
            except Exception:
                pass

    def send_chat_list_page(self):
        rooms = []
        with chat_lock:
            for rid, room in CHAT_ROOMS.items():
                rooms.append({
                    'id': rid,
                    'name': room.get('name', rid),
                    'count': len(room.get('messages', [])),
                    'created': room.get('created', 0),
                    'createdText': time.strftime(
                        '%Y-%m-%d %H:%M', time.localtime(room.get('created', 0))
                    ),
                })
        rooms.sort(key=lambda r: r['created'])

        content = render_template(
            webassets.CHAT_LIST,
            ROOMS_JSON=json.dumps({'rooms': rooms}, ensure_ascii=False),
        )
        page = page_shell(
            'chatlist', '聊天室', self.host_header(), content,
            mobile_title='聊天室', active='chat',
        )
        self.send_html(page)

    def send_chat_room_page(self, room_id):
        room = CHAT_ROOMS.get(room_id)
        if not room:
            self.send_error(404, "Room not found")
            return
        room_json = json.dumps({
            'id': room_id,
            'name': room.get('name', room_id),
            'me': self.client_address[0],
        }, ensure_ascii=False)

        content = render_template(
            webassets.CHAT_ROOM,
            ROOM_NAME=html.escape(room.get('name', room_id)),
            ROOM_META=str(len(room.get('messages', []))) + ' 条消息',
            ROOM_JSON=room_json,
        )
        page = page_shell(
            'chatroom', room.get('name', '群聊'), self.host_header(), content,
            mobile_title=room.get('name', '群聊'), active='chat',
        )
        self.send_html(page)

    def format_size(self, size):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return "{:.1f} {}".format(size, unit)
            size /= 1024.0
        return "{:.1f} TB".format(size)


    # ══════════════════════════════════════════════════════════════════════════════
    # HTML Templates
    # ══════════════════════════════════════════════════════════════════════════════

    # ══════════════════════════════════════════════════════════════════════════════
    # Server
    # ══════════════════════════════════════════════════════════════════════════════
