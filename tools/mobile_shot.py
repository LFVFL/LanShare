# -*- coding: utf-8 -*-
"""用手机尺寸（390×844）打开 Web 端：截图 + 检查横向溢出 / 多选条。

    python tools\\mobile_shot.py

需要本机有 Chrome；脚本自己起服务、自己收拾临时目录。
输出两张图到 截图\\：手机端-文件页.png、手机端-多选条.png。
"""
import base64
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import lanshare as j  # noqa: E402
from ui_test import CDP, page_ws, wait_port  # noqa: E402

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT, CDP_PORT = 6461, 9461
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")

PROBE = """(function(){
  var d=document.documentElement, b=document.body, over=[];
  document.querySelectorAll('*').forEach(function(el){
    var r=el.getBoundingClientRect();
    if (r.width>0 && r.right > d.clientWidth + 1) {
      over.push((el.className||el.tagName)+' right='+Math.round(r.right)
                +' text='+(el.textContent||'').trim().slice(0,14));
    }
  });
  return {
    vw: d.clientWidth, scrollW: d.scrollWidth, overflow: over.slice(0,8),
    rows: document.querySelectorAll('.list .row').length,
    dl: (function(){var e=document.querySelector('.row-dl'); if(!e) return null;
        var r=e.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)];})()
  };
})()"""

SELBAR = """(function(){
  var cb = document.querySelector('.list .row .cbx');
  if (cb) cb.click();
  var bar = document.querySelector('.selbar');
  if (!bar) return {ok:false};
  bar.setAttribute('data-open','true');
  var vw = document.documentElement.clientWidth;
  var btns = [].slice.call(bar.querySelectorAll('button')).map(function(b){
    var r=b.getBoundingClientRect();
    return {t:(b.textContent||'').trim().slice(0,6),
            l:Math.round(r.left), r:Math.round(r.right)};
  });
  return {vw: vw, barW: Math.round(bar.getBoundingClientRect().width),
          scrollW: bar.scrollWidth, btns: btns};
})()"""


def main():
    share = Path(tempfile.mkdtemp(dir=str(ROOT), prefix="lan6-m-"))
    (share / "docs").mkdir()
    (share / "docs" / "报告.md").write_text("hi", encoding="utf-8")
    (share / "照片.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (share / "一个很长的文件名用来测试省略号是不是正常工作.txt").write_text("x", encoding="utf-8")

    sharer = j.FileSharer()
    ok, result = sharer.start(path=str(share), port=PORT)
    if not ok:
        print("服务起不来:", result)
        shutil.rmtree(share, ignore_errors=True)
        return 1

    chrome = subprocess.Popen([
        CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
        "--remote-debugging-port=%d" % CDP_PORT,
        "--user-data-dir=%s" % r"C:\Windows\TEMP\lan6\m-profile", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    bad = 0
    try:
        wait_port(CDP_PORT, 25)
        cdp = CDP(page_ws(CDP_PORT))
        cdp.call("Page.enable")
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True})
        cdp.call("Emulation.setUserAgentOverride", {"userAgent": UA})
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d/" % PORT})
        time.sleep(1.6)

        info = cdp.js(PROBE)
        print("视口 %s / 内容 %s / 行数 %s / 下载按钮 %s"
              % (info["vw"], info["scrollW"], info["rows"], info["dl"]))
        if info["scrollW"] > info["vw"]:
            print("  [FAIL] 有横向滚动：", info["overflow"])
            bad += 1
        else:
            print("  [OK]   没有横向溢出")
        shot = cdp.call("Page.captureScreenshot", {"format": "png"})
        (ROOT / "截图" / "手机端-文件页.png").write_bytes(base64.b64decode(shot["data"]))

        sel = cdp.js(SELBAR)
        overflow = [b for b in sel["btns"] if b["r"] > sel["vw"]
                    or b["l"] < 0]
        print("多选条 %s/%s 个按钮，最右 %s"
              % (sel["scrollW"], sel["barW"], max(b["r"] for b in sel["btns"])))
        if overflow:
            print("  [FAIL] 按钮超出屏幕：", overflow)
            bad += 1
        else:
            print("  [OK]   按钮都排得下")
        shot = cdp.call("Page.captureScreenshot", {"format": "png"})
        (ROOT / "截图" / "手机端-多选条.png").write_bytes(base64.b64decode(shot["data"]))
    finally:
        chrome.terminate()
        sharer.stop()
        shutil.rmtree(share, ignore_errors=True)

    print("截图已更新：截图/手机端-文件页.png、截图/手机端-多选条.png")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
