"""二维码渲染：segno 可用就画真码，不可用返回 None（界面退化成纯地址文本）。

不把 segno 变成硬依赖：它只是"手机扫码"这条捷径，缺了不影响共享本身。
"""


def make_png(text, scale=6, border=2):
    """生成 PNG 字节；没有 segno 或生成失败都返回 None。"""
    if not text:
        return None
    try:
        import io

        import segno
    except ImportError:
        return None
    try:
        buf = io.BytesIO()
        segno.make(text, error='m').save(buf, kind='png', scale=scale, border=border)
        return buf.getvalue()
    except Exception:
        return None


def make_pixmap(text, size=150):
    """返回正方形象限图；拿不到二维码时返回 None。"""
    data = make_png(text)
    if not data:
        return None
    try:
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QPixmap
    except ImportError:
        return None
    pix = QPixmap()
    if not pix.loadFromData(data):
        return None
    return pix.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
