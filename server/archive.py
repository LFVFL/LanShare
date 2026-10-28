"""打包下载：默认缓存可续传，stream=1 边压边发；任务绑定共享目录代次。"""
import hashlib
import io
import os
import re
import tempfile
import threading
import time
import urllib.parse
import uuid
import zipfile
from pathlib import Path

from .config import *
from .paths import *
from .storage import *
from .chat_store import *
from .pages import *
from .paths import _password_ok
from .storage import _upload_key
from .storage import _retire_zip_paths


class _ZipTaskRevoked(PermissionError):
    pass


class _ZipSink:
    """把 socket 的 wfile 包成一个"只能往后写"的文件对象，喂给 zipfile。

    zipfile 发现 seek 不可用时会改用 data descriptor 记录大小和 CRC，
    所以不需要提前知道总长度，也不需要回退重写。
    """

    def __init__(self, wfile, guard=None):
        self._w = wfile
        self._written = 0
        self._guard = guard

    def write(self, data):
        if data:
            if self._guard:
                self._guard()
            self._w.write(data)
            self._written += len(data)
        return len(data)

    def flush(self):
        try:
            self._w.flush()
        except Exception:
            pass

    def tell(self):
        return self._written

    def seek(self, *args):
        raise io.UnsupportedOperation("streaming zip is not seekable")

    def seekable(self):
        return False


