"""Build the separate 64-bit Windows 7 package with pinned legacy tooling.

    python tools/build_win7_exe.py
    python tools/build_win7_exe.py --work E:\\LanShare-builds

This wrapper intentionally requires Python 3.8 and PyInstaller 5.13.2. The
normal v7 builder remains on Python 3.12 and produces the current Windows
package; changing that build must not silently change Win7 support.
"""
import importlib.metadata
import platform
import sys

import build_exe


EXPECTED = {
    'PyInstaller': '5.13.2',
    'PyQt5': '5.15.9',
    'Pillow': '10.4.0',
    'pillow-heif': '0.18.0',
    'pillow-avif-plugin': '1.4.6',
    'brotli': '1.1.0',
    'segno': '1.6.1',
}


def main():
    if sys.version_info[:2] != (3, 8):
        print('Win7 包必须使用 Python 3.8；当前为 %s' % sys.version.split()[0])
        return 2
    if platform.architecture()[0] != '64bit':
        print('Win7 包当前要求 64 位 Python')
        return 2
    for package, expected in EXPECTED.items():
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            print('缺少 Win7 构建依赖：%s==%s' % (package, expected))
            return 2
        if actual != expected:
            print('Win7 构建依赖版本不匹配：%s 需要 %s，实际 %s' % (package, expected, actual))
            return 2
    print('Win7 构建：Python %s / PyInstaller %s / 64-bit' % (
        sys.version.split()[0], EXPECTED['PyInstaller']))
    return build_exe.main()


if __name__ == '__main__':
    raise SystemExit(main())
