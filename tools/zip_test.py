"""流式打包测试：边压边发，且不落临时文件。

用法：python tools/zip_test.py            # 默认 6263 端口
"""
import io
import json
import os
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
import zipfile
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


def call(port, method, path, body=b"", ctype="application/x-www-form-urlencoded", timeout=60,
         headers=None):
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    head = ("%s %s HTTP/1.1\r\nHost: h\r\nConnection: close\r\n" % (method, path)).encode()
    if body:
        head += ("Content-Type: %s\r\nContent-Length: %d\r\n" % (ctype, len(body))).encode()
    for key, val in (headers or {}).items():
        head += ("%s: %s\r\n" % (key, val)).encode()
    s.sendall(head + b"\r\n" + body)
    data = b""
    while True:
        d = s.recv(65536)
        if not d:
            break
        data += d
    s.close()
    head_part, _, payload = data.partition(b"\r\n\r\n")
    status = int(head_part.split(b" ")[1]) if b" " in head_part else 0
    headers = {}
    for line in head_part.split(b"\r\n")[1:]:
        k, _, v = line.decode("latin-1").partition(":")
        headers[k.strip().lower()] = v.strip()
    return status, headers, payload


def check(label, ok, detail=""):
    print("  %-40s %s %s" % (label, "ok" if ok else "FAIL", "" if ok else detail))
    if not ok:
        FAILS.append((label, detail))


