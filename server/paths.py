"""路径清理、包含判断、体积格式化、口令比较。"""
import urllib.parse
from pathlib import Path


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
