"""一键打包：复制源码到 ASCII 路径 → 自检 → PyInstaller（带图标）→ 启动实测。

为什么不用 .ps1：Windows PowerShell 5.1 把子进程的 stderr 当异常、脚本编码还要 BOM，
踩了三次坑。Python 里 subprocess + 编码都是确定的，一次写完。

为什么必须 ASCII 路径：项目在 E:\\code\\局域网\\第六版，PyInstaller 的 Qt 钩子会把路径
变成 "E:/code/???/..." 然后报「Qt plugin directory does not exist」。源码路径可以是中文
（只是复制过去），真正跑打包的目录必须是 ASCII。

用法（用系统 python 跑，它会自己准备 venv）：
    python tools\\build_exe.py
    python tools\\build_exe.py --work E:\\lanbuild --venv E:\\lanvenv --port 6370
"""

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPS = ["PyQt5", "pyinstaller", "brotli", "segno", "pillow"]
COPY = ["lanshare.py", "webassets.py", "icon.ico", "server", "ui"]

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def step(msg):
    print("\n=== %s" % msg)


def ok(msg):
    print("  [OK]   %s" % msg)


def bad(msg):
    print("  [FAIL] %s" % msg)
    sys.exit(1)


def is_ascii(text):
    try:
        text.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def run(cmd, cwd=None, env=None, stream=True):
    """跑一条命令；stream=True 时把输出直接透传（保留顺序）。"""
    if stream:
        return subprocess.call(cmd, cwd=cwd, env=env)
    return subprocess.call(cmd, cwd=cwd, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(ROOT))
    ap.add_argument("--work", default=r"E:\lanbuild")
    ap.add_argument("--venv", default=r"E:\lanvenv")
    ap.add_argument("--base-python", default=r"D:\Program Files\python\python.exe")
    ap.add_argument("--port", type=int, default=6370)
    ap.add_argument("--skip-test", action="store_true")
    args = ap.parse_args()

    source = Path(args.source).resolve()
    work = Path(args.work).resolve()
    venv = Path(args.venv).resolve()

    step("检查路径")
    if not is_ascii(str(work)):
        bad("打包目录含非 ASCII 字符：%s（PyInstaller 的 Qt 钩子会失败）" % work)
    if not is_ascii(str(venv)):
        bad("venv 路径含非 ASCII 字符：%s" % venv)
    ok("源码 %s%s" % (source, "" if is_ascii(str(source)) else "（含中文，复制后打包，没问题）"))
    ok("打包 %s" % work)
    ok("环境 %s" % venv)

    step("准备打包环境")
    py = venv_python(venv)
    if not py.exists():
        base = Path(args.base_python)
        if not base.exists():
            bad("找不到基础解释器：%s（用 --base-python 指定）" % base)
        print("  创建 venv ...")
        run([str(base), "-m", "venv", str(venv)], stream=False)
        if not py.exists():
            bad("创建 venv 失败")
        ok("venv 已创建")

    probe = subprocess.run([str(py), "-c", "import PyQt5, PyInstaller, PIL"],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        print("  安装依赖（首次会下载 PyQt5，约 1 分钟）...")
        run([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"], stream=False)
        run([str(py), "-m", "pip", "install", "-q"] + DEPS, stream=False)
        probe = subprocess.run([str(py), "-c", "import PyQt5, PyInstaller, PIL"],
                               capture_output=True, text=True)
        if probe.returncode != 0:
            bad("依赖安装失败：%s" % probe.stderr.strip()[-300:])
    ok("PyQt5 / PyInstaller / Pillow 就绪")

    step("复制源码到 %s" % work)
    if work.exists():
        for child in work.iterdir():
            if child.name == "dist":
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    else:
        work.mkdir(parents=True)
    for name in COPY:
        src = source / name
        dst = work / name
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    (work / "share").mkdir(exist_ok=True)
    (work / "tools").mkdir(exist_ok=True)
    shutil.copy2(source / "tools" / "preflight.py", work / "tools" / "preflight.py")
    for cache in work.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    ok("已复制 %s" % " / ".join(COPY))

    step("打包前自检")
    rc = run([str(py), str(work / "tools" / "preflight.py")])
    if rc != 0:
        bad("自检未通过，先按上面的提示处理")
    ok("自检通过")

    step("开始打包（1~2 分钟）")
    exe = work / "dist" / ("lanshare.exe" if os.name == "nt" else "lanshare")
    if exe.exists():
        subprocess.run(["taskkill", "/F", "/IM", exe.name], capture_output=True)
        time.sleep(0.5)
        try:
            exe.unlink()
        except OSError:
            pass
    env = dict(os.environ)
    env.pop("QT_PLUGIN_PATH", None)
    env.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)
    icon = work / "icon.ico"
    log = work / "build.log"
    # segno 是函数里才 import 的（可选依赖），PyInstaller 静态分析看不到，得显式声明
    cmd = [str(py), "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed",
           "--name", "lanshare", "--icon", str(icon),
           "--hidden-import", "webassets", "--hidden-import", "segno",
           "--add-data", "%s%s." % (icon, os.pathsep), "lanshare.py"]
    with open(log, "w", encoding="utf-8", errors="replace") as f:
        rc = subprocess.call(cmd, cwd=str(work), env=env, stdout=f, stderr=f)
    if rc != 0 or not exe.exists():
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
        print("\n".join(tail))
        bad("PyInstaller 失败（完整日志：%s）" % log)
    ok("生成 %s（%.1f MB）" % (exe, exe.stat().st_size / 1024 / 1024))

    if args.skip_test:
        step("完成（已跳过启动实测）")
        print("  exe: %s" % exe)
        return 0

    step("启动 exe 实测")
    run_env = dict(env)
    run_env["QT_QPA_PLATFORM"] = "offscreen"
    proc = subprocess.Popen([str(exe), str(work / "share"), "-p", str(args.port), "--startup"],
                            env=run_env, cwd=str(work))
    try:
        base = "http://127.0.0.1:%d" % args.port
        ready = False
        for _ in range(30):
            time.sleep(0.5)
            try:
                with urllib.request.urlopen(base + "/login", timeout=3) as r:
                    if r.status == 200:
                        ready = True
                        break
            except Exception:
                pass
        if not ready:
            bad("exe 起来了但 /login 没响应")
        ok("/login 200")

        for path, min_bytes in (("/static/app.css", 1000), ("/favicon.ico", 1000)):
            with urllib.request.urlopen(base + path, timeout=5) as r:
                body = r.read()
            if len(body) < min_bytes:
                bad("%s 异常（%d 字节）" % (path, len(body)))
            ok("%s 200（%d 字节）" % (path, len(body)))
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()

    step("完成")
    print("  exe: %s" % exe)
    print("  提示：exe 可复制到任何地方运行，chat_data 会建在它同级。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
