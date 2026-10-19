"""应用图标：磁盘 icon.ico 优先，打包后回退到 webassets 内嵌的那份。"""
from pathlib import Path

from PyQt5.QtGui import QIcon, QPixmap

from server.config import APP_BASE_DIR
from server.pages import load_icon_bytes

# 窗口与托盘共用同一个图标对象；用 set/get 避免各处 global APP_ICON
APP_ICON = None


def set_app_icon(icon):
    global APP_ICON
    APP_ICON = icon


def get_app_icon():
    return APP_ICON


def build_app_icon():
    """
    用 icon.ico 构建 QIcon。ico 里已有 16/32/48/64/128/256 六档 32bpp 图像，
    以前那套「解 2048x2048 JPG → 逐像素抠白底 → 再缩放 9 档」实测 ≈4 秒启动开销，
    还往源码里塞了 179 KB base64。
    单文件 exe 旁边没有 icon.ico 时，用 webassets 里内嵌的那份兜底。
    """

    for candidate in (APP_BASE_DIR / "icon.ico",
                      Path(__file__).resolve().parent / "icon.ico"):
        try:
            if candidate.exists():
                icon = QIcon(str(candidate))
                if not icon.isNull():
                    return icon
        except Exception as e:
            print(f"[Icon] 加载 {candidate} 失败: {e}")

    try:
        data = load_icon_bytes()
        if data:
            pixmap = QPixmap()
            if pixmap.loadFromData(data, "ICO") and not pixmap.isNull():
                return QIcon(pixmap)
    except Exception as e:
        print(f"[Icon] 内嵌图标解码失败: {e}")

    print("[Icon] 未找到可用图标，使用系统默认图标")
    return None
