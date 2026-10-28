"""协议层测试：HTTP/1.1 长连接 + gzip + Content-Length 不变式。

为什么单独一个文件：这三个东西一旦出错，表现是「浏览器一直转圈」这种
很难从功能测试里看出来的症状。这里用裸连接一条条验证。

用法：python tools/protocol_test.py            # 默认 6225 端口
"""
import argparse
import gzip
import http.client
import json
import re
import shutil
import socket
import subprocess
import sys
try:                      # Windows 控制台默认 GBK，打印 ✓ 之类的字符会崩
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import tempfile
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
from test_support import isolate_test_environment

TEST_ENV = isolate_test_environment()
FAILS = []
ADMIN = "proto-admin"

BOOT = """
import sys, time
sys.path.insert(0, r'{root}')
import lanshare as j
s = j.FileSharer()
ok, res = s.start(path=r'{share}', port={port}, password=None, admin_password={adm!r})
if not ok:
    print("FAILED", res, flush=True)
    raise SystemExit(2)
print('READY' if ok else 'FAILED', flush=True)
while True:
    time.sleep(1)
"""


def wait(port, timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.4).close()
            return True
        except OSError:
            time.sleep(0.15)
    return False


def fetch(port, method, path, body=None, headers=None, conn=None):
    """返回 (状态, 头字典, 响应体字节, 用过的连接)。conn 传入就复用它（验证长连接）。"""
    own = conn is None
    if own:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": "127.0.0.1:%d" % port}
    h.update(headers or {})
    conn.request(method, path, body=body, headers=h)
    r = conn.getresponse()
    data = r.read()
    hdrs = {k.lower(): v for k, v in r.getheaders()}
    status = r.status
    if own:
        conn.close()
        return status, hdrs, data, None
    return status, hdrs, data, conn


