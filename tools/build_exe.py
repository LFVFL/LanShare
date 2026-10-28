"""Build in a fresh owned ASCII staging directory; never erase an old build.

    python tools/build_exe.py
    python tools/build_exe.py --work E:\\lanshare-builds --skip-test

--work is an owned build container. Each invocation creates a new child in it.
An existing unmarked directory is refused, not cleaned or adopted.
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from windows_job import OwnedProcessJob


ROOT = Path(__file__).resolve().parent.parent
DEPS = ["PyQt5>=5.15", "Pillow>=10.2", "pyinstaller", "brotli>=1.1", "segno>=1.5"]
IMAGE_DEPS = ["pillow-heif>=0.18", "pillow-avif-plugin>=1.4"]
COPY = ("lanshare.py", "webassets.py", "icon.ico", "server", "ui")
OWNER_FILE = ".lanshare-build-container.json"
OWNER = {"tool": "LanShare/tools/build_exe.py", "version": 1}
WINDOWS_RUNTIME_TMPDIR = r"%LOCALAPPDATA%\LanShare\Temp"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


def step(message):
    print("\n=== %s" % message)


def ok(message):
    print("  [OK]   %s" % message)


def is_ascii(text):
    return str(text).isascii()


def within(path, parent):
    try:
        Path(path).relative_to(parent)
        return True
    except ValueError:
        return False


def windows_directory():
    if os.name != 'nt':
        return None
    return Path(os.environ.get('SystemRoot') or os.environ.get('WINDIR') or r'C:\Windows').resolve()


def default_work():
    temporary = Path(tempfile.gettempdir())
    windows = windows_directory()
    if is_ascii(temporary) and (windows is None or not within(temporary.resolve(), windows)):
        return temporary / "LanShare-builds"
    if os.name == "nt":
        public = Path(os.environ.get("PUBLIC", r"C:\Users\Public"))
        if is_ascii(public):
            return public / "LanShare-builds"
    raise ValueError("临时路径包含非 ASCII 字符，请用 --work 指定新的 ASCII 构建目录")


def prepare_staging(source, requested):
    """Validate final absolute paths before creating anything; no cleanup here."""
    source = Path(source).resolve(strict=True)
    container = Path(requested).resolve()
    if not source.is_dir() or not (source / "lanshare.py").is_file():
        raise ValueError("源码目录无效：%s" % source)
    if not is_ascii(container):
        raise ValueError("构建目录必须是 ASCII 路径：%s" % container)
    if container == Path(container.anchor):
        raise ValueError("不能把卷根目录用于构建")
    if within(source, container) or within(container, source):
        raise ValueError("构建目录不能是源码目录、源码祖先或源码内部目录")

    marker = container / OWNER_FILE
    if container.exists():
        if not container.is_dir() or marker.is_symlink() or not marker.is_file():
            raise ValueError("已有目录没有构建归属标记，拒绝使用；请选择一个新目录：%s" % container)
        try:
            if json.loads(marker.read_text(encoding="utf-8")) != OWNER:
                raise ValueError("构建目录归属标记不匹配")
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("无法验证构建目录归属标记") from exc
    else:
        container.mkdir(parents=True, exist_ok=False)
        marker.write_text(json.dumps(OWNER) + "\n", encoding="utf-8")

    staging = Path(tempfile.mkdtemp(prefix="build-", dir=str(container))).resolve()
    if staging.parent != container or not is_ascii(staging):
        raise ValueError("新建的构建路径不在已验证目录中")
    (staging / ".lanshare-build.json").write_text(
        json.dumps(dict(OWNER, source=str(source), created=time.time()), ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return staging


def run(command, cwd=None, env=None, stream=True):
    if stream:
        return subprocess.call(command, cwd=cwd, env=env)
    return subprocess.call(command, cwd=cwd, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def venv_python(venv):
    return Path(venv) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def clean_python_environment():
    environment = dict(os.environ)
    for name in ('PYTHONHOME', 'PYTHONPATH', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QTDIR'):
        environment.pop(name, None)
    environment['QT_QPA_PLATFORM'] = 'offscreen'
    environment['PYTHONIOENCODING'] = 'utf-8'
    environment['PYTHONNOUSERSITE'] = '1'
    return environment


def validate_environment(python, venv):
    config = venv / 'pyvenv.cfg'
    if config.is_symlink() or not config.is_file():
        raise ValueError('目标解释器没有 pyvenv.cfg，拒绝向非虚拟环境安装依赖')
    probe = subprocess.run(
        [str(python), '-c', 'import sys,json; print(json.dumps([sys.prefix,sys.base_prefix]))'],
        capture_output=True, text=True, timeout=15, env=clean_python_environment(),
    )
    try:
        prefix, base_prefix = json.loads(probe.stdout)
        if probe.returncode or Path(prefix).resolve() != venv or Path(prefix).resolve() == Path(base_prefix).resolve():
            raise ValueError('目标解释器不是指定目录的独立虚拟环境')
    except (TypeError, ValueError) as exc:
        raise ValueError('无法验证虚拟环境归属；未安装依赖') from exc


def prepare_environment(base_python, venv, extra_images=False):
    venv = Path(venv).resolve()
    if not is_ascii(venv) or venv == Path(venv.anchor):
        raise ValueError("venv 必须是非卷根的 ASCII 路径")
    windows = windows_directory()
    if windows is not None and within(venv, windows):
        raise ValueError("venv 不能位于 Windows 系统目录内：PyInstaller 会排除其中的 Qt/图片 DLL；请选择系统目录之外的 ASCII 路径")
    py = venv_python(venv)
    environment = clean_python_environment()
    if not py.is_file():
        if venv.exists():
            raise ValueError("已有 --venv 目录不是虚拟环境，拒绝修改：%s" % venv)
        if run([str(base_python), "-m", "venv", str(venv)], env=environment) != 0 or not py.is_file():
            raise RuntimeError("创建 venv 失败")

    validate_environment(py, venv)
    modules = "import PyQt5, PyInstaller, PIL, brotli, segno"
    if extra_images:
        modules += "; import pillow_heif, pillow_avif"
    probe_command = [str(py), "-c", modules]
    probe = subprocess.run(probe_command, capture_output=True, text=True, env=environment)
    if probe.returncode:
        step("安装构建依赖（需要网络）")
        if run([str(py), "-m", "pip", "install"] + DEPS + (IMAGE_DEPS if extra_images else []), env=environment) != 0:
            raise RuntimeError("依赖安装失败；未删除任何旧构建或源码")
        probe = subprocess.run(probe_command, capture_output=True, text=True, env=environment)
        if probe.returncode:
            raise RuntimeError("依赖检查失败：%s" % probe.stderr.strip()[-500:])
    return py


def build_environment(python, staging):
    environment = clean_python_environment()
    environment['BUILD_OUT'] = str(staging)
    environment['LANSHARE_DATA_DIR'] = str(Path(staging) / '_preflight' / 'chat_data')
    environment['PYINSTALLER_CONFIG_DIR'] = str(Path(staging) / '_pyinstaller-cache')
    if os.name == 'nt':
        probe = subprocess.run(
            [str(python), '-c', 'import sys,json; print(json.dumps(sys.base_prefix))'],
            capture_output=True, text=True, timeout=15, env=environment,
        )
        try:
            base = Path(json.loads(probe.stdout)).resolve(strict=True)
            if probe.returncode or not base.is_dir():
                raise ValueError('Invalid base Python directory')
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError('无法验证 Python 基础运行时目录；未进行打包') from exc
        windows = windows_directory()
        # Dependency scanning must not pick unrelated DLLs from a host app's PATH.
        paths = (Path(python).resolve().parent, base, base / 'DLLs', windows / 'System32',
                 windows, windows / 'System32' / 'Wbem',
                 windows / 'System32' / 'WindowsPowerShell' / 'v1.0')
        environment['PATH'] = os.pathsep.join(dict.fromkeys(str(path) for path in paths if path.is_dir()))
    return environment


def verify_qt_archive(python, exe, environment):
    if os.name != 'nt':
        return
    probe = subprocess.run(
        [str(python), '-c', 'import json,sys; from PyInstaller.archive.readers import CArchiveReader; '
         'print(json.dumps(list(CArchiveReader(sys.argv[1]).toc)))', str(exe)],
        capture_output=True, text=True, timeout=30, env=environment,
    )
    try:
        entries = json.loads(probe.stdout)
        if probe.returncode or not isinstance(entries, list):
            raise ValueError('Archive reader failed')
        names = {name.replace('\\', '/').rsplit('/', 1)[-1].lower() for name in entries}
    except (TypeError, ValueError) as exc:
        raise RuntimeError('无法核验 EXE 内的 Qt 依赖') from exc
    required = {'qt5core.dll', 'qt5gui.dll', 'qt5widgets.dll', 'qt5network.dll',
                'qtcore.pyd', 'qwindows.dll', 'qoffscreen.dll', 'msvcp140_1.dll', 'concrt140.dll'}
    missing = required - names
    if missing:
        raise RuntimeError('EXE 缺少 Qt 依赖：%s；请检查 venv 位置与构建 PATH' % ', '.join(sorted(missing)))
    ok('EXE 已包含 Qt 主 DLL、平台插件与 VC 运行时')


def runtime_tmpdir_flags():
    # Windows expands the placeholder before Python starts; never bake in the builder's profile.
    return ['--runtime-tmpdir', WINDOWS_RUNTIME_TMPDIR] if os.name == 'nt' else []


def verify_runtime_tmpdir_archive(python, exe, environment):
    if os.name != 'nt':
        return
    probe = subprocess.run(
        [str(python), '-c', 'import json,sys; from PyInstaller.archive.readers import CArchiveReader; '
         'print(json.dumps(CArchiveReader(sys.argv[1]).options))', str(exe)],
        capture_output=True, text=True, timeout=30, env=environment,
    )
    try:
        options = json.loads(probe.stdout)
        expected = 'pyi-runtime-tmpdir ' + WINDOWS_RUNTIME_TMPDIR.replace('\\', '/')
        if probe.returncode or not isinstance(options, list) or expected not in options:
            raise ValueError('Missing portable runtime directory option')
    except (TypeError, ValueError) as exc:
        raise RuntimeError('EXE 缺少当前用户临时解包配置，或配置已被展开为构建者路径') from exc
    ok('EXE 使用当前用户的独立临时解包目录，不依赖 TMP/TEMP')


def snapshot_source(source, staging, python, environment=None):
    assets = source / "tools" / "build_assets.py"
    if not assets.is_file():
        raise ValueError("源码缺少 tools/build_assets.py")
    # Generate from the requested source, not from this script's own checkout.
    if run([str(python), str(assets)], cwd=str(source),
           env=environment if environment is not None else clean_python_environment()) != 0:
        raise RuntimeError("前端资源生成失败")
    for name in COPY:
        src, dst = source / name, staging / name
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        else:
            shutil.copy2(src, dst)
    tools = staging / "tools"
    tools.mkdir()
    for name in ("preflight.py", "test_support.py"):
        shutil.copy2(source / "tools" / name, tools / name)


def stop_owned_process(process, profile=None):
    """Only the Popen handle created by this invocation is ever terminated."""
    if process.poll() is not None:
        return
    if profile is not None:
        (Path(profile) / 'stop.request').write_text('stop\n', encoding='ascii')
        try:
            process.wait(timeout=20)
            return
        except subprocess.TimeoutExpired:
            print('  [WARN] 测试 EXE 未正常退出，正在结束本次专属测试进程树')
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def unused_port(requested=0):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if os.name == "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("127.0.0.1", requested))
        return probe.getsockname()[1]


def smoke_environment(environment, profile):
    profile = Path(profile)
    result = dict(environment, QT_QPA_PLATFORM='offscreen',
                  LANSHARE_DATA_DIR=str(profile / 'chat_data'))
    if os.name == 'nt':
        result['LOCALAPPDATA'] = str(profile / 'local-app-data')
    return result


def smoke_exe(exe, staging, environment, requested_port):
    profile = staging / "_smoke"
    profile.mkdir()
    share = profile / "share"
    share.mkdir()
    run_environment = smoke_environment(environment, profile)
    extraction = None
    port = unused_port(requested_port)
    command = [str(exe), str(share), "-p", str(port), "--startup", "--test-profile", str(profile)]
    job = OwnedProcessJob()
    try:
        process = subprocess.Popen(command, env=run_environment, cwd=str(staging))
    except Exception:
        job.close()
        raise
    try:
        job.assign(process)
        base = "http://127.0.0.1:%d" % port
        ready = False
        for _ in range(60):
            if process.poll() is not None:
                raise RuntimeError("测试 EXE 提前退出：%s" % process.returncode)
            try:
                with urllib.request.urlopen(base + "/api/list?limit=1", timeout=1) as response:
                    data = json.load(response)
                if Path(data.get("path", "")).resolve() != share.resolve():
                    raise RuntimeError("端口不是本次测试共享目录，拒绝继续")
                ready = True
                break
            except (OSError, ValueError):
                time.sleep(0.5)
        if not ready:
            raise RuntimeError("测试 EXE 未能启动 HTTP 服务")
        if os.name == 'nt':
            runtime_base = Path(run_environment['LOCALAPPDATA']) / 'LanShare' / 'Temp'
            extracted = list(runtime_base.glob('_MEI*'))
            if len(extracted) != 1 or not extracted[0].is_dir():
                raise RuntimeError('EXE 未使用本次测试的独立临时解包目录')
            extraction = extracted[0]
        ok("EXE 独立测试实例 /api/list 200")
        for path, minimum in (("/static/app.css", 1000), ("/favicon.ico", 1000)):
            with urllib.request.urlopen(base + path, timeout=5) as response:
                data = response.read()
            if len(data) < minimum:
                raise RuntimeError("%s 资源异常：%d 字节" % (path, len(data)))
            ok("%s 200 (%d 字节)" % (path, len(data)))
    finally:
        try:
            stop_owned_process(process, profile)
        finally:
            # Onefile has a bootloader child; the private job prevents orphans.
            job.close()
        if extraction is not None and extraction.exists():
            raise RuntimeError('测试 EXE 退出后仍残留临时解包目录：%s' % extraction)


def qt_runtime_flags(python, environment):
    if os.name != 'nt':
        return []
    probe = subprocess.run(
        [str(python), '-c', 'import json; from PyQt5.QtCore import QLibraryInfo; '
         'print(json.dumps(QLibraryInfo.location(QLibraryInfo.BinariesPath)))'],
        capture_output=True, text=True, timeout=15, env=environment,
    )
    try:
        runtime = (Path(json.loads(probe.stdout)) / 'concrt140.dll').resolve(strict=True)
        venv = Path(python).resolve().parent.parent
        if probe.returncode or not runtime.is_file() or not within(runtime, venv):
            raise ValueError('Qt runtime is not inside the selected environment')
    except (OSError, TypeError, ValueError) as exc:
        raise RuntimeError('缺少独立 venv 内的 Qt VC 延迟运行时 concrt140.dll') from exc
    # PyInstaller scans direct imports but misses this MSVCP140 delay import.
    return ['--add-binary', str(runtime) + os.pathsep + 'PyQt5/Qt5/bin']


def image_plugin_flags(python, environment=None):
    probe = subprocess.run(
        [str(python), "-c", "import importlib.util,json; print(json.dumps([m for m in "
         "('pillow_heif','pillow_avif') if importlib.util.find_spec(m)]))"],
        capture_output=True, text=True,
        env=environment if environment is not None else clean_python_environment(),
    )
    if probe.returncode:
        raise RuntimeError("检查可选图片解码器失败")
    flags = []
    for module in json.loads(probe.stdout):
        flags.extend(("--collect-all", module))
    return flags


def pyinstaller_command(python, staging, environment):
    icon = Path(staging) / 'icon.ico'
    command = [str(python), '-m', 'PyInstaller', '--noconfirm', '--onefile', '--windowed',
               '--name', 'lanshare', '--icon', str(icon), '--hidden-import', 'webassets',
               '--hidden-import', 'segno', '--add-data', '%s%s.' % (icon, os.pathsep)]
    command.extend(runtime_tmpdir_flags())
    command.extend(qt_runtime_flags(python, environment))
    command.extend(image_plugin_flags(python, environment))
    command.append('lanshare.py')
    return command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(ROOT))
    parser.add_argument("--work", help="新的 ASCII 构建容器，或已有带归属标记的容器")
    parser.add_argument("--venv", help="复用已有虚拟环境；默认在本次构建目录创建")
    parser.add_argument("--base-python", default=sys.executable)
    parser.add_argument("--port", type=int, default=0, help="实测端口，默认自动选择空闲端口")
    parser.add_argument("--skip-test", action="store_true")
    parser.add_argument("--extra-images", action="store_true", help="在构建环境安装并打包 HEIC/HEIF 与 AVIF 解码器")
    args = parser.parse_args()

    try:
        source = Path(args.source).resolve(strict=True)
        base_python = Path(shutil.which(args.base_python) or args.base_python).resolve(strict=True)
        step("准备专属构建目录（保留所有旧构建）")
        staging = prepare_staging(source, args.work or default_work())
        ok("源码：%s" % source)
        ok("本次构建：%s" % staging)
        venv = Path(args.venv).resolve() if args.venv else staging / "env"
        if within(source, venv):
            raise ValueError("venv 不能覆盖源码目录或源码祖先")
        python = prepare_environment(base_python, venv, extra_images=args.extra_images)
        environment = build_environment(python, staging)

        step("重建前端资源并复制源码")
        snapshot_source(source, staging, base_python, environment)
        step("打包前自检")
        if run([str(python), str(staging / "tools" / "preflight.py")], cwd=str(staging),
               env=environment) != 0:
            raise RuntimeError("自检未通过")

        step("PyInstaller 打包")
        exe = staging / "dist" / ("lanshare.exe" if os.name == "nt" else "lanshare")
        command = pyinstaller_command(python, staging, environment)
        log = staging / "build.log"
        with log.open("w", encoding="utf-8", errors="replace") as output:
            result = subprocess.call(command, cwd=str(staging), env=environment,
                                     stdout=output, stderr=output)
        if result or not exe.is_file():
            print("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]))
            raise RuntimeError("PyInstaller 失败；完整日志：%s" % log)
        ok("生成 %s (%.1f MB)" % (exe, exe.stat().st_size / 1024 ** 2))
        verify_qt_archive(python, exe, environment)
        verify_runtime_tmpdir_archive(python, exe, environment)
        if not args.skip_test:
            step("独立 offscreen EXE 冒烟测试（不控制正式实例或系统集成）")
            smoke_exe(exe, staging, environment, args.port)
        print("\n完成：%s" % exe)
        print("本次构建目录和日志已保留，不会清理旧目录，也不会终止正式 LanShare。")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print("  [FAIL] %s" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
