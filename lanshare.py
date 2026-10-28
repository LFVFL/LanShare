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
from pathlib import Path

from PyQt5.QtCore import QSettings, QTimer
from PyQt5.QtWidgets import QApplication, QMessageBox

from server import APP_VERSION
from server.config import *                       # noqa: F401,F403
from server.pages import webassets                # noqa: F401
from server.pages import ASSET_VERSION
from server.storage import start_zip_cleanup_thread
from server.handler import FileShareHandler        # noqa: F401  外部脚本会用到
from server.runner import FileSharer               # noqa: F401

from ui.icon import build_app_icon, set_app_icon
from ui.widgets import ClickableLabel             # noqa: F401
from ui.main_window import FileShareGUI
from ui.single_instance import SingleInstance, validate_test_profile


def watch_test_exit(window, profile):
    """A validated test profile can ask its own Qt process to stop gracefully."""
    if profile is None:
        return None
    stop_file = Path(profile) / 'stop.request'
    timer = QTimer(window)

    def check_request():
        if stop_file.is_file():
            timer.stop()
            window.quit_application()

    timer.timeout.connect(check_request)
    timer.start(100)
    window._test_exit_timer = timer
    return timer


def main():
    parser = argparse.ArgumentParser(description='局域网云盘共享工具')
    parser.add_argument('path', nargs='?', help='要共享的文件夹路径')
    parser.add_argument('-p', '--port', type=int, help='服务端口')
    parser.add_argument('--startup', action='store_true', help='开机自启模式')
    parser.add_argument('--open-here', metavar='DIRECTORY', help='启动共享或切换已运行实例的目录')
    parser.add_argument('--test-profile', metavar='ABS_PATH', help='隔离的离屏测试目录（禁用系统集成）')
    args = parser.parse_args()
    if args.open_here is not None and args.path is not None:
        parser.error('path 与 --open-here 不能同时使用')
    try:
        profile = validate_test_profile(args.test_profile)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if profile is not None:
        profile.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    app.setApplicationName("局域网云盘")
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("FileShareTool")

    instance = SingleInstance(app, profile=profile)
    try:
        primary = instance.acquire()
        if not primary:
            target = args.open_here if args.open_here is not None else args.path
            request = ({'action': 'open_here', 'path': str(Path(target).resolve())}
                       if target else {'action': 'status' if args.startup else 'activate'})
            response = instance.request(request)
            if not response['ok']:
                raise OSError(response.get('error', '已有实例未接受命令'))
            return 0
    except (OSError, ValueError) as exc:
        instance.close()
        QMessageBox.warning(None, '无法打开云盘', str(exc))
        return 1

    # The per-user owner is acquired before any chat loading, migration or cleanup.
    if webassets is None:
        msg = ("缺少 webassets.py。\n\n"
               "打包时必须让 webassets.py 和 lanshare.py 在同一个目录：\n"
               "  1) 先运行 python tools/build_assets.py\n"
               "  2) 打包工具里把这两个文件一起带上\n\n"
               "（源码运行时也一样，缺文件无法渲染网页。）")
        print(msg)
        try:
            QMessageBox.critical(None, "缺少资源文件", msg)
        except Exception:
            pass
        instance.close()
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

    icon = build_app_icon()
    set_app_icon(icon)
    if icon and not icon.isNull():
        app.setWindowIcon(icon)

    window = FileShareGUI(
        initial_path=args.open_here if args.open_here is not None else args.path,
        initial_port=args.port,
        auto_start=args.startup or args.open_here is not None,
        settings=(QSettings(str(profile / 'settings.ini'), QSettings.IniFormat)
                  if profile is not None else None),
        system_integration_enabled=profile is None
    )
    instance.set_handler(window.handle_instance_request)
    watch_test_exit(window, profile)

    if args.startup:
        window.hide()
    else:
        window.show()

    try:
        return app.exec_()
    finally:
        instance.close()


if __name__ == '__main__':
    sys.exit(main())
