"""页面模板与图标：webassets 是 tools/build_assets.py 从 web/ 打出来的。"""
import base64
import hashlib
import html
import re
from pathlib import Path

from .config import APP_BASE_DIR

try:
    import webassets
except ImportError:
    webassets = None
    print("[Web] 缺少 webassets.py，请先运行 tools/build_assets.py")

# icon.ico 的字节缓存，避免每次请求都读盘
_ICON_BYTES_CACHE = None


def load_icon_bytes():
    """
    取应用图标字节：优先磁盘上的 icon.ico；
    打成单文件 exe、旁边没有图标文件时，退回 webassets 里内嵌的那份。
    """
    global _ICON_BYTES_CACHE
    if _ICON_BYTES_CACHE is not None:
        return _ICON_BYTES_CACHE

    data = b''
    for candidate in (APP_BASE_DIR / "icon.ico",
                      Path(__file__).resolve().parent / "icon.ico"):
        try:
            if candidate.exists():
                data = candidate.read_bytes()
                break
        except OSError:
            continue

    if not data and webassets is not None:
        try:
            b64 = getattr(webassets, "ICON_ICO_B64", "") or ""
            data = base64.b64decode(b64) if b64 else b''
        except Exception:
            data = b''

    _ICON_BYTES_CACHE = data
    return data


# ══════════════════════════════════════════════════════════════════════════════
# 页面渲染（模板来自 webassets，占位符形如 {{NAME}}）
# ══════════════════════════════════════════════════════════════════════════════

_PLACEHOLDER_RE = re.compile(r"\{\{[A-Z_][A-Z0-9_]*\}\}")


def _asset_version():
    """静态资源的版本号：内容变了它才变，用来拼 ?v=xxx。

    有了它，CSS/JS 就能用「一年 + immutable」的长缓存：改完源码重新
    build_assets 后 URL 变了，浏览器自然会去取新的，不需要每次 304 校验。
    """
    if webassets is None:
        return "0"
    digest = hashlib.sha1()
    digest.update((webassets.CSS or "").encode("utf-8"))
    digest.update((webassets.JS or "").encode("utf-8"))
    digest.update((getattr(webassets, "THEME_BOOT", "") or "").encode("utf-8"))
    return digest.hexdigest()[:10]


ASSET_VERSION = _asset_version()


def render_template(template, **ctx):
    out = template or ""
    for key, value in ctx.items():
        out = out.replace("{{" + key + "}}", "" if value is None else str(value))
    return _PLACEHOLDER_RE.sub("", out)


def page_shell(page, title, host, content, topbar="", extra="",
               mobile_title="", mobile_actions="", mobile_left=None,
               active="files", body_attrs=""):
    """把内容塞进统一外壳（侧栏 / 移动端顶栏 / 底部导航）"""
    nav_files = ' aria-current="page"' if active == "files" else ""
    nav_chat = ' aria-current="page"' if active == "chat" else ""
    if mobile_left is None:
        mobile_left = '<img src="/favicon.ico" alt="">'

    return render_template(
        webassets.LAYOUT,
        TITLE=html.escape(title),
        PAGE=page,
        HOST=html.escape(host),
        BODY_ATTRS=body_attrs,
        NAV_FILES=nav_files,
        NAV_CHAT=nav_chat,
        TAB_FILES=nav_files,
        TAB_CHAT=nav_chat,
        MOBILE_LEFT=mobile_left,
        MOBILE_TITLE=html.escape(mobile_title or title),
        MOBILE_ACTIONS=mobile_actions,
        TOPBAR=topbar,
        CONTENT=content,
        EXTRA=extra,
        ASSET_V=ASSET_VERSION,
    )

# ══════════════════════════════════════════════════════════════════════════════
# HTTP Handler
# ══════════════════════════════════════════════════════════════════════════════
