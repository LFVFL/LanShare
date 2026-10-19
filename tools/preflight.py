"""打包前自检：在**要打包的那个解释器**里跑一遍。

    .venv\\Scripts\\python.exe tools\\preflight.py
    python tools\\preflight.py                  :: 只检查当前解释器

检查项失败会直接告诉你缺什么、怎么补，不用等 Nuitka 跑到一半才炸。
"""

import importlib
import os
import platform
import shutil
import sys
try:                      # Windows 控制台默认 GBK，打印 ✓ 之类的字符会崩
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OK, WARN, BAD = "  [OK]  ", "  [WARN]", "  [FAIL]"
problems = []


def line(flag, text):
    print(flag, text)
    if flag is BAD:
        problems.append(text)


def check_python():
    print("\n[1] 解释器")
    line(OK, "%s %s (%d bit)" % (sys.implementation.name, platform.python_version(),
                                 64 if sys.maxsize > 2 ** 32 else 32))
    if sys.version_info < (3, 8):
        line(BAD, "Python 版本太低，需要 3.8+")
    if sys.maxsize <= 2 ** 32:
        line(WARN, "32 位解释器打包出来只能跑 32 位")


def check_qt():
    print("\n[2] PyQt5 / Qt 插件")
    # Adobe 之类软件会往环境里塞 QT_PLUGIN_PATH，Nuitka 会因此找不到 PyQt5 插件
    for var in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        val = os.environ.get(var)
        if val:
            line(WARN, "%s=%s（打包时建议清空，否则可能报 pyqt5 插件检测失败）" % (var, val))
    try:
        import PyQt5
        base = Path(PyQt5.__file__).parent
        plugins = base / "Qt5" / "plugins"
        line(OK, "PyQt5 在 %s" % base)
        if plugins.is_dir():
            line(OK, "自带 Qt 插件目录 %s" % plugins)
        else:
            line(WARN, "没找到 Qt5/plugins，运行时可能缺 qwindows.dll")
        os.environ.pop("QT_PLUGIN_PATH", None)
        os.environ.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        # 明确指过去：不然 PyQt5 找不到自己的 platforms 目录
        for cand in (plugins / "platforms", base / "Qt5" / "bin" / "platforms"):
            if (cand / "qwindows.dll").exists() or (cand / "qoffscreen.dll").exists():
                os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(cand)
                line(OK, "platforms 插件目录 %s" % cand)
                break
        from PyQt5.QtWidgets import QApplication
        app = QApplication([])          # noqa: F841  能建起来就说明插件对
        line(OK, "QApplication 可以创建（offscreen）")
    except Exception as e:
        line(BAD, "PyQt5 不可用: %r" % (e,))


def check_modules():
    print("\n[3] 项目模块")
    sys.path.insert(0, str(ROOT))
    for name in ("webassets", "server.config", "server.handler", "server.runner",
                 "ui.main_window", "ui.icon"):
        try:
            importlib.import_module(name)
            line(OK, "import %s" % name)
        except Exception as e:
            line(BAD, "import %s 失败: %r" % (name, e))
    try:
        from server.pages import ASSET_VERSION
        line(OK, "前端资源版本 %s（页面里 ?v= 应该一致）" % ASSET_VERSION)
    except Exception as e:
        line(BAD, "取不到 ASSET_VERSION: %r" % (e,))
    check_method_signatures()


def check_method_signatures():
    """类里的方法第一个参数必须是 self/cls，除非显式 @staticmethod。

    这个坑踩过两次：`log_message` 多了个 @staticmethod、
    `AuthMixin._hostname` 少了 @staticmethod，两次都只在特定路径下 500，
    跑测试也发现不了（异常被兜底吞掉 / 现代浏览器走不到那段）。
    """
    import ast

    bad = []
    for path in sorted(list((ROOT / "server").glob("*.py")) + list((ROOT / "ui").glob("*.py"))):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            bad.append("%s 语法错误: %s" % (path.name, exc))
            continue
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            for fn in cls.body:
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                names = [getattr(d, "id", "") for d in fn.decorator_list]
                if "staticmethod" in names or "classmethod" in names:
                    continue
                args = [a.arg for a in fn.args.args]
                if not args or args[0] not in ("self", "cls"):
                    bad.append("%s:%d %s.%s(%s)" % (path.name, fn.lineno, cls.name,
                                                    fn.name, ", ".join(args)))
    if bad:
        for item in bad:
            line(BAD, "方法签名不对（缺 self 或漏了 @staticmethod）：" + item)
    else:
        line(OK, "类方法签名都带 self/@staticmethod")


def check_optional():
    print("\n[4] 可选依赖（缺了也能跑，只是少点能力）")
    for mod, why in (("brotli", "静态资源用 br 压缩（比 gzip 再省约 6%）"),
                     ("segno", "桌面端生成二维码"),
                     ("PIL", "服务端补生成聊天图片缩略图")):
        try:
            m = importlib.import_module(mod)
            ver = getattr(m, "__version__", "?")
            line(OK, "%s %s —— %s" % (mod, ver, why))
        except ImportError:
            line(WARN, "%s 没装 —— %s 会退化" % (mod, why))


def check_assets():
    print("\n[5] 构建产物")
    wa = ROOT / "webassets.py"
    if wa.exists():
        line(OK, "webassets.py %.1f KB" % (wa.stat().st_size / 1024))
    else:
        line(BAD, "缺 webassets.py，先跑 tools/build_assets.py")
    # web/js 只有想重新打包前端时才需要，打包 exe 靠的是 webassets.py
    js_dir = ROOT / "web" / "js"
    if js_dir.is_dir():
        frags = sorted(js_dir.glob("*.js"))
        line(OK, "前端分片 %d 个" % len(frags))
        check_module_isolation(frags)
    else:
        line(WARN, "没有 web/js/（不影响打包 exe，只影响重新生成 webassets）")
    if (ROOT / "web" / "app.js").exists():
        line(WARN, "web/app.js 还在，可能忘记删了（现在用 web/js/）")


