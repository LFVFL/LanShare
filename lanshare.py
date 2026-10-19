"""局域网云盘共享工具 —— 程序入口。

目录结构：
    lanshare.py   入口（本文件）：解析参数、建 Qt 应用、开主窗口
    server/       HTTP 服务：配置、运行状态、页面、文件与聊天接口、路由
    ui/           桌面界面：主窗口、控件、图标
    web/          前端源码（CSS/JS/页面），由 tools/build_assets.py 打包
    webassets.py  打包产物，运行时被 import（打包成 exe 时也要带上）

依赖方向是单向的：入口 → ui/server → handler → {auth, files_api, http_util}
→ {storage, chat_store, pages, paths, config}。
"""
import argparse
import platform
import sys

from PyQt5.QtWidgets import QApplication

from server.config import *                       # noqa: F401,F403
from server.pages import webassets                # noqa: F401
from server.pages import ASSET_VERSION
from server.storage import start_zip_cleanup_thread
from server.handler import FileShareHandler        # noqa: F401  外部脚本会用到
from server.runner import FileSharer               # noqa: F401

from ui.icon import build_app_icon, set_app_icon
from ui.widgets import ClickableLabel             # noqa: F401
from ui.main_window import FileShareGUI


def main():
    if webassets is None:
        msg = ("缺少 webassets.py。\n\n"
               "打包时必须让 webassets.py 和 lanshare.py 在同一个目录：\n"
               "  1) 先运行 python tools/build_assets.py\n"
               "  2) 打包工具里把这两个文件一起带上\n\n"
               "（源码运行时也一样，缺文件无法渲染网页。）")
        print(msg)
        try:
            from PyQt5.QtWidgets import QApplication as _QA, QMessageBox as _QMB
            _app = _QA.instance() or _QA(sys.argv)
            _QMB.critical(None, "缺少资源文件", msg)
        except Exception:
            pass
        return 1

    if platform.system() == 'Windows':
        try:
            import ctypes
            myappid = 'FileShareTool.LanCloudShare.App.7'
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception as e:
            print(f"[Main] 设置 AppUserModelID 失败: {e}")

    start_zip_cleanup_thread()

    # 资源版本：改了 web/ 之后必须重新 build_assets 并重启，
    # 打印出来是为了和浏览器里 /static/app.css?v= 对一眼，省得猜"到底生效没"
    print("[Web] 前端资源版本 %s（改动 web/ 后需 build_assets.py + 重启）" % ASSET_VERSION)

    app = QApplication(sys.argv)
    app.setApplicationName("局域网云盘")
    app.setOrganizationName("FileShareTool")

    icon = build_app_icon()
    set_app_icon(icon)
    if icon and not icon.isNull():
        app.setWindowIcon(icon)

    parser = argparse.ArgumentParser(description='局域网云盘共享工具')
    parser.add_argument('path', nargs='?', help='要共享的文件夹路径')
    parser.add_argument('-p', '--port', type=int, help='服务端口')
    parser.add_argument('--startup', action='store_true', help='开机自启模式')
    args = parser.parse_args()

    window = FileShareGUI(
        initial_path=args.path,
        initial_port=args.port,
        auto_start=args.startup
    )

    if args.startup:
        window.hide()
    else:
        window.show()

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
