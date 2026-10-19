"""传输：下载（含 Range）、上传、断点续传、存在性与指纹。"""
import mimetypes
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
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


class TransferMixin:
    """传输：下载（含 Range）、上传、断点续传、存在性与指纹。"""

    def download_file(self, filename, current_path):
        try:
            filename = urllib.parse.unquote(filename)
            current_path = urllib.parse.unquote(current_path)
            file_path = (Path(current_path) / filename).resolve()

            if not is_path_within(file_path, self.shared_path):
                self.send_error(403)
                return

            if not file_path.exists() or not file_path.is_file():
                self.send_error(404)
                return

            file_size = file_path.stat().st_size
            content_type, encoding = mimetypes.guess_type(str(file_path))
            if not content_type:
                content_type = 'application/octet-stream'

            range_header = self.headers.get('Range')
            start, end, status_code = 0, file_size - 1, 200

            if range_header:
                m = re.search(r'bytes=(\d+)-(\d*)', range_header)
                if m:
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else file_size - 1
                    end = min(end, file_size - 1)

                    if start > end or start >= file_size:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{file_size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return

                    status_code = 206

            length = end - start + 1

            self.send_response(status_code)
            self.send_header('Content-Type', content_type)
            self.send_header('Accept-Ranges', 'bytes')

            if status_code == 206:
                self.send_header(
                    'Content-Range',
                    f'bytes {start}-{end}/{file_size}'
                )

            self.send_header('Content-Length', str(length))

            qp = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            is_preview = qp.get('preview', ['0'])[0] == '1'

            # 只有白名单里的类型才允许 inline；.html/.svg/.js 之类一律强制下载，
            # 否则上传一个 .html 再点预览，就是在同源执行任意脚本
            suffix = file_path.suffix.lower()
            if is_preview and suffix in INLINE_PREVIEW_EXTS:
                disp = 'inline'
            else:
                disp = 'attachment'

            encoded_filename = urllib.parse.quote(filename)
            self.send_header(
                'Content-Disposition',
                f'{disp}; filename="download"; filename*=UTF-8\'\'{encoded_filename}'
            )

            # 预览响应禁止执行脚本（inline SVG/HTML 也在内），但不加 sandbox，
            # 否则 Chrome 内置 PDF 预览会被一起禁掉
            self.send_header(
                'Content-Security-Policy',
                "default-src 'none'; img-src 'self' data:; media-src 'self'; "
                "style-src 'unsafe-inline'"
            )

            if encoding:
                self.send_header('Content-Encoding', encoding)

            self.end_headers()

            with open(file_path, 'rb') as f:
                f.seek(start)
                sent = 0

                while sent < length:
                    chunk = f.read(min(65536, length - sent))
                    if not chunk:
                        break

                    try:
                        self.wfile.write(chunk)
                        sent += len(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        break

        except Exception as e:
            print(f"[HTTP] 下载失败: {e}")
            try:
                self.send_error(500, "Download failed")
            except Exception:
                pass

    def check_file_exists(self, filename, current_path):
        """
        断点续传的"续"必须由服务端确认，不能只看文件大小。
        客户端把本地 file.size + lastModified 一起送上来，服务端只有在这个
        指纹和上次中断的记录一致时，才允许从 offset 续写；否则一律从 0 开始，
        避免「同名但内容不同」的两个文件被拼成坏文件。
        """
        try:
            filename = sanitize_relative_path(filename)
            current_path = Path(urllib.parse.unquote(current_path)).resolve()
            shared_path = Path(self.shared_path).resolve()

            if not filename:
                self.send_json_response(
                    {'size': 0, 'exists': False, 'mtime': 0, 'offset': 0, 'resume': False}
                )
                return

            file_path = (current_path / filename).resolve()

            if not is_path_within(file_path, shared_path):
                self.send_error(403)
                return

            exists = file_path.exists() and file_path.is_file()
            if exists:
                st = file_path.stat()
                size, mtime = st.st_size, int(st.st_mtime * 1000)
            else:
                size, mtime = 0, 0

            qp = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                local_size = int(qp.get('size', [''])[0])
                local_mtime = int(qp.get('mtime', [''])[0])
            except (TypeError, ValueError):
                local_size, local_mtime = -1, -1

            offset = 0
            resume = False
            state = None
            if local_size >= 0:
                with UPLOAD_STATE_LOCK:
                    state = UPLOAD_STATE.get(_upload_key(current_path, filename))
                if (
                    state
                    and state['size'] == local_size
                    and state['mtime'] == local_mtime
                    and state['offset'] == size
                    and exists
                ):
                    offset = state['offset']
                    resume = True

            self.send_json_response({
                'size': size,
                'exists': exists,
                'mtime': mtime,
                'offset': offset,
                'resume': resume,
            })

        except Exception as e:
            print(f"[HTTP] 检查文件失败: {e}")
            self.send_error(500)

    def handle_upload(self, current_path):
        save_path = None
        try:
            current_path = Path(urllib.parse.unquote(current_path)).resolve()
            shared_path = Path(self.shared_path).resolve()
            if not is_path_within(current_path, shared_path):
                current_path = shared_path

            content_type = self.headers.get('Content-Type', '')
            if 'boundary=' not in content_type:
                self.send_error(400)
                return

            content_length = int(self.headers.get('Content-Length', 0))
            if content_length == 0:
                self.send_error(400)
                return

            boundary = content_type.split('boundary=')[1].strip().strip('"')
            boundary_bytes = b'--' + boundary.encode('utf-8')

            bytes_read = 0
            filename = None
            while True:
                line = self.rfile.readline()
                bytes_read += len(line)
                if not line:
                    break
                if b'filename="' in line:
                    line_str = line.decode('utf-8', errors='ignore')
                    filename = line_str.split('filename="')[1].split('"')[0]
                if line == b'\r\n':
                    break

            if not filename:
                self.send_error(400)
                return

            filename = sanitize_relative_path(filename)
            if not filename:
                self.send_error(400, "Invalid filename")
                return
            save_path = (current_path / filename).resolve()
            if not is_path_within(save_path, self.shared_path):
                self.send_error(403)
                return
            save_path.parent.mkdir(parents=True, exist_ok=True)

            counter = 1
            orig = save_path
            while save_path.exists():
                save_path = orig.parent / "{}_{}{}".format(orig.stem, counter, orig.suffix)
                counter += 1

            remain = content_length - bytes_read
            with open(save_path, 'wb') as f:
                prev = self.rfile.read(min(65536, remain))
                remain -= len(prev)
                while True:
                    if remain <= 0:
                        if boundary_bytes in prev:
                            real = prev[:prev.find(boundary_bytes)]
                            if real.endswith(b'\r\n'):
                                real = real[:-2]
                                f.write(real)
                            else:
                                f.write(prev)
                        break
                    chunk = self.rfile.read(min(65536, remain))
                    remain -= len(chunk)
                    if not chunk:
                        f.write(prev)
                        break
                    if boundary_bytes in chunk:
                        combined = prev + chunk
                        pos = combined.find(boundary_bytes)
                        real = combined[:pos]
                        if real.endswith(b'\r\n'):
                            real = real[:-2]
                        f.write(real)
                        break
                    f.write(prev)
                    prev = chunk

            self.send_json_response({'success': True})
        except Exception as e:
            if save_path and save_path.exists():
                try:
                    os.remove(save_path)
                except Exception:
                    pass
            try:
                self.send_error(500, str(e))
            except Exception:
                pass

    def handle_chunk_upload(self, current_path, filename, offset):
        try:
            current_path = Path(urllib.parse.unquote(current_path)).resolve()
            shared_path = Path(self.shared_path).resolve()

            if not is_path_within(current_path, shared_path):
                self.send_error(403)
                return

            filename = sanitize_relative_path(filename)
            if not filename:
                self.send_error(400, "Invalid filename")
                return

            save_path = (current_path / filename).resolve()

            if not is_path_within(save_path, shared_path):
                self.send_error(403)
                return

            save_path.parent.mkdir(parents=True, exist_ok=True)

            chunk_len = int(self.headers.get('Content-Length', 0))
            if chunk_len < 0:
                self.send_error(400, "Bad chunk")
                return
            # chunk_len == 0 是合法情况：前端用它创建 0 字节文件

            # offset 不能超过已写入长度：否则 seek 会在文件中间留空洞（或撑爆磁盘），
            # 前端只要算错一次就会得到一个看起来"成功"的坏文件
            existing_size = save_path.stat().st_size if save_path.exists() else 0
            if offset > existing_size:
                self.send_api_error(
                    f"offset({offset}) 超过已写入长度({existing_size})", 400
                )
                return

            # 检查磁盘剩余空间，保留至少 512MB
            try:
                total, used, free = shutil.disk_usage(str(save_path.parent))
                if free < chunk_len + 512 * 1024 * 1024:
                    self.send_error(507, "Insufficient Storage")
                    return
            except Exception:
                pass

            lock = get_upload_lock(save_path)

            written = 0
            with lock:
                if offset == 0:
                    save_path.write_bytes(b'')

                mode = 'r+b' if save_path.exists() else 'wb'

                with open(save_path, mode) as f:
                    f.seek(offset)

                    remaining = chunk_len
                    while remaining > 0:
                        data = self.rfile.read(min(65536, remaining))
                        if not data:
                            break
                        f.write(data)
                        written += len(data)
                        remaining -= len(data)

            # 记下这次写到哪，下次续传时用来校验指纹
            qp = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                hint_size = int(qp.get('size', [''])[0])
                hint_mtime = int(qp.get('mtime', [''])[0])
            except (TypeError, ValueError):
                hint_size = hint_mtime = None
            if hint_size is not None:
                remember_upload(current_path, filename, hint_size, hint_mtime, offset + written)

            self.send_json_response({'success': True, 'offset': offset + written})

        except Exception as e:
            print(f"[HTTP] 分片上传失败: {e}")
            try:
                self.send_api_error("Upload failed", 500)
            except Exception:
                pass

    def finish_chunk_upload(self, current_path, filename):
        """客户端宣告传完了：清掉续传记录"""
        try:
            current_path = Path(urllib.parse.unquote(current_path)).resolve()
            filename = sanitize_relative_path(filename)
            if filename:
                forget_upload(current_path, filename)
            self.send_json_response({'success': True})
        except Exception as e:
            print(f"[HTTP] 结束上传失败: {e}")
            self.send_json_response({'success': False}, 500)
