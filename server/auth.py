"""会话与口令校验：同源检查、Cookie 会话、管理员口令。"""
import base64
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


class AuthMixin:
    """会话与口令校验：同源检查、Cookie 会话、管理员口令。"""

    @staticmethod
    def _hostname(netloc):
        """netloc 里去掉端口和 userinfo，只留主机名

        必须显式 @staticmethod：写成 `def _hostname(netloc)` 而不带装饰器，
        调用处的 `self._hostname(x)` 会变成两个位置参数，直接 500。
        现代浏览器带 Sec-Fetch-Site 时根本走不到这里，所以桌面端一直没暴露；
        老浏览器 / App 内置 WebView 只发 Origin，一提交表单就炸。
        """
        netloc = (netloc or '').strip().lower()
        if '@' in netloc:
            netloc = netloc.rsplit('@', 1)[1]
        if netloc.startswith('['):                 # IPv6
            return netloc.split(']')[0] + ']'
        return netloc.rsplit(':', 1)[0] if ':' in netloc else netloc

    def _check_same_origin(self):
        """
        判断写操作是否来自同源页面。判定顺序：
        1. Sec-Fetch-Site（现代浏览器都发，最可靠）
        2. Origin / Referer 与 Host 的主机名比对（忽略端口差异：反代/端口映射很常见）
        3. 两边都拿不到信息时放行（curl、脚本、老客户端），否则本机自用会被自己挡住
        """
        site = (self.headers.get('Sec-Fetch-Site') or '').strip().lower()
        if site in ('same-origin', 'same-site', 'none'):
            return True
        if site == 'cross-site':
            return False

        origin = self.headers.get('Origin') or self.headers.get('Referer')
        if not origin or origin.strip().lower() == 'null':
            return True

        try:
            netloc = urllib.parse.urlsplit(origin).netloc
        except ValueError:
            return True
        if not netloc:
            return True

        host = self.headers.get('Host') or ''
        if not host:
            return True

        return self._hostname(netloc) == self._hostname(host)

    def _reject_cross_site(self):
        if self._check_same_origin():
            return False
        origin = self.headers.get('Origin') or self.headers.get('Referer') or '-'
        print(f"[Auth] 跨站写操作已拒绝 origin={origin} host={self.headers.get('Host')}")
        self.send_json_response(
            {'success': False, 'message': '跨站请求已被拒绝'}, 403
        )
        return True

    def get_token_from_cookie(self):
        cookie_header = self.headers.get('Cookie')
        if cookie_header:
            cookies = {}
            for part in cookie_header.split(';'):
                part = part.strip()
                if '=' in part:
                    k, v = part.split('=', 1)
                    cookies[k.strip()] = v.strip()
            return cookies.get('auth_token')
        return None

    def check_auth(self):
        cleanup_sessions()
        if not self.password:
            return True
        token = self.get_token_from_cookie()
        return bool(token and token in SESSIONS_STORE)

    def check_admin_auth(self):
        cleanup_sessions()
        if not self.admin_password:
            return True
        token = self.get_token_from_cookie()
        return bool(
            token
            and token in SESSIONS_STORE
            and SESSIONS_STORE[token].get('is_admin', False)
        )

    def _issue_session(self, is_admin=False, redirect=None):
        """创建会话并回写 Cookie；redirect 给定时返回 302，否则返回 JSON"""
        token = str(uuid.uuid4())
        SESSIONS_STORE[token] = {'is_admin': bool(is_admin), 'create_time': time.time()}
        cookie = (
            f'auth_token={token}; Path=/; Max-Age={SESSION_TTL}; '
            f'HttpOnly; SameSite=Lax'
        )
        if redirect is not None:
            self.send_response(302)
            self.send_header('Set-Cookie', cookie)
            self.send_header('Location', redirect)
            # 长连接下必须给 Content-Length，否则浏览器一直等这个 302 的响应体
            self.send_header('Content-Length', '0')
            self.end_headers()
        else:
            body = json.dumps({'success': True}, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Set-Cookie', cookie)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def _has_valid_session(self):
        token = self.get_token_from_cookie()
        return bool(token and token in SESSIONS_STORE)
