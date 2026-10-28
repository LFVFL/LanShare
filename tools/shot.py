# -*- coding: utf-8 -*-
"""用 CDP 给本地 HTML 文件截图（Chrome headless 的 --screenshot 在中文路径下不出图）。

用法：
    python tools\\shot.py 样稿\\桌面端.html 截图\\桌面端-样稿.png [宽] [高]
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from test_support import isolate_test_environment

TEST_ENV = isolate_test_environment()
sys.path.insert(0, os.path.join(ROOT, "tools"))
from ui_test import CDP, wait_port  # noqa: E402

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_chrome():
    for p in CHROME_CANDIDATES:
        if os.path.isfile(p):
            return p
    raise SystemExit("找不到 chrome.exe")


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    src = os.path.abspath(os.path.join(ROOT, sys.argv[1]))
    dst = os.path.abspath(os.path.join(ROOT, sys.argv[2]))
    width = int(sys.argv[3]) if len(sys.argv) > 3 else 1040
    height = int(sys.argv[4]) if len(sys.argv) > 4 else 720
    port = 9388
    profile = str(TEST_ENV.root / "chrome-shot-profile")
    proc = subprocess.Popen([
        find_chrome(), "--headless=new", "--disable-gpu", "--no-sandbox",
        "--hide-scrollbars", "--remote-debugging-port=0",
        "--window-size=%d,%d" % (width, height),
        "--user-data-dir=" + profile, "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        port = TEST_ENV.wait_browser(proc, profile)
        url = "file:///" + src.replace("\\", "/")
        req = urllib.request.Request(
            "http://127.0.0.1:%d/json/new?about:blank" % port, method="PUT")
        info = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        cdp = CDP(info["webSocketDebuggerUrl"])
        cdp.call("Page.enable")
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height,
            "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": url})
        time.sleep(1.4)
        r = cdp.call("Page.captureScreenshot", {"format": "png"})
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(base64.b64decode(r["data"]))
        print("已保存 %s  %.1f KB" % (dst, os.path.getsize(dst) / 1024.0))
    finally:
        TEST_ENV.stop_process(proc)


if __name__ == "__main__":
    main()
