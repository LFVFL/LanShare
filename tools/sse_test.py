"""SSE 聊天推送测试：起一个临时服务，验证「发出来就能推过去」。

用法：python tools/sse_test.py            # 默认 6229 端口
"""
import argparse
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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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


def wait(port, timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.4).close()
            return True
        except OSError:
            time.sleep(0.15)
    return False


def http(port, method, path, body=b"", ctype="application/json", timeout=10):
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    head = ("%s %s HTTP/1.1\r\nHost: h\r\nConnection: close\r\n" % (method, path)).encode()
    if body:
        head += ("Content-Type: %s\r\nContent-Length: %d\r\n" % (ctype, len(body))).encode()
    s.sendall(head + b"\r\n" + body)
    data = b""
    while True:
        d = s.recv(8192)
        if not d:
            break
        data += d
    s.close()
    head_part, _, payload = data.partition(b"\r\n\r\n")
    status = int(head_part.split(b" ")[1]) if b" " in head_part else 0
    return status, head_part, payload


def send(port, text):
    return http(port, "POST", "/chat/send",
                json.dumps({"room": "default", "text": text}).encode())[0]


def check(label, ok, detail=""):
    print("  %-36s %s %s" % (label, "ok" if ok else "FAIL", "" if ok else detail))
    if not ok:
        FAILS.append((label, detail))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=6229)
    args = ap.parse_args()
    port = args.port

    tmp = Path(tempfile.mkdtemp(dir=str(ROOT), prefix="lan6-sse-"))
    share = tmp / "share"
    share.mkdir()
    boot = tmp / "b.py"
    boot.write_text(BOOT.format(root=str(ROOT), share=str(share), port=port),
                    encoding="utf-8")
    child = subprocess.Popen([sys.executable, str(boot)], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, cwd=str(ROOT))
    try:
        if not wait(port):
            print("服务没起来：")
            child.kill()
            print(child.stdout.read().decode("utf-8", "replace"))
            return 2

        print("[1] 先发一条基线，拿到 after")
        check("发基线 200", send(port, "sse-baseline") == 200)
        st, _, payload = http(port, "GET", "/chat/history?room=default")
        msgs = json.loads(payload.decode("utf-8"))
        after = msgs[-1]["id"] if msgs else ""
        check("历史里有基线", any(m.get("text") == "sse-baseline" for m in msgs))

        print("[2] 连上 /chat/stream（响应头 + retry 帧）")
        s = socket.create_connection(("127.0.0.1", port), timeout=10)
        s.sendall(("GET /chat/stream?room=default&after=%s HTTP/1.1\r\nHost: h\r\n"
                   "Accept: text/event-stream\r\n\r\n" % after).encode())
        s.settimeout(6)
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = s.recv(1)
            if not chunk:
                break
            head += chunk
        first = head.split(b"\r\n")[0]
        check("状态 200", b"200" in first, first)
        check("Content-Type 是 event-stream", b"text/event-stream" in head)
        check("带 Connection: close", b"Connection: close" in head)
        # SSE 是流式响应，不能有 Content-Length
        check("没有 Content-Length", b"Content-Length" not in head)

        buf = b""
        t0 = time.time()
        while time.time() - t0 < 2 and b"retry:" not in buf:
            buf += s.recv(4096)
        check("收到 retry 帧", b"retry:" in buf, buf[:80])

        print("[3] 新消息要被推过来（不等轮询）")
        time.sleep(0.3)
        send(port, "sse-live-42")
        buf = b""
        t0 = time.time()
        got = False
        while time.time() - t0 < 5:
            try:
                d = s.recv(4096)
            except socket.timeout:
                break
            if not d:
                break
            buf += d
            if b"sse-live-42" in buf:
                got = True
                break
        check("推送到了", got, buf[-200:])
        if got:
            line = [l for l in buf.split(b"\n") if b"sse-live-42" in l][0]
            check("是 data: 帧", line.startswith(b"data: "), line[:40])
            msg = json.loads(line[6:].decode("utf-8"))
            check("字段完整", msg.get("room") is None and msg.get("id") and msg.get("time"))
        s.close()

        print("[4] 边界情况")
        check("不存在的房间 404",
              http(port, "GET", "/chat/stream?room=nope")[0] == 404)
        st, _, payload = http(port, "GET", "/chat/history?room=default&after=" + after)
        fresh = json.loads(payload.decode("utf-8"))
        check("after 之后只回增量", len(fresh) >= 1 and fresh[-1].get("text") == "sse-live-42")
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
