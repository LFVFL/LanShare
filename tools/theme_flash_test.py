# -*- coding: utf-8 -*-
"""深色模式下切换页面会不会闪白。

背景：`data-theme` 原先写死在模板里的 "light"，要等 app.js 在 DOMContentLoaded
里读完 localStorage 才改。于是每次进子目录都是「浅色先画一帧 → 再变深色」，
深色模式下就是刺眼的白闪。

修法是在 <head> 里加一段同步脚本，首屏之前就把 data-theme / data-palette /
data-glass 设好（见 web/pages/layout.html）。这个测试量的是"第一帧"。

    python tools\\theme_flash_test.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
from test_support import isolate_test_environment

TEST_ENV = isolate_test_environment()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import lanshare as j  # noqa: E402
from ui_test import CDP, page_ws, wait_port  # noqa: E402

PORT, CDP_PORT = 6491, 9491
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PASSED, FAILED = [], []

# 首帧探针：在文档开始执行时就装好，记录第一次 rAF 时的主题与背景色
PROBE = """
window.__first = null;
requestAnimationFrame(function () {
  var d = document.documentElement;
  var b = getComputedStyle(document.body || d).backgroundColor;
  window.__first = {theme: d.getAttribute('data-theme'), bg: b,
                    cs: getComputedStyle(d).colorScheme};
});
"""


def check(label, ok, detail=""):
    (PASSED if ok else FAILED).append(label if ok else (label, detail))
    print("  %-46s %s%s" % (label, "ok" if ok else "FAIL", "" if ok else " " + str(detail)))


def main():
    share = Path(TEST_ENV.temporary_dir(prefix="lan6-flash-"))
    (share / "docs").mkdir()
    (share / "docs" / "a.txt").write_text("hi", encoding="utf-8")
    (share / "b.txt").write_text("hi", encoding="utf-8")

    sharer = j.FileSharer()
    ok, result = sharer.start(path=str(share), port=PORT)
    if not ok:
        print("服务起不来:", result)
        return 1

    chrome_profile = str(TEST_ENV.root / "chrome-flash-profile")
    chrome = subprocess.Popen([
        CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
        "--remote-debugging-port=0",
        "--user-data-dir=%s" % chrome_profile,
        "http://127.0.0.1:%d/" % PORT,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        CDP_PORT = TEST_ENV.wait_browser(chrome, chrome_profile)
        cdp = CDP(page_ws(CDP_PORT))
        cdp.call("Page.enable")
        cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": PROBE})
        time.sleep(1.2)

        print("\n[1] 切到深色模式")
        cdp.js("""(function(){
          localStorage.setItem('lanshare.theme', 'dark');
          localStorage.setItem('lanshare.palette', 'indigo');
          localStorage.setItem('lanshare.glass', 'normal');
        })()""")
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d/" % PORT})
        time.sleep(1.2)
        check("页面是深色", cdp.js("document.documentElement.dataset.theme") == "dark",
              cdp.js("document.documentElement.dataset.theme"))

        print("\n[2] 服务端下发的 HTML：theme-boot.js 必须在样式表之前")
        # 这才是"深色不闪白"的机制保证：同步脚本在 <head> 里、且排在样式表之前。
        # 脚本内容单独成文件（为了能开严格 CSP），所以两处都要断言。
        import urllib.request

        with urllib.request.urlopen(
                "http://127.0.0.1:%d/static/theme-boot.js" % PORT, timeout=5) as resp:
            boot_js = resp.read().decode("utf-8")
            boot_status = resp.status
        check("theme-boot.js 能取到", boot_status == 200, boot_status)
        check("theme-boot.js 里读的是 lanshare.theme", "lanshare.theme" in boot_js, "")
        check("theme-boot.js 设置了三个属性",
              all(k in boot_js for k in ("data-theme", "data-palette", "data-glass")), "")

        for path in ("/", "/dir?path=docs", "/chat", "/login"):
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d%s" % (PORT, path), timeout=5) as resp:
                html = resp.read().decode("utf-8")
            has_script = "theme-boot.js" in html
            before_css = has_script and html.index("theme-boot.js") < html.index("app.css")
            label = path.split("?")[0]
            check("%s 引了 theme-boot.js" % label, has_script, "")
            check("%s 它排在样式表之前" % label, before_css, "")

        print("\n[3] 进子目录：页面主题和背景都必须是深色")
        for target in ("/dir?path=docs", "/?path=", "/chat"):
            cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d%s" % (PORT, target)})
            time.sleep(1.0)
            first = cdp.js("window.__first")
            label = target.split("?")[0]
            check("%s 主题是 dark" % label,
                  isinstance(first, dict) and first.get("theme") == "dark", first)
            bg = (first or {}).get("bg") or ""
            # 深色背景是深灰，不是纯白
            check("%s 背景不是白色" % label,
                  bg not in ("rgb(255, 255, 255)", "rgba(0, 0, 0, 0)"), bg)
            check("%s color-scheme 是 dark" % label,
                  "dark" in ((first or {}).get("cs") or ""), first)

        print("\n[4] 切回浅色也别把浅色画成深色")
        cdp.js("localStorage.setItem('lanshare.theme', 'light')")
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d/dir?path=docs" % PORT})
        time.sleep(1.0)
        first = cdp.js("window.__first")
        check("首帧主题是 light", (first or {}).get("theme") == "light", first)
    finally:
        TEST_ENV.stop_process(chrome)
        sharer.stop(wait=True, timeout=10)
        TEST_ENV.remove_test_dir(share)

    print("\n结果: 通过 %d 项" % len(PASSED))
    if FAILED:
        print("失败 %d 项" % len(FAILED))
        for item in FAILED:
            print("  -", item)
        return 1
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
