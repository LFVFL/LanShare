"""文件列表：目录项分类、面包屑、同名改名。"""
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


class ListingMixin:
    """文件列表：目录项分类、面包屑、同名改名、共享内搜索。"""

    # 搜索的硬上限：宁可少给几条，也不能让一次搜索把硬盘扫穿
    SEARCH_MAX_RESULTS = 80
    SEARCH_MAX_SCAN = 20000
    SEARCH_MAX_SECONDS = 4.0
    SEARCH_SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.svn', '.idea'}

    FILE_KINDS = {
        'image': {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg', '.ico', '.heic'},
        'video': {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v'},
        'audio': {'.mp3', '.wav', '.flac', '.aac', '.m4a', '.ogg', '.wma'},
        'archive': {'.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.iso'},
        'doc': {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
                '.txt', '.md', '.csv', '.rtf', '.epub'},
        'code': {'.py', '.js', '.ts', '.json', '.html', '.css', '.sh', '.bat',
                 '.c', '.h', '.cpp', '.java', '.go', '.rs', '.yml', '.yaml', '.xml'},
    }

    def entry_kind(self, item):
        try:
            if item.is_dir():
                return 'folder'
        except OSError:
            return 'other'
        ext = item.suffix.lower()
        for kind, exts in self.FILE_KINDS.items():
            if ext in exts:
                return kind
        return 'other'

    def build_listing(self, current_path):
        """返回一份目录清单，HTML 页面与 /api/list 共用"""
        shared_path = Path(self.shared_path).resolve()
        current_path = Path(current_path).resolve()
        if not is_path_within(current_path, shared_path):
            current_path = shared_path

        entries = []
        try:
            for item in current_path.iterdir():
                try:
                    if not is_path_within(item.resolve(), shared_path):
                        continue
                    stat = item.stat()
                    is_dir = item.is_dir()
                    entries.append({
                        'name': item.name,
                        'enc': urllib.parse.quote(item.name),
                        'kind': self.entry_kind(item),
                        'size': 0 if is_dir else stat.st_size,
                        'sizeText': '-' if is_dir else self.format_size(stat.st_size),
                        'mtime': int(stat.st_mtime * 1000),
                        'timeText': time.strftime('%m-%d %H:%M', time.localtime(stat.st_mtime)),
                        'href': '/dir?path=' + urllib.parse.quote(str(item)) if is_dir else '',
                    })
                except OSError:
                    continue
        except (PermissionError, OSError):
            pass

        entries.sort(key=lambda e: e['name'].lower())
        return {
            'path': str(current_path),
            'entries': entries,
            'breadcrumb': self.get_breadcrumb(current_path),
        }

    def get_breadcrumb(self, current_path):
        shared_path = Path(self.shared_path).resolve()
        current_path = Path(current_path).resolve()
        parts = [
            '<a href="/dir?path={0}">根目录</a>'.format(
                urllib.parse.quote(str(shared_path))
            )
        ]

        if current_path != shared_path:
            try:
                rel_parts = current_path.relative_to(shared_path).parts
                current_dir = shared_path

                for part in rel_parts:
                    current_dir = current_dir / part
                    parts.append('<span>/</span>')
                    parts.append(
                        '<a href="/dir?path={0}">{1}</a>'.format(
                            urllib.parse.quote(str(current_dir)),
                            html.escape(part)
                        )
                    )
            except ValueError:
                pass

        return ''.join(parts)

    # ── 共享内搜索 ──────────────────────────────────────────────────────
    def api_search(self):
        """GET /api/search?q=关键词&path=起点目录

        只在共享目录内部递归找，命中文件名（不分大小写）。
        有结果上限 + 扫描上限 + 时间上限，扫不完就标记 truncated，
        免得有人对着一个几万文件的目录按回车。
        """
        needle = (self.q('q') or '').strip()
        if not needle:
            self.send_api_error('请输入关键词', 400)
            return
        if len(needle) > 100:
            self.send_api_error('关键词太长', 400)
            return

        shared_path = Path(self.shared_path).resolve()
        root = Path(urllib.parse.unquote(self.q('path', self.shared_path))).resolve()
        if not is_path_within(root, shared_path) or not root.is_dir():
            self.send_json_response(
                {'success': False, 'message': '路径不可访问'}, 403)
            return

        needle_lc = needle.lower()
        hits = []
        scanned = 0
        truncated = False
        deadline = time.time() + self.SEARCH_MAX_SECONDS

        for cur_dir, dirs, files in os.walk(root):
            # 跳过常见的依赖/版本库目录，别在这上面浪费时间
            dirs[:] = [d for d in dirs
                       if d not in self.SEARCH_SKIP_DIRS and not d.startswith('.')]
            dir_path = Path(cur_dir)
            for name in dirs + files:
                if needle_lc not in name.lower():
                    continue
                item = dir_path / name
                try:
                    if not is_path_within(item.resolve(), shared_path):
                        continue
                    stat = item.stat()
                    is_dir = item.is_dir()
                    try:
                        rel_dir = str(dir_path.relative_to(shared_path)).replace('\\', '/')
                    except ValueError:
                        rel_dir = ''
                    hits.append({
                        'name': name,
                        'enc': urllib.parse.quote(name),
                        'dir': rel_dir,
                        'dirHref': '/dir?path=' + urllib.parse.quote(str(dir_path)),
                        'encPath': urllib.parse.quote(
                            (rel_dir + '/' + name) if rel_dir else name),
                        'kind': self.entry_kind(item),
                        'size': 0 if is_dir else stat.st_size,
                        'sizeText': '-' if is_dir else self.format_size(stat.st_size),
                        'mtime': int(stat.st_mtime * 1000),
                        'timeText': time.strftime('%m-%d %H:%M',
                                                  time.localtime(stat.st_mtime)),
                    })
                except OSError:
                    continue
                if len(hits) >= self.SEARCH_MAX_RESULTS:
                    truncated = True
                    break

            scanned += len(dirs) + len(files)
            if (truncated or scanned >= self.SEARCH_MAX_SCAN
                    or time.time() > deadline):
                truncated = truncated or True
                break

        hits.sort(key=lambda e: (e['dir'], e['name'].lower()))
        self.send_json_response({
            'success': True,
            'query': needle,
            'root': str(root),
            'count': len(hits),
            'scanned': scanned,
            'truncated': truncated,
            'entries': hits,
        })

    def unique_name(self, current_path, name):
        """给同名文件找一个不冲突的副本名：a.txt → a (2).txt"""
        try:
            base = Path(urllib.parse.unquote(current_path)).resolve()
            name = sanitize_relative_path(name)
            if not name:
                return ''
            if not is_path_within(base, Path(self.shared_path).resolve()):
                base = Path(self.shared_path).resolve()
            if not (base / name).exists():
                return name
            stem, suffix = os.path.splitext(name)
            for i in range(2, 1000):
                candidate = f"{stem} ({i}){suffix}"
                if not (base / candidate).exists():
                    return candidate
        except Exception as e:
            print(f"[HTTP] 生成副本名失败: {e}")
        return name