class ArchiveMixin:
    """打包下载：登记清单（POST /zip）+ 流式发送（GET /zip_download）。"""

    # 同时进行的流式打包条数上限。每条会占一个线程直到发完，
    # 所以单独设个小额度，别把并发名额全占了导致页面打不开。
    ZIP_STREAM_MAX = 4
    _zip_slots = threading.BoundedSemaphore(ZIP_STREAM_MAX)

    # ── 登记 ────────────────────────────────────────────────────────────
    def handle_zip_download(self, current_path, files):
        try:
            cleanup_zip_store()

            current_path = urllib.parse.unquote(current_path)
            shared_root, generation = shared_root_snapshot(self.shared_path)
            base_dir = checked_shared_path(Path(current_path), shared_root)

            if not files:
                self.send_json_response({'success': False, 'message': 'No files selected'}, 400)
                return

            timestamp = time.strftime('%Y%m%d_%H%M%S')
            if len(files) > 1:
                zip_name = f"批量下载_{timestamp}.zip"
            else:
                first_name = sanitize_relative_path(files[0])
                zip_name = f"{Path(first_name).stem}_{timestamp}.zip"

            entries, total = self._collect_zip_entries(base_dir, files, shared_root)
            if not entries:
                self.send_json_response(
                    {'success': False, 'message': 'Nothing to pack'}, 400)
                return

            zip_id = str(uuid.uuid4())
            info = {
                'entries': entries,          # [(绝对路径, 压缩包内名字)]
                'filename': zip_name,
                'size': total,               # 原始总大小，只用于前端提示
                'count': len(entries),
                'etag': self._zip_etag(entries),
                'time': time.time(),
                'root': shared_root,
                'generation': generation,
                'lock': threading.RLock(),
                'active': 0,
            }
            with SHARE_STATE_LOCK:
                if shared_root_snapshot(self.shared_path) != (shared_root, generation):
                    self.send_api_error('共享目录已切换，请重新选择文件', 409)
                    return
                with ZIP_STORE_LOCK:
                    ZIP_DOWNLOAD_STORE[zip_id] = info

            self.send_json_response({
                "success": True,
                "download_url": f"/zip_download?id={zip_id}",
                "count": len(entries),
                "size": total,
            })

        except PermissionError:
            self.send_api_error('路径越权或包含链接，无法打包', 403)
        except Exception as e:
            print(f"[HTTP] ZIP 打包失败: {e}")
            try:
                self.send_json_response(
                    {'success': False, 'message': 'ZIP failed'}, 500)
            except Exception:
                pass

    def _collect_zip_entries(self, base_dir, files, shared_root=None):
        """把选中的文件/目录展开成 [(绝对路径, 包内相对名)]，顺手算总大小。"""
        entries = []
        total = 0
        shared_root = shared_root or self.shared_path
        for fname in files:
            fname = sanitize_relative_path(fname)
            if not fname:
                continue

            try:
                target = checked_shared_path(base_dir / fname, shared_root)
            except FileNotFoundError:
                continue
            for full, is_directory in iter_shared_tree(target, shared_root):
                if is_directory:
                    continue
                arc = str(full.relative_to(base_dir))
                entries.append((full, arc))
                total += full.stat().st_size
        return entries, total

    def _check_zip_task(self, info, validate_entries=False):
        if (info.get('invalidated')
                or shared_root_snapshot(self.shared_path)
                != (info.get('root'), info.get('generation'))):
            raise _ZipTaskRevoked('Shared directory changed')
        if validate_entries:
            for path, _arc in info['entries']:
                try:
                    checked_shared_path(path, info['root'])
                except FileNotFoundError:
                    continue

    # ── 发送 ────────────────────────────────────────────────────────────
    def _zip_etag(self, entries):
        """按「包内路径 + 大小 + 修改时间」算版本号：文件变了 etag 就变，
        断点续传时用来判断"这还是不是同一份包"。"""
        digest = hashlib.sha1()
        for path, arc in entries:
            try:
                st = path.stat()
                digest.update(("%s|%d|%d" % (arc, st.st_size,
                                             int(st.st_mtime))).encode('utf-8'))
            except OSError:
                digest.update(("%s|missing" % arc).encode('utf-8'))
        return '"' + digest.hexdigest()[:16] + '"'

    def _build_zip_file(self, entries, info):
        """把 zip 真正生成到临时文件（只有续传才用），返回路径。"""
        fd, tmp_path = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        added = 0
        try:
            with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                for path, arc in entries:
                    try:
                        self._check_zip_task(info)
                        checked_shared_path(path, info['root'])
                        zf.write(path, arc)
                        added += 1
                    except PermissionError:
                        raise
                    except (OSError, ValueError) as e:
                        print(f"[ZIP] 跳过 {path}: {e}")
            self._check_zip_task(info)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise
        size = os.path.getsize(tmp_path)
        print(f"[ZIP] 已生成缓存 {Path(tmp_path).name}（{added} 个文件，{size / 1024:.0f} KB）")
        return Path(tmp_path)

    def _serve_zip_file(self, file_path, info, etag, start, end):
        """从缓存文件里发一段（206）或整份（200）。"""
        self._check_zip_task(info, validate_entries=True)
        size = os.path.getsize(file_path)
        if start is None:
            start, end, status = 0, size - 1, 200
        else:
            status = 206

        if start >= size:
            self.send_response(416)
            self.send_header('Content-Range', 'bytes */%d' % size)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return

        end = min(end, size - 1)
        length = end - start + 1

        self._zip_response_started = True
        self.send_response(status)
        self.send_header('Content-Type', 'application/zip')
        encoded = urllib.parse.quote(info['filename'])
        self.send_header(
            'Content-Disposition',
            f'attachment; filename="download.zip"; filename*=UTF-8\'\'{encoded}'
        )
        self.send_header('Content-Length', str(length))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('ETag', etag)
        self.send_header('Cache-Control', 'no-store')
        if status == 206:
            self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
        self.end_headers()

        with open(file_path, 'rb') as f:
            f.seek(start)
            left = length
            while left > 0:
                self._check_zip_task(info)
                chunk = f.read(min(262144, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def api_zip_download(self):
        """GET /zip_download?id=xxx[&stream=1]

        默认：先把 zip 生成到缓存文件，再按 Range 发 —— 浏览器能断点续传。
        stream=1：边压边发（零临时文件、立刻开始），但没法续传（不宣告 Accept-Ranges）。
        """
        self._zip_response_started = False
        cleanup_zip_store()
        zip_id = self.q('id')
        with ZIP_STORE_LOCK:
            info = ZIP_DOWNLOAD_STORE.get(zip_id)
        if not info:
            self.send_error(404, "Download expired")
            return

        if not self._zip_slots.acquire(blocking=False):
            self.send_error(503, "Too many downloads")
            return

        task_locked = info['lock'].acquire(blocking=False)
        if not task_locked:
            self._zip_slots.release()
            self.send_api_error('该打包任务正在下载，请稍后再试', 503)
            return
        with ZIP_STORE_LOCK:
            info['active'] += 1
        try:
            self._check_zip_task(info, validate_entries=True)
            # Serialize each task so a second request cannot replace its cache.
            etag = self._zip_etag(info['entries'])
            if etag != info.get('etag'):
                if info.get('path'):
                    _retire_zip_paths([info['path']])
                info['path'] = None
                info['etag'] = etag

            if self.q('stream') == '1':
                self._stream_zip(info, etag)
                return

            # 可续传模式：确保缓存文件在，然后按 Range 发
            if not info.get('path') or not Path(info['path']).is_file():
                info['path'] = str(self._build_zip_file(info['entries'], info))
                # 生成完立刻看一次总容量，别等后台线程（10 分钟太久）
                info['used'] = time.time()
                enforce_zip_cache(keep_path=info['path'])
            else:
                info['used'] = time.time()

            size = os.path.getsize(info['path'])
            range_header = self.headers.get('Range') or ''
            if_range = self.headers.get('If-Range')

            start = end = None
            if range_header and (not if_range or if_range == etag):
                m = re.search(r'bytes=(\d+)-(\d*)', range_header)
                if m:
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
            self._serve_zip_file(Path(info['path']), info, etag, start, end)

        except _ZipTaskRevoked:
            if info.get('path'):
                _retire_zip_paths([info['path']])
            if not self._zip_response_started:
                self.send_api_error('共享目录已切换，请重新打包', 410)
            else:
                self.close_connection = True
        except PermissionError:
            if not self._zip_response_started:
                self.send_api_error('文件路径发生变化或包含链接，下载已拒绝', 403)
            else:
                self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except OSError:
            if not self._zip_response_started:
                self.send_api_error('打包文件不可用，请重新打包', 404)
            else:
                self.close_connection = True
        except Exception as e:        # 头已经发出去了，只能记日志
            print(f"[ZIP] 发送失败: {e}")
            if not self._zip_response_started:
                self.send_api_error('打包下载失败', 500)
            else:
                self.close_connection = True
        finally:
            with ZIP_STORE_LOCK:
                info['active'] -= 1
            info['lock'].release()
            self._zip_slots.release()
            if info.get('invalidated') and info.get('path'):
                _retire_zip_paths([info['path']])
            cleanup_zip_store()

    def _stream_zip(self, info, etag):
        """边压边发。不宣告 Accept-Ranges：两条路径的 zip 字节布局不同
        （流式用 data descriptor），混着续传会拿到错位的内容。"""
        self._check_zip_task(info, validate_entries=True)
        self._zip_response_started = True
        self.send_response(200)
        self.send_header('Content-Type', 'application/zip')
        encoded = urllib.parse.quote(info['filename'])
        self.send_header(
            'Content-Disposition',
            f'attachment; filename="download.zip"; filename*=UTF-8\'\'{encoded}'
        )
        self.send_header('Cache-Control', 'no-store')
        self.send_header('ETag', etag)
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True

        sink = _ZipSink(self.wfile, lambda: self._check_zip_task(info))
        added = 0
        with zipfile.ZipFile(sink, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for path, arc in info['entries']:
                try:
                    self._check_zip_task(info)
                    checked_shared_path(path, info['root'])
                    zf.write(path, arc)
                    added += 1
                except PermissionError:
                    raise
                except (OSError, ValueError) as e:
                    print(f"[ZIP] 跳过 {path}: {e}")
                    continue
        print(f"[ZIP] 已流式发送 {info['filename']}（{added} 个文件，"
              f"{sink._written / 1024:.0f} KB）")
