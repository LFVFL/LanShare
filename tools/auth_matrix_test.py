"""鉴权矩阵测试：起一个「访问密码 + 管理密码」都设了的服务，逐条核对拒绝形态。

用法：
    python tools/auth_matrix_test.py            # 默认 6220 端口
    python tools/auth_matrix_test.py --port 6221

为什么不并进 smoke_test.py：那边是「无密码」的常态，这边专门验 302/403/401 三种拒绝，
分成两个文件跑一次就能覆盖两种部署形态。
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

ROOT = Path(__file__).resolve().parent.parent
PW, ADMIN = "pw123", "admin123"
FAILS = []

BOOT = """
import sys, time
sys.path.insert(0, r'{root}')
import lanshare as j
s = j.FileSharer()
ok, res = s.start(path=r'{share}', port={port}, password={pw!r}, admin_password={adm!r})
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


def call(port, method, path, body=None, headers=None, cookie=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": "127.0.0.1:%d" % port}
    if cookie:
        h["Cookie"] = cookie
    h.update(headers or {})
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    r.read()
    setc = r.getheader("Set-Cookie") or ""
    c.close()
    return r.status, setc


def check(label, got, want):
    ok = got == want
    print("  %-30s %-4s %s" % (label, got, "ok" if ok else "期望 %s" % want))
    if not ok:
        FAILS.append((label, got, want))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=6220)
    args = ap.parse_args()
    port = args.port

    tmp = Path(tempfile.mkdtemp(dir=str(ROOT), prefix="lan6-auth-"))
    share = tmp / "share"
    share.mkdir()
    (share / "a.txt").write_text("x", encoding="utf-8")
    boot = tmp / "b.py"
    boot.write_text(BOOT.format(root=str(ROOT), share=str(share), port=port,
                                pw=PW, adm=ADMIN), encoding="utf-8")
    child = subprocess.Popen([sys.executable, str(boot)], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, cwd=str(ROOT))
    q = lambda **kw: urllib.parse.urlencode(kw)
    try:
        if not wait(port):
            print("服务没起来：")
            child.kill()
            print(child.stdout.read().decode("utf-8", "replace"))
            return 2

        print("\n[未登录]")
        check("GET /            → 302", call(port, "GET", "/")[0], 302)
        check("GET /dir         → 302", call(port, "GET", "/dir?path=")[0], 302)
        check("GET /chat        → 302", call(port, "GET", "/chat")[0], 302)
        check("GET /chat/room   → 302", call(port, "GET", "/chat/room?id=default")[0], 302)
        check("GET /chat/rooms  → 403", call(port, "GET", "/chat/rooms")[0], 403)
        check("GET /chat/history→ 403", call(port, "GET", "/chat/history?room=default")[0], 403)
        check("GET /api/list    → 403", call(port, "GET", "/api/list")[0], 403)
        check("GET /download    → 302", call(port, "GET", "/download?file=a.txt")[0], 302)
        check("GET /is_admin    → 302", call(port, "GET", "/is_admin")[0], 302)
        check("GET /upload/uniq → 401", call(port, "GET", "/upload/unique?name=a.txt")[0], 401)
        check("POST /upload     → 403", call(port, "POST", "/upload?path=", b"x")[0], 403)
        check("POST /delete     → 403", call(port, "POST", "/delete?file=a.txt")[0], 403)
        check("POST /chat/send  → 403",
              call(port, "POST", "/chat/send", json.dumps({"text": "x"}),
                   {"Content-Type": "application/json"})[0], 403)
        check("POST /zip        → 403",
              call(port, "POST", "/zip", "path=&files[]=a.txt",
                   {"Content-Type": "application/x-www-form-urlencoded"})[0], 403)
        check("GET /static/app.css→200", call(port, "GET", "/static/app.css")[0], 200)
        check("GET /favicon.ico  →200", call(port, "GET", "/favicon.ico")[0], 200)
        check("GET /nope         →404", call(port, "GET", "/nope")[0], 404)
        check("GET /login        →200", call(port, "GET", "/login")[0], 200)

        print("\n[登录后、非管理员]")
        st, setc = call(port, "POST", "/login", "password=" + urllib.parse.quote(PW),
                        {"Content-Type": "application/x-www-form-urlencoded"})
        check("POST /login      → 302", st, 302)
        cookie = setc.split(";")[0]
        check("GET /            → 200", call(port, "GET", "/", cookie=cookie)[0], 200)
        check("GET /api/list    → 200", call(port, "GET", "/api/list", cookie=cookie)[0], 200)
        check("GET /chat        → 200", call(port, "GET", "/chat", cookie=cookie)[0], 200)
        check("GET /is_admin    → 200", call(port, "GET", "/is_admin", cookie=cookie)[0], 200)
        check("GET /upload/uniq → 401",
              call(port, "GET", "/upload/unique?name=a.txt", cookie=cookie)[0], 401)
        check("POST /upload     → 401", call(port, "POST", "/upload?path=", b"x", cookie=cookie)[0], 401)
        check("POST /mkdir      → 401", call(port, "POST", "/mkdir?name=d", cookie=cookie)[0], 401)
        check("GET /delete      → 405", call(port, "GET", "/delete?file=a.txt", cookie=cookie)[0], 405)

        print("\n[管理员]")
        st, _ = call(port, "POST", "/verify_admin", json.dumps({"password": ADMIN}),
                     {"Content-Type": "application/json"}, cookie=cookie)
        check("POST /verify_admin→200", st, 200)
        check("GET /upload/uniq → 200",
              call(port, "GET", "/upload/unique?name=a.txt", cookie=cookie)[0], 200)
        check("POST /mkdir      → 200", call(port, "POST", "/mkdir?name=d1", cookie=cookie)[0], 200)
        check("POST /rename     → 200",
              call(port, "POST", "/rename?old=a.txt&new=b.txt", cookie=cookie)[0], 200)

        print("\n[跨站]")
        check("POST /delete 跨站 → 403",
              call(port, "POST", "/delete?file=b.txt", cookie=cookie,
                   headers={"Origin": "http://evil.example",
                            "Sec-Fetch-Site": "cross-site"})[0], 403)
        check("文件仍在", (share / "b.txt").exists(), True)
    finally:
        child.terminate()
        try:
            child.wait(5)
        except subprocess.TimeoutExpired:
            child.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n结果:", "全部通过 ✓" if not FAILS else "失败 %d 项" % len(FAILS))
    for f in FAILS:
        print("  -", f)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
