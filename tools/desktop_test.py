# -*- coding: utf-8 -*-
"""桌面端界面回归（离屏，不需要显示器）。

检查的是"界面有没有搭对、状态切换对不对"，不是像素：
窗口能建、控件齐全、起停时按钮/胶囊/地址/二维码跟着变、日志按状态码着色。

    python tools\\desktop_test.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt5.QtWidgets import QApplication  # noqa: E402

import lanshare as j  # noqa: E402
from server import netinfo  # noqa: E402
from ui import qr, style  # noqa: E402

PASSED = []
FAILED = []


def check(label, ok, detail=""):
    if ok:
        PASSED.append(label)
        print("  %-42s ok" % label)
    else:
        FAILED.append((label, detail))
        print("  %-42s FAIL %s" % (label, detail))


def main():
    share = Path(tempfile.mkdtemp(dir=str(ROOT), prefix="lan6-desk-"))
    (share / "docs").mkdir()
    (share / "docs" / "a.txt").write_text("hi", encoding="utf-8")
    app = QApplication([])
    gui = j.FileShareGUI(auto_start=False)

    print("\n[1] 窗口与控件")
    check("窗口建出来了", gui.width() >= 820 and gui.height() >= 600,
          "%dx%d" % (gui.width(), gui.height()))
    for name in ("path_entry", "port_entry", "password_entry", "admin_password_entry",
                 "toggle_share_btn", "browser_btn", "menu_btn", "startup_btn",
                 "qr_label", "qr_combo", "log_text", "pill", "stat_label"):
        check("有 %s" % name, hasattr(gui, name))
    check("样式表已应用", len(gui.styleSheet()) > 500, len(gui.styleSheet()))

    print("\n[2] 初始（未启动）状态")
    check("按钮是「启动服务」", gui.toggle_share_btn.text() == "启动服务",
          gui.toggle_share_btn.text())
    check("胶囊是已停止", "已停止" in gui.pill.text(), gui.pill.text())
    check("胶囊是灰色调", gui.pill.property("tone") == "muted", gui.pill.property("tone"))
    check("浏览器按钮禁用", not gui.browser_btn.isEnabled())
    check("二维码区显示未启动", "未启动" in gui.qr_label.text(), gui.qr_label.text())
    check("地址框有占位提示", gui.addr_box.count() == 1, gui.addr_box.count())

    print("\n[3] 启动后：地址 / 二维码 / 状态")
    ok, result = gui.sharer.start(path=str(share), port=6445,
                                 password=None, admin_password=None)
    check("服务起来了", ok, result if not ok else "")
    gui.on_start_completed(True, result)
    urls = [result["local_url"]] + list(result["network_urls"])
    check("地址行数量对得上", gui.addr_box.count() == len(urls),
          "%d vs %d" % (gui.addr_box.count(), len(urls)))
    check("下拉框装了全部地址", gui.qr_combo.count() == len(urls), gui.qr_combo.count())
    check("默认选中推荐地址", gui.qr_combo.currentData() == result["primary_url"],
          "%s vs %s" % (gui.qr_combo.currentData(), result["primary_url"]))
    check("推荐地址不是 localhost", not str(result["primary_url"]).startswith("http://localhost"),
          result["primary_url"])
    pix = gui.qr_label.pixmap()
    check("二维码画出来了", pix is not None and not pix.isNull(),
          "无（可能没装 segno）")
    if not pix or pix.isNull():
        check("降级文案在位", "segno" in gui.qr_label.text(), gui.qr_label.text())
    check("按钮变成「停止服务」", gui.toggle_share_btn.text() == "停止服务",
          gui.toggle_share_btn.text())
    check("胶囊变绿", gui.pill.property("tone") == "ok", gui.pill.property("tone"))
    check("胶囊带端口", "6445" in gui.pill.text(), gui.pill.text())
    check("浏览器按钮可用", gui.browser_btn.isEnabled())
    check("运行中路径框仍可改（换目录立刻生效）", not gui.path_entry.isReadOnly())
    check("统计文案有地址数", "地址" in gui.stat_label.text(), gui.stat_label.text())

    print("\n[4] 切换二维码地址")
    if gui.qr_combo.count() > 1:
        gui.qr_combo.setCurrentIndex(1)
        check("切换后仍是有效二维码",
              gui.qr_label.pixmap() is None or not gui.qr_label.pixmap().isNull())
        check("切换后数据跟着变",
              gui.qr_combo.currentData() == gui.qr_combo.itemData(1),
              gui.qr_combo.currentData())
        check("第 1 项不是推荐那条", gui.qr_combo.itemData(1) != result["primary_url"],
              gui.qr_combo.itemData(1))
    else:
        check("只有一个地址时可切换项为 0", gui.qr_combo.count() == 1)

    print("\n[5] 日志着色")
    for status, tone in ((200, "ok"), (302, "info"), (404, "warn"), (500, "err"),
                         ("Not Found", "muted"), (None, "muted")):
        check("%s → %s" % (status, tone), gui._tone_of(status) == tone, gui._tone_of(status))
    gui.log_queue.put(("10:00:00", "127.0.0.1", "下载文件 | 文件: a.txt", 200))
    gui.log_queue.put(("10:00:01", "127.0.0.1", "删除文件 | 文件: b.txt", 403))
    gui.update_log()
    text = gui.log_text.toPlainText()
    check("两条日志都写进去了", "a.txt" in text and "b.txt" in text, text[:60])
    colors = set()
    block = gui.log_text.document().begin()
    while block.isValid():
        for fragment in range(block.layout().formats().__len__() if block.layout() else 0):
            pass
        block = block.next()
    check("颜色表里有 5 档", len(style.tone_colors("light")) == 5,
          len(style.tone_colors("light")))
    check("暗色也有 5 档", len(style.tone_colors("dark")) == 5,
          len(style.tone_colors("dark")))

    print("\n[6] 运行中切换共享目录（不重启服务）")
    other = Path(tempfile.mkdtemp(dir=str(ROOT), prefix="lan6-desk-b-"))
    (other / "another.txt").write_text("b", encoding="utf-8")
    before = gui.path_entry.text()
    gui.apply_share_path(str(other))
    check("输入框跟着变", gui.path_entry.text() == str(other), gui.path_entry.text())
    check("服务端的共享路径也变了",
          Path(gui.sharer.shared_path) == other.resolve(), gui.sharer.shared_path)
    check("还在运行（没有重启服务）", gui.sharer.is_running)
    gui.apply_share_path(str(share))
    check("能切回去", Path(gui.sharer.shared_path) == share.resolve(), gui.sharer.shared_path)
    check("路径框运行中也可编辑", not gui.path_entry.isReadOnly())
    check("浏览按钮运行中可用", gui.select_path_btn.isEnabled())
    shutil.rmtree(other, ignore_errors=True)
    check("原路径还在", bool(before), before)

    print("\n[7] 停止后回到初始态")
    gui.stop_share()
    check("按钮回到「启动服务」", gui.toggle_share_btn.text() == "启动服务",
          gui.toggle_share_btn.text())
    check("胶囊回到已停止", gui.pill.property("tone") == "muted", gui.pill.property("tone"))
    check("地址被清空成占位", gui.addr_box.count() == 1, gui.addr_box.count())
    check("二维码回到未启动", "未启动" in gui.qr_label.text(), gui.qr_label.text())
    check("路径框可编辑", not gui.path_entry.isReadOnly())

    print("\n[7] 网卡地址排序（二维码到底指向谁）")
    ranked = netinfo.rank_ips(["198.18.0.1", "192.168.1.9", "100.72.4.2",
                               "10.2.3.4", "169.254.3.9"])
    check("192.168 排第一", ranked[0] == "192.168.1.9", ranked)
    check("代理 TUN 排最后", ranked[-1] in ("169.254.3.9", "198.18.0.1"), ranked)
    check("198.18 被判为虚拟", netinfo.is_virtual("198.18.0.1"))
    check("100.64/10 被判为虚拟", netinfo.is_virtual("100.72.4.2"))
    check("192.168 是私网", netinfo.is_private("192.168.1.9"))
    check("域名服务器不是私网", not netinfo.is_private("8.8.8.8"))
    check("首选地址有「推荐」标签",
          netinfo.ip_label(ranked[0], ranked[0]) == "推荐",
          netinfo.ip_label(ranked[0], ranked[0]))

    print("\n[8] 端口输入框")
    from PyQt5.QtWidgets import QSpinBox
    check("端口是 QSpinBox", isinstance(gui.port_entry, QSpinBox),
          type(gui.port_entry).__name__)
    check("范围 1-65535", gui.port_entry.minimum() == 1 and gui.port_entry.maximum() == 65535,
          (gui.port_entry.minimum(), gui.port_entry.maximum()))
    gui.port_entry.setValue(70000)
    check("超范围被夹住", gui.port_entry.value() == 65535, gui.port_entry.value())
    gui.port_entry.setValue(0)
    check("0 被夹到 1", gui.port_entry.value() == 1, gui.port_entry.value())
    gui.port_entry.setValue(5995)

    print("\n[9] 外观：配色 / 明暗 / 字号")
    original = (gui.palette, gui.mode, gui.size)
    check("外观按钮在", hasattr(gui, "theme_btn"), "")
    check("6 套配色都在菜单里", len(gui._palette_actions) == 6,
          len(gui._palette_actions))
    check("明暗三项（含跟随系统）", len(gui._mode_actions) == 3, len(gui._mode_actions))
    check("字号三档", len(gui._size_actions) == 3, len(gui._size_actions))

    for name, pal in style.PALETTES.items():
        qss = style.build_qss(name, "light", "大")
        check("配色 %s 能生成样式" % name, pal["brand"] in qss, name)

    gui.set_theme(palette="rose", mode="dark", size="特大")
    qss = gui.styleSheet()
    check("切暗色后背景变深", style.MODES["dark"]["bg"] in qss, "")
    check("切配色后主色变了", style.PALETTES["rose"]["brand"] in qss, "")
    check("字号档位生效", "%dpx" % style.px("label", "特大") in qss,
          style.px("label", "特大"))
    check("暗色日志色也换了",
          gui._log_format("ok").foreground().color().name()
          == style.tone_colors("dark")["ok"],
          gui._log_format("ok").foreground().color().name())
    gui.set_theme(*original)
    check("能切回原样", gui.styleSheet() == style.build_qss(*original), "")

    print("\n[10] 跟随系统暗色（auto）")
    check("明暗菜单有三项", len(gui._mode_actions) == 3, len(gui._mode_actions))
    check("菜单里有「跟随系统」", "auto" in gui._mode_actions,
          list(gui._mode_actions))
    real_is_dark = style.system_is_dark
    try:
        style.system_is_dark = lambda: True
        gui.set_theme(mode="auto")
        check("系统是暗色时 auto 变暗", style.MODES["dark"]["bg"] in gui.styleSheet(), "")
        check("auto 的日志色跟着暗色",
              gui._log_format("ok").foreground().color().name()
              == style.TONE_TONES["dark"]["ok"],
              gui._log_format("ok").foreground().color().name())
        style.system_is_dark = lambda: False
        gui.set_theme(mode="auto")
        check("系统是浅色时 auto 变浅", style.MODES["light"]["bg"] in gui.styleSheet(), "")
    finally:
        style.system_is_dark = real_is_dark
    gui.set_theme(*original)

    print("\n[11] 日志行数上限")
    gui.log_queue = __import__("queue").Queue()
    for i in range(2100):
        gui.log_queue.put(("10:00:%02d" % (i % 60), "1.1.1.1", "第 %d 条" % i, 200))
    gui.update_log()
    blocks = gui.log_text.document().blockCount()
    check("日志被截到 2000 行以内", blocks <= gui.LOG_MAX_LINES + 1, blocks)
    check("留下的是最新的", "第 2099 条" in gui.log_text.toPlainText(), "")

    gui.close()
    app.quit()
    shutil.rmtree(share, ignore_errors=True)

    print("\n结果: 通过 %d 项" % len(PASSED))
    if FAILED:
        print("失败 %d 项" % len(FAILED))
        for label, detail in FAILED:
            print("  - (%r, %r)" % (label, detail))
        return 1
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
