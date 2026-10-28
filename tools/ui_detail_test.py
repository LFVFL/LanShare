# -*- coding: utf-8 -*-
"""界面细节回归：图标居中、表头圆角、手机排序、面包屑、删除缺参。

    python tools\\ui_detail_test.py
"""
import http.client
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
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

PORT, CDP_PORT = 6621, 9621
PASSED, FAILED = [], []


def check(label, ok, detail=""):
    (PASSED if ok else FAILED).append(label if ok else (label, detail))
    print("  %-40s %s%s" % (label, "ok" if ok else "FAIL", "" if ok else " " + str(detail)))


def main():
    share = Path(TEST_ENV.temporary_dir(prefix="lan6-uid-"))
    deep = share / "文档" / "子目录"
    deep.mkdir(parents=True)
    (deep / "x.txt").write_text("x", encoding="utf-8")
    for n, sz in (("a.txt", 100), ("b.pdf", 5000), ("c.zip", 90000)):
        (share / n).write_bytes(b"x" * sz)

    sharer = j.FileSharer()
    ok, _ = sharer.start(path=str(share), port=PORT)
    if not ok:
        print("服务起不来")
        return 1

    req = urllib.request.Request(
        "http://127.0.0.1:%d/chat/create" % PORT,
        data=json.dumps({"name": "细节"}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        room = json.loads(r.read().decode())["room_id"]

    chrome_profile = str(TEST_ENV.root / "chrome-uid-profile")
    chrome = subprocess.Popen([
        r"C:\Program Files\Google\Chrome\Application\chrome.exe", "--headless=new",
        "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
        "--remote-debugging-port=0",
        "--user-data-dir=%s" % chrome_profile,
        "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cdp = None
    try:
        CDP_PORT = TEST_ENV.wait_browser(chrome, chrome_profile)
        cdp = CDP(page_ws(CDP_PORT))
        cdp.call("Page.enable")

        print("\n[1] 图标注水后必须像素级居中（曾经偏上 2.2px）")
        for label, w, h, mobile in (("桌面", 1280, 800, False), ("手机", 390, 844, True)):
            cdp.call("Emulation.setDeviceMetricsOverride", {
                "width": w, "height": h, "deviceScaleFactor": 1, "mobile": mobile})
            cdp.call("Page.navigate", {
                "url": "http://127.0.0.1:%d/chat/room?id=%s" % (PORT, room)})
            time.sleep(2.2)
            d = json.loads(cdp.js("""(function(){
              var btn = document.querySelector('[data-chat-image]');
              var svg = btn && btn.querySelector('svg');
              var send = document.querySelector('.composer .send');
              function cy(el){ var r=el.getBoundingClientRect(); return r.y+r.height/2; }
              return JSON.stringify({
                svg: svg? cy(svg): null, send: send? cy(send): null,
                box: btn? getComputedStyle(btn.querySelector('.icon-box')).display : null
              });
            })()"""))
            check("%s 图标容器是 flex" % label, d["box"] in ("flex", "inline-flex"), d["box"])
            delta = abs(d["svg"] - d["send"]) if d["svg"] and d["send"] else 99
            check("%s 图标与发送按钮中线一致" % label, delta < 0.6, round(delta, 2))

        print("\n[2] 全站图标不得残留 data-icon、全部补齐 icon-box")
        for name, url in (("首页", "/"), ("聊天列表", "/chat"),
                          ("聊天室", "/chat/room?id=%s" % room)):
            cdp.call("Emulation.setDeviceMetricsOverride", {
                "width": 1280, "height": 800, "deviceScaleFactor": 1, "mobile": False})
            cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d%s" % (PORT, url)})
            time.sleep(1.8)
            d = json.loads(cdp.js("""(function(){
              var bad=0, n=0;
              document.querySelectorAll('.icon-box').forEach(function(box){
                var s=box.querySelector('svg'); if(!s) return;
                n++;
                var b=box.getBoundingClientRect(), r=s.getBoundingClientRect();
                if(b.height && (Math.abs((b.y+b.height/2)-(r.y+r.height/2))>0.6)) bad++;
              });
              return JSON.stringify({n:n, bad:bad,
                leftover:document.querySelectorAll('[data-icon]').length});
            })()"""))
            check("%s 图标无偏移" % name, d["bad"] == 0, d)
            check("%s 无残留 data-icon" % name, d["leftover"] == 0, d["leftover"])

        print("\n[3] 首页表头四角都是圆角（曾经上圆下方）")
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d/" % PORT})
        time.sleep(1.8)
        r = cdp.js("""(function(){
          var cs = getComputedStyle(document.querySelector('.list-head'));
          return [cs.borderTopLeftRadius, cs.borderTopRightRadius,
                  cs.borderBottomLeftRadius, cs.borderBottomRightRadius].join(',');
        })()""")
        corners = [x.strip() for x in (r or "").split(",")]
        check("四角圆角值一致", len(set(corners)) == 1 and corners[0] != "0px", corners)

        print("\n[4] 手机端有排序入口（曾经完全没有）")
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True})
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d/" % PORT})
        time.sleep(1.8)
        d = json.loads(cdp.js("""(function(){
          var bar = document.querySelector('[data-mobile-sort]');
          var btns = [].slice.call(document.querySelectorAll(
            '[data-mobile-sort] [data-sort]'));
          return JSON.stringify({
            display: bar? getComputedStyle(bar).display : 'none',
            count: btns.length,
            visible: btns.filter(function(b){return b.getBoundingClientRect().width>0;}).length
          });
        })()"""))
        check("排序条显示出来", d["display"] != "none", d["display"])
        check("三个排序按钮可见", d["count"] == 3 and d["visible"] == 3, d)

        print("\n[5] 手机排序真的能改顺序，且与表头状态同步")
        ORDER = ("[].slice.call(document.querySelectorAll('.list .row .row-name'))"
                 ".map(function(n){return n.textContent.trim();}).join(',')")
        asc = cdp.js(ORDER)
        cdp.js("document.querySelectorAll('[data-mobile-sort] [data-sort]')[1].click()")
        time.sleep(0.5)
        cdp.js("document.querySelectorAll('[data-mobile-sort] [data-sort]')[1].click()")
        time.sleep(0.5)
        desc = cdp.js(ORDER)
        # 按大小降序：文件从大到小，目录(size=0)排最后
        files = [x for x in (desc or "").split(",") if x in ("a.txt", "b.pdf", "c.zip")]
        check("按大小降序生效",
              asc != desc and files == ["c.zip", "b.pdf", "a.txt"], desc)
        sync = cdp.js("""(function(){
          var a=document.querySelector('.list-head [data-sort="size"]');
          var b=document.querySelector('[data-mobile-sort] [data-sort="size"]');
          return JSON.stringify({head:a.getAttribute('data-dir'),
            mobile:b.getAttribute('data-dir')});
        })()""")
        check("两端排序状态同步", json.loads(sync)["head"] == json.loads(sync)["mobile"], sync)

        print("\n[6] 面包屑有样式类且当前项不可点")
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": 1280, "height": 800, "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d/dir?path=%s"
                                        % (PORT, urllib.parse.quote(str(deep)))})
        time.sleep(1.8)
        d = json.loads(cdp.js("""(function(){
          var crumbs = document.querySelectorAll('.crumbs .crumb');
          var cur = document.querySelector('.crumbs .crumb--current');
          var first = document.querySelector('.crumbs a.crumb');
          return JSON.stringify({
            count: crumbs.length,
            hasCurrent: !!cur,
            currentIsLink: cur ? cur.tagName === 'A' : null,
            padding: first ? getComputedStyle(first).padding : null,
            seps: document.querySelectorAll('.crumbs .crumb-sep').length
          });
        })()"""))
        check("面包屑用了 .crumb 类", d["count"] >= 2, d)
        check("有当前项且不可点", d["hasCurrent"] and d["currentIsLink"] is False, d)
        check("有样式（padding 非 0）", d["padding"] not in (None, "0px"), d["padding"])
        check("分隔符有类名", d["seps"] >= 1, d["seps"])

        print("\n[7] POST /delete 缺 file 要明确报错，不能静默断连")
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=6)
        conn.request("POST", "/delete?path=" + urllib.parse.quote(str(share)),
                     body=b"", headers={"Connection": "close"})
        try:
            resp = conn.getresponse()
            st, body = resp.status, resp.read()
            check("返回 400 而不是断连", st == 400, st)
            check("响应体说明缺参数", b"file" in body, body[:60])
        except Exception as exc:
            check("返回 400 而不是断连", False, type(exc).__name__)
        finally:
            conn.close()
    finally:
        if cdp:
            try:
                cdp.call("Page.navigate", {"url": "about:blank"})
            except Exception:
                pass
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
