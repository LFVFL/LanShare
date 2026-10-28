"""冒烟测试：自带一个临时服务，跑完自动关闭。

用法：
    python tools/smoke_test.py            # 默认 6199 端口
    python tools/smoke_test.py --port 6201

只依赖标准库；不碰你的真实共享目录（自建临时目录）。
"""
import argparse
import http.client
import json
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

ROOT = Path(__file__).resolve().parent.parent      # 第六版/
from test_support import isolate_test_environment

TEST_ENV = isolate_test_environment()
ADMIN_PW = "smoke-admin"
COOKIE = {}
FAILS = []

BOOT = """
import sys, time
sys.path.insert(0, r'{root}')
import lanshare as j
s = j.FileSharer()
ok, res = s.start(path=r'{share}', port={port}, password=None, admin_password={pw!r})
if not ok:
    print("FAILED", res, flush=True)
    raise SystemExit(2)
print('READY' if ok else 'FAILED: %s' % (res,), flush=True)
while True:
    time.sleep(1)
"""


def wait_port(port, timeout=25.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return True
        except OSError:
            time.sleep(0.15)
    return False


def req(port, method, path, body=None, headers=None, expect=200, tag=""):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    h = {"Host": "127.0.0.1:%d" % port}
    if COOKIE:
        h["Cookie"] = "; ".join("%s=%s" % kv for kv in COOKIE.items())
    if headers:
        h.update(headers)
    try:
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        data = r.read()
        for k, v in r.getheaders():
            if k.lower() == "set-cookie":
                name, _, rest = v.partition("=")
                COOKIE[name] = rest.split(";")[0]
    except Exception as e:                                   # 连接被断开也要算失败
        conn.close()
        FAILS.append((tag or path, "异常 %r" % (e,)))
        print("  FAIL %-32s %s" % (tag or path, e))
        return 0, {}
    finally:
        conn.close()

    ok = r.status == expect
    print("  %-4s %-32s %-4s %s" % (method, tag or path.split("?")[0], r.status,
                                    "ok" if ok else "期望 %s" % expect))
    if not ok:
        FAILS.append((tag or path, r.status, data[:160]))
    try:
        return r.status, json.loads(data.decode("utf-8"))
    except Exception:
        return r.status, data


def jget(obj, key, default=None):
    """响应可能不是 JSON（比如 500 错误页），取值时别炸。"""
    return obj.get(key, default) if isinstance(obj, dict) else default


def run(port, share):
    d = str(share)
    q = lambda **kw: urllib.parse.urlencode(kw)
    print("\n[1] 页面与静态资源")
    req(port, "GET", "/", tag="/")
    req(port, "GET", "/dir?" + urllib.parse.urlencode({"path": d}), tag="/dir")
    req(port, "GET", "/chat", tag="/chat")
    req(port, "GET", "/chat/room?id=default", tag="/chat/room")
    req(port, "GET", "/static/app.css", tag="app.css")
    req(port, "GET", "/static/app.js", tag="app.js")
    req(port, "GET", "/favicon.ico", tag="favicon")
    st, css = req(port, "GET", "/static/app.css", tag="app.css 第二次")
    etag = None
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/static/app.css", headers={"Host": "h"})
    r = conn.getresponse()
    r.read()
    etag = r.getheader("ETag")
    conn.close()
    if etag:
        req(port, "GET", "/static/app.css", headers={"If-None-Match": etag},
            expect=304, tag="app.css 304")
    else:
        FAILS.append(("etag", "缺少 ETag"))
        print("  FAIL 静态资源没有 ETag")

    print("\n[2] 目录清单")
    st, j = req(port, "GET", "/api/list", tag="/api/list")
    if str(Path(j.get("path") or "")).lower() != d.lower():
        FAILS.append(("api/list", j.get("path")))
        print("  FAIL list.path=%r 期望 %r" % (j.get("path"), d))

    st, j = req(port, "GET", "/api/search?" + q(q="keep", path=d), tag="/api/search")
    if jget(j, "count") != 1:
        FAILS.append(("api/search", j))
        print("  FAIL 共享内搜索没找到 keep.txt: %r" % (j,))

    print("\n[3] 管理员口令")
    st, j = req(port, "POST", "/verify_admin", body=json.dumps({"password": "wrong"}),
                headers={"Content-Type": "application/json"}, tag="错误口令")
    if jget(j, "success") is not False:
        FAILS.append(("错误口令", j))
        print("  FAIL 错误口令竟然通过了")
    req(port, "POST", "/verify_admin", body=json.dumps({"password": ADMIN_PW}),
        headers={"Content-Type": "application/json"}, tag="正确口令")

    print("\n[4] 上传 / 下载 / 删除")
    name, payload = "smoke.txt", b"hello-refactor"
    req(port, "POST", "/upload?" + q(offset=0, name=name, path=d,
                                     size=len(payload), mtime=1),
        body=payload, headers={"Content-Type": "application/octet-stream"},
        tag="分片上传")
    st, got = req(port, "GET", "/download?" + q(file=name, path=d), tag="下载")
    if got != payload:
        FAILS.append(("download", got))
        print("  FAIL 下载内容不符: %r" % (got,))
    st, j = req(port, "GET", "/check_exists?" + q(file=name, path=d), tag="存在性")
    if not j.get("exists"):
        FAILS.append(("check_exists", j))
        print("  FAIL check_exists 没看到刚上传的文件")
    st, j = req(port, "GET", "/upload/unique?" + q(name=name, path=d), tag="另存副本名")
    if jget(j, "name") == name or not jget(j, "name"):
        FAILS.append(("upload/unique", j))
        print("  FAIL 另存副本没有换名: %r" % (j,))
    req(port, "POST", "/delete?" + q(file=name, path=d), tag="删除")
    if (share / name).exists():
        FAILS.append(("delete", "文件还在"))
        print("  FAIL 删除后文件仍在磁盘上")

    print("\n[4b] 暂停后继续（只传一块、不调 done，偏移应保留）")
    pname = "paused.bin"
    part1, part2 = b"A" * 1024, b"B" * 512
    req(port, "POST", "/upload?" + q(offset=0, name=pname, path=d,
                                     size=len(part1) + len(part2), mtime=7),
        body=part1, headers={"Content-Type": "application/octet-stream"},
        tag="传第一块（暂停点）")
    st, j = req(port, "GET", "/check_exists?" + q(file=pname, path=d,
                                                 size=len(part1) + len(part2), mtime=7),
                tag="查续传偏移")
    if jget(j, "offset") != len(part1) or not jget(j, "resume"):
        FAILS.append(("resume state", j))
        print("  FAIL 偏移没保留: %r" % (j,))
    req(port, "POST", "/upload?" + q(offset=len(part1), name=pname, path=d,
                                     size=len(part1) + len(part2), mtime=7),
        body=part2, headers={"Content-Type": "application/octet-stream"},
        tag="接着传第二块")
    req(port, "POST", "/upload/done?" + q(name=pname, path=d, size=len(part1) + len(part2),
                                          mtime=7), tag="收尾")
    st, got = req(port, "GET", "/download?" + q(file=pname, path=d), tag="下载拼接结果")
    if got != part1 + part2:
        FAILS.append(("pause-resume", got))
        print("  FAIL 续传内容不对: %d 字节" % len(got or b""))
    req(port, "POST", "/delete?" + q(file=pname, path=d), tag="清理")

    print("\n[5] 聊天")
    req(port, "POST", "/chat/send",
        body=json.dumps({"room": "default", "text": "smoke-msg"}),
        headers={"Content-Type": "application/json"}, tag="发消息")
    st, j = req(port, "GET", "/chat/history?room=default", tag="历史")
    if "smoke-msg" not in json.dumps(j, ensure_ascii=False):
        FAILS.append(("chat/history", "没找到刚发的消息"))
        print("  FAIL 聊天历史里没有刚发的消息")
    req(port, "GET", "/chat/rooms", tag="房间列表")

    print("\n[6] 打包下载")
    st, j = req(port, "POST", "/zip",
                body=urllib.parse.urlencode({"path": d, "files[]": "keep.txt"}),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                tag="打包请求")
    url = jget(j, "download_url")
    if not url:
        FAILS.append(("zip", j))
        print("  FAIL 打包没返回下载地址: %r" % (j,))
    else:
        st, blob = req(port, "GET", url, tag="取 zip")
        if not (isinstance(blob, bytes) and blob[:2] == b"PK"):
            FAILS.append(("zip_download", blob[:40] if isinstance(blob, bytes) else blob))
            print("  FAIL 不是 zip 内容")

    print("\n[7] 写接口的防护")
    req(port, "GET", "/delete?" + q(file="keep.txt", path=d), expect=405, tag="GET 删除")
    if not (share / "keep.txt").exists():
        FAILS.append(("405", "keep.txt 被删了"))
    req(port, "POST", "/delete?" + q(file="keep.txt", path=d),
        headers={"Origin": "http://evil.example", "Sec-Fetch-Site": "cross-site"},
        expect=403, tag="跨站删除")
    if not (share / "keep.txt").exists():
        FAILS.append(("403", "跨站把 keep.txt 删了"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=6199)
    ap.add_argument("--keep", action="store_true", help="保留临时目录，方便排查")
    args = ap.parse_args()
    TEST_ENV.keep = args.keep

    tmp = Path(TEST_ENV.temporary_dir(prefix="lan6-smoke-"))
    share = tmp / "share"
    share.mkdir()
    (share / "keep.txt").write_text("keep", encoding="utf-8")

    boot = tmp / "_boot.py"
    boot.write_text(BOOT.format(root=str(ROOT), share=str(share),
                                port=args.port, pw=ADMIN_PW), encoding="utf-8")

    print("临时共享目录:", share)
    child = subprocess.Popen([sys.executable, str(boot)],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             cwd=str(ROOT))
    try:
        if not TEST_ENV.wait_server(child, args.port, share):
            print("服务没起来，输出如下：")
            child.kill()
            print(child.stdout.read().decode("utf-8", "replace"))
            return 2
        run(args.port, share)
    finally:
        child.terminate()
        try:
            child.wait(5)
        except subprocess.TimeoutExpired:
            child.kill()
        if not args.keep:
            TEST_ENV.remove_test_dir(tmp)

    print("\n结果:", "全部通过 ✓" if not FAILS else "失败 %d 项" % len(FAILS))
    for f in FAILS:
        print("  -", f)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
