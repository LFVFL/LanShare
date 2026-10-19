"""共享内搜索测试。

用法：python tools/search_test.py            # 默认 6278 端口
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


def get_json(port, path, timeout=20):
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    s.sendall(("GET %s HTTP/1.1\r\nHost: h\r\nConnection: close\r\n\r\n" % path).encode())
    data = b""
    while True:
        d = s.recv(65536)
        if not d:
            break
        data += d
    s.close()
    head, _, payload = data.partition(b"\r\n\r\n")
    status = int(head.split(b" ")[1]) if b" " in head else 0
    try:
        return status, json.loads(payload.decode("utf-8"))
    except Exception:
        return status, payload


def check(label, ok, detail=""):
    print("  %-40s %s %s" % (label, "ok" if ok else "FAIL", "" if ok else detail))
    if not ok:
        FAILS.append((label, detail))


def search(port, q, path=""):
    qs = {"q": q}
    if path:
        qs["path"] = path
    return get_json(port, "/api/search?" + urllib.parse.urlencode(qs))


def main():
    port = 6278
    tmp = Path(tempfile.mkdtemp(dir=str(ROOT), prefix="lan6-search-"))
    share = tmp / "share"
    (share / "docs" / "2024").mkdir(parents=True)
    (share / "media").mkdir()
    (share / "node_modules" / "pkg").mkdir(parents=True)
    (share / "report.txt").write_text("root report", encoding="utf-8")
    (share / "docs" / "REPORT-final.txt").write_text("x" * 2048, encoding="utf-8")
    (share / "docs" / "2024" / "report.md").write_text("y" * 100, encoding="utf-8")
    (share / "media" / "报告.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (share / "media" / "other.bin").write_bytes(b"z" * 512)
    (share / "node_modules" / "pkg" / "report.js").write_text("dep", encoding="utf-8")

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

        print("[1] 递归搜索（不分大小写）")
        st, d = search(port, "report")
        check("200", st == 200, st)
        names = sorted(e["name"] for e in d.get("entries", []))
        check("找到 3 个 report", names == ["REPORT-0final.txt".replace("0", "-").replace("--", "-")] + ["report.md", "report.txt"]
              or names == ["REPORT-final.txt", "report.md", "report.txt"], names)
        check("不扫 node_modules", all("node_modules" not in e["dir"] for e in d["entries"]))

        print("[2] 结果带目录位置，能直接跳过去")
        md = next((e for e in d["entries"] if e["name"] == "report.md"), {})
        check("dir 是相对路径", md.get("dir") == "docs/2024", md.get("dir"))
        check("dirHref 指向绝对目录", "/dir?path=" in (md.get("dirHref") or ""), md.get("dirHref"))
        check("encPath 带目录", "docs%2F2024%2Freport.md" == md.get("encPath")
              or "docs/2024/report.md" == urllib.parse.unquote(md.get("encPath") or ""),
              md.get("encPath"))
        check("大小/时间字段齐", md.get("size") == 100 and md.get("timeText"), md)

        print("[3] 中文关键词")
        st, d2 = search(port, "报告")
        check("找到 1 个", st == 200 and d2.get("count") == 1, d2.get("count"))
        check("是 png", (d2.get("entries") or [{}])[0].get("kind") == "image",
              (d2.get("entries") or [{}])[0].get("kind"))

        print("[4] 指定起点目录")
        st, d3 = search(port, "report", str(share / "docs"))
        check("只在 docs 下找", d3.get("count") == 2, d3.get("count"))

        print("[5] 边界")
        check("空关键词 400", search(port, "  ")[0] == 400)
        check("越权路径 403", search(port, "x", str(share.parent))[0] == 403)
        st, d4 = search(port, "zzz-not-exist")
        check("没结果也是 200", st == 200 and d4.get("count") == 0, st)
        st, d5 = search(port, ".")
        check("通配符按字面处理", "truncated" in d5, list(d5)[:5])
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
