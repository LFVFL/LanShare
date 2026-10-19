"""前端行为测试：真浏览器 + CDP，不需要装 Playwright/Selenium。

为什么要有这个：HTTP 层的测试全绿，但「点一次发送出现两条消息」这种问题
只有真的跑一遍前端才发现得了。这里用 Chrome 自带的调试协议驱动：

    Chrome(headless, --remote-debugging-port) ← WebSocket → Runtime.evaluate

覆盖：
  1. 聊天室点一次发送 → 只多一条消息（这一条就是那个 bug 的回归）
  2. 顶部「N 条消息」跟着更新
  3. 聊天页用 SSE，不再轮询 /chat/history
  4. 文件页搜索框回车 → 出结果条

用法：python tools/ui_test.py            # 默认服务 6320、CDP 9223
测试会自己建一个房间，跑完删掉，不动 default 房间。
"""
import argparse
import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
try:                      # Windows 控制台默认 GBK，打印 ✓ 之类的字符会崩
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
FAILS = []

BOOT = """
import sys, time
sys.path.insert(0, r'{root}')
import lanshare as j
s = j.FileSharer()
ok, res = s.start(path=r'{share}', port={port}, password=None, admin_password=None)
print('READY' if ok else 'FAILED', flush=True)
while True:
    time.sleep(1)
"""


# ── 极简 WebSocket 客户端（只需要文本帧）────────────────────────────────
class WS:
    def __init__(self, url, timeout=15):
        u = urllib.parse.urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + (("?" + u.query) if u.query else "")
        req = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
               "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n"
               % (path, u.hostname, u.port, key))
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise EOFError("websocket handshake closed")
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise RuntimeError("handshake failed: %r" % head.split(b"\r\n")[0])
        self.buf = rest

    def _exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError("websocket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text):
        data = text.encode("utf-8")
        header = bytearray([0x81])
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        while True:
            b1, b2 = self._exact(2)
            opcode = b1 & 0x0F
            masked = b2 & 0x80
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._exact(8))[0]
            mask = self._exact(4) if masked else None
            payload = self._exact(length) if length else b""
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x1:
                return payload.decode("utf-8", "replace")
            if opcode == 0x8:
                raise EOFError("closed by peer")
            # ping/0xA/continuation（我们只发小消息，不会分片）都忽略

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class CDP:
    def __init__(self, ws_url):
        self.ws = WS(ws_url)
        self.next_id = 0
        self.events = []

    def call(self, method, params=None, timeout=20):
        self.next_id += 1
        mid = self.next_id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("%s -> %s" % (method, msg["error"]))
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)
        raise TimeoutError(method)

    def js(self, expr):
        r = self.call("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": True,
        })
        res = r.get("result", {})
        if r.get("exceptionDetails"):
            raise RuntimeError("JS 异常: %s" % (r["exceptionDetails"],))
        return res.get("value")

    def wait_js(self, expr, expect=True, timeout=12):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if bool(self.js(expr)) == expect:
                    return True
            except Exception:
                pass
            time.sleep(0.15)
        return False

    def urls(self):
        out = []
        for ev in self.events:
            if ev.get("method") == "Network.requestWillBeSent":
                out.append(ev["params"]["request"]["url"])
        return out


def check(label, ok, detail=""):
    print("  %-44s %s %s" % (label, "ok" if ok else "FAIL", "" if ok else detail))
    if not ok:
        FAILS.append((label, detail))


def wait_port(port, timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.4).close()
            return True
        except OSError:
            time.sleep(0.15)
    return False


