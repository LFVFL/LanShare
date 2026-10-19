"""HTTP 入口：路由表 + 分发表。

要加一个接口，只需两步：
  1. 在 GET_ROUTES / POST_ROUTES 里加一行（路径 → 方法名 → 鉴权方式）
  2. 在 routes.py 或 chat_api.py 里写那个方法

匹配顺序就是表里的顺序；前缀匹配（如 /chat/image/）单独列在 PREFIX_ROUTES。
"""
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler

from .config import *
from .paths import *
from .storage import *
from .chat_store import *
from .pages import *

from .auth import AuthMixin
from .archive import ArchiveMixin
from .chat_api import ChatApiMixin
from .fs_ops import FsOpsMixin
from .listing import ListingMixin
from .transfer import TransferMixin
from .http_util import HttpUtilMixin
from .routes import RoutesMixin

# ── 鉴权方式 ────────────────────────────────────────────────────────────
AUTH_PUBLIC = 0          # 不判断（页面、静态资源；聊天接口自己判，见 chat_api.py）
AUTH_REDIRECT = 1        # 没登录 → 302 /login
AUTH_403 = 2             # 没登录 → 403
AUTH_ADMIN = 3           # 没登录 → 403；不是管理员 → 401 JSON
AUTH_ADMIN_JSON = 4      # 只要不是管理员就 401 JSON（含没登录）

# (路径, 方法名, 鉴权, 固定参数)
GET_ROUTES = (
    ('/login',           'page_login',         AUTH_PUBLIC,   None),
    ('/favicon.ico',     'serve_icon',         AUTH_PUBLIC,   None),
    ('/static/app.css',  'serve_asset',        AUTH_PUBLIC,   '/static/app.css'),
    ('/static/app.js',   'serve_asset',        AUTH_PUBLIC,   '/static/app.js'),
    ('/static/theme-boot.js', 'serve_asset',   AUTH_PUBLIC,   '/static/theme-boot.js'),
    ('/static/app-files.js',  'serve_asset',   AUTH_PUBLIC,   '/static/app-files.js'),
    ('/static/app-chat.js',   'serve_asset',   AUTH_PUBLIC,   '/static/app-chat.js'),
    ('/static/app-login.js',  'serve_asset',   AUTH_PUBLIC,   '/static/app-login.js'),
    ('/chat',            'page_chat_list',     AUTH_PUBLIC,   None),
    ('/chat/room',       'page_chat_room',     AUTH_PUBLIC,   None),
    ('/chat/rooms',      'api_chat_rooms',     AUTH_PUBLIC,   None),
    ('/chat/history',    'api_chat_history',   AUTH_PUBLIC,   None),
    ('/chat/stream',     'api_chat_stream',    AUTH_PUBLIC,   None),
    ('/upload/unique',   'api_upload_unique',  AUTH_ADMIN_JSON, None),
    ('/api/list',        'api_list',           AUTH_403,      None),
    ('/api/search',      'api_search',         AUTH_403,      None),
    ('/zip_download',    'api_zip_download',   AUTH_REDIRECT, None),
    ('/is_admin',        'api_is_admin',       AUTH_REDIRECT, None),
    ('/download',        'api_download',       AUTH_REDIRECT, None),
    ('/check_exists',    'api_check_exists',   AUTH_REDIRECT, None),
    ('/open',            'api_open',           AUTH_REDIRECT, None),
    ('/delete',          'api_delete_via_get', AUTH_REDIRECT, None),
    ('/dir',             'page_dir',           AUTH_REDIRECT, None),
    ('/',                'page_root',          AUTH_REDIRECT, None),
    ('',                 'page_root',          AUTH_REDIRECT, None),
)

POST_ROUTES = (
    ('/login',            'post_login',        AUTH_PUBLIC,     None),
    ('/chat/create',      'api_chat_create',   AUTH_PUBLIC,     None),
    ('/chat/delete_room', 'api_chat_delete_room', AUTH_PUBLIC,  None),
    ('/chat/send',        'api_chat_send',     AUTH_PUBLIC,     None),
    ('/chat/delete',      'api_chat_delete_msg', AUTH_PUBLIC,   None),
    ('/chat/clear',       'api_chat_clear',    AUTH_PUBLIC,     None),
    ('/chat/cleanup',     'api_chat_cleanup',  AUTH_PUBLIC,     None),
    ('/zip',              'api_zip',           AUTH_403,        None),
    ('/verify_admin',     'api_verify_admin',  AUTH_403,        None),
    ('/upload/done',      'post_upload_done',  AUTH_ADMIN,      None),
    ('/upload',           'post_upload',       AUTH_ADMIN,      None),
    ('/rename',           'post_rename',       AUTH_ADMIN,      None),
    ('/delete',           'post_delete',       AUTH_ADMIN,      None),
    ('/mkdir',            'post_mkdir',        AUTH_ADMIN,      None),
    ('/move',             'post_move',         AUTH_ADMIN,      None),
    ('/copy',             'post_copy',         AUTH_ADMIN,      None),
)

