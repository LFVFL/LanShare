"""运行期状态：会话、上传锁与偏移、ZIP 临时文件。"""
import threading
import time
from pathlib import Path

from .config import SESSION_TTL, ZIP_EXPIRE_SECONDS

SESSIONS_STORE = {}
UPLOAD_LOCKS = {}
UPLOAD_LOCKS_LOCK = threading.Lock()

# 断点续传的"可信偏移"：只有客户端上报的 size + mtime 与记录一致才允许续写
UPLOAD_STATE = {}
UPLOAD_STATE_LOCK = threading.Lock()
UPLOAD_STATE_MAX = 500

ZIP_DOWNLOAD_STORE = {}

# 聊天数据结构的读写锁（房间字典本身在 chat_store 里）
chat_lock = threading.Lock()


def _upload_key(base_path, filename):
    try:
        base = str(Path(base_path).resolve())
    except OSError:
        base = str(base_path)
    return base + '|' + filename


def remember_upload(base_path, filename, size, mtime, offset):
    """记录「这个文件传到哪了」。超过上限就整体清空（简单但足够）。"""
    with UPLOAD_STATE_LOCK:
        if len(UPLOAD_STATE) >= UPLOAD_STATE_MAX:
            UPLOAD_STATE.clear()
        UPLOAD_STATE[_upload_key(base_path, filename)] = {
            'size': int(size),
            'mtime': int(mtime),
            'offset': int(offset),
        }


def forget_upload(base_path, filename):
    with UPLOAD_STATE_LOCK:
        UPLOAD_STATE.pop(_upload_key(base_path, filename), None)


def cleanup_sessions():
    """清理过期 Session"""
    now = time.time()
    expired = [
        token for token, info in SESSIONS_STORE.items()
        if now - info.get('create_time', 0) > SESSION_TTL
    ]
    for token in expired:
        SESSIONS_STORE.pop(token, None)


def cleanup_zip_store():
    """清理过期的打包任务。

    现在打包是流式的，不落临时文件，所以这里只是把过期的"清单"丢掉；
    老版本留下的 'path' 字段如果还在，顺手把文件删掉。
    """
    now = time.time()
    for zid in list(ZIP_DOWNLOAD_STORE.keys()):
        info = ZIP_DOWNLOAD_STORE.get(zid)
        if not info:
            continue
        if now - info.get('time', 0) > ZIP_EXPIRE_SECONDS:
            if info.get('path'):
                try:
                    Path(info['path']).unlink(missing_ok=True)
                except Exception:
                    pass
            ZIP_DOWNLOAD_STORE.pop(zid, None)


def start_zip_cleanup_thread():
    """启动 ZIP 临时文件后台清理线程"""
    def loop():
        while True:
            time.sleep(300)
            cleanup_zip_store()

    t = threading.Thread(target=loop, daemon=True)
    t.start()


def get_upload_lock(path):
    """获取某个上传目标文件对应的锁，避免并发写坏文件"""
    key = str(Path(path).resolve())
    with UPLOAD_LOCKS_LOCK:
        if key not in UPLOAD_LOCKS:
            UPLOAD_LOCKS[key] = threading.Lock()
        return UPLOAD_LOCKS[key]
