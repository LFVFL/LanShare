"""HTTP 服务器与共享控制器（开始/停止、地址、二维码）。"""
import queue
import socket
import threading
import time
from http.server import HTTPServer
from socketserver import ThreadingMixIn

from .config import *
from .paths import *
from .storage import *
from .chat_store import *
from .pages import *
from .handler import FileShareHandler
from . import netinfo

# 当前运行中的实例，供退出/托盘等场景引用
server_instance = None

# 同时处理的请求数上限。ThreadingMixIn 默认无限起线程，
# 局域网里有人开了几十个下载窗口就会把内存吃光，这里加个闸。
MAX_WORKERS = 32
QUEUE_TIMEOUT = 10          # 排队超过这么多秒直接 503，别让浏览器干等


class FastBindHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    address_family = socket.AF_INET

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._slots = threading.BoundedSemaphore(MAX_WORKERS)

    def server_bind(self):
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()

    def process_request(self, request, client_address):
        # 拿不到名额就回 503；拿得到再交给父类去开线程
        if not self._slots.acquire(timeout=QUEUE_TIMEOUT):
            try:
                request.sendall(
                    b'HTTP/1.0 503 Service Unavailable\r\n'
                    b'Content-Type: text/plain; charset=utf-8\r\n'
                    b'Content-Length: 15\r\n'
                    b'Connection: close\r\n\r\n'
                    b'server is busy\n'
                )
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class FileSharer:
    def __init__(self):
        self.server = None
        self.server_thread = None
        self.shared_path = None
        self.password = None
        self.admin_password = None
        self.port = DEFAULT_PORT
        self.is_running = False
        self.status_callback = None
        self.log_queue = queue.Queue()

    def set_shared_path(self, path):
        """运行中切换共享目录：改全局值，下一次请求立刻生效。

        不需要重启服务——handler 每次请求都会重新读 SHARE_STATE。
        """
        target = Path(path).resolve() if path else None
        if not target or not target.is_dir():
            return False, "目录不存在或不是文件夹"
        self.shared_path = str(target)
        SHARE_STATE['path'] = self.shared_path
        print("[Share] 共享目录切换为 %s" % self.shared_path)
        return True, self.shared_path

    def set_status_callback(self, callback):
        self.status_callback = callback

    def update_status(self, message, is_running):
        self.is_running = is_running
        if self.status_callback:
            self.status_callback(message, is_running)

    def stop(self):
        if not self.is_running or not self.server:
            return

        self.update_status("正在停止...", True)
        chat_room_saver.flush()

        def cleanup():
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception as e:
                print(f"[Server] 停止服务失败: {e}")
            finally:
                self.server = None

                global server_instance
                server_instance = None

                self.update_status("已停止", False)

        threading.Thread(target=cleanup, daemon=True).start()
    def get_all_local_ips(self):
        # 排序交给 netinfo：默认路由网卡优先，代理 TUN / APIPA 这类虚拟地址垫底
        return netinfo.local_ips()

    def start(self, path=None, port=None, password=None, admin_password=None):
        if self.is_running:
            return False, "服务已在运行中。"
        self.update_status("正在启动...", True)
        self.shared_path = path if path and Path(path).exists() else DEFAULT_SHARE_PATH
        SHARE_STATE['path'] = self.shared_path
        self.port = port if port and isinstance(port, int) and 1 <= port <= 65535 else DEFAULT_PORT
        self.password = password if password else None
        self.admin_password = admin_password if admin_password else None
        self.log_queue.put((
            time.strftime('%H:%M:%S'),
            '系统',
            f'聊天记录保存位置：{CHAT_ROOMS_FILE_PATH.resolve()}'
        ))

        self.log_queue.put((
            time.strftime('%H:%M:%S'),
            '系统',
            f'聊天图片保存位置：{CHAT_IMAGE_DIR.resolve()}'
        ))
        SESSIONS_STORE.clear()

        if not Path(self.shared_path).exists():
            self.update_status("启动失败", False)
            return False, "路径不存在"

        shared_path = self.shared_path
        password = self.password
        admin_password = self.admin_password

        def access_logger(client_ip, request_line, custom_msg=None, status=None):
            """
            优化访问日志显示，解码 URL，并隐藏部分频繁接口。
            修复点：
            1. 不再错误调用 FileSharer 不存在的 self.format_size；
            2. 统一使用全局 format_size_value；
            3. query 参数统一解码。

            custom_msg 为空时只把"人话消息 + 状态码"丢给桌面端；
            请求行的解码与拦截已经在 http_util.log_message 里做过一遍了。
            """
            try:
                now = time.strftime('%H:%M:%S')

                if request_line is None:
                    request_line = ''

                if custom_msg is not None:
                    self.log_queue.put((now, client_ip, custom_msg, status))
                    return

                self.log_queue.put((now, client_ip, request_line, status))
            except Exception:
                pass

        try:
            def handler_factory(*args, **kwargs):
                kwargs['shared_path'] = shared_path
                kwargs['access_logger'] = access_logger
                kwargs['password'] = password
                kwargs['admin_password'] = admin_password
                return FileShareHandler(*args, **kwargs)

            self.server = FastBindHTTPServer(('', self.port), handler_factory)
            self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.server_thread.start()

            local_ips = self.get_all_local_ips()
            local_url = "http://localhost:{}".format(self.port)
            network_urls = ["http://{}:{}".format(ip, self.port) for ip in local_ips]
            # 二维码默认扫这个：排序后的第一个地址（路由器那条局域网），不是 localhost
            primary_url = network_urls[0] if network_urls else local_url

            global server_instance
            server_instance = self
            self.update_status("运行中 (端口 {})".format(self.port), True)

            return True, {
                'local_url': local_url,
                'network_urls': network_urls,
                'primary_url': primary_url,
                'port': self.port,
                'folder': self.shared_path,
                'log_queue': self.log_queue,
                'has_password': bool(self.password),
                'has_admin_password': bool(self.admin_password)
            }
        except Exception as e:
            self.update_status("启动失败", False)
            return False, "启动失败: {}".format(str(e))


# ══════════════════════════════════════════════════════════════════════════════
# GUI
# ══════════════════════════════════════════════════════════════════════════════