def check(label, ok, detail=""):
    print("  %-40s %s %s" % (label, "ok" if ok else "FAIL", "" if ok else detail))
    if not ok:
        FAILS.append((label, detail))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=6225)
    args = ap.parse_args()
    port = args.port

    tmp = Path(TEST_ENV.temporary_dir(prefix="lan6-proto-"))
    share = tmp / "share"
    share.mkdir()
    (share / "keep.txt").write_text("keep", encoding="utf-8")
    (share / "big.txt").write_text("x" * 4096, encoding="utf-8")
    boot = tmp / "b.py"
    boot.write_text(BOOT.format(root=str(ROOT), share=str(share), port=port, adm=ADMIN),
                    encoding="utf-8")
    child = subprocess.Popen([sys.executable, str(boot)], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, cwd=str(ROOT))
    q = lambda **kw: urllib.parse.urlencode(kw)
    try:
        if not TEST_ENV.wait_server(child, port, share):
            print("服务没起来：")
            child.kill()
            print(child.stdout.read().decode("utf-8", "replace"))
            return 2

        # 先拿管理员，后面的写接口才好过
        st, hd, _, _ = fetch(port, "POST", "/verify_admin",
                             json.dumps({"password": ADMIN}),
                             {"Content-Type": "application/json"})
        cookie = (hd.get("set-cookie") or "").split(";")[0]
        auth = {"Cookie": cookie} if cookie else {}

        print("\n[1] 每个响应都要有 Content-Length（304 除外）")
        cases = [
            ("GET", "/", None, auth),
            ("GET", "/dir?" + q(path=str(share)), None, auth),
            ("GET", "/chat", None, auth),
            ("GET", "/chat/room?id=default", None, auth),
            ("GET", "/login", None, {}),
            ("GET", "/favicon.ico", None, {}),
            ("GET", "/static/app.css", None, {}),
            ("GET", "/static/app.js", None, {}),
            ("GET", "/api/list", None, auth),
            ("GET", "/is_admin", None, auth),
            ("GET", "/download?" + q(file="keep.txt", path=str(share)), None, auth),
            ("GET", "/download?" + q(file="missing.txt", path=str(share)), None, auth),
            ("GET", "/check_exists?" + q(file="keep.txt", path=str(share)), None, auth),
            ("GET", "/zip_download?id=nope", None, auth),
            ("GET", "/upload/unique?" + q(name="keep.txt", path=str(share)), None, auth),
            ("GET", "/nope", None, {}),
            ("GET", "/delete?" + q(file="keep.txt", path=str(share)), None, auth),
            ("POST", "/mkdir?" + q(name="d1", path=str(share)), None, auth),
            ("POST", "/chat/send", json.dumps({"text": "hi"}),
             dict(auth, **{"Content-Type": "application/json"})),
            ("POST", "/zip", "path=%s&files[]=keep.txt" % urllib.parse.quote(str(share)),
             dict(auth, **{"Content-Type": "application/x-www-form-urlencoded"})),
        ]
        for method, path, body, hdr in cases:
            st, hd, data, _ = fetch(port, method, path, body, hdr)
            has = "content-length" in hd
            check("%s %s -> %s" % (method, path.split("?")[0][:26], st), has,
                  "缺 Content-Length")

        print("\n[2] Range 越界要回 416 且带长度")
        st, hd, _, _ = fetch(port, "GET", "/download?" + q(file="keep.txt", path=str(share)),
                             headers=dict(auth, **{"Range": "bytes=99999-"}))
        check("416 且 Content-Length: 0",
              st == 416 and hd.get("content-length") == "0",
              "%s %r" % (st, hd.get("content-length")))

        print("\n[3] 长连接复用（同一 TCP 连接连发 4 个请求）")
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        seq = ["/", "/static/app.css", "/api/list", "/is_admin"]
        ok_all = True
        for p in seq:
            st, hd, _, conn = fetch(port, "GET", p, headers=auth, conn=conn)
            if st != 200:
                ok_all = False
                print("     %s -> %s" % (p, st))
        check("4 个请求都 200", ok_all)
        check("连接没被服务端提前关掉", conn.sock is not None)
        conn.close()

        print("\n[4] gzip：大文本要压、解压后与明文一致")
        st1, h1, plain, _ = fetch(port, "GET", "/static/app.js")
        st2, h2, gz, _ = fetch(port, "GET", "/static/app.js",
                               headers={"Accept-Encoding": "gzip"})
        check("app.js 压了", h2.get("content-encoding") == "gzip", str(h2.get("content-encoding")))
        check("解压后与明文一致", gzip.decompress(gz) == plain)
        check("压缩确实变小了", len(gz) < len(plain),
              "%d vs %d" % (len(gz), len(plain)))
        check("带 Vary: Accept-Encoding", "accept-encoding" in (h2.get("vary") or "").lower())
        check("Content-Length 是压缩后的长度", h2.get("content-length") == str(len(gz)))

        st3, h3, html_gz, _ = fetch(port, "GET", "/", headers={"Accept-Encoding": "gzip"})
        check("首页也压了", h3.get("content-encoding") == "gzip")
        st4, h4, js_plain, _ = fetch(port, "GET", "/api/list", headers=auth)
        check("小 JSON 不压", h4.get("content-encoding") is None)

        print("\n[4b] brotli（服务端装了才生效，没装就退回 gzip/不压）")
        st_br, h_br, body_br, _ = fetch(port, "GET", "/static/app.js",
                                        headers={"Accept-Encoding": "br"})
        supports_br = h_br.get("content-encoding") == "br"
        print("     服务端 brotli:", "支持" if supports_br else "未安装")
        if supports_br:
            try:
                import brotli as _br
                check("br 解压后与明文一致", _br.decompress(body_br) == plain)
                check("br 比 gzip 更小", len(body_br) < len(gz),
                      "br=%d gzip=%d" % (len(body_br), len(gz)))
                check("br 带 Vary", "accept-encoding" in (h_br.get("vary") or "").lower())
                check("Content-Length 是 br 长度",
                      h_br.get("content-length") == str(len(body_br)))
            except ImportError:
                print("     （测试进程没有 brotli，跳过解压校验）")
        else:
            check("只给 br 时不压（没有该算法）",
                  h_br.get("content-encoding") is None, str(h_br.get("content-encoding")))
        # 同时接受 br 和 gzip 时要挑更省的那个
        st_mix, h_mix, body_mix, _ = fetch(
            port, "GET", "/static/app.js", headers={"Accept-Encoding": "gzip, br"})
        check("同时接受时优先 br" if supports_br else "同时接受时用 gzip",
              h_mix.get("content-encoding") == ("br" if supports_br else "gzip"),
              str(h_mix.get("content-encoding")))

        print("\n[5] ETag / 304 带 Vary，且不带 Content-Encoding")
        etag = h1.get("etag")
        st5, h5, body5, _ = fetch(port, "GET", "/static/app.js",
                                  headers={"If-None-Match": etag or "'x'"})
        check("命中 ETag 回 304", st5 == 304, str(st5))
        check("304 带 Vary", "accept-encoding" in (h5.get("vary") or "").lower())

        print("\n[6] 静态资源长缓存：带对版本号才 immutable")
        st_page, h_page, page_body, _ = fetch(port, "GET", "/", headers=auth)
        m = re.search(rb'/static/app\.css\?v=([0-9a-f]+)', page_body)
        check("页面里 CSS 带版本号", bool(m), page_body[:200])
        version = m.group(1).decode() if m else "x"
        st_v, h_v, _, _ = fetch(port, "GET", "/static/app.css?v=" + version)
        check("带对 v → immutable",
              "immutable" in (h_v.get("cache-control") or ""), h_v.get("cache-control"))
        check("带对 v → 一年 max-age",
              "max-age=31536000" in (h_v.get("cache-control") or ""), h_v.get("cache-control"))
        st_n, h_n, _, _ = fetch(port, "GET", "/static/app.css")
        check("不带 v → no-cache",
              (h_n.get("cache-control") or "") == "no-cache", h_n.get("cache-control"))
        st_w, h_w, _, _ = fetch(port, "GET", "/static/app.css?v=deadbeef")
        check("v 对不上 → no-cache",
              (h_w.get("cache-control") or "") == "no-cache", h_w.get("cache-control"))

        print("\n[7] 跨站写入仍被拒")
        st6, _, _, _ = fetch(port, "POST", "/delete?" + q(file="keep.txt", path=str(share)),
                             headers=dict(auth, **{"Origin": "http://evil.example",
                                                   "Sec-Fetch-Site": "cross-site"}))
        check("跨站 403", st6 == 403, str(st6))
        check("文件还在", (share / "keep.txt").exists())
    finally:
        child.terminate()
        try:
            child.wait(5)
        except subprocess.TimeoutExpired:
            child.kill()
        TEST_ENV.remove_test_dir(tmp)

    print("\n结果:", "全部通过 ✓" if not FAILS else "失败 %d 项" % len(FAILS))
    for f in FAILS:
        print("  -", f)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
