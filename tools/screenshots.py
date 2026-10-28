# -*- coding: utf-8 -*-
"""生成 README 用的界面截图（全部用假数据，不暴露本机网卡与真实目录）。

    python tools\\screenshots.py

输出到 docs/images/：
    web-files-light.png / web-files-dark.png / web-chat.png / mobile-files.png
    desktop-light.png / desktop-dark.png
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
from test_support import isolate_test_environment

TEST_ENV = isolate_test_environment()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import lanshare as j  # noqa: E402
from ui_test import CDP, page_ws, wait_port  # noqa: E402

OUT = ROOT / "docs" / "images"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT, CDP_PORT = 6501, 9501

DEMO_FILES = [
    ("文档", None), ("照片", None), ("代码", None),
    ("季度报告.pdf", 320_000), ("会议纪要.docx", 48_000), ("产品演示.mp4", 8_600_000),
    ("素材包.zip", 42_000_000), ("说明.txt", 1_200), ("数据统计.xlsx", 96_000),
]


def build_demo_dir():
    demo = Path(TEST_ENV.temporary_dir(prefix="lan6-shot-"))
    for name, size in DEMO_FILES:
        target = demo / name
        if size is None:
            target.mkdir()
            (target / "占位.txt").write_text("demo", encoding="utf-8")
        else:
            target.write_bytes(b"\0" * size)
    return demo


def post(path, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path), data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def shot(cdp, name):
    raw = cdp.call("Page.captureScreenshot", {"format": "png"})
    path = OUT / name
    path.write_bytes(base64.b64decode(raw["data"]))
    print("  %-24s %.0f KB" % (name, path.stat().st_size / 1024))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    demo = build_demo_dir()

    sharer = j.FileSharer()
    ok, _ = sharer.start(path=str(demo), port=PORT)
    if not ok:
        print("服务起不来")
        return 1

    # 房间和消息要在服务起来之后造
    room = post("/chat/create", {"name": "运维值班"})
    room_id = room.get("room_id") or "default"
    for who, text in (("小林", "打印机又卡纸了，谁去楼下看一下？"),
                      ("阿May", "我顺路，十分钟到"),
                      ("小林", "文件我先传到共享里了：季度报告.pdf")):
        post("/chat/send", {"room": room_id, "text": text, "sender": who})

    chrome_profile = str(TEST_ENV.root / "chrome-shot-profile")
    chrome = subprocess.Popen([
        CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
        "--force-device-scale-factor=1", "--remote-debugging-port=0",
        "--user-data-dir=%s" % chrome_profile,
        "http://127.0.0.1:%d/" % PORT,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        CDP_PORT = TEST_ENV.wait_browser(chrome, chrome_profile)
        cdp = CDP(page_ws(CDP_PORT))
        cdp.call("Page.enable")
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": 1280, "height": 800, "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d/" % PORT})
        time.sleep(1.8)
        # 默认就是翠绿 + 浅色
        cdp.js("localStorage.setItem('lanshare.palette','mint');"
               "localStorage.setItem('lanshare.theme','light');")
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d/" % PORT})
        time.sleep(1.6)
        shot(cdp, "web-files-light.png")

        print("深色")
        cdp.js("localStorage.setItem('lanshare.theme','dark')")
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d/" % PORT})
        time.sleep(1.6)
        shot(cdp, "web-files-dark.png")

        print("聊天室（深色）")
        cdp.call("Page.navigate", {
            "url": "http://127.0.0.1:%d/chat/room?id=%s" % (PORT, room_id)})
        time.sleep(2.0)
        # 消息的 sender 就是客户端 IP，同一台机器发的都算"我"。
        # 截图时把前两条改成一个假的对端 IP，画面才像正常的一问一答。
        cdp.js("""(function(){
          var st = Lan.chat.state, msgs = st.msgs;
          if (msgs[0]) msgs[0].sender = '192.168.1.11';
          if (msgs[1]) msgs[1].sender = '192.168.1.22';
          Lan.chat.render();
        })()""")
        time.sleep(0.5)
        shot(cdp, "web-chat.png")

        print("手机端")
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True})
        cdp.call("Emulation.setUserAgentOverride", {
            "userAgent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
                         "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
                         "Mobile/15E148 Safari/604.1"})
        cdp.js("localStorage.setItem('lanshare.theme','light')")
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d/" % PORT})
        time.sleep(1.8)
        shot(cdp, "mobile-files.png")
        cdp.call("Emulation.clearDeviceMetricsOverride")
    finally:
        TEST_ENV.stop_process(chrome)
        sharer.stop(wait=True, timeout=10)

    # 桌面端：把网卡地址换成一串假的，避免截图里出现本机内网 IP
    from server import netinfo

    netinfo.local_ips = lambda: ["192.168.1.100", "192.168.56.1", "198.18.0.1"]
    # 注意：不能用 offscreen——离屏平台没有字体，截出来全是空白方块。
    # 这里只 grab() 不进事件循环，窗口不会真的弹出来。
    os.environ.pop("QT_QPA_PLATFORM", None)
    from PyQt5.QtWidgets import QApplication

    from ui import style
    from ui.main_window import FileShareGUI

    app = QApplication([])
    gui = TEST_ENV.gui(auto_start=False)
    # 这台机器上的 QSettings 里可能还存着别的配色，截图固定用默认的青翠 + 大字号
    gui.settings.remove("theme/palette")
    gui.settings.remove("theme/size")
    gui.palette = style.DEFAULT_PALETTE
    gui.size = style.DEFAULT_SIZE
    gui.apply_modern_style()
    gui.port_entry.setValue(5995)
    gui.password_entry.setText("lan2026")
    gui.admin_password_entry.setText("admin2026")
    ok, result = gui.sharer.start(path=str(demo), port=5995,
                                  password="lan2026", admin_password="admin2026")
    if ok:
        gui.on_start_completed(True, result)
        gui.path_entry.setText(r"D:\共享\公司资料")
        # 启动时服务会写两条"聊天记录保存位置"的系统日志，里面是本机真实路径；
        # 丢掉它们，换成假的，再补几条访问日志
        try:
            while True:
                gui.log_queue.get_nowait()
        except Exception:
            pass
        for ts, ip, msg, status in (
                ("14:22:00", "系统", r"聊天记录保存位置：D:\共享\chat_data\rooms.json", None),
                ("14:22:00", "系统", r"聊天图片保存位置：D:\共享\chat_data\images", None),
                ("14:22:01", "192.168.1.11", "访问首页", 200),
                ("14:22:02", "192.168.1.11", "浏览目录 | 目录: 文档", 200),
                ("14:22:09", "192.168.1.22", "上传分片 | 文件: 季度报告.pdf | 位置: 2.0 MB", 200),
                ("14:22:12", "192.168.1.11", "下载文件 | 文件: 季度报告.pdf", 200),
                ("14:22:18", "192.168.1.22", "删除文件 | 文件: 旧版.docx", 403),
                ("14:22:20", "192.168.1.99", "/upload", 500)):
            gui.log_queue.put((ts, ip, msg, status))
        gui.update_log()
        gui.hint_label.setText(gui._hint_default)
    print("桌面端（浅色）")
    gui.grab().save(str(OUT / "desktop-light.png"))
    print("  desktop-light.png       %.0f KB" % ((OUT / "desktop-light.png").stat().st_size / 1024))
    gui.set_theme(mode="dark")
    gui.grab().save(str(OUT / "desktop-dark.png"))
    print("  desktop-dark.png        %.0f KB" % ((OUT / "desktop-dark.png").stat().st_size / 1024))
    gui.set_theme(mode="light")
    gui.stop_share()
    gui.sharer.wait_stopped(10)
    app.processEvents()
    app.quit()

    TEST_ENV.remove_test_dir(demo)
    print("完成，输出在", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
