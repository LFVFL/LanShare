# -*- coding: utf-8 -*-
"""运行中切换共享目录：不重启服务就要立刻生效。

    python tools\\share_switch_test.py
"""
import json
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import lanshare as j  # noqa: E402

PORT = 6531
PASSED, FAILED = [], []


def check(label, ok, detail=""):
    (PASSED if ok else FAILED).append(label if ok else (label, detail))
    print("  %-44s %s%s" % (label, "ok" if ok else "FAIL", "" if ok else " " + str(detail)))


def get(path):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (PORT, path), timeout=10) as r:
        return r.status, r.read()


def names(path="/api/list?path="):
    st, body = get(path)
    data = json.loads(body.decode("utf-8"))
    return st, [e["name"] for e in data.get("entries", [])], data.get("path")


def main():
    dir_a = Path(tempfile.mkdtemp(dir=str(ROOT), prefix="lan6-sw-a-"))
    dir_b = Path(tempfile.mkdtemp(dir=str(ROOT), prefix="lan6-sw-b-"))
    (dir_a / "from-a.txt").write_text("A", encoding="utf-8")
    (dir_b / "from-b.txt").write_text("B", encoding="utf-8")

    sharer = j.FileSharer()
    ok, _ = sharer.start(path=str(dir_a), port=PORT)
    if not ok:
        print("服务起不来")
        return 1
    try:
        print("\n[1] 一开始共享的是 A")
        st, got, path = names()
        check("列出 from-a.txt", "from-a.txt" in got, got)
        check("看不到 from-b.txt", "from-b.txt" not in got, got)

        print("\n[2] 运行中切到 B（不重启）")
        ok, detail = sharer.set_shared_path(str(dir_b))
        check("set_shared_path 返回成功", ok, detail)
        st, got, path = names()
        check("立刻能看到 from-b.txt", "from-b.txt" in got, got)
        check("from-a.txt 不见了", "from-a.txt" not in got, got)
        check("返回的 path 是 B", Path(path) == dir_b.resolve(), path)

        print("\n[3] 老页面还指着 A 的绝对路径：要自动回到新根，不能报错")
        st, got, path = names("/api/list?path=" + urllib.parse.quote(str(dir_a)))
        check("仍然 200", st == 200, st)
        check("被归到新根 B", Path(path) == dir_b.resolve(), path)
        check("列的是 B 的内容", "from-b.txt" in got, got)

        print("\n[4] 下载也跟着走")
        st, body = get("/download?file=from-b.txt&path=")
        check("新目录的文件能下", st == 200 and body == b"B", (st, body[:10]))
        try:
            get("/download?file=from-a.txt&path=")
            check("旧目录的文件下不到", False, "居然下到了")
        except urllib.error.HTTPError as exc:
            check("旧目录的文件下不到", exc.code == 404, exc.code)

        print("\n[5] 传个不存在的目录：拒绝，且保持原样")
        bad, msg = sharer.set_shared_path(str(dir_a / "nope"))
        check("返回失败", bad is False, msg)
        st, got, _ = names()
        check("还在 B", "from-b.txt" in got, got)

        print("\n[6] 切回 A")
        sharer.set_shared_path(str(dir_a))
        st, got, _ = names()
        check("又看到 from-a.txt", "from-a.txt" in got, got)
    finally:
        sharer.stop()
        shutil.rmtree(dir_a, ignore_errors=True)
        shutil.rmtree(dir_b, ignore_errors=True)

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