def http_json(port, method, path, body=None):
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), data=body,
        headers={"Content-Type": "application/json"} if body else {})
    req.method = method
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def page_ws(port):
    """取第一个 page 目标的 websocket 地址。"""
    end = time.time() + 15
    while time.time() < end:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=3) as r:
                targets = json.loads(r.read().decode("utf-8"))
            for t in targets:
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.3)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=6320)
    ap.add_argument("--cdp", type=int, default=9223)
    args = ap.parse_args()
    port, cdp_port = args.port, args.cdp

    tmp = Path(tempfile.mkdtemp(dir=str(ROOT), prefix="lan6-ui-"))
    share = tmp / "share"
    (share / "docs").mkdir(parents=True)
    (share / "keep.txt").write_text("keep", encoding="utf-8")
    (share / "docs" / "report.md").write_text("hello", encoding="utf-8")
    boot = tmp / "b.py"
    boot.write_text(BOOT.format(root=str(ROOT), share=str(share), port=port), encoding="utf-8")
    server = subprocess.Popen([sys.executable, str(boot)], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, cwd=str(ROOT))
    chrome = None
    cdp = None
    room_id = None
    try:
        if not wait_port(port):
            print("服务没起来：")
            server.kill()
            print(server.stdout.read().decode("utf-8", "replace"))
            return 2

        # 建一个专用房间，别往 default 里灌测试消息
        room = http_json(port, "POST", "/chat/create",
                         json.dumps({"name": "ui-test"}).encode())
        room_id = room.get("room_id")
        check("建测试房间", bool(room_id), room)

        userdir = tmp / "chrome"
        chrome = subprocess.Popen([
            CHROME, "--headless", "--disable-gpu", "--no-sandbox", "--no-first-run",
            "--disable-extensions", "--mute-audio",
            "--remote-debugging-port=%d" % cdp_port,
            "--user-data-dir=%s" % userdir,
            "http://127.0.0.1:%d/chat/room?id=%s" % (port, room_id),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        ws_url = page_ws(cdp_port)
        if not ws_url:
            print("连不上 Chrome 调试端口")
            return 2
        cdp = CDP(ws_url)
        cdp.call("Runtime.enable")
        cdp.call("Network.enable")
        cdp.call("Page.enable")
        # Chrome 启动时就加载了一次页面，那时还没开 Network 监听；
        # 这里重载一次，才能抓到首屏的请求（尤其是 /chat/stream）
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d/chat/room?id=%s"
                                   % (port, room_id)})

        print("\n[1] 聊天室：点一次发送只多一条")
        check("页面就绪", cdp.wait_js(
            "document.readyState === 'complete' && !!document.querySelector('[data-chat-input]')"))
        check("初始 0 条", cdp.js("document.querySelectorAll('.msg').length") == 0)

        cdp.js("""(function(){
            var i = document.querySelector('[data-chat-input]');
            i.value = 'UI-TEST-1';
            i.dispatchEvent(new Event('input', {bubbles: true}));
            return true;
        })()""")
        check("发送按钮已启用",
              cdp.js("!document.querySelector('[data-chat-send]').disabled"))
        cdp.js("document.querySelector('[data-chat-send]').click()")
        check("1.2 秒内出现 1 条",
              cdp.wait_js("document.querySelectorAll('.msg').length === 1"))
        time.sleep(1.2)
        count = cdp.js("document.querySelectorAll('.msg').length")
        check("仍是 1 条（没有重复）", count == 1, "实际 %s 条" % count)

        print("\n[2] 顶部计数跟着更新")
        meta = cdp.js("document.querySelector('[data-room-meta]').textContent")
        check("显示「1 条消息」", "1 条消息" in (meta or ""), meta)

        print("\n[2b] 聊天记录搜索（纯前端过滤）")
        cdp.js("""(function(){
            var i = document.querySelector('[data-chat-input]');
            i.value = 'UI-TEST-2';
            i.dispatchEvent(new Event('input', {bubbles: true}));
            document.querySelector('[data-chat-send]').click();
            return true;
        })()""")
        check("变成 2 条", cdp.wait_js("document.querySelectorAll('.msg').length === 2"))
        cdp.js("""(function(){
            var s = document.querySelector('[data-chat-search]');
            s.value = 'UI-TEST-1';
            s.dispatchEvent(new Event('input', {bubbles: true}));
            return true;
        })()""")
        check("过滤后只剩 1 条", cdp.wait_js("document.querySelectorAll('.msg').length === 1"))
        meta2 = cdp.js("document.querySelector('[data-room-meta]').textContent")
        check("顶部显示「找到 1 条」", "找到 1 条" in (meta2 or ""), meta2)
        cdp.js("""(function(){
            var s = document.querySelector('[data-chat-search]');
            s.value = '';
            s.dispatchEvent(new Event('input', {bubbles: true}));
            return true;
        })()""")
        check("清空搜索恢复 2 条", cdp.wait_js("document.querySelectorAll('.msg').length === 2"))

        print("\n[3] 用的是 SSE，不是轮询")
        urls = cdp.urls()
        streams = [u for u in urls if "/chat/stream" in u]
        polls = [u for u in urls if "/chat/history" in u and "after=" in u]
        check("开了 1 条 /chat/stream", len(streams) == 1, urls)
        check("没有轮询 /chat/history?after=", not polls, polls)

        print("\n[4] 文件页：搜索框回车出结果")
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d/" % port})
        check("文件页就绪", cdp.wait_js(
            "document.readyState === 'complete' && !!document.querySelector('[data-files-search]')"))
        cdp.js("""(function(){
            var i = document.querySelector('[data-files-search]');
            i.value = 'report';
            i.dispatchEvent(new Event('input', {bubbles: true}));
            i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
            return true;
        })()""")
        check("出现搜索结果条", cdp.wait_js("!!document.querySelector('.search-note')"))
        hits = cdp.js("document.querySelectorAll('a.row--hit').length")
        check("找到 1 个 report.md", hits == 1, hits)
        href = cdp.js("(document.querySelector('a.row--hit')||{}).getAttribute "
                      "&& document.querySelector('a.row--hit').getAttribute('href')")
        check("结果指向所在目录", "/dir?path=" in (href or ""), href)

        print("\n[5] 行内下载按钮")
        # 上一节是搜索结果页（整行是链接，没有行内按钮），这里重新进一次文件页
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d/" % port})
        check("回到文件列表", cdp.wait_js(
            "document.readyState === 'complete' && document.querySelectorAll('.row[data-kind]').length > 0"))
        n_files = cdp.js("document.querySelectorAll('.row[data-kind]:not([data-kind=\"folder\"])').length")
        n_links = cdp.js("document.querySelectorAll('a.row-dl').length")
        check("每个文件行一个下载按钮", n_links == n_files, "%s 文件 / %s 按钮" % (n_files, n_links))
        href = cdp.js("(document.querySelector('a.row-dl')||{}).href || ''")
        check("href 指向 /download", "/download?file=" in (href or ""), href)
        check("带 download 属性（不跳页）",
              cdp.js("!!(document.querySelector('a.row-dl')||{}).hasAttribute "
                     "&& document.querySelector('a.row-dl').hasAttribute('download')"))
        n_folder_ph = cdp.js("document.querySelectorAll('.row[data-kind=\"folder\"] .row-dl--empty').length")
        n_folder = cdp.js("document.querySelectorAll('.row[data-kind=\"folder\"]').length")
        check("文件夹行是占位（不可点）", n_folder_ph == n_folder,
              "%s 文件夹 / %s 占位" % (n_folder, n_folder_ph))
        check("表头也留了同样宽度", cdp.js(
            "getComputedStyle(document.querySelector('.col-more')).width") == "76px",
            cdp.js("getComputedStyle(document.querySelector('.col-more')).width"))
        # 真的按这个 href 取一次，确认能下到内容
        href = cdp.js("(document.querySelector('a.row-dl')||{}).href || ''")
        try:
            with urllib.request.urlopen(href, timeout=5) as r:
                body = r.read()
            check("按按钮的链接能拿到文件", r.status == 200 and body == b"keep",
                  "%s %r" % (r.status, body[:20]))
        except Exception as e:
            check("按按钮的链接能拿到文件", False, repr(e))

        print("\n[6] 窄屏（390px）不出现横向滚动")
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True})
        time.sleep(0.4)
        overflow = cdp.js("document.documentElement.scrollWidth - window.innerWidth")
        check("没有横向溢出", (overflow or 0) <= 1, "溢出 %s px" % overflow)
        check("下载按钮仍在", cdp.js("document.querySelectorAll('a.row-dl').length") > 0)
        cdp.call("Emulation.clearDeviceMetricsOverride")

        print("\n[7] 对外命名空间 window.Lan")
        check("Lan 存在", cdp.js("typeof window.Lan === 'object'"))
        check("有 files/api/chat/util/upload/appearance", cdp.js(
            "['files','api','chat','util','upload','appearance'].every(function(k){"
            "return window.Lan[k] && typeof window.Lan[k] === 'object';})"))
        check("Lan.util.esc 可用", cdp.js("window.Lan.util.esc('<b>')") == "&lt;b&gt;")
        check("Lan.chat.state 是对象", cdp.js("typeof window.Lan.chat.state === 'object'"))
        check("Lan.files.render 是函数", cdp.js("typeof window.Lan.files.render === 'function'"))

        print("\n[8] 没有前端报错")
        errors = [e for e in cdp.events
                  if e.get("method") == "Runtime.exceptionThrown"]
        check("无 JS 异常", not errors, str(errors[:1])[:200])
    finally:
        if cdp:
            try:
                if room_id:
                    http_json(port, "POST", "/chat/delete_room",
                              json.dumps({"id": room_id}).encode())
            except Exception as e:
                print("删除测试房间失败:", e)
            cdp.ws.close()
        if chrome:
            chrome.terminate()
        server.terminate()
        try:
            server.wait(5)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n结果:", "全部通过 ✓" if not FAILS else "失败 %d 项" % len(FAILS))
    for f in FAILS:
        print("  -", f)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
