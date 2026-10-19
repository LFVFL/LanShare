"""页面与文件/系统接口。路由表按「路径 → 方法名」指向这里的方法。

鉴权统一在 handler.py 的分发表里按 AUTH_* 标记做，所以这些方法里不再各自判断，
只有聊天接口例外（它要区分 302 / 403 / 401 三种拒绝形态，见 chat_api.py）。
"""
import shutil
import urllib.parse
from pathlib import Path

from .config import *
from .paths import *
from .storage import *
from .chat_store import *
from .pages import *
from .paths import _password_ok
from .storage import _upload_key


class RoutesMixin:
    """非聊天接口。逻辑与原 do_GET / do_POST 分支逐行对应。"""

    # ── 页面 ────────────────────────────────────────────────────────────
    def page_root(self):
        self.current_path = self.shared_path
        self.send_main_page()

    def page_dir(self):
        self.current_path = urllib.parse.unquote(self.q('path', self.shared_path))
        self.send_main_page()

    def page_login(self):
        # 没设访问密码就没有登录这回事，直接回首页
        if not self.password:
            self.redirect('/')
            return
        self.send_login_page()

    # ── 读接口 ──────────────────────────────────────────────────────────
    def api_list(self):
        dir_path = self.q('path', self.shared_path)
        # limit/offset 可选：不传就是整份（老行为），传了就分页
        listing = self.build_listing(
            urllib.parse.unquote(dir_path),
            limit=self.q('limit', '0'),
            offset=self.q('offset', '0'),
        )
        self.send_json_response({
            'path': listing['path'],
            'entries': listing['entries'],
            'total': listing.get('total', len(listing['entries'])),
            'offset': listing.get('offset', 0),
            'hasMore': bool(listing.get('hasMore')),
        })

    def api_upload_unique(self):
        self.send_json_response({
            'name': self.unique_name(
                self.q('path', self.shared_path),
                self.q('name'),
            )
        })

    def api_is_admin(self):
        """前端靠这个决定要不要显示「管理员权限」入口：
        needs_admin=False（没设管理密码）时整条入口都该藏起来，
        否则用户会看到一个点了没用的锁。"""
        self.send_json_response({
            'is_admin': self.check_admin_auth(),
            'needs_admin': bool(self.admin_password),
        })

    def api_download(self):
        filename = self.q('file')
        if filename:
            self.download_file(filename, self.q('path', self.shared_path))

    def api_check_exists(self):
        self.check_file_exists(self.q('file'), self.q('path', self.shared_path))

    def api_open(self):
        filename = self.q('file')
        if filename:
            self.open_file(filename, self.q('path', self.shared_path))

    def api_delete_via_get(self):
        # 写操作只能走 POST：GET 删除会被任意网页用一张 <img> 触发（CSRF）
        self.send_json_response(
            {'success': False, 'message': '请使用 POST 调用 /delete'}, 405
        )

    # api_zip_download 在 archive.py 里（流式发送，需要自己的名额控制）

    # ── 写接口 ──────────────────────────────────────────────────────────
    def post_login(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8', 'replace')
        post_params = urllib.parse.parse_qs(body)
        input_password = post_params.get('password', [''])[0]

        # 没设访问密码时不存在"登录"这一步，直接放行到首页
        if not self.password:
            self._issue_session(is_admin=False, redirect='/')
            return

        if _password_ok(input_password, self.password):
            if self.access_logger:
                self.access_logger(
                    self.client_address[0], self.requestline, 'POST 登录成功'
                )
            self._issue_session(is_admin=False, redirect='/')
        else:
            # 本机自用，故意把输入的口令打出来方便排查（公用机器请自行删掉这行）
            if self.access_logger:
                self.access_logger(
                    self.client_address[0], self.requestline,
                    f'POST 登录失败 | 密码: {input_password}'
                )
            self.send_login_page("密码错误，请重试。")

    def api_verify_admin(self):
        data = self._read_json()
        input_admin_pwd = data.get('password', '') if data else ''

        # 没设管理密码：一切操作本来就不需要管理员
        if not self.admin_password:
            self.send_json_response({'success': True})
            return

        if _password_ok(input_admin_pwd, self.admin_password):
            token = self.get_token_from_cookie()
            if token and token in SESSIONS_STORE:
                SESSIONS_STORE[token]['is_admin'] = True
                self.send_json_response({'success': True})
            else:
                # 没设访问密码时根本不会有会话，这里补一个，
                # 否则「访问密码留空 + 管理密码非空」永远解不开
                self._issue_session(is_admin=True)
                print("[Auth] 无既有会话，已为管理员校验创建新会话")
        else:
            if self.access_logger:
                self.access_logger(
                    self.client_address[0], self.requestline,
                    f'POST 管理员校验失败 | 密码: {input_admin_pwd}'
                )
            self.send_json_response({'success': False, 'message': 'Wrong password'})

    def api_zip(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        post_params = urllib.parse.parse_qs(body)
        current_path = post_params.get('path', [self.shared_path])[0]
        files = post_params.get('files[]', []) or post_params.get('file', [])
        self.handle_zip_download(current_path, files)

    def post_upload_done(self):
        self.finish_chunk_upload(self.q('path', self.shared_path), self.q('name'))

    def post_upload(self):
        current_path = self.q('path', self.shared_path)
        offset = self._query.get('offset', [None])[0]
        if offset is None:
            self.handle_upload(current_path)
            return
        try:
            offset_val = int(offset)
        except (TypeError, ValueError):
            self.send_api_error("offset 不合法", 400)
            return
        if offset_val < 0:
            self.send_api_error("offset 不能为负数", 400)
            return
        self.handle_chunk_upload(current_path, self.q('name'), offset_val)

    def post_rename(self):
        old_name, new_name = self.q('old'), self.q('new')
        if old_name and new_name:
            self.rename_file(old_name, new_name, self.q('path', self.shared_path))
        else:
            self.send_error(400, "Bad Request")

    def post_delete(self):
        filename = self.q('file')
        if filename:
            self.delete_file(filename, self.q('path', self.shared_path))

    def post_mkdir(self):
        dirname = self.q('name')
        if dirname:
            self.create_directory(dirname, self.q('path', self.shared_path))
        else:
            self.send_error(400, "Bad Request")
