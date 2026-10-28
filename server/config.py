"""配置常量：路径、上限、白名单。改动集中在这里。"""
import os
import sys
from pathlib import Path

IS_FROZEN = getattr(sys, 'frozen', False) or "__compiled__" in globals()
DEFAULT_PORT = 5995
DEFAULT_SHARE_PATH = str(Path.home() / "Downloads")

# 会话与临时文件
SESSION_TTL = 86400          # 会话有效期：24 小时
ZIP_EXPIRE_SECONDS = 600     # ZIP 临时文件过期：10 分钟
ZIP_CACHE_MAX_BYTES = 2 * 1024 ** 3   # 打包缓存总量上限（超了按最久没用淘汰）

# 聊天
MAX_CHAT_LEN = 500                     # 每个房间保留条数
MAX_CHAT_IMAGE_BASE64_LEN = 7 * 1024 * 1024   # base64 后体积（原图约 5MB）
MAX_CHAT_THUMB_BASE64_LEN = 200 * 1024        # 缩略图（前端 canvas 生成，最长边 320）
ALLOWED_CHAT_IMAGE_EXTS = {"png", "jpg", "gif", "webp"}


def _env_limit(name, default):
    try:
        return max(0, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


# Binary uploads are chunked; 0 means no fixed per-image size ceiling.
CHAT_IMAGE_MAX_BYTES = _env_limit('LANSHARE_CHAT_IMAGE_MAX_MB', 0) * 1024 ** 2
CHAT_MEDIA_CHUNK_BYTES = 1024 ** 2
CHAT_MEDIA_TEMP_BYTES = _env_limit('LANSHARE_CHAT_TEMP_MB', 2048) * 1024 ** 2
CHAT_MEDIA_MIN_FREE_BYTES = _env_limit('LANSHARE_CHAT_MIN_FREE_MB', 512) * 1024 ** 2
CHAT_MEDIA_MAX_UPLOADS = 8
CHAT_MEDIA_EXPIRE_SECONDS = 1800
CHAT_MEDIA_MAX_PIXELS = _env_limit('LANSHARE_CHAT_MAX_PIXELS', 80000000)

# 请求体上限
MAX_JSON_BODY_LEN = 10 * 1024 * 1024


def get_app_base_dir():
    """
    获取程序所在目录。
    避免 Path("chat_data") 受当前工作目录影响。
    """
    if IS_FROZEN:
        return Path(sys.executable).resolve().parent
    # 本文件在 server/ 下，程序目录是它的上一级
    return Path(__file__).resolve().parent.parent


APP_BASE_DIR = get_app_base_dir()

# 聊天数据保存目录
CHAT_DATA_DIR = Path(os.environ.get('LANSHARE_DATA_DIR') or APP_BASE_DIR / "chat_data").resolve()
CHAT_IMAGE_DIR = CHAT_DATA_DIR / "images"
CHAT_UPLOAD_DIR = CHAT_DATA_DIR / "uploads"

# 这里保存的是聊天室集合，不只是单纯 history 列表，所以命名改准确一些
CHAT_ROOMS_FILE_PATH = CHAT_DATA_DIR / "rooms.json"

# 统一安全响应头（end_headers 时自动补）
SECURITY_HEADERS = (
    ('X-Content-Type-Options', 'nosniff'),
    ('X-Frame-Options', 'SAMEORIGIN'),
    ('Referrer-Policy', 'no-referrer'),
    ('Permissions-Policy', 'geolocation=(), microphone=(), camera=()'),
)

# 页面专用的 CSP：脚本全部同源（首屏那段明暗脚本也拆成了 /static/theme-boot.js，
# 所以不需要 'unsafe-inline'）；样式还留着 'unsafe-inline'，因为模板里有 style="" 属性
PAGE_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "frame-ancestors 'self'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "object-src 'none'"
)

# Compatibility export; paths.py is the single MIME/preview allowlist.
# HTML/SVG/Office/archives are downloads, never same-origin active previews.
from .paths import PREVIEW_MIME_TYPES

INLINE_PREVIEW_EXTS = frozenset(PREVIEW_MIME_TYPES)