# (前缀, 方法名, 鉴权)
PREFIX_ROUTES = (
    ('/chat/image/', 'serve_chat_image', AUTH_PUBLIC),
)


class FileShareHandler(HttpUtilMixin, AuthMixin, ChatApiMixin, RoutesMixin,
                       ListingMixin, TransferMixin, ArchiveMixin, FsOpsMixin,
                       BaseHTTPRequestHandler):
    """把各块能力拼成一个请求处理器，路由见上面的三张表。"""

    _open_cache = {}
    _open_lock = threading.Lock()
    _icon_bytes = None

    # 不暴露 Python 版本
    server_version = "LanShare"
    sys_version = ""

    # HTTP/1.1：默认长连接，一次 TCP 连接可复用（打开一页要 4 个资源，省 3 次握手）。
    # 前提是每个响应都带正确的 Content-Length —— 由 tools/protocol_test.py 守着。
    protocol_version = "HTTP/1.1"
    # 空闲连接最多占 60 秒，否则并发名额会被挂死的浏览器连接吃光
    timeout = 60

    def __init__(self, *args, **kwargs):
        self.shared_path = kwargs.pop('shared_path', None)
        self.current_path = kwargs.pop('current_path', None) or self.shared_path
        self.access_logger = kwargs.pop('access_logger', None)
        self.password = kwargs.pop('password', None)
        self.admin_password = kwargs.pop('admin_password', None)
        self._sec_headers_sent = False
        self._query = {}
        super().__init__(*args, **kwargs)

    # ── 分发 ────────────────────────────────────────────────────────────
    def _pass_auth(self, mode):
        """按 AUTH_* 判断这张表项能不能进；不通过时顺手把拒绝响应发出去。"""
        if mode == AUTH_PUBLIC:
            return True
        if mode == AUTH_REDIRECT:
            if self.check_auth():
                return True
            self.redirect('/login')
            return False
        if mode == AUTH_403:
            if self.check_auth():
                return True
            self.send_error(403, "Forbidden")
            return False
        if mode == AUTH_ADMIN:
            if not self.check_auth():
                self.send_error(403, "Forbidden")
                return False
            if not self.check_admin_auth():
                self.send_json_response({'error': 'admin_required'}, 401)
                return False
            return True
        if mode == AUTH_ADMIN_JSON:
            if not self.check_admin_auth():
                self.send_json_response({'error': 'admin_required'}, 401)
                return False
            return True
        return True

    def _dispatch(self, path, table):
        for route_path, name, auth, arg in table:
            if path != route_path:
                continue
            if not self._pass_auth(auth):
                return
            handler = getattr(self, name)
            if arg is None:
                handler()
            else:
                handler(arg)
            return

        for prefix, name, auth in PREFIX_ROUTES:
            if path.startswith(prefix):
                if not self._pass_auth(auth):
                    return
                getattr(self, name)(path)
                return

        self.send_error(404, "Not Found")

    def do_GET(self):
        try:
            parsed_path = urllib.parse.urlparse(self.path)
            self._query = urllib.parse.parse_qs(parsed_path.query)
            self._dispatch(parsed_path.path, GET_ROUTES)
        except (ConnectionError, BrokenPipeError):
            # 浏览器切页/关标签会中途断开，这不是服务器出错，别再报 500 吓人
            self._client_gone()
        except Exception as e:
            try:
                self.send_error(500, f"Server error: {e}")
            except Exception:
                pass

    def do_POST(self):
        try:
            parsed_path = urllib.parse.urlparse(self.path)
            self._query = urllib.parse.parse_qs(parsed_path.query)

            # 所有写操作先过同源校验，挡掉跨站 POST
            if self._reject_cross_site():
                return

            self._dispatch(parsed_path.path, POST_ROUTES)
        except (ConnectionError, BrokenPipeError):
            self._client_gone()
        except Exception as e:
            try:
                self.send_error(500, f"Error: {e}")
            except Exception:
                pass

    def _client_gone(self):
        """客户端提前断开：只记一行，不写响应（也写不出去）。"""
        try:
            print("[HTTP] 客户端提前断开：%s %s" % (
                getattr(self, 'command', '-'), getattr(self, 'path', '-')))
        except Exception:
            pass
