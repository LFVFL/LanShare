# -*- coding: utf-8 -*-
"""目录分页：超大目录不再一次性塞给前端。

    python tools\\paging_test.py
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
from test_support import isolate_test_environment

TEST_ENV = isolate_test_environment()
sys.path.insert(0, str(ROOT))

import lanshare as j  # noqa: E402

PORT = 6521
PASSED, FAILED = [], []


def check(label, ok, detail=""):
    (PASSED if ok else FAILED).append(label if ok else (label, detail))
    print("  %-42s %s%s" % (label, "ok" if ok else "FAIL", "" if ok else " " + str(detail)))


def get(path):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (PORT, path), timeout=10) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def main():
    share = Path(TEST_ENV.temporary_dir(prefix="lan6-page-"))
    for i in range(450):
        (share / ("f%03d.txt" % i)).write_text("x", encoding="utf-8")

    sharer = j.FileSharer()
    ok, _ = sharer.start(path=str(share), port=PORT)
    if not ok:
        print("服务起不来")
        return 1
    try:
        print("\n[1] 不带 limit：整份返回（老行为不变）")
        st, data = get("/api/list?path=")
        check("状态 200", st == 200, st)
        check("450 条全给了", len(data["entries"]) == 450, len(data["entries"]))
        check("total 正确", data.get("total") == 450, data.get("total"))
        check("没有下一页", data.get("hasMore") is False, data.get("hasMore"))

        print("\n[2] limit=400：第一页 + 还有下一页")
        st, data = get("/api/list?path=&limit=400&offset=0")
        check("第一页 400 条", len(data["entries"]) == 400, len(data["entries"]))
        check("hasMore 为真", data.get("hasMore") is True, data.get("hasMore"))
        check("total 仍是 450", data.get("total") == 450, data.get("total"))
        first_last = data["entries"][-1]["name"]

        print("\n[3] offset=400：拿下剩下的 50 条，且不重复")
        st, data2 = get("/api/list?path=&limit=400&offset=400")
        check("第二页 50 条", len(data2["entries"]) == 50, len(data2["entries"]))
        check("没有下一页了", data2.get("hasMore") is False, data2.get("hasMore"))
        check("两页不重叠", data2["entries"][0]["name"] != first_last,
              (first_last, data2["entries"][0]["name"]))
        check("排序稳定（f000 在最前）",
              data["entries"][0]["name"] == "f000.txt", data["entries"][0]["name"])
        check("拼起来正好 450 条",
              len(data["entries"]) + len(data2["entries"]) == 450, "")

        print("\n[4] 参数乱填也不炸")
        for qs in ("limit=abc", "limit=-5", "offset=-1", "limit=0"):
            try:
                st, data = get("/api/list?path=&" + qs)
                check("%s 不报错" % qs, st == 200 and "entries" in data, st)
            except Exception as exc:
                check("%s 不报错" % qs, False, repr(exc))
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
