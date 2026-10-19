"""聊天图片缩略图测试：原图 + _t 缩略图 的存、取、删。

用法：python tools/thumb_test.py            # 默认 6245 端口
"""
import base64
import json
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
import zlib
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


def make_png(w=64, h=48, rgb=(200, 60, 60)):
    """手搓一张 PNG，免得依赖 Pillow。"""
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def data_url(payload):
    return "data:image/png;base64," + base64.b64encode(payload).decode()


def wait(port, timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.4).close()
            return True
        except OSError:
            time.sleep(0.15)
    return False


def http(port, method, path, body=b"", ctype="application/json", timeout=15):
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    head = ("%s %s HTTP/1.1\r\nHost: h\r\nConnection: close\r\n" % (method, path)).encode()
    if body:
        head += ("Content-Type: %s\r\nContent-Length: %d\r\n" % (ctype, len(body))).encode()
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
    return status, payload


def check(label, ok, detail=""):
    print("  %-38s %s %s" % (label, "ok" if ok else "FAIL", "" if ok else detail))
    if not ok:
        FAILS.append((label, detail))


def main():
    port = 6245
    tmp = Path(tempfile.mkdtemp(dir=str(ROOT), prefix="lan6-thumb-"))
    share = tmp / "share"
    share.mkdir()
    boot = tmp / "b.py"
    boot.write_text(BOOT.format(root=str(ROOT), share=str(share), port=port), encoding="utf-8")
    child = subprocess.Popen([sys.executable, str(boot)], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, cwd=str(ROOT))
    img = make_png()
    try:
        if not wait(port):
            print("服务没起来：")
            child.kill()
            print(child.stdout.read().decode("utf-8", "replace"))
            return 2

        print("[1] 带缩略图发一条图片消息")
        body = json.dumps({
            "room": "default", "text": "",
            "image": data_url(img), "thumb": data_url(make_png(20, 15, (30, 90, 200))),
        }).encode()
        st, payload = http(port, "POST", "/chat/send", body)
        check("发送 200", st == 200, "%s %r" % (st, payload[:160]))
        d = json.loads(payload.decode("utf-8"))
        msg = d.get("msg") or {}
        check("有原图 URL", msg.get("image", "").startswith("/chat/image/"), msg.get("image"))
        check("有缩略图 URL", msg.get("thumb", "").endswith("_t.png"), msg.get("thumb"))

        print("[2] 两个文件都能取到，且内容不同")
        st1, full = http(port, "GET", msg.get("image", "/x"))
        st2, thumb = http(port, "GET", msg.get("thumb", "/x"))
        check("原图 200", st1 == 200 and full == img, "%s %d 字节" % (st1, len(full)))
        check("缩略图 200", st2 == 200 and len(thumb) > 0, st2)
        check("缩略图 != 原图", thumb != full)

        print("[3] 历史里带 thumb 字段")
        st, payload = http(port, "GET", "/chat/history?room=default")
        hist = json.loads(payload.decode("utf-8"))
        last = hist[-1] if hist else {}
        check("最后一条是图片消息", last.get("type") == "image", last.get("type"))
        check("历史保留了 thumb", bool(last.get("thumb")), last.get("thumb"))

        print("[4] 删消息要把原图和缩略图一起删掉")
        st, payload = http(port, "POST", "/chat/delete",
                           json.dumps({"room": "default", "id": last.get("id")}).encode())
        check("删除 200", st == 200, payload[:80])
        check("原图 404", http(port, "GET", msg.get("image", "/x"))[0] == 404)
        check("缩略图 404", http(port, "GET", msg.get("thumb", "/x"))[0] == 404)

        print("[5] 缩略图超限就只存原图（不报错）")
        body = json.dumps({
            "room": "default", "text": "",
            "image": data_url(img),
            "thumb": "data:image/png;base64," + "A" * 300000,
        }).encode()
        st, payload = http(port, "POST", "/chat/send", body)
        d = json.loads(payload.decode("utf-8"))
        check("仍然 200", st == 200, payload[:120])
        check("只存了原图", bool((d.get("msg") or {}).get("image"))
              and not (d.get("msg") or {}).get("thumb"), d.get("msg"))

        print("[6] 非图片的 thumb 被拒绝（只存原图）")
        body = json.dumps({
            "room": "default", "text": "",
            "image": data_url(img), "thumb": "data:image/png;base64,bm90LWEtcG5n",
        }).encode()
        st, payload = http(port, "POST", "/chat/send", body)
        d = json.loads(payload.decode("utf-8"))
        check("仍然 200 且无 thumb", st == 200 and not (d.get("msg") or {}).get("thumb"),
              d.get("msg"))
        legacy_id = (d.get("msg") or {}).get("id")     # 这条就是"老消息"样本

        print("[7] 老消息（只有原图）读历史时补缩略图")
        try:
            import PIL  # noqa: F401
            has_pil = True
        except ImportError:
            has_pil = False
        if not has_pil:
            print("     服务端没装 Pillow，跳过（会退回用原图）")
        else:
            # 造一张"照片感"的大图（纯色小图压出来反而比缩略图小，测不出效果）
            from PIL import Image as _Img
            import io as _io
            big = _io.BytesIO()
            _Img.effect_noise((1200, 900), 48).convert("RGB").save(big, "JPEG", quality=92)
            big_bytes = big.getvalue()
            body = json.dumps({
                "room": "default", "text": "",
                "image": "data:image/jpeg;base64," + base64.b64encode(big_bytes).decode(),
            }).encode()
            st, payload = http(port, "POST", "/chat/send", body)
            legacy_id = (json.loads(payload.decode("utf-8")).get("msg") or {}).get("id")
            check("有老消息样本（大图、无 thumb）", bool(legacy_id), payload[:120])

            st, payload = http(port, "GET", "/chat/history?room=default")
            hist2 = json.loads(payload.decode("utf-8"))
            same = next((m for m in hist2 if m.get("id") == legacy_id), {})
            check("补出了 thumb", (same.get("thumb") or "").endswith("_t.jpg"), same.get("thumb"))
            if (same.get("thumb") or "").endswith("_t.jpg"):
                st_thumb, thumb_bytes = http(port, "GET", same["thumb"])
                st_full, full_bytes = http(port, "GET", same["image"])
                check("缩略图能取到", st_thumb == 200 and len(thumb_bytes) > 0, st_thumb)
                check("缩略图明显更小", len(thumb_bytes) * 4 < len(full_bytes),
                      "缩略图 %d vs 原图 %d" % (len(thumb_bytes), len(full_bytes)))
                check("原图仍在", st_full == 200 and full_bytes == big_bytes, st_full)

        print("[8] 孤儿图片清理（/chat/cleanup）")
        images_dir = Path(ROOT, "chat_data", "images")
        images_dir.mkdir(parents=True, exist_ok=True)
        import os as _os
        old = time.time() - 3600

        orphan_thumb = images_dir / "zz_orphan_t.jpg"      # 只有缩略图，没有原图
        orphan_thumb.write_bytes(b"\xff\xd8\xff\xdb" + b"0" * 200)
        unref_full = images_dir / "zz_unref.png"           # 原图没人引用
        unref_full.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 200)
        unref_thumb = images_dir / "zz_unref_t.jpg"
        unref_thumb.write_bytes(b"\xff\xd8\xff\xdb" + b"0" * 100)
        for f in (orphan_thumb, unref_full, unref_thumb):
            _os.utime(f, (old, old))

        # 记下被消息引用的文件，清理后必须还在
        st, payload = http(port, "GET", "/chat/history?room=default")
        hist3 = json.loads(payload.decode("utf-8"))
        referenced = [m.get("image") for m in hist3 if m.get("image")]
        keep_files = [images_dir / Path(u).name for u in referenced if u]

        st, payload = http(port, "POST", "/chat/cleanup?grace=0")
        d = json.loads(payload.decode("utf-8"))
        check("清理接口 200", st == 200 and d.get("success"), d)
        check("至少删了 3 个", (d.get("removed") or 0) >= 3, d)
        check("孤儿缩略图被删", not orphan_thumb.exists())
        check("无人引用的原图被删", not unref_full.exists())
        check("无人引用的缩略图被删", not unref_thumb.exists())
        check("被引用的图片保留", all(p.exists() for p in keep_files),
              [str(p.name) for p in keep_files if not p.exists()])

        print("[9] 新文件有宽限期，不会被误删")
        fresh = images_dir / "zz_fresh.png"
        fresh.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
        st, payload = http(port, "POST", "/chat/cleanup")     # 默认 grace=600
        check("刚建的孤儿文件先留着", fresh.exists(), "被删了")
        st, payload = http(port, "POST", "/chat/cleanup?grace=0")
        check("grace=0 时才删", not fresh.exists())
    finally:
        child.terminate()
        try:
            child.wait(5)
        except subprocess.TimeoutExpired:
            child.kill()
        shutil.rmtree(tmp, ignore_errors=True)
        # 聊天图片存在项目目录下，测试产生的文件要清掉
        for f in Path(ROOT, "chat_data", "images").glob("*"):
            try:
                f.unlink()
            except OSError:
                pass

    print("\n结果:", "全部通过 ✓" if not FAILS else "失败 %d 项" % len(FAILS))
    for f in FAILS:
        print("  -", f)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
