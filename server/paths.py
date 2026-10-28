"""路径清理、包含判断、体积格式化、口令比较。"""
import os
import stat
import urllib.parse
from pathlib import Path


PREVIEW_MIME_TYPES = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
    '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp',
    '.ico': 'image/x-icon', '.pdf': 'application/pdf',
    '.txt': 'text/plain; charset=utf-8', '.log': 'text/plain; charset=utf-8',
    '.md': 'text/plain; charset=utf-8', '.csv': 'text/plain; charset=utf-8',
    '.mp4': 'video/mp4', '.webm': 'video/webm', '.ogg': 'audio/ogg',
    '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.m4a': 'audio/mp4',
}


def preview_mime_type(path):
    """Stable browser MIME allowlist; do not trust Windows registry MIME guesses."""
    return PREVIEW_MIME_TYPES.get(Path(path).suffix.lower())


def can_preview(path):
    return preview_mime_type(path) is not None


def sanitize_relative_path(name):
    """
    清理上传相对路径。
    支持文件夹上传，但过滤空路径、.、..。
    """
    name = urllib.parse.unquote(name or "")
    name = name.replace('\\', '/').lstrip('/')
    parts = []
    for part in name.split('/'):
        part = part.strip()
        if not part or part in ('.', '..'):
            continue
        parts.append(part)
    return '/'.join(parts)


def is_path_within(child_path, parent_path):
    child = Path(child_path).resolve()
    parent = Path(parent_path).resolve()
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def is_link_or_reparse(path):
    """Include Windows junctions, which are not reported as symbolic links."""
    info = Path(path).lstat()
    return bool(
        stat.S_ISLNK(info.st_mode)
        or getattr(info, 'st_file_attributes', 0)
        & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
    )


def checked_shared_path(path, shared_root, allow_missing=False):
    """Validate lexical ancestry before resolve() can hide a nested link."""
    root = Path(shared_root).resolve(strict=True)
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise PermissionError('Path is outside the shared directory') from exc

    current = root
    for part in relative.parts:
        current /= part
        try:
            if is_link_or_reparse(current):
                raise PermissionError('Links and reparse points are not allowed')
        except FileNotFoundError:
            if allow_missing:
                break
            raise

    resolved = candidate.resolve(strict=not allow_missing)
    if not is_path_within(resolved, root):
        raise PermissionError('Path is outside the shared directory')
    return resolved


def iter_shared_tree(path, shared_root):
    """Visit only regular files/directories, never follow a reparse point."""
    stack = [Path(path)]
    while stack:
        current = checked_shared_path(stack.pop(), shared_root)
        info = current.lstat()
        if stat.S_ISDIR(info.st_mode):
            yield current, True
            stack.extend(reversed(list(current.iterdir())))
        elif stat.S_ISREG(info.st_mode):
            yield current, False
        else:
            raise PermissionError('Unsupported file entry')


def format_size_value(size):
    """格式化文件大小，供 access_logger 等 Handler 外部使用。"""
    try:
        size = float(size)
    except Exception:
        return str(size)

    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return "{:.1f} {}".format(size, unit)
        size /= 1024.0

    return "{:.1f} TB".format(size)


def _password_ok(input_value, expected):
    """常量时间比较，避免用 == 逐字符比较被计时攻击"""
    import hmac
    if expected is None:
        return False
    try:
        return hmac.compare_digest(
            str(input_value).encode('utf-8'), str(expected).encode('utf-8')
        )
    except Exception:
        return False
