"""把 web/ 下的静态资源与页面模板打包成 Python 模块 webassets.py。

为什么这么做：程序要打成单文件 exe，运行时不想依赖磁盘上的散文件。
开发时改 web/*.css、web/js/*.js 与 web/pages/*.html，然后跑一次：

    python tools/build_assets.py

生成的 webassets.py 会被 lanshare.py 直接 import。

前端 JS 拆成 web/js/*.js 多个分片，按文件名排序后原样拼接。每个分片都是
**自己的 IIFE**，彼此之间不共享闭包，只能通过 window.Lan 互相调用：

    00-core.js   命名空间 + util（esc / readJson / downloadUrl / copyText）
    10-icons.js  图标雪碧            → Lan.icons
    20-appearance.js 主题与玻璃       → Lan.appearance
    30-ui.js     toast / modal / menu → Lan.ui
    40-api.js    请求与管理员         → Lan.api
    50-files.js  文件页               → Lan.files
    60-upload.js 上传队列             → Lan.upload
    70-lightbox.js 灯箱 + 文件页启动  → Lan.lightbox / Lan.pages.files
    80-chat-list.js 聊天室列表        → Lan.pages.chatList
    85-chat-room.js 聊天室            → Lan.chat
    88-login.js  登录页               → Lan.pages.login
    90-boot.js   启动编排（只调用，不实现）

依赖方向只允许从低编号指向高编号；文件头的 `var x = Lan.y.x` 就是显式契约，
少一个会在第一次调用时报 ReferenceError（不会静默变成 undefined）。
"""

import base64
import io
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
PAGES = WEB / "pages"
JS_DIR = WEB / "js"
OUT = ROOT / "webassets.py"


def read(name: Path) -> str:
    with io.open(name, "r", encoding="utf-8") as f:
        return f.read()


def js_source() -> str:
    """把 web/js/*.js 按文件名顺序原样拼接。

    每个分片文件都以换行结尾（含结尾的空行），所以这里直接 ''.join 即可，
    不要在拼接时再补/去换行——补了会出现双空行，去了会把分片之间的空行吃掉。
    文件名前缀就是拼接顺序（00-、10- … 90-）。

    不再外套公用 IIFE：每个分片自己就是 IIFE，只在 window.Lan 上相遇。
    """
    files = sorted(JS_DIR.glob("*.js"))
    if not files:
        raise SystemExit("web/js/ 下没有 .js 文件")

    head = ("/* 局域网共享 · 前端（原生 JS，无依赖）\n"
            "   分片各自独立（自己的 IIFE），只通过 window.Lan 互相调用；\n"
            "   由 tools/build_assets.py 按文件名顺序拼成这一份并打包进 webassets.py */\n\n")
    return head + "".join(read(f) for f in files)


def pack(text: str) -> str:
    """用三引号包起来；内容里如果有三引号就退化成单行拼接。"""
    if '"""' not in text and not text.endswith("\\"):
        return 'r"""' + text + '"""'
    # 兜底：按行拼接，避免三引号冲突
    parts = text.split("\n")
    return "(\n" + "\n".join("    " + repr(p + "\n") for p in parts) + "\n)"


def main():
    css = read(WEB / "tokens.css") + "\n" + read(WEB / "app.css")
    js = js_source()

    # 图标也一起打包：单文件 exe 旁边没有 icon.ico 时还能显示应用图标
    icon_path = ROOT / "icon.ico"
    icon_b64 = ""
    if icon_path.exists():
        icon_b64 = base64.b64encode(icon_path.read_bytes()).decode("ascii")

    parts = [
        '"""自动生成，请改 web/ 下的源文件后重新运行 tools/build_assets.py。"""',
        "",
        "CSS = " + pack(css),
        "",
        "JS = " + pack(js),
        "",
        "ICON_ICO_B64 = " + repr(icon_b64),
        "",
    ]

    for page, const in [
        ("layout.html", "LAYOUT"),
        ("files.html", "FILES"),
        ("chat_list.html", "CHAT_LIST"),
        ("chat_room.html", "CHAT_ROOM"),
        ("login.html", "LOGIN"),
    ]:
        parts.append(const + " = " + pack(read(PAGES / page)))
        parts.append("")

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(parts))

    size = OUT.stat().st_size
    print("wrote %s (%.1f KB)" % (OUT.name, size / 1024))
    print("  css %.1f KB / js %.1f KB / icon %.1f KB" % (
        len(css) / 1024, len(js) / 1024, len(icon_b64) / 1024))


if __name__ == "__main__":
    main()
