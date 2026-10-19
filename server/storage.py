"""运行期状态：会话、上传锁与偏移、ZIP 临时文件。"""
import os
import threading
import time
from pathlib import Path

from .config import SESSION_TTL, ZIP_CACHE_MAX_BYTES, ZIP_EXPIRE_SECONDS

SESSIONS_STORE = {}
UPLOAD_LOCKS = {}
UPLOAD_LOCKS_LOCK = threading.Lock()

# 断点续传的"可信偏移"：只有客户端上报的 size + mtime 与记录一致才允许续写
UPLOAD_STATE = {}
UPLOAD_STATE_LOCK = threading.Lock()
UPLOAD_STATE_MAX = 500

ZIP_DOWNLOAD_STORE = {}
ZIP_STORE_LOCK = threading.Lock()

# 当前共享目录：换成"全局可改"的值，handler 每次请求都读它，
# 这样运行中切换目录立刻生效（不用重启服务）。
SHARE_STATE = {'path': None}

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


def enforce_zip_cache(keep_path=None, max_bytes=None):
    """打包缓存的总容量上限。

    只按时间过期是不够的：十个人同时各打一个 5 GB 的包，10 分钟内就能把磁盘写满。
    这里按"最久没用过的先删"淘汰，正在生成/正在下载的那份（keep_path）不动。

    返回释放的字节数。
    """
    limit = ZIP_CACHE_MAX_BYTES if max_bytes is None else max_bytes
    if limit <= 0:
        return 0
    keep = os.path.abspath(keep_path) if keep_path else None

    with ZIP_STORE_LOCK:
        entries = []
        total = 0
        for zid, info in list(ZIP_DOWNLOAD_STORE.items()):
            path = info.get('path')
            if not path:
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                info['path'] = None          # 文件已经不在了，清掉引用
                continue
            entries.append((info.get('used') or info.get('time', 0), zid, path, size))
            total += size

        if total <= limit:
            return 0

        entries.sort()
        freed = 0
        for _stamp, zid, path, size in entries:
            if total - freed <= limit:
                break
            if keep and os.path.abspath(path) == keep:
                continue
            try:
                os.unlink(path)
            except OSError:
                continue
            info = ZIP_DOWNLOAD_STORE.get(zid)
            if info:
                info['path'] = None
            freed += size

    if freed:
        print("[ZIP] 缓存超过 %.0f MB 上限，淘汰了 %.1f MB"
              % (limit / 1024 / 1024, freed / 1024 / 1024))
    return freed


def start_zip_cleanup_thread():
    """启动 ZIP 临时文件后台清理线程"""
    def loop():
        while True:
            time.sleep(300)
            cleanup_zip_store()
            enforce_zip_cache()

    t = threading.Thread(target=loop, daemon=True)
    t.start()


def get_upload_lock(path):
    """获取某个上传目标文件对应的锁，避免并发写坏文件"""
    key = str(Path(path).resolve())
    with UPLOAD_LOCKS_LOCK:
        if key not in UPLOAD_LOCKS:
            UPLOAD_LOCKS[key] = threading.Lock()
        return UPLOAD_LOCKS[key]