def main():
    port = 6263
    tmp = Path(tempfile.mkdtemp(dir=str(ROOT), prefix="lan6-zip-"))
    share = tmp / "share"
    (share / "sub").mkdir(parents=True)
    (share / "a.txt").write_text("hello " * 2000, encoding="utf-8")
    (share / "sub" / "b.txt").write_text("world " * 2000, encoding="utf-8")
    (share / "sub" / "big.bin").write_bytes(os.urandom(3 * 1024 * 1024))
    boot = tmp / "b.py"
    boot.write_text(BOOT.format(root=str(ROOT), share=str(share), port=port), encoding="utf-8")
    child = subprocess.Popen([sys.executable, str(boot)], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, cwd=str(ROOT))
    # 记下测试前系统临时目录里已有的 zip，后面用来证明"没新增临时文件"
    zip_before = set(Path(tempfile.gettempdir()).glob("*.zip"))
    try:
        if not wait(port):
            print("服务没起来：")
            child.kill()
            print(child.stdout.read().decode("utf-8", "replace"))
            return 2

        print("[1] 登记打包任务（单个文件）")
        body = urllib.parse.urlencode({"path": str(share), "files[]": "a.txt"}).encode()
        st, hd, payload = call(port, "POST", "/zip", body)
        d = json.loads(payload.decode("utf-8"))
        check("POST /zip 200", st == 200, payload[:120])
        check("返回 download_url", "/zip_download?id=" in (d.get("download_url") or ""), d)
        check("返回文件数 1", d.get("count") == 1, d.get("count"))

        print("[2] 默认下载（可续传）：带长度、带 ETag、内容正确")
        st, hd, payload = call(port, "GET", d["download_url"])
        check("GET 200", st == 200, st)
        check("Content-Type 是 zip", hd.get("content-type") == "application/zip", hd.get("content-type"))
        check("带 Content-Length", hd.get("content-length") == str(len(payload)),
              hd.get("content-length"))
        check("声明支持 Range", (hd.get("accept-ranges") or "") == "bytes", hd.get("accept-ranges"))
        check("带 ETag", bool(hd.get("etag")), hd.get("etag"))
        check("内容以 PK 开头", payload[:2] == b"PK", payload[:8])
        zf = zipfile.ZipFile(io.BytesIO(payload))
        check("包内条目", zf.namelist() == ["a.txt"], zf.namelist())
        check("CRC 全部正确", zf.testzip() is None)
        check("内容一致", zf.read("a.txt") == (share / "a.txt").read_bytes())
        zf.close()

        print("[3] 打包整个文件夹（含子目录 + 3MB 随机文件）")
        body = urllib.parse.urlencode({"path": str(share), "files[]": "sub"}).encode()
        st, hd, payload = call(port, "POST", "/zip", body)
        d2 = json.loads(payload.decode("utf-8"))
        check("POST 200 且 2 个文件", st == 200 and d2.get("count") == 2, d2.get("count"))
        st, hd, payload = call(port, "GET", d2["download_url"])
        zf = zipfile.ZipFile(io.BytesIO(payload))
        names = sorted(zf.namelist())
        check("子目录结构保留", names == ["sub/b.txt", "sub/big.bin"], names)
        check("二进制文件 CRC 正确", zf.testzip() is None)
        check("二进制内容一致",
              zf.read("sub/big.bin") == (share / "sub" / "big.bin").read_bytes())
        zf.close()

        print("[4] stream=1：仍是流式（无 Content-Length、不宣告 Range）")
        zips_before_stream = set(Path(tempfile.gettempdir()).glob("*.zip"))
        st, hd, payload = call(port, "GET", d2["download_url"] + "&stream=1")
        check("GET 200", st == 200, st)
        check("没有 Content-Length", "content-length" not in hd, hd.get("content-length"))
        check("带 Connection: close", hd.get("connection") == "close", hd.get("connection"))
        check("不宣告 Accept-Ranges", "accept-ranges" not in hd, hd.get("accept-ranges"))
        check("内容仍是合法 zip", zipfile.ZipFile(io.BytesIO(payload)).testzip() is None)
        new_zips = set(Path(tempfile.gettempdir()).glob("*.zip")) - zips_before_stream
        check("流式模式不产生临时 zip", not new_zips, [str(p) for p in list(new_zips)[:3]])

        print("[4b] 断点续传：Range 请求拿到对应的那一段")
        body = urllib.parse.urlencode({"path": str(share), "files[]": "sub"}).encode()
        st, hd, payload = call(port, "POST", "/zip", body)
        d3 = json.loads(payload.decode("utf-8"))
        st, hd, full = call(port, "GET", d3["download_url"])
        etag = hd.get("etag")
        check("流式响应带 ETag", bool(etag), hd)
        check("声明支持 Range", (hd.get("accept-ranges") or "") == "bytes", hd.get("accept-ranges"))

        mid = len(full) // 2
        st, hd2, part = call(port, "GET", d3["download_url"],
                             headers={"Range": "bytes=%d-%d" % (mid, mid + 999)})
        check("Range 请求回 206", st == 206, st)
        check("Content-Range 正确",
              (hd2.get("content-range") or "") ==
              "bytes %d-%d/%d" % (mid, mid + 999, len(full)), hd2.get("content-range"))
        check("这一段字节与整包一致", part == full[mid:mid + 1000],
              "%d vs %d" % (len(part), 1000))
        check("Content-Length 是这一段长度", hd2.get("content-length") == str(len(part)))

        st, hd3, tail = call(port, "GET", d3["download_url"],
                             headers={"Range": "bytes=%d-" % (len(full) - 100)})
        check("开放式 Range 也能用", st == 206 and tail == full[-100:], st)

        st, hd4, _ = call(port, "GET", d3["download_url"],
                          headers={"Range": "bytes=%d-" % (len(full) + 10)})
        check("越界 Range → 416", st == 416, st)

        st, hd5, whole = call(port, "GET", d3["download_url"],
                              headers={"Range": "bytes=0-99", "If-Range": '"stale"'})
        check("If-Range 对不上 → 200 整包", st == 200 and whole == full, st)

        print("[5] 边界")
        check("不存在的 id → 404", call(port, "GET", "/zip_download?id=nope")[0] == 404)
        body = urllib.parse.urlencode({"path": str(share), "files[]": "../outside"}).encode()
        st, hd, payload = call(port, "POST", "/zip", body)
        check("越权路径被拒（400/403）", st in (400, 403), st)
        body = urllib.parse.urlencode({"path": str(share), "files[]": ""}).encode()
        check("空选择 → 400", call(port, "POST", "/zip", body)[0] == 400)
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
