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
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            # Windows SO_REUSEADDR allows a second live listener on the same port.
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
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
    STOPPED = 'stopped'
    STARTING = 'starting'
    RUNNING = 'running'
    STOPPING = 'stopping'

    def __init__(self):
        self.server = None
        self.server_thread = None
        self.shared_path = None
        self.password = None
        self.admin_password = None
        self.port = DEFAULT_PORT
        self._state = self.STOPPED
        self._state_lock = threading.RLock()
        self._operation = 0
        self._stopped = threading.Event()
        self._stopped.set()
        self.status_callback = None
        self.log_queue = queue.Queue()

    @property
    def state(self):
        with self._state_lock:
            return self._state

    @property
    def is_running(self):
        return self.state == self.RUNNING

    @property
    def is_busy(self):
        return self.state in (self.STARTING, self.STOPPING)

    def set_shared_path(self, path):
        """Publish a validated root atomically; requests keep their own snapshot."""
        try:
            target = Path(path).resolve() if path else None
            if not target or not target.is_dir():
                return False, "目录不存在或不是文件夹"
            with self._state_lock:
                if self._state in (self.STARTING, self.STOPPING):
                    return False, "服务正在启动或停止，请稍后重试"
                set_shared_root(str(target))
                self.shared_path = str(target)
        except (OSError, ValueError) as exc:
            return False, "切换目录失败: %s" % exc
        print("[Share] 共享目录切换为 %s" % self.shared_path)
        return True, self.shared_path

    def set_status_callback(self, callback):
        self.status_callback = callback

    def update_status(self, message, is_running):
        if self.status_callback:
            try:
                self.status_callback(message, is_running)
            except Exception as exc:
                print('[Server] 状态回调失败:', exc)

    def wait_stopped(self, timeout=None):
        """Wait for the listener to close, rather than only for a stop request."""
        return self._stopped.wait(timeout)

    def stop(self, wait=False, timeout=None):
        with self._state_lock:
            if self._state == self.STOPPED:
                return True
            if self._state == self.STOPPING:
                server = None
            else:
                self._state = self.STOPPING
                operation = self._operation
                server = self.server
                server_thread = self.server_thread
                self.update_status("正在停止...", False)

        # A starting worker owns its not-yet-published socket and closes it itself.
        if server is None:
            return self.wait_stopped(timeout) if wait else False

        def cleanup():
            try:
                try:
                    server.shutdown()
                finally:
                    server.server_close()
                if server_thread and server_thread is not threading.current_thread():
                    server_thread.join()
                chat_room_saver.flush()
            except Exception as e:
                print(f"[Server] 停止服务失败: {e}")
            finally:
                with self._state_lock:
                    if self._operation == operation:
                        self.server = None
                        self.server_thread = None
                        self._state = self.STOPPED
                        global server_instance
                        if server_instance is self:
                            server_instance = None
                        self._stopped.set()
                        self.update_status("已停止", False)

        threading.Thread(target=cleanup, daemon=True).start()
        return self.wait_stopped(timeout) if wait else False

    def get_all_local_ips(self):
        # 排序交给 netinfo：默认路由网卡优先，代理 TUN / APIPA 这类虚拟地址垫底
        return netinfo.local_ips()

    def start(self, path=None, port=None, password=None, admin_password=None, cancel_event=None):
        try:
            target = Path(path or DEFAULT_SHARE_PATH).resolve()
            if not target.is_dir():
                return False, "路径不存在或不是文件夹"
        except (OSError, ValueError) as exc:
            return False, "路径无效: %s" % exc

        with self._state_lock:
            if cancel_event is not None and cancel_event.is_set():
                return False, "启动已取消"
            if self._state != self.STOPPED:
                return False, "服务已在运行或正在切换状态。"
            self._operation += 1
            operation = self._operation
            self._state = self.STARTING
            self._stopped.clear()
            self.shared_path = str(target)
            self.port = port if isinstance(port, int) and 1 <= port <= 65535 else DEFAULT_PORT
            self.password = password or None
            self.admin_password = admin_password or None
            self.update_status("正在启动...", False)
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

        server = None
        started = False
        try:
            initialize_chat_store()
            local_ips = self.get_all_local_ips()
            with self._state_lock:
                if self._state != self.STARTING or self._operation != operation:
                    return False, "启动已取消"

            def handler_factory(*args, **kwargs):
                kwargs['shared_path'] = shared_path
                kwargs['access_logger'] = access_logger
                kwargs['password'] = password
                kwargs['admin_password'] = admin_password
                return FileShareHandler(*args, **kwargs)

            server = FastBindHTTPServer(('', self.port), handler_factory)
            with self._state_lock:
                if self._state != self.STARTING or self._operation != operation:
                    return False, "启动已取消"
                set_shared_root(shared_path)
                SESSIONS_STORE.clear()
                self.server = server
                self.server_thread = threading.Thread(target=server.serve_forever, daemon=True)
                self.server_thread.start()
                self._state = self.RUNNING
                started = True
                global server_instance
                server_instance = self
                self.update_status("运行中 (端口 {})".format(self.port), True)

            local_url = "http://localhost:{}".format(self.port)
            network_urls = ["http://{}:{}".format(ip, self.port) for ip in local_ips]
            # 二维码默认扫这个：排序后的第一个地址（路由器那条局域网），不是 localhost
            primary_url = network_urls[0] if network_urls else local_url

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
            return False, "启动失败: {}".format(str(e))
        finally:
            if not started:
                if server is not None:
                    try:
                        server.server_close()
                    except OSError as exc:
                        print('[Server] 关闭启动失败的监听失败:', exc)
                with self._state_lock:
                    if self._operation == operation:
                        cancelled = self._state == self.STOPPING
                        self.server = None
                        self.server_thread = None
                        self._state = self.STOPPED
                        self._stopped.set()
                        self.update_status("已停止" if cancelled else "启动失败", False)


# ══════════════════════════════════════════════════════════════════════════════
# GUI
# ══════════════════════════════════════════════════════════════════════════════
