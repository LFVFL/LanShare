"""并发闸门测试：名额满要快速回 503，名额正常时并发请求不能出错。

用法：python tools/limits_test.py
"""
import http.client
import sys
try:                      # Windows 控制台默认 GBK，打印 ✓ 之类的字符会崩
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
from test_support import isolate_test_environment

TEST_ENV = isolate_test_environment()
sys.path.insert(0, str(ROOT))
sys.argv = ["x"]

import lanshare as j
from server import runner

SHARE = TEST_ENV.temporary_dir(prefix="limits-share-")
FAILS = []


def one(port, path="/api/list"):
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        c.request("GET", path, headers={"Host": "h"})
        r = c.getresponse()
        r.read()
        c.close()
        return r.status
    except Exception as e:
        return repr(e)


def start(port):
    s = j.FileSharer()
    ok, res = s.start(path=str(SHARE), port=port, password=None, admin_password=None)
    assert ok, res
    return s


def main():
    print("[1] 名额为 0 时应快速 503")
    runner.MAX_WORKERS = 0
    runner.QUEUE_TIMEOUT = 0.3
    s = start(6230)
    t0 = time.time()
    st = one(6230)
    print("    状态: %s 耗时 %.2fs" % (st, time.time() - t0))
    if st != 503:
        FAILS.append(("503", st))
    s.stop(wait=True, timeout=10)
    time.sleep(0.4)

    print("[2] 默认名额下 20 并发应全 200")
    runner.MAX_WORKERS = 32
    runner.QUEUE_TIMEOUT = 10
    s = start(6231)
    codes = []
    lock = threading.Lock()

    def worker():
        st = one(6231)
        with lock:
            codes.append(st)

    ts = [threading.Thread(target=worker) for _ in range(20)]
    t0 = time.time()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    print("    耗时 %.2fs 状态集合: %s" % (time.time() - t0, sorted(set(map(str, codes)))))
    if codes.count(200) != 20:
        FAILS.append(("concurrent", codes))

    print("[3] 串行 5 次仍正常")
    for _ in range(5):
        st = one(6231)
        if st != 200:
            FAILS.append(("serial", st))
            break
    s.stop(wait=True, timeout=10)

    print("\n结果:", "全部通过 ✓" if not FAILS else FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
