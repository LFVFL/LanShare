"""Per-user Explorer entries. All writes are explicit and injectable for tests."""
import os
import subprocess
import sys
from pathlib import Path


START_CAPTION = "在此处启动网盘"
SWITCH_CAPTION = "切换到此目录"


def launch_parts():
    if getattr(sys, 'frozen', False):
        return [str(Path(sys.executable).resolve())]
    python = Path(sys.executable).resolve()
    pythonw = python.with_name('pythonw.exe')
    if python.name.lower() == 'python.exe' and pythonw.is_file():
        python = pythonw
    return [str(python), str(Path(sys.argv[0]).resolve())]


class ContextMenuIntegration:
    def __init__(self, key_name='FileShareTool', registry=None, command_parts=None, enabled=True):
        if registry is None and enabled and os.name == 'nt':
            import winreg
            registry = winreg
        self.registry = registry if enabled else None
        self.command_parts = command_parts if command_parts is not None else launch_parts()
        self.paths = (
            (r'Software\Classes\Directory\Background\shell' + '\\' + key_name, '%V'),
            (r'Software\Classes\Directory\shell' + '\\' + key_name, '%1'),
        )

    def is_installed(self):
        reg = self.registry
        if reg is None:
            return False
        try:
            for path, _placeholder in self.paths:
                with reg.OpenKey(reg.HKEY_CURRENT_USER, path + r'\command', 0, reg.KEY_READ):
                    pass
            return True
        except OSError:
            return False

    def install(self, running=False):
        reg = self.registry
        if reg is None:
            raise OSError('此功能仅支持 Windows 系统')
        prefix = subprocess.list2cmdline(self.command_parts)
        caption = SWITCH_CAPTION if running else START_CAPTION
        for path, placeholder in self.paths:
            with reg.CreateKey(reg.HKEY_CURRENT_USER, path) as key:
                reg.SetValueEx(key, '', 0, reg.REG_SZ, caption)
                reg.SetValueEx(key, 'Icon', 0, reg.REG_SZ, '"%s",0' % self.command_parts[0])
            with reg.CreateKey(reg.HKEY_CURRENT_USER, path + r'\command') as key:
                # The suffix also quotes a drive root safely ("C:\" is not a valid argv quote).
                command = '%s --open-here "%s\\."' % (prefix, placeholder)
                reg.SetValueEx(key, '', 0, reg.REG_SZ, command)

    def update_caption(self, running):
        reg = self.registry
        if not self.is_installed():
            return False
        caption = SWITCH_CAPTION if running else START_CAPTION
        for path, _placeholder in self.paths:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, path, 0, reg.KEY_SET_VALUE) as key:
                reg.SetValueEx(key, '', 0, reg.REG_SZ, caption)
        return True

    def remove(self):
        reg = self.registry
        if reg is None:
            return
        for path, _placeholder in self.paths:
            for subkey in (path + r'\command', path):
                try:
                    reg.DeleteKey(reg.HKEY_CURRENT_USER, subkey)
                except FileNotFoundError:
                    pass
