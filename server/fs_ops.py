"""本机文件操作：打开、删除、改名、新建目录。"""
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


class FsOpsMixin:
    """本机文件操作：打开、删除、改名、新建目录。"""

    def open_file(self, filename, current_path):
        try:
            filename = urllib.parse.unquote(filename)
            current_path = urllib.parse.unquote(current_path)
            file_path = (Path(current_path) / filename).resolve()
            if not is_path_within(file_path, self.shared_path):
                self.send_error(403, "Access denied")
                return
            if not file_path.exists() or not file_path.is_file():
                self.send_error(404, "File not found")
                return

            cache_key = str(file_path)
            current_time = time.time()
            with type(self)._open_lock:
                if cache_key in type(self)._open_cache:
                    if current_time - type(self)._open_cache[cache_key] < 1.5:
                        self.send_json_response({'success': True})
                        return
                type(self)._open_cache[cache_key] = current_time

            system = platform.system()
            if system == "Windows":
                os.startfile(str(file_path))
            elif system == "Darwin":
                subprocess.Popen(["open", str(file_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", str(file_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            self.send_json_response({'success': True})
        except Exception:
            self.send_json_response({'success': False}, 500)

    def delete_file(self, filename, current_path):
        try:
            filename = urllib.parse.unquote(filename)
            current_path = urllib.parse.unquote(current_path)
            target = (Path(current_path) / filename).resolve()
            if not is_path_within(target, self.shared_path):
                self.send_error(403)
                return
            # 共享根本不参与"删除"：`.` 或空名字解析出来就是根自己，
            # is_path_within(根, 根) 是成立的，会直接把整个共享目录 rmtree 掉。
            # 父级同理（`foo/..`）。这里显式挡掉，别让它走到下面的 rmtree。
            root = Path(self.shared_path).resolve()
            if target == root or target == root.parent:
                self.send_api_error("不能删除共享根目录", 400)
                return
            if not target.exists():
                self.send_error(404)
                return
            if target.is_file():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)
            self.send_json_response({'success': True})
        except Exception as e:
            self.send_error(500, str(e))

    def rename_file(self, old_name, new_name, current_path):
        try:
            old_name = urllib.parse.unquote(old_name)
            new_name = urllib.parse.unquote(new_name).strip()
            current_path = urllib.parse.unquote(current_path)

            if not new_name or '/' in new_name or '\\' in new_name or new_name in ('.', '..'):
                self.send_api_error("Invalid name", 400)
                return

            old_path = (Path(current_path) / old_name).resolve()
            new_path = (Path(current_path) / new_name).resolve()

            if not is_path_within(old_path, self.shared_path) or not is_path_within(new_path, self.shared_path):
                self.send_error(403)
                return

            if not old_path.exists():
                self.send_error(404)
                return

            if new_path.exists():
                self.send_error(409)
                return

            old_path.rename(new_path)
            self.send_json_response({'success': True})

        except Exception as e:
            print(f"[HTTP] 重命名失败: {e}")
            self.send_api_error("Rename failed", 500)

    def create_directory(self, dirname, current_path):
        try:
            dirname = urllib.parse.unquote(dirname).strip()
            current_path = urllib.parse.unquote(current_path)

            if not dirname or '/' in dirname or '\\' in dirname or dirname in ('.', '..'):
                self.send_api_error("Invalid directory name", 400)
                return

            new_dir = (Path(current_path) / dirname).resolve()

            if not is_path_within(new_dir, self.shared_path):
                self.send_error(403)
                return

            if new_dir.exists():
                self.send_error(409)
                return

            new_dir.mkdir(parents=True)
            self.send_json_response({'success': True})

        except Exception as e:
            print(f"[HTTP] 创建目录失败: {e}")
            self.send_api_error("Create directory failed", 500)

    # ── 批量移动 / 复制 ─────────────────────────────────────────────────
    def post_move(self):
        self._relocate(copy=False)

    def post_copy(self):
        self._relocate(copy=True)

    def _free_target(self, dest_dir, name):
        """目标目录里重名就加序号：a.txt → a (2).txt"""
        target = dest_dir / name
        if not target.exists():
            return target
        stem, suffix = os.path.splitext(name)
        for i in range(2, 1000):
            candidate = dest_dir / f"{stem} ({i}){suffix}"
            if not candidate.exists():
                return candidate
        return None

    def _post_params(self):
        """合并 query 与表单 body 的参数（POST 写接口两种传法都支持）。"""
        merged = {}
        for key, vals in (self._query or {}).items():
            merged[key] = list(vals)
        try:
            length = int(self.headers.get('Content-Length', 0))
        except (TypeError, ValueError):
            length = 0
        if length > 0:
            raw = self.rfile.read(length).decode('utf-8', 'replace')
            for key, vals in urllib.parse.parse_qs(raw).items():
                merged.setdefault(key, []).extend(vals)
        return merged

    def _relocate(self, copy=False):
        """把选中的若干项移动/复制到 dst 指定的子目录（相对当前目录或共享根）。

        参数：path=当前目录，dst=目标相对路径，name=可重复的条目名
        （浏览器把参数放在表单 body 里，所以这里合并 query + body 一起看）
        """
        try:
            params = self._post_params()

            def one(key, default=''):
                return (params.get(key) or [default])[0]

            base = Path(urllib.parse.unquote(one('path', self.shared_path))).resolve()
            shared = Path(self.shared_path).resolve()
            if not is_path_within(base, shared) or not base.is_dir():
                self.send_error(403, "Access denied")
                return

            raw_dst = urllib.parse.unquote(one('dst')).replace('\\', '/')
            # 不支持用 .. 往上跳：宁可让用户把路径写全，也不做遍历语义
            if any(part == '..' for part in raw_dst.split('/')):
                self.send_api_error("目标路径不能包含 ..", 400)
                return
            dst_rel = sanitize_relative_path(raw_dst)
            dest_dir = (base / dst_rel).resolve() if dst_rel else base
            if not is_path_within(dest_dir, shared):
                self.send_api_error("目标目录越权", 403)
                return
            # 目标不存在就建出来（界面上写的是"会自动创建"）
            if not dest_dir.exists():
                try:
                    dest_dir.mkdir(parents=True)
                except OSError as e:
                    self.send_api_error("创建目标目录失败: %s" % e, 400)
                    return
            elif not dest_dir.is_dir():
                self.send_api_error("目标不是文件夹", 400)
                return

            names = params.get('name', [])
            if not names:
                self.send_api_error("没有选中文件", 400)
                return
            if len(names) > 200:
                self.send_api_error("一次最多 200 项", 400)
                return

            moved, skipped = [], []
            for raw in names:
                name = sanitize_relative_path(urllib.parse.unquote(raw))
                if not name:
                    continue
                src = (base / name).resolve()
                if not is_path_within(src, shared) or not src.exists():
                    skipped.append({'name': name, 'reason': 'not found'})
                    continue
                if src == dest_dir or src.parent == dest_dir:
                    skipped.append({'name': name, 'reason': 'same dir'})
                    continue
                # 目录不能搬进自己里面
                if src.is_dir() and is_path_within(dest_dir, src):
                    skipped.append({'name': name, 'reason': 'into itself'})
                    continue

                target = self._free_target(dest_dir, src.name)
                if target is None:
                    skipped.append({'name': name, 'reason': 'no free name'})
                    continue
                try:
                    if copy:
                        if src.is_dir():
                            shutil.copytree(src, target)
                        else:
                            shutil.copy2(src, target)
                    else:
                        shutil.move(str(src), str(target))
                    moved.append(str(target.relative_to(shared)))
                except (OSError, shutil.Error) as e:
                    skipped.append({'name': name, 'reason': str(e)})

            self.send_json_response({
                'success': True,
                'moved': moved,
                'skipped': skipped,
                'copy': bool(copy),
            })

        except Exception as e:
            print(f"[HTTP] 移动/复制失败: {e}")
            self.send_api_error("Move/Copy failed", 500)
