"""批量移动 / 复制测试。

用法：python tools/fsops_test.py            # 默认 6340 端口
"""
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


def post(port, path, fields):
    """fields 是 [(key, value)]，同名 key 会重复提交（name 需要多值）。"""
    body = urllib.parse.urlencode(fields, doseq=True).encode()
    s = socket.create_connection(("127.0.0.1", port), timeout=20)
    s.sendall(("POST %s HTTP/1.1\r\nHost: h\r\nConnection: close\r\n"
               "Content-Type: application/x-www-form-urlencoded\r\n"
               "Content-Length: %d\r\n\r\n" % (path, len(body))).encode() + body)
    data = b""
    while True:
        c = s.recv(65536)
        if not c:
            break
        data += c
    s.close()
    head, _, payload = data.partition(b"\r\n\r\n")
    status = int(head.split(b" ")[1]) if b" " in head else 0
    try:
        return status, json.loads(payload.decode("utf-8"))
    except Exception:
        return status, payload


def check(label, ok, detail=""):
    print("  %-42s %s %s" % (label, "ok" if ok else "FAIL", "" if ok else detail))
    if not ok:
        FAILS.append((label, detail))


def main():
    port = 6340
    tmp = Path(tempfile.mkdtemp(dir=str(ROOT), prefix="lan6-fsops-"))
    share = tmp / "share"
    (share / "docs").mkdir(parents=True)
    (share / "a.txt").write_text("aaa", encoding="utf-8")
    (share / "b.txt").write_text("bbb", encoding="utf-8")
    (share / "docs" / "keep.md").write_text("keep", encoding="utf-8")
    boot = tmp / "b.py"
    boot.write_text(BOOT.format(root=str(ROOT), share=str(share), port=port), encoding="utf-8")
    child = subprocess.Popen([sys.executable, str(boot)], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, cwd=str(ROOT))
    try:
        if not wait(port):
            print("服务没起来：")
            child.kill()
            print(child.stdout.read().decode("utf-8", "replace"))
            return 2

        print("[1] 移动两个文件到新目录（目录自动创建）")
        st, d = post(port, "/move", [("path", str(share)), ("dst", "归档/2026"),
                                     ("name", "a.txt"), ("name", "b.txt")])
        check("200 且成功", st == 200 and d.get("success"), d)
        check("移动了 2 项", len(d.get("moved") or []) == 2, d.get("moved"))
        check("原位置没了", not (share / "a.txt").exists())
        check("新位置有文件", (share / "归档" / "2026" / "a.txt").read_text(encoding="utf-8") == "aaa")

        print("[2] 复制（同名自动加序号）")
        st, d = post(port, "/copy", [("path", str(share)), ("dst", "docs"),
                                     ("name", "归档/2026/a.txt")])   # 名字里带子目录，应被 sanitize 掉
        check("第一次复制成功或跳过", st == 200, d)
        st, d = post(port, "/copy", [("path", str(share)), ("dst", "docs"),
                                     ("name", "归档/2026/a.txt")])
        check("没有覆盖已有文件", st == 200, d)

        print("[3] 移动到自己所在目录 → 跳过")
        st, d = post(port, "/move", [("path", str(share / "docs")), ("dst", "."),
                                     ("name", "keep.md")])
        check("200", st == 200, d)
        check("被跳过（same dir）", any(s.get("reason") == "same dir"
                                        for s in (d.get("skipped") or [])), d.get("skipped"))
        check("keep.md 还在原地", (share / "docs" / "keep.md").exists())

        print("[4] 目录不能搬进自己里面")
        (share / "outer" / "inner").mkdir(parents=True)
        (share / "outer" / "f.txt").write_text("x", encoding="utf-8")
        st, d = post(port, "/move", [("path", str(share)), ("dst", "outer/inner"),
                                     ("name", "outer")])
        check("被拒绝", any(s.get("reason") == "into itself"
                            for s in (d.get("skipped") or [])), d.get("skipped"))

        print("[5] 边界")
        st, d = post(port, "/move", [("path", str(share)), ("dst", "../outside"),
                                     ("name", "docs/keep.md")])
        check("含 .. 的目标被拒", st == 400, st)
        check("没有在共享外建目录", not (tmp / "outside").exists())
        st, d = post(port, "/move", [("path", str(share)), ("dst", "docs")])
        check("没选文件 → 400", st == 400, st)
        st, d = post(port, "/copy", [("path", str(share)), ("dst", "docs"),
                                     ("name", "不存在的文件.txt")])
        check("不存在的源被跳过", st == 200 and not (d.get("moved")),
              d.get("skipped"))
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