def check_module_isolation(frags):
    """每个分片必须自己包成 IIFE，且不能在顶层（2 空格缩进）声明任何东西——
    一旦漏在顶层，就会退回"共享闭包"，分片之间又能偷偷互相调用了。"""
    head = "(function (Lan) {"
    tail = "})(window.Lan = window.Lan || {});"
    leak = []
    for f in frags:
        text = f.read_text(encoding="utf-8")
        if head not in text or not text.rstrip().endswith(tail):
            leak.append(f.name + " 没有独立 IIFE")
            continue
        body = text.split(head, 1)[1].rsplit(tail, 1)[0]
        for no, ln in enumerate(body.splitlines(), 1):
            if ln.startswith("  ") and not ln.startswith("   ") and ln.strip() \
                    and not ln.lstrip().startswith(("/*", "*", "//")):
                leak.append("%s:%d 顶层声明泄漏" % (f.name, no))
    if leak:
        for item in leak:
            line(BAD, item)
    else:
        line(OK, "分片互不共享闭包（各自 IIFE，只走 window.Lan）")
    check_undeclared_calls(frags)


# 分片里可以直接用的浏览器全局（其余裸标识符必须来自文件头的 `var x = Lan.y.x`）
_JS_GLOBALS = {
    'document', 'window', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval',
    'fetch', 'JSON', 'Object', 'Array', 'String', 'Number', 'Math', 'Date', 'Promise',
    'encodeURIComponent', 'decodeURIComponent', 'encodeURI', 'decodeURI', 'parseInt',
    'parseFloat', 'isNaN', 'alert', 'confirm', 'console', 'Set', 'Map', 'WeakMap',
    'URL', 'URLSearchParams', 'FileReader', 'Image', 'Event', 'CustomEvent', 'FormData',
    'Blob', 'File', 'localStorage', 'sessionStorage', 'navigator', 'location', 'history',
    'requestAnimationFrame', 'cancelAnimationFrame', 'Intl', 'RegExp', 'Error', 'Boolean',
    'Symbol', 'structuredClone', 'btoa', 'atob', 'getComputedStyle', 'matchMedia',
    'crypto', 'queueMicrotask', 'TextEncoder', 'TextDecoder', 'AbortController',
    'EventSource', 'WebSocket', 'MutationObserver', 'IntersectionObserver',
    'ResizeObserver', 'Element', 'Node', 'NodeList', 'HTMLElement', 'Audio',
    'performance', 'screen', 'devicePixelRatio', 'Lan',
}
_JS_KEYWORDS = {
    'if', 'for', 'while', 'switch', 'catch', 'return', 'typeof', 'function', 'new',
    'do', 'else', 'try', 'finally', 'delete', 'void', 'in', 'of', 'instanceof',
    'throw', 'case', 'yield', 'await', 'async', 'with', 'super',
}


def check_undeclared_calls(frags):
    """分片里调用的裸函数名，必须是本地声明的、浏览器全局、或文件头从 Lan 取的别名。

    这条是补课：`60-upload.js` 里调了 `openMenu(...)` 却没写
    `var openMenu = Lan.ui.openMenu;`，模块化之后一上传就
    ReferenceError——按钮点了完全没反应，而且只有真去点才暴露。
    """
    import re

    call = re.compile(r'(?<![\w.$])([A-Za-z_$][\w$]*)\s*\(')
    decl = re.compile(r'(?:^|[;{}\n])\s*(?:var|let|const|function)\s+([A-Za-z_$][\w$]*)')
    missing = []
    for f in frags:
        text = f.read_text(encoding="utf-8")
        declared = set(decl.findall(text))
        declared |= set(re.findall(r'function\s+([A-Za-z_$][\w$]*)', text))
        declared |= set(re.findall(r'([A-Za-z_$][\w$]*)\s*[:=]\s*function', text))
        params = set()
        for group in re.findall(r'function\s*[A-Za-z_$\w]*\s*\(([^)]*)\)', text):
            for part in group.split(','):
                name = part.strip().split('=')[0].strip()
                if re.fullmatch(r'[A-Za-z_$][\w$]*', name or ''):
                    params.add(name)
        used = set(call.findall(text)) - _JS_KEYWORDS
        for name in sorted(used - declared - _JS_GLOBALS - params):
            missing.append("%s 调用了未声明的 %s()（漏了 Lan.* 别名？）" % (f.name, name))
    if missing:
        for item in missing:
            line(BAD, item)
    else:
        line(OK, '分片里没有「用了但没声明」的函数调用')


def check_disk():
    print("\n[6] 磁盘（打包会临时占几百 MB）")
    out = Path(os.environ.get("BUILD_OUT", str(ROOT)))
    free = shutil.disk_usage(str(out)).free / (1024 ** 3)
    line(OK if free > 2 else BAD, "%s 剩余 %.1f GB" % (out.drive or out, free))


def main():
    print("打包前自检 —— 解释器: %s" % sys.executable)
    check_python()
    check_qt()
    check_modules()
    check_optional()
    check_assets()
    check_disk()
    print("\n结果:", "可以打包 ✓" if not problems else "有 %d 项要处理" % len(problems))
    for p in problems:
        print("  -", p)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
