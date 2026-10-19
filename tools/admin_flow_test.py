# -*- coding: utf-8 -*-
"""管理密码全流程回归：设了管理密码之后，上传 / 删聊天记录到底能不能用。

这一轮修的两个 bug 都在这条链上：
  1. 上传：`ensureAdmin(... fileInput.click())` 把打开选择框放进了 Promise 回调，
     浏览器此时已经不算"用户激活"，`input.click()` 被静默忽略 → 点了没反应。
  2. 删消息：走的是裸 `apiFetch`，401 只换来一句"删除失败"，不会弹密码框。
另外手机端根本没有管理员入口（侧栏在 ≤900px 是 display:none）。

    python tools\\admin_flow_test.py
"""
import json
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import lanshare as j  # noqa: E402
from ui_test import CDP, page_ws, wait_port  # noqa: E402

PORT, CDP_PORT = 6477, 9477
ADMIN_PWD = "adm123"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PASSED, FAILED = [], []


def api(port, method, path, body=None):
    """自己发 JSON 请求（ui_test.http_json 只收已编码的字节）。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 data=data, headers=headers)
    req.method = method
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def check(label, ok, detail=""):
    (PASSED if ok else FAILED).append(label if ok else (label, detail))
    print("  %-46s %s%s" % (label, "ok" if ok else "FAIL", "" if ok else " " + str(detail)))


def main():
    import subprocess

    share = Path(tempfile.mkdtemp(dir=str(ROOT), prefix="lan6-adm-"))
    (share / "docs").mkdir()
    (share / "docs" / "说明.txt").write_text("hello", encoding="utf-8")
    (ROOT / "lan6-adm-upload.txt").write_text("uploaded by test", encoding="utf-8")

    sharer = j.FileSharer()
    ok, result = sharer.start(path=str(share), port=PORT,
                              password=None, admin_password=ADMIN_PWD)
    if not ok:
        print("服务起不来:", result)
        return 1

    room = api(PORT, "POST", "/chat/create", {"name": "管理员测试室"}) or {}
    room_id = room.get("room_id") or "default"
    # 先发一条消息（发消息不需要管理密码），待会儿删它来验证鉴权
    api(PORT, "POST", "/chat/send", {"room": room_id, "text": "待删除的消息"})

    chrome = subprocess.Popen([
        CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
        "--remote-debugging-port=%d" % CDP_PORT,
        "--user-data-dir=%s" % r"C:\Windows\TEMP\lan6\adm-profile",
        "http://127.0.0.1:%d/" % PORT,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        wait_port(CDP_PORT, 25)
        cdp = CDP(page_ws(CDP_PORT))
        cdp.call("Page.enable")
        cdp.call("DOM.enable")
        time.sleep(1.2)

        print("\n[1] 管理员入口：设了密码就要看得见")
        state = cdp.js("""(function(){
          var b=document.querySelector('.sidebar [data-admin-open]');
          return {needs: Lan.api.ctx.needsAdmin, admin: Lan.api.ctx.isAdmin,
                  hidden: b ? b.hidden : null,
                  text: b ? b.textContent.trim() : null};
        })()""")
        check("/is_admin 说需要管理密码", state["needs"] is True, state)
        check("此时还不是管理员", state["admin"] is False, state)
        check("侧栏入口可见", state["hidden"] is False, state)
        check("入口文字是「管理员权限」", "管理员权限" in (state["text"] or ""), state)

        print("\n[2] 上传：点按钮要立刻弹出选择框（不能被异步鉴权挡住）")
        # 拦一下 input.click，记录"点击发生时有没有挂起的网络请求"
        cdp.js("""(function(){
          window.__pick = {calls: 0, pendingVerify: null, t: 0};
          var proto = HTMLInputElement.prototype;
          var orig = proto.click;
          proto.click = function () {
            window.__pick.calls++;
            window.__pick.t = Date.now();
            window.__pick.pendingVerify = window.__verifyCount || 0;
          };
        })()""")
        cdp.js("""(function(){
          window.__verifyCount = 0;
          if (!window.__patched) {
            window.__patched = true;
            var of = window.fetch;
            window.fetch = function (u) {
              if (String(u).indexOf('/verify_admin') >= 0) window.__verifyCount++;
              return of.apply(this, arguments);
            };
          }
          document.querySelector('[data-upload-pick]').click();
          var item = document.querySelector('.menu [data-key="file"]');
          if (item) item.click();
        })()""")
        picked = cdp.js("window.__pick")
        check("管理菜单点了「上传文件」就打开了选择框",
              picked and picked["calls"] == 1, picked)
        check("打开选择框时没有先走 /verify_admin",
              picked and picked["pendingVerify"] == 0, picked)

        print("\n[3] 选完文件后才要管理密码，输对就上传成功")
        root = cdp.call("DOM.getDocument", {"depth": 1})
        node = cdp.call("DOM.querySelector", {
            "nodeId": root["root"]["nodeId"], "selector": "#fileInput"})
        cdp.call("DOM.setFileInputFiles", {
            "nodeId": node["nodeId"],
            "files": [str(ROOT / "lan6-adm-upload.txt")]})
        modal = cdp.wait_js(
            "!!document.querySelector('.scrim .modal')", True, 8)
        title = cdp.js("(document.querySelector('.modal-title')||{}).textContent")
        check("弹出管理密码框", bool(modal), modal)
        check("弹窗标题正确", "管理密码" in (title or ""), title)

        cdp.js("""(function(){
          var i = document.querySelector('.modal input');
          i.value = %s;
          i.dispatchEvent(new Event('input', {bubbles:true}));
          document.querySelectorAll('[data-modal-ok]').length && [].slice.call(document.querySelectorAll('[data-modal-ok]')).pop().click();
        })()""" % json.dumps(ADMIN_PWD))
        cdp.wait_js("!document.querySelector('.scrim')", True, 10)

        uploaded = False
        for _ in range(30):
            listing = api(PORT, "GET", "/api/list?path=") or {}
            names = [e.get("name") for e in (listing.get("entries") or [])]
            if "lan6-adm-upload.txt" in names:
                uploaded = True
                break
            time.sleep(0.3)
        check("文件真的传上去了", uploaded, "没在目录里看到")

        print("\n[4] 清空聊天记录：换一个没解锁的会话，必须弹密码框")
        # 上一步的上传已经把当前浏览器会话解锁成管理员了，
        # 这里清掉 Cookie 重来，才能验证 401 → 弹框 → 重试这条路径
        cdp.call("Network.clearBrowserCookies")
        cdp.call("Page.navigate", {
            "url": "http://127.0.0.1:%d/chat/room?id=%s" % (PORT, room_id)})
        time.sleep(1.5)
        before = cdp.js("document.querySelectorAll('.msg').length")
        check("聊天室里有消息", (before or 0) >= 1, before)
        check("清空后仍未解锁", cdp.js("Lan.api.ctx.isAdmin") is False,
              cdp.js("Lan.api.ctx.isAdmin"))

        # 清空聊天记录需要管理员：走 writeFetch，应当弹密码框
        clicked = cdp.js("""(function(){
          var b = document.querySelector('[data-chat-clear]');
          if (!b) return 'no-button';
          b.click();
          var ok = document.querySelector('[data-modal-ok]');
          if (!ok) return 'no-modal';
          ok.click();
          return 'clicked';
        })()""")
        check("清空按钮存在", clicked != "no-button", clicked)
        asked = cdp.wait_js("!!document.querySelector('.scrim .modal input')", True, 8)
        check("401 后弹出了管理密码框", bool(asked), asked)
        cdp.js("""(function(){
          var i = document.querySelector('.modal input');
          i.value = %s;
          i.dispatchEvent(new Event('input', {bubbles:true}));
          document.querySelectorAll('[data-modal-ok]').length && [].slice.call(document.querySelectorAll('[data-modal-ok]')).pop().click();
        })()""" % json.dumps(ADMIN_PWD))
        time.sleep(1.0)
        after = cdp.js("document.querySelectorAll('.msg').length")
        check("消息被清空了", (after or 0) == 0, after)

        print("\n[5] 输错密码要明确说「不对」，不能静默失败")
        api(PORT, "POST", "/chat/send", {"room": room_id, "text": "再来一条"})
        cdp.call("Network.clearBrowserCookies")
        cdp.call("Page.navigate", {
            "url": "http://127.0.0.1:%d/chat/room?id=%s" % (PORT, room_id)})
        time.sleep(1.5)
        cdp.js("""(function(){
          var b = document.querySelector('[data-chat-clear]');
          if (b) b.click();
          var ok = document.querySelector('[data-modal-ok]');
          if (ok) ok.click();
        })()""")
        cdp.wait_js("!!document.querySelector('.scrim .modal input')", True, 8)
        cdp.js("""(function(){
          var i = document.querySelector('.modal input');
          i.value = 'wrong-password';
          i.dispatchEvent(new Event('input', {bubbles:true}));
          document.querySelectorAll('[data-modal-ok]').length && [].slice.call(document.querySelectorAll('[data-modal-ok]')).pop().click();
        })()""")
        time.sleep(1.0)
        toast_text = cdp.js("""(function(){
          return [].map.call(document.querySelectorAll('.toast'), function(t){
            return t.textContent.trim();
          }).join(' | ');
        })()""")
        check("提示了密码不对", "不对" in (toast_text or "") or "密码" in (toast_text or ""),
              toast_text)
        still = cdp.js("document.querySelectorAll('.msg').length")
        check("密码错时没有误删", (still or 0) >= 1, still)

        print("\n[6] 手机端也要有管理员入口")
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True})
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d/" % PORT})
        time.sleep(1.5)
        mob = cdp.js("""(function(){
          var b = document.querySelector('.mobile-bar [data-admin-open]');
          if (!b) return {exists:false};
          var r = b.getBoundingClientRect();
          return {exists:true, hidden:b.hidden, w:Math.round(r.width),
                  h:Math.round(r.height), right:Math.round(r.right),
                  vw: document.documentElement.clientWidth,
                  needs: Lan.api.ctx.needsAdmin};
        })()""")
        check("顶栏有管理员按钮", mob.get("exists") is True, mob)
        check("手机端不是隐藏状态", mob.get("hidden") is False, mob)
        check("按钮可点（有尺寸且不出屏）",
              (mob.get("w") or 0) >= 28 and (mob.get("right") or 0) <= (mob.get("vw") or 0),
              mob)

        cdp.js("""(function(){
          document.querySelector('.mobile-bar [data-admin-open]').click();
        })()""")
        mob_modal = cdp.wait_js("!!document.querySelector('.scrim .modal input')", True, 6)
        check("手机上点它能弹出密码框", bool(mob_modal), mob_modal)

        print("\n[7] 没设管理密码时入口应当收起")
        cdp.call("Emulation.clearDeviceMetricsOverride")
        # 另一个服务实例：只设访问密码，不设管理密码
        plain = j.FileSharer()
        ok2, _ = plain.start(path=str(share), port=PORT + 1, password=None,
                             admin_password=None)
        if not ok2:
            check("起第二个实例（不设管理密码）", False, "端口被占？")
        else:
            try:
                cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d/" % (PORT + 1)})
                time.sleep(1.4)
                state = cdp.js("""(function(){
                  var side=document.querySelector('.sidebar [data-admin-open]');
                  var mob=document.querySelector('.mobile-bar [data-admin-open]');
                  return {needs: Lan.api.ctx.needsAdmin,
                          side: side ? side.hidden : null,
                          mob: mob ? mob.hidden : null};
                })()""")
                check("needs_admin=false", state.get("needs") is False, state)
                check("侧栏入口隐藏", state.get("side") is True, state)
                check("手机端入口隐藏", state.get("mob") is True, state)
            finally:
                plain.stop()

        print("\n[8] 访问密码 + 管理密码同时开（最常见的部署方式）")
        both = j.FileSharer()
        ok3, _ = both.start(path=str(share), port=PORT + 2, password="pw1",
                            admin_password="adm1")
        if not ok3:
            check("起第三个实例", False, "端口被占？")
        else:
            try:
                cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d/" % (PORT + 2)})
                time.sleep(1.2)
                check("先被拦到登录页",
                      cdp.js("location.pathname") == "/login",
                      cdp.js("location.pathname"))
                cdp.js("""(function(){
                  document.getElementById('pwd').value = 'pw1';
                  document.querySelector('[data-login-form]').submit();
                })()""")
                time.sleep(1.5)
                check("登录后回到首页",
                      cdp.js("location.pathname") == "/", cdp.js("location.pathname"))
                check("仍然需要管理密码",
                      cdp.js("Lan.api.ctx.needsAdmin") is True,
                      cdp.js("Lan.api.ctx.needsAdmin"))
                check("此时还不是管理员",
                      cdp.js("Lan.api.ctx.isAdmin") is False,
                      cdp.js("Lan.api.ctx.isAdmin"))
                (ROOT / "lan6-both-upload.txt").write_text("both", encoding="utf-8")
                root2 = cdp.call("DOM.getDocument", {"depth": 1})
                node2 = cdp.call("DOM.querySelector", {
                    "nodeId": root2["root"]["nodeId"], "selector": "#fileInput"})
                cdp.call("DOM.setFileInputFiles", {
                    "nodeId": node2["nodeId"],
                    "files": [str(ROOT / "lan6-both-upload.txt")]})
                asked2 = cdp.wait_js("!!document.querySelector('.scrim .modal input')",
                                     True, 8)
                check("上传会要管理密码", bool(asked2), asked2)
                cdp.js("""(function(){
                  var i = document.querySelector('.modal input');
                  i.value = 'adm1';
                  i.dispatchEvent(new Event('input', {bubbles:true}));
                  [].slice.call(document.querySelectorAll('[data-modal-ok]')).pop().click();
                })()""")
                landed = False
                for _ in range(25):
                    if (share / "lan6-both-upload.txt").exists():
                        landed = True
                        break
                    time.sleep(0.3)
                check("两个密码都在时上传成功", landed, "文件没落地")
                (ROOT / "lan6-both-upload.txt").unlink(missing_ok=True)
            finally:
                both.stop()
    finally:
        chrome.terminate()
        sharer.stop()
        shutil.rmtree(share, ignore_errors=True)
        (ROOT / "lan6-adm-upload.txt").unlink(missing_ok=True)

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
