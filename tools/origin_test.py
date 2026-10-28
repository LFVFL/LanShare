# -*- coding: utf-8 -*-
"""同源校验回归：模拟"只发 Origin、不发 Sec-Fetch-Site"的老浏览器 / 内置 WebView。

背景：`AuthMixin._hostname` 曾经漏写 `@staticmethod`，`self._hostname(x)` 变成
两个位置参数 → POST 直接 500。现代浏览器带 Sec-Fetch-Site 时走不到那段代码，
所以桌面端一直没暴露；手机上的老内核只发 Origin，一提交表单就是 500。

    python tools\\origin_test.py
"""
import http.cookiejar
import json
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
from test_support import isolate_test_environment

TEST_ENV = isolate_test_environment()
sys.path.insert(0, str(ROOT))

import lanshare as j  # noqa: E402

PORT = 6456
BASE = "http://127.0.0.1:%d" % PORT
PASSED, FAILED = [], []

IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")


def check(label, ok, detail=""):
    (PASSED if ok else FAILED).append(label if ok else (label, detail))
    print("  %-46s %s%s" % (label, "ok" if ok else "FAIL", "" if ok else " " + str(detail)))


def main():
    share = Path(TEST_ENV.temporary_dir(prefix="lan6-origin-"))
    (share / "a.txt").write_text("hi", encoding="utf-8")
    sharer = j.FileSharer()
    ok, result = sharer.start(path=str(share), port=PORT,
                              password="pw123", admin_password=None)
    if not ok:
        print("服务起不来:", result)
        return 1

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(path, data=None, headers=None, form=False):
        hdrs = {"User-Agent": IPHONE_UA}
        body = None
        if data is not None:
            if form:
                body = urllib.parse.urlencode(data).encode()
                hdrs["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                body = json.dumps(data).encode()
                hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        req = urllib.request.Request(BASE + path, data=body, headers=hdrs,
                                     method="POST" if body else "GET")
        try:
            resp = opener.open(req, timeout=10)
            return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    try:
        print("\n[1] 老内核：只有 Origin，没有 Sec-Fetch-Site")
        status, body = call("/login", {"password": "pw123"},
                            {"Origin": BASE}, form=True)
        check("登录不再 500", status != 500, status)
        check("登录成功（200 或 302）", status in (200, 302), status)

        status, _ = call("/is_admin", None, {"Origin": BASE})
        check("同源 GET 正常", status == 200, status)

        status, body = call("/chat/create", {"name": "手机测试"},
                            {"Origin": BASE})
        check("建聊天室不是 500", status != 500, status)
        check("建聊天室成功", status == 200 and b'"success": true' in body,
              body[:80])
        room = None
        try:
            payload = json.loads(body.decode())
        except ValueError:
            payload = {}
        if isinstance(payload, dict):
            room = payload.get("id") or payload.get("room_id")
        if not room:
            status, body = call("/chat/rooms", None, {"Origin": BASE})
            data = json.loads(body.decode())
            rooms = data.get("rooms") if isinstance(data, dict) else data
            room = (rooms or [{}])[0].get("id")
        check("拿到聊天室 id", bool(room), room)

        status, body = call("/chat/send", {"room": room, "text": "手机上发的"},
                            {"Origin": BASE})
        check("发消息不是 500", status != 500, status)
        check("发消息成功", status == 200 and b'"success": true' in body, body[:120])

        print("\n[2] 跨站/同源判定仍然有效")
        status, body = call("/chat/send", {"room": room, "text": "x"},
                            {"Origin": "http://evil.example"})
        check("跨站 POST 被拒（403）", status == 403, status)
        status, _ = call("/chat/send", {"room": room, "text": "x"},
                         {"Origin": BASE, "Sec-Fetch-Site": "cross-site"})
        check("Sec-Fetch-Site: cross-site 被拒", status == 403, status)
        status, body = call("/chat/send", {"room": room, "text": "再发一条"},
                            {"Origin": BASE, "Sec-Fetch-Site": "same-origin"})
        check("现代浏览器同源照常", status == 200 and b'"success": true' in body, body[:60])

        print("\n[3] 其他来源形式")
        status, _ = call("/chat/send", {"room": room, "text": "referer"},
                         {"Referer": BASE + "/chat/room?id=" + room})
        check("只有 Referer 也放行", status == 200, status)
        status, _ = call("/chat/send", {"room": room, "text": "no-origin"})
        check("没有任何来源头（curl）照常放行", status == 200, status)
        status, _ = call("/chat/send", {"room": room, "text": "bad origin"},
                         {"Origin": "null"})
        check("Origin: null 按缺省处理", status == 200, status)

        print("\n[4] _hostname 本身")
        from server.auth import AuthMixin

        host = AuthMixin._hostname
        check("去端口", host("192.168.1.100:5995") == "192.168.1.100", host("192.168.1.100:5995"))
        check("去 userinfo", host("a@b:80") == "b", host("a@b:80"))
        check("IPv6 保留方括号", host("[::1]:5995") == "[::1]", host("[::1]:5995"))
        check("大小写归一", host("MyHost:80") == "myhost", host("MyHost:80"))
        check("空值不炸", host(None) == "", host(None))
        check("可以直接静态调用", AuthMixin._hostname("h:1") == "h")
    finally:
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
