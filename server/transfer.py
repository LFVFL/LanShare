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
from email import policy
from email.parser import BytesParser
from pathlib import Path

from .config import *
from .paths import *
from .storage import *
from .chat_store import *
from .pages import *
from .paths import _password_ok
from .storage import _upload_key


class _MultipartReader:
    """Length-bounded multipart reader with a sliding delimiter window."""

    def __init__(self, stream, length):
        self.stream = stream
        self.remaining = length
        self.buffer = bytearray()

    def read_more(self):
        if self.remaining <= 0:
            raise ValueError('Truncated multipart body')
        data = self.stream.read(min(65536, self.remaining))
        if not data:
            raise ValueError('Truncated multipart body')
        self.remaining -= len(data)
        self.buffer.extend(data)

    def readline(self, limit=8192):
        while True:
            index = self.buffer.find(b'\r\n')
            if index >= 0:
                if index + 2 > limit:
                    raise ValueError('Multipart header is too long')
                line = bytes(self.buffer[:index + 2])
                del self.buffer[:index + 2]
                return line
            if len(self.buffer) > limit:
                raise ValueError('Multipart header is too long')
            self.read_more()

    def headers(self):
        lines = []
        length = 0
        while True:
            line = self.readline()
            length += len(line)
            if length > 65536:
                raise ValueError('Multipart headers are too large')
            if line == b'\r\n':
                return BytesParser(policy=policy.default).parsebytes(b''.join(lines))
            lines.append(line)

    def copy_part(self, output, boundary):
        marker = b'\r\n--' + boundary
        keep = len(marker) + 2
        while True:
            index = self.buffer.find(marker)
            if index >= 0:
                end = index + len(marker)
                while len(self.buffer) < end + 2:
                    self.read_more()
                suffix = bytes(self.buffer[end:end + 2])
                consumed = end + 2
                is_boundary = suffix == b'\r\n'
                if suffix == b'--':
                    while len(self.buffer) < end + 4 and self.remaining:
                        self.read_more()
                    if len(self.buffer) == end + 2 and not self.remaining:
                        is_boundary = True
                    elif bytes(self.buffer[end + 2:end + 4]) == b'\r\n':
                        is_boundary = True
                        consumed = end + 4
                if is_boundary:
                    if output is not None:
                        output.write(self.buffer[:index])
                    del self.buffer[:consumed]
                    return suffix == b'--'
                # A delimiter-like byte sequence inside the file is still data.
                safe = index + 2
                if output is not None:
                    output.write(self.buffer[:safe])
                del self.buffer[:safe]
                continue
            else:
                safe = max(0, len(self.buffer) - keep)
            if safe:
                if output is not None:
                    output.write(self.buffer[:safe])
                del self.buffer[:safe]
            self.read_more()

    def finish(self):
        # Consume the declared epilogue so the next keep-alive request is clean.
        self.buffer.clear()
        while self.remaining:
            self.read_more()
            self.buffer.clear()


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
            qp = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            is_preview = qp.get('preview', ['0'])[0] == '1'
            preview_type = preview_mime_type(file_path) if is_preview else None
            # 第二项（原文件自身编码，如 .gz → gzip）这里用不到：
            # 我们要发的是原样字节，绝不能拿它当 Content-Encoding 响应头。
            content_type, _file_encoding = mimetypes.guess_type(str(file_path))
            if preview_type:
                content_type = preview_type
            elif not content_type:
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

            # Unknown/active document types remain downloads, not false previews.
            disp = 'inline' if preview_type else 'attachment'

            encoded_filename = urllib.parse.quote(filename)
            self.send_header(
                'Content-Disposition',
                f'{disp}; filename="download"; filename*=UTF-8\'\'{encoded_filename}'
            )

            # No sandbox: it also disables Chrome's built-in PDF viewer.
            # Active formats such as HTML/SVG are never inline in the first place.
            self.send_header(
                'Content-Security-Policy',
                "default-src 'none'; img-src 'self' data:; media-src 'self'; "
                "style-src 'unsafe-inline'"
            )

            # 注意：mimetypes.guess_type() 的第二项（encoding）描述的是"这个文件
            # 本身是什么编码"（.gz → gzip），不是"本次响应做过压缩"。把它当
            # Content-Encoding 发出去，浏览器会把已经是 gzip 的字节再解压一遍，
            # 用户下到的 backup.tar.gz 其实是解压后的明文——压缩包直接打不开。
            # 所以这里什么都不发；真要压缩响应，得像 _encode_body 那样真去压。

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
            shared_path = Path(self.shared_path).resolve()
            current_path = checked_shared_path(
                Path(urllib.parse.unquote(current_path)), shared_path
            )
            content_type = BytesParser(policy=policy.default).parsebytes(
                ('Content-Type: ' + self.headers.get('Content-Type', '')
                 + '\r\n\r\n').encode('utf-8')
            )
            if content_type.get_content_type() != 'multipart/form-data':
                raise ValueError('Expected multipart/form-data')
            boundary = (content_type.get_boundary() or '').encode('ascii')
            if (not boundary or len(boundary) > 70
                    or any(byte < 32 or byte > 126 for byte in boundary)):
                raise ValueError('Invalid multipart boundary')
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length <= 0:
                raise ValueError('Empty multipart body')
            reader = _MultipartReader(self.rfile, content_length)
            if reader.readline() != b'--' + boundary + b'\r\n':
                raise ValueError('Invalid initial multipart boundary')
            final = False
            for _part in range(64):
                headers = reader.headers()
                filename = headers.get_filename()
                if filename is None:
                    final = reader.copy_part(None, boundary)
                else:
                    if save_path is not None:
                        raise ValueError('Only one file is accepted per request')
                    filename = sanitize_relative_path(filename)
                    if not filename:
                        raise ValueError('Invalid filename')
                    original = checked_shared_path(
                        current_path / filename, shared_path, allow_missing=True
                    )
                    original.parent.mkdir(parents=True, exist_ok=True)
                    counter = 0
                    while True:
                        candidate = original if counter == 0 else original.with_name(
                            '{}_{}{}'.format(original.stem, counter, original.suffix)
                        )
                        checked_shared_path(candidate, shared_path, allow_missing=True)
                        try:
                            output = candidate.open('xb')
                            break
                        except FileExistsError:
                            counter += 1
                    save_path = candidate
                    with output:
                        final = reader.copy_part(output, boundary)
                if final:
                    break
            if not final or save_path is None:
                raise ValueError('Missing file or final multipart boundary')
            reader.finish()
            self.send_json_response({'success': True})
        except PermissionError:
            if save_path is not None:
                save_path.unlink(missing_ok=True)
            self.close_connection = True
            self.send_api_error('路径越权或包含链接，上传已拒绝', 403)
        except (ValueError, UnicodeError, TimeoutError, ConnectionError) as e:
            if save_path is not None:
                save_path.unlink(missing_ok=True)
            self.close_connection = True
            self.send_api_error('上传内容不完整或格式错误: ' + str(e), 400)
        except Exception as e:
            if save_path is not None and save_path.exists():
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
