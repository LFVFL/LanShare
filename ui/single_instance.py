"""Single owner per OS user, with bounded same-user local IPC (never TCP)."""
import ctypes
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from PyQt5.QtCore import QLockFile, QObject, QTimer
from PyQt5.QtNetwork import QLocalServer, QLocalSocket


MAX_MESSAGE_BYTES = 16384
MAX_LOCAL_CLIENTS = 16
REQUEST_TIMEOUT_MS = 5000


def current_user_identity():
    if os.name != 'nt':
        return str(os.getuid())
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                          wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p,
                                             ctypes.POINTER(wintypes.LPWSTR)]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents.value
        text = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return text.value
        finally:
            kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        kernel.CloseHandle(token)


def validate_request(request):
    if not isinstance(request, dict):
        raise ValueError('请求必须是对象')
    action = request.get('action')
    allowed = {'action', 'path'} if action == 'open_here' else {'action'}
    if action not in ('activate', 'status', 'open_here') or set(request) != allowed:
        raise ValueError('不支持的本地命令')
    if action == 'open_here':
        path = request.get('path')
        if not isinstance(path, str) or not path or '\x00' in path or len(path) > 4096:
            raise ValueError('目录参数无效')
        if not os.path.isabs(path) or not os.path.isdir(path):
            raise ValueError('目录不存在或不是绝对路径')
        request = dict(request, path=os.path.realpath(path))
    return request


def validate_test_profile(profile, environment=None):
    if profile is None:
        return None
    environment = os.environ if environment is None else environment
    path = Path(profile)
    if not path.is_absolute():
        raise ValueError('--test-profile 必须为绝对路径')
    if environment.get('QT_QPA_PLATFORM') != 'offscreen':
        raise ValueError('--test-profile 仅允许 QT_QPA_PLATFORM=offscreen')
    data_dir = environment.get('LANSHARE_DATA_DIR')
    if not data_dir:
        raise ValueError('--test-profile 要求设置独立 LANSHARE_DATA_DIR')
    path = path.resolve()
    try:
        Path(data_dir).resolve().relative_to(path)
    except ValueError:
        raise ValueError('LANSHARE_DATA_DIR 必须位于 --test-profile 目录内')
    return path


class SingleInstance(QObject):
    def __init__(self, parent=None, name=None, profile=None):
        super().__init__(parent)
        user_hash = hashlib.sha256(current_user_identity().encode('utf-8')).hexdigest()[:24]
        profile_suffix = ('-test-' + hashlib.sha256(str(profile).encode('utf-8')).hexdigest()[:16]
                          if profile is not None else '')
        self.name = name or 'LanShare-v7-' + user_hash + profile_suffix
        self.server = None
        self._handler = None
        self._mutex = None
        self._file_lock = None
        self._clients = {}
        self._primary = False

    def acquire(self):
        if self._primary:
            return True
        if os.name == 'nt':
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            kernel.CreateMutexW.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.CreateMutexW(None, False, 'Global\\' + self.name)
            error = ctypes.get_last_error()
            if not handle:
                raise ctypes.WinError(error)
            if error == 183:  # ERROR_ALREADY_EXISTS
                kernel.CloseHandle(handle)
                return False
            self._mutex = handle
        else:
            self._file_lock = QLockFile(os.path.join(tempfile.gettempdir(), self.name + '.lock'))
            if not self._file_lock.tryLock(0):
                return False
        self._primary = True
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        if os.name != 'nt':
            QLocalServer.removeServer(self.name)
        if not self.server.listen(self.name):
            error = self.server.errorString()
            self.close()
            raise OSError('无法建立本地控制通道: ' + error)
        self.server.newConnection.connect(self._accept_connections)
        return True

    def set_handler(self, handler):
        self._handler = handler

    def request(self, request, timeout_ms=REQUEST_TIMEOUT_MS):
        request = validate_request(request)
        payload = json.dumps(request, ensure_ascii=True).encode('ascii') + b'\n'
        if len(payload) > MAX_MESSAGE_BYTES:
            raise ValueError('本地命令过长')
        sock = QLocalSocket()
        deadline = time.monotonic() + timeout_ms / 1000.0
        try:
            sock.connectToServer(self.name)
            if not sock.waitForConnected(timeout_ms):
                raise OSError('已有实例正在初始化或退出，请稍后重试')
            sock.write(payload)
            if not sock.waitForBytesWritten(max(1, int((deadline - time.monotonic()) * 1000))):
                raise OSError('发送本地命令失败')
            response = bytearray()
            while b'\n' not in response:
                remaining = max(0, int((deadline - time.monotonic()) * 1000))
                if not remaining or (not sock.bytesAvailable() and not sock.waitForReadyRead(remaining)):
                    raise OSError('已有实例未响应，未启动第二个服务')
                response.extend(bytes(sock.readAll()))
                if len(response) > MAX_MESSAGE_BYTES:
                    raise ValueError('本地响应过长')
            result = json.loads(bytes(response).split(b'\n', 1)[0].decode('utf-8'))
            if not isinstance(result, dict) or not isinstance(result.get('ok'), bool):
                raise ValueError('本地响应无效')
            return result
        finally:
            sock.abort()

    def _accept_connections(self):
        while self.server and self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            if len(self._clients) >= MAX_LOCAL_CLIENTS:
                sock.abort()
                sock.deleteLater()
                continue
            timer = QTimer(sock)
            timer.setSingleShot(True)
            timer.timeout.connect(sock.abort)
            self._clients[sock] = bytearray()
            sock.readyRead.connect(lambda socket=sock: self._read_request(socket))
            sock.disconnected.connect(lambda socket=sock: self._drop_client(socket))
            timer.start(REQUEST_TIMEOUT_MS)
            if sock.bytesAvailable():
                self._read_request(sock)

    def _read_request(self, sock):
        buffer = self._clients.get(sock)
        if buffer is None:
            return
        buffer.extend(bytes(sock.readAll()))
        if len(buffer) > MAX_MESSAGE_BYTES:
            self._reply(sock, {'ok': False, 'error': '本地命令过长'})
            return
        if b'\n' not in buffer:
            return
        try:
            frame, rest = bytes(buffer).split(b'\n', 1)
            if rest.strip():
                raise ValueError('每个连接只接受一条命令')
            request = validate_request(json.loads(frame.decode('utf-8')))
            if self._handler is None:
                result = {'ok': False, 'error': '软件正在初始化，请稍后重试'}
            else:
                result = self._handler(request)
                if not isinstance(result, dict) or not isinstance(result.get('ok'), bool):
                    raise ValueError('本地命令处理失败')
        except Exception as exc:
            result = {'ok': False, 'error': str(exc)}
        self._reply(sock, result)

    def _reply(self, sock, result):
        self._clients.pop(sock, None)
        payload = json.dumps(result, ensure_ascii=True).encode('ascii') + b'\n'
        sock.write(payload)
        sock.flush()
        sock.disconnectFromServer()

    def _drop_client(self, sock):
        self._clients.pop(sock, None)
        sock.deleteLater()

    def close(self):
        if self.server is not None:
            self.server.close()
            self.server.deleteLater()
            self.server = None
        for sock in list(self._clients):
            sock.abort()
        self._clients.clear()
        if self._mutex is not None:
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(self._mutex)
            self._mutex = None
        if self._file_lock is not None:
            self._file_lock.unlock()
            self._file_lock = None
        self._primary = False
