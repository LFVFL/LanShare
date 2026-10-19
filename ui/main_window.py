"""主窗口：共享设置、二维码、日志、托盘。"""
import base64
import json
import os
import platform
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QPoint, QSettings, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (QColor, QFont, QIcon, QImage, QPainter, QPalette,
                         QPixmap, QTextCharFormat, QTextCursor)
from PyQt5.QtWidgets import (
    QAction, QApplication, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPushButton, QSpinBox, QSplitter, QStyle, QSystemTrayIcon, QTextEdit,
    QToolButton, QVBoxLayout, QWidget,
)

from server.config import *
from server.paths import *
from server.storage import *
from server.chat_store import *
from server.pages import *
from server.runner import FileSharer
from server import netinfo

from . import qr, style
from .icon import build_app_icon, get_app_icon
from .widgets import ClickableLabel

if platform.system() == 'Windows':
    try:
        import winreg
    except ImportError:
        winreg = None
else:
    winreg = None


class FileShareGUI(QMainWindow):
    status_updated = pyqtSignal(str, bool)
    start_completed = pyqtSignal(bool, object)
    system_checks_ready = pyqtSignal(bool, bool)   # (开机自启, 右键菜单)

    def __init__(self, initial_path=None, initial_port=None, auto_start=True):
        super().__init__()

        self.setWindowTitle("局域网云盘 v6.1")
        self.set_window_icon()
        self.settings = QSettings("FileShareTool", "App")
        self.sharer = FileSharer()
        self.start_result = None
        self.initial_path = initial_path
        self.initial_port = initial_port
        self.auto_start = auto_start
        self.reg_key_name = "FileShareTool"
        self.startup_reg_key_name = "FileShareToolStartup"
        self.startup_task_name = "FileShareToolStartup"
        self.log_queue = None
        self.log_timer = QTimer(self)
        self.setAcceptDrops(True)
        self.qr_items = []
        self.qr_primary = None
        self.selected_qr_index = None
        # 这两个状态要跑 schtasks / 读注册表，启动时先留空，后台查完再回填
        self._startup_enabled = None
        self._menu_installed = None

        # 外观（配色 / 明暗 / 字号）先读出来，建控件前就要用
        self.palette = self.settings.value("theme/palette", style.DEFAULT_PALETTE)
        self.mode = self.settings.value("theme/mode", style.DEFAULT_MODE)
        self.size = self.settings.value("theme/size", style.DEFAULT_SIZE)
        if self.palette not in style.PALETTES:
            self.palette = style.DEFAULT_PALETTE
        if self.mode not in ("light", "dark", "auto"):
            self.mode = style.DEFAULT_MODE
        if self.size not in style.SIZES:
            self.size = style.DEFAULT_SIZE

        self._tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        self.apply_modern_style()
        self.setup_tray_icon()
        self.create_widgets()
        self.connect_signals()
        self.load_settings()
        self._apply_running_state(False)
        self.update_menu_btn_text()
        self.update_startup_btn_text()
        self.update_status("已停止", False)
        # 窗口先显示出来，再在后台查那两个系统开关（schtasks 要几百毫秒）
        self.refresh_system_checks()
        if self.auto_start:
            QTimer.singleShot(100, self.auto_start_share)

    def apply_modern_style(self):
        self.setStyleSheet(style.build_qss(self.palette, self.mode, self.size))

    def set_theme(self, palette=None, mode=None, size=None):
        """换配色/明暗/字号：重新算 QSS，日志颜色跟着换（已写入的旧行不重绘）。"""
        if palette:
            self.palette = palette
        if mode:
            self.mode = mode
        if size:
            self.size = size
        self.settings.setValue("theme/palette", self.palette)
        self.settings.setValue("theme/mode", self.mode)
        self.settings.setValue("theme/size", self.size)
        self.apply_modern_style()
        mode_text = {"auto": "跟随系统", "dark": "暗色"}.get(self.mode, "明亮")
        self._flash("外观已更新：%s · %s · 字号%s"
                    % (style.PALETTES[self.palette]["name"], mode_text, self.size))

    def set_window_icon(self):
        icon = get_app_icon()

        if icon and not icon.isNull():
            self.setWindowIcon(icon)
            self._window_qicon = icon
        else:
            fallback = self.style().standardIcon(QStyle.SP_ComputerIcon)
            self.setWindowIcon(fallback)
            self._window_qicon = fallback

    def setup_tray_icon(self):
        icon = get_app_icon()

        self.tray_icon = QSystemTrayIcon(self)

        if icon and not icon.isNull():
            self._tray_qicon = icon
        else:
            self._tray_qicon = self.style().standardIcon(QStyle.SP_ComputerIcon)

        self.tray_icon.setIcon(self._tray_qicon)
        self.setWindowIcon(self._tray_qicon)

        tray_menu = QMenu()

        show_action = QAction("显示窗口", self)
        show_action.triggered.connect(self.show_window)
        tray_menu.addAction(show_action)

        toggle_action = QAction("启动/停止", self)
        toggle_action.triggered.connect(self.toggle_share)
        tray_menu.addAction(toggle_action)

        tray_menu.addSeparator()

        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit_application)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.tray_icon_activated)
        self.tray_icon.setToolTip("局域网云盘")

        if self._tray_available:
            self.tray_icon.show()

    def show_window(self):
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def tray_icon_activated(self, reason):
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self.show_window()

    def quit_application(self):
        self.save_settings()
        if self.sharer.is_running:
            self.sharer.stop()
            time.sleep(0.2)
        chat_room_saver.flush()
        self.tray_icon.hide()
        QApplication.quit()

    def closeEvent(self, event):
        self.save_settings()
        if self.sharer.is_running and self._tray_available:
            event.ignore()
            self.hide()
            self.tray_icon.showMessage(
                "局域网云盘", "服务在后台继续运行，双击托盘图标可恢复窗口",
                QSystemTrayIcon.Information, 2500
            )
        else:
            chat_room_saver.flush()
            self.tray_icon.hide()
            event.accept()

    # ── 界面小工具 ────────────────────────────────────────────────

    def _card(self, title=None, tools=None):
        """白底圆角卡片，返回 (卡片, 竖向布局)。"""
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 13, 16, 14)
        lay.setSpacing(9)
        if title:
            head = QHBoxLayout()
            head.setSpacing(8)
            label = QLabel(title)
            label.setObjectName("cardTitle")
            head.addWidget(label)
            head.addStretch(1)
            for widget in tools or ():
                head.addWidget(widget)
            lay.addLayout(head)
        return card, lay

    def _label(self, text, width=0):
        """行首小标签。宽度按当前字号算，字号调大不会把字挤掉。"""
        label = QLabel(text)
        unit = style.px("label", self.size)
        # 4 个汉字（"共享目录"/"访问密码"）宽度打底，保证几行左边对齐
        label.setFixedWidth(max(width, int(unit * 4 * 1.06) + 8))
        label.setObjectName("muted")
        return label

    def _tiny_btn(self, text, slot=None):
        btn = QPushButton(text)
        btn.setObjectName("tiny")
        btn.setCursor(Qt.PointingHandCursor)
        if slot:
            btn.clicked.connect(slot)
        return btn

    def set_pill(self, text, tone="muted"):
        """顶部状态胶囊：tone = ok / warn / err / muted"""
        self.pill.setText(text)
        self.pill.setProperty("tone", tone)
        self.pill.style().unpolish(self.pill)
        self.pill.style().polish(self.pill)

    def _flash(self, text):
        """底部提示闪一下（复制成功之类的即时反馈）。"""
        self.hint_label.setText(text)
        QTimer.singleShot(1800, lambda: self.hint_label.setText(self._hint_default))

    # ── 界面搭建 ──────────────────────────────────────────────────

    def create_widgets(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 16, 18, 14)
        outer.setSpacing(11)

        outer.addLayout(self._build_header())
        outer.addWidget(self._build_settings())
        outer.addLayout(self._build_middle())
        outer.addWidget(self._build_log(), 1)
        outer.addLayout(self._build_footer())

        self.resize(1020, 900)
        self.setMinimumSize(900, 720)

    def _build_header(self):
        bar = QHBoxLayout()
        bar.setSpacing(10)

        logo = QLabel()
        icon = get_app_icon()
        if icon and not icon.isNull():
            logo.setPixmap(icon.pixmap(26, 26))
        bar.addWidget(logo)

        brand = QLabel("局域网云盘")
        brand.setObjectName("brand")
        bar.addWidget(brand)

        ver = QLabel("v6.1")
        ver.setObjectName("ver")
        bar.addWidget(ver)
        bar.addSpacing(6)

        self.pill = QLabel("● 已停止")
        self.pill.setObjectName("pill")
        self.pill.setProperty("tone", "muted")
        bar.addWidget(self.pill)
        bar.addStretch(1)

        bar.addWidget(self._build_theme_button())

        self.browser_btn = QPushButton("打开浏览器")
        self.browser_btn.setObjectName("ghost")
        self.browser_btn.setEnabled(False)
        bar.addWidget(self.browser_btn)

        self.toggle_share_btn = QPushButton("启动服务")
        self.toggle_share_btn.setObjectName("primary")
        self.toggle_share_btn.setMinimumWidth(108)
        bar.addWidget(self.toggle_share_btn)
        return bar

    def _build_theme_button(self):
        """右上角「外观」：6 套配色 / 明暗 / 字号。"""
        btn = QToolButton()
        btn.setText("外观 ▾")
        btn.setPopupMode(QToolButton.InstantPopup)
        btn.setCursor(Qt.PointingHandCursor)

        menu = QMenu(btn)

        palette_menu = menu.addMenu("配色")
        self._palette_actions = {}
        for key, pal in style.PALETTES.items():
            act = QAction(pal["name"], self, checkable=True)
            act.setChecked(key == self.palette)
            act.triggered.connect(lambda _=False, k=key: self._pick_palette(k))
            palette_menu.addAction(act)
            self._palette_actions[key] = act

        mode_menu = menu.addMenu("明暗")
        self._mode_actions = {}
        for key, label in (("light", "明亮"), ("dark", "暗色"),
                           ("auto", "跟随系统")):
            act = QAction(label, self, checkable=True)
            act.setChecked(key == self.mode)
            act.triggered.connect(lambda _=False, k=key: self._pick_mode(k))
            mode_menu.addAction(act)
            self._mode_actions[key] = act

        size_menu = menu.addMenu("字号")
        self._size_actions = {}
        for key, factor in style.SIZES.items():
            act = QAction("%s（%dpx 正文）" % (key, int(round(style.BASE_PX["label"] * factor))),
                          self, checkable=True)
            act.setChecked(key == self.size)
            act.triggered.connect(lambda _=False, k=key: self._pick_size(k))
            size_menu.addAction(act)
            self._size_actions[key] = act

        btn.setMenu(menu)
        self.theme_btn = btn
        return btn

    def _pick_palette(self, key):
        self.set_theme(palette=key)
        self._sync_theme_checks()

    def _pick_mode(self, key):
        self.set_theme(mode=key)
        self._sync_theme_checks()

    def _pick_size(self, key):
        self.set_theme(size=key)
        self._sync_theme_checks()

    def _sync_theme_checks(self):
        for key, act in self._palette_actions.items():
            act.setChecked(key == self.palette)
        for key, act in self._mode_actions.items():
            act.setChecked(key == self.mode)
        for key, act in self._size_actions.items():
            act.setChecked(key == self.size)

    def _build_settings(self):
        card, lay = self._card("共享设置")
        lay.addLayout(self._row_path())
        lay.addLayout(self._row_ports())
        return card

    def _row_path(self):
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._label("共享目录", 56))
        self.path_entry = QLineEdit()
        self.path_entry.setPlaceholderText("选择要共享的文件夹…")
        row.addWidget(self.path_entry, 1)
        self.select_path_btn = QPushButton("浏览…")
        self.select_path_btn.setObjectName("ghost")
        self.select_path_btn.clicked.connect(self.browse_path)
        row.addWidget(self.select_path_btn)
        return row

    def _row_ports(self):
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._label("端口", 56))
        self.port_entry = QSpinBox()
        self.port_entry.setRange(1, 65535)
        self.port_entry.setValue(DEFAULT_PORT)
        self.port_entry.setFixedWidth(96)
        self.port_entry.setButtonSymbols(QSpinBox.NoButtons)
        self.port_entry.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        row.addWidget(self.port_entry)
        row.addSpacing(12)
        row.addWidget(self._label("访问密码", 62))
        self.password_entry = QLineEdit()
        self.password_entry.setPlaceholderText("留空则免密码")
        self.password_entry.setEchoMode(QLineEdit.Password)
        row.addWidget(self.password_entry, 1)
        row.addSpacing(12)
        row.addWidget(self._label("管理密码", 62))
        self.admin_password_entry = QLineEdit()
        self.admin_password_entry.setPlaceholderText("上传/删除时校验，留空则免")
        self.admin_password_entry.setEchoMode(QLineEdit.Password)
        row.addWidget(self.admin_password_entry, 1)
        return row

    def _build_middle(self):
        row = QHBoxLayout()
        row.setSpacing(11)

        addr_card, addr_lay = self._card("访问方式")
        self.addr_box = QVBoxLayout()
        self.addr_box.setSpacing(6)
        addr_lay.addLayout(self.addr_box)
        addr_lay.addStretch(1)
        self.stat_label = QLabel("服务未启动")
        self.stat_label.setObjectName("muted")
        addr_lay.addWidget(self.stat_label)
        row.addWidget(addr_card, 1)

        row.addWidget(self._build_qr())
        return row

    def _build_qr(self):
        card, lay = self._card("手机扫码")
        card.setFixedWidth(252)

        self.qr_label = QLabel("服务未启动")
        self.qr_label.setObjectName("muted")
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setFixedSize(152, 152)
        self.qr_label.setCursor(Qt.PointingHandCursor)
        self.qr_label.setToolTip("双击放大二维码")
        self.qr_label.mouseDoubleClickEvent = lambda _e: self._zoom_qr()
        lay.addWidget(self.qr_label, 0, Qt.AlignHCenter)

        self.qr_combo = QComboBox()
        self.qr_combo.setMinimumWidth(214)
        self.qr_combo.setToolTip("扫码后要打开的地址：默认是路由器那条局域网")
        self.qr_combo.setEnabled(False)
        self.qr_combo.currentIndexChanged.connect(self._on_qr_addr_changed)
        lay.addWidget(self.qr_combo)

        self.qr_url_label = QLabel("")
        self.qr_url_label.setObjectName("muted")
        self.qr_url_label.setAlignment(Qt.AlignCenter)
        self.qr_url_label.setWordWrap(True)
        self.qr_url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.qr_url_label)

        self.qr_hint = QLabel("扫码打开　·　双击放大")
        self.qr_hint.setObjectName("muted")
        self.qr_hint.setAlignment(Qt.AlignCenter)
        self.qr_hint.setWordWrap(True)
        lay.addWidget(self.qr_hint)
        lay.addStretch(1)
        return card

    def _zoom_qr(self):
        """双击缩略码 → 弹出大图，手机举远一点也能扫。"""
        url = self.qr_combo.currentData() or self.qr_primary
        if not url:
            self._flash("先启动服务，才有可扫的地址")
            return
        self.show_qr_code(url)

    def _build_log(self):
        self.log_pause_btn = self._tiny_btn("暂停", self.toggle_log_pause)
        self.log_clear_btn = self._tiny_btn("清空", self.clear_log)
        self.log_export_btn = self._tiny_btn("导出…", self.export_log)

        card, lay = self._card("访问日志", [self.log_pause_btn,
                                            self.log_clear_btn,
                                            self.log_export_btn])
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(200)
        lay.addWidget(self.log_text, 1)
        return card

    def _build_footer(self):
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._hint_default = "提示：可以先把文件夹拖进窗口"
        self.hint_label = QLabel(self._hint_default)
        self.hint_label.setObjectName("muted")
        bar.addWidget(self.hint_label)
        bar.addStretch(1)

        self.menu_btn = QPushButton("添加到右键菜单")
        self.menu_btn.setObjectName("ghost")
        bar.addWidget(self.menu_btn)

        self.startup_btn = QPushButton("开机自启")
        self.startup_btn.setObjectName("ghost")
        bar.addWidget(self.startup_btn)
        return bar

    def connect_signals(self):
        self.toggle_share_btn.clicked.connect(self.toggle_share)
        self.browser_btn.clicked.connect(self.open_browser)
        self.menu_btn.clicked.connect(self.toggle_context_menu_integration)
        self.startup_btn.clicked.connect(self.toggle_startup)
        self.sharer.set_status_callback(self.update_status)
        self.status_updated.connect(self.on_status_updated)
        self.start_completed.connect(self.on_start_completed)
        self.system_checks_ready.connect(self.on_system_checks_ready)
        self.log_timer.timeout.connect(self.update_log)

    # ── 地址列表与二维码 ──────────────────────────────────────────

    def _clear_layout(self, layout):
        """立刻摘掉旧行：只 deleteLater 的话，控件要等事件循环才消失，
        截图或刷新瞬间会看到上一轮的残影。"""
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

    @staticmethod
    def _ip_of(url):
        return url.split('//')[-1].split(':')[0]

    def _address_rows(self, urls, primary):
        """每个地址一行：标签 + 地址 + 复制；默认那条标「推荐」并加粗。

        颜色走 QSS + 动态属性（不写死在内联样式里），否则切明暗主题时
        这些行不会跟着变，暗色下深色字直接看不见。
        """
        unit = style.px("small", self.size)
        for url in urls:
            ip = self._ip_of(url)
            is_best = ip == primary
            row = QHBoxLayout()
            row.setSpacing(8)

            tag = QLabel(netinfo.ip_label(ip, primary))
            tag.setObjectName("addrTag")
            tag.setFixedWidth(int(unit * 4 * 1.06) + 8)
            tag.setProperty("tone", "best" if is_best
                            else ("virtual" if netinfo.is_virtual(ip) else "normal"))
            row.addWidget(tag)

            link = QLabel(url)
            link.setObjectName("addrUrl")
            link.setProperty("tone", "best" if is_best else "normal")
            link.setTextInteractionFlags(Qt.TextSelectableByMouse)
            link.setCursor(Qt.IBeamCursor)
            row.addWidget(link, 1)

            copy_btn = self._tiny_btn("复制")
            copy_btn.clicked.connect(lambda _=False, u=url: self.copy_address(u))
            row.addWidget(copy_btn)

            holder = QWidget()
            holder.setLayout(row)
            row.setContentsMargins(0, 0, 0, 0)
            self.addr_box.addWidget(holder)

    def refresh_addresses(self, urls, primary_url):
        """urls 里第一条通常是 localhost，primary_url 是推荐别人用手机扫的那条。"""
        primary = self._ip_of(primary_url) if primary_url else None
        self._clear_layout(self.addr_box)
        if not urls:
            empty = QLabel("服务启动后会显示可访问的地址")
            empty.setObjectName("muted")
            self.addr_box.addWidget(empty)
            return
        self._address_rows(urls, primary)
        self.qr_combo.blockSignals(True)
        self.qr_combo.clear()
        for url in urls:
            ip = self._ip_of(url)
            self.qr_combo.addItem("%s · %s" % (netinfo.ip_label(ip, primary), ip), url)
        index = self.qr_combo.findData(primary_url)
        self.qr_combo.setCurrentIndex(max(0, index))
        self.qr_combo.blockSignals(False)
        self.qr_combo.setEnabled(len(urls) > 1)
        self._render_qr(self.qr_combo.currentData() or primary_url)

    def _on_qr_addr_changed(self, _index):
        url = self.qr_combo.currentData()
        if url:
            self._render_qr(url)

    def _render_qr(self, url):
        self.qr_url_label.setText(url or "")
        pix = qr.make_pixmap(url, 148)
        if pix is None:
            self.qr_label.clear()
            self.qr_label.setText("没装 segno\n用上面的地址手动访问")
            self.qr_hint.setText("装上 segno 就能扫码")
            return
        self.qr_label.setPixmap(pix)
        self.qr_hint.setText("扫码打开　·　双击放大")

    def _qr_placeholder(self, text="服务未启动"):
        """清掉位图再写文字：QLabel 设过空 QPixmap 会把文字一起吃掉。"""
        self.qr_label.clear()
        self.qr_label.setText(text)
        self.qr_url_label.setText("")

    def copy_address(self, url):
        QApplication.clipboard().setText(url)
        self._flash("已复制：%s" % url)

    def toggle_log_pause(self):
        if self.log_timer.isActive():
            self.log_timer.stop()
            self.log_pause_btn.setText("继续")
            self._flash("日志已暂停")
        else:
            self.log_timer.start(500)
            self.log_pause_btn.setText("暂停")
            self._flash("日志已继续")

    def clear_log(self):
        self.log_text.clear()

    def export_log(self):
        name = "访问日志-%s.txt" % datetime.now().strftime("%Y%m%d-%H%M")
        path, _ = QFileDialog.getSaveFileName(self, "导出访问日志", name, "文本文件 (*.txt)")
        if not path:
            return
        try:
            Path(path).write_text(self.log_text.toPlainText(), encoding="utf-8")
            self._flash("已导出：%s" % path)
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def browse_path(self):
        current = self.path_entry.text() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "选择共享文件夹", current)
        if folder:
            self.apply_share_path(folder)

    def apply_share_path(self, path_text, quiet=False):
        """改共享目录：运行中也立刻生效，不需要重启服务。"""
        path_text = (path_text or "").strip()
        if not path_text:
            return False
        target = Path(path_text)
        if not target.is_dir():
            if not quiet:
                QMessageBox.warning(self, "路径无效", "这个路径不存在或不是文件夹：\n%s" % path_text)
            return False

        self.path_entry.setText(str(target))
        if not self.sharer.is_running:
            self._flash("共享目录：%s（启动服务后生效）" % target)
            return True

        ok, result = self.sharer.set_shared_path(str(target))
        if not ok:
            if not quiet:
                QMessageBox.warning(self, "切换失败", str(result))
            return False

        # 日志里留一行，方便对照"到底切没切"
        if self.log_queue:
            self.log_queue.put((datetime.now().strftime('%H:%M:%S'), '系统',
                                '共享目录已切换为 %s' % target, None))
        self.stat_label.setText("共享目录：%s" % target)
        self._flash("共享目录已切换：%s" % target)
        return True

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_dir():
                self.apply_share_path(path)

    def toggle_share(self):
        if self.sharer.is_running:
            self.stop_share()
        else:
            self.start_share()

    def start_share(self):
        path = self.path_entry.text().strip()
        if not path or not Path(path).exists():
            QMessageBox.warning(self, "错误", "请选择有效的共享路径")
            return

        port = self.port_entry.value()

        password = self.password_entry.text().strip() or None
        admin_password = self.admin_password_entry.text().strip() or None

        self.toggle_share_btn.setEnabled(False)
        self.toggle_share_btn.setText("启动中...")

        def start_worker():
            success, result = self.sharer.start(path, port, password, admin_password)
            self.start_completed.emit(success, result)

        threading.Thread(target=start_worker, daemon=True).start()

    def on_start_completed(self, success, result):
        self.toggle_share_btn.setEnabled(True)
        if success:
            self.start_result = result
            self.log_queue = result['log_queue']
            self.log_timer.start(500)
            self.display_server_info(result)
            self._apply_running_state(True, result)
            if self._tray_available:
                self.tray_icon.showMessage("局域网云盘", "服务已成功启动", QSystemTrayIcon.Information, 2000)
        else:
            self._apply_running_state(False)
            QMessageBox.critical(self, "启动失败", str(result))

    def _apply_running_state(self, running, result=None):
        """服务起停时统一改按钮、胶囊、输入框可编辑性、地址与二维码。"""
        # 共享目录允许运行中直接改（handler 每次请求都读全局值，立刻生效）
        self.path_entry.setReadOnly(False)
        self.select_path_btn.setEnabled(True)
        self.port_entry.setReadOnly(running)
        self.password_entry.setReadOnly(running)
        self.admin_password_entry.setReadOnly(running)
        self.browser_btn.setEnabled(running)
        self.toggle_share_btn.setText("停止服务" if running else "启动服务")
        self.toggle_share_btn.setObjectName("danger" if running else "primary")
        self.toggle_share_btn.style().unpolish(self.toggle_share_btn)
        self.toggle_share_btn.style().polish(self.toggle_share_btn)

        if running and result:
            port = result.get('port') or result.get('local_url', '').rsplit(':', 1)[-1]
            self.set_pill("● 运行中 · 端口 %s" % port, "ok")
            primary = result.get('primary_url')
            # 地址清单按可信度排：推荐（路由器那条）在最前，本机放最后
            urls = list(result['network_urls']) + [result['local_url']]
            self.qr_primary = primary
            self.refresh_addresses(urls, primary)
            self.stat_label.setText("共 %d 个地址，二维码默认用「推荐」那条" % len(urls))
        else:
            self.set_pill("● 已停止", "muted")
            self.qr_primary = None
            self.refresh_addresses([], None)
            self._qr_placeholder()
            self.qr_hint.setText("启动服务后自动生成")
            self.stat_label.setText("服务未启动")

    def stop_share(self):
        self.log_timer.stop()
        self.sharer.stop()
        self.start_result = None
        self.log_queue = None
        self._apply_running_state(False)
        if self._tray_available:
            self.tray_icon.showMessage("局域网云盘", "服务已停止", QSystemTrayIcon.Information, 2000)

    def display_server_info(self, result):
        """服务信息不再单独占一块；地址在「访问方式」卡片里，
        这里只留一个列表给双击放大二维码那条老路径用。"""
        self.qr_items = list(result.get('network_urls') or [])
        self.info_text = None

    def show_qr_code(self, url):
        if not url:
            return
        try:
            import segno, io as _io
            buf = _io.BytesIO()
            segno.make(url, error='m').save(buf, kind='png', scale=8, border=2)
            buf.seek(0)
            pixmap = QPixmap()
            pixmap.loadFromData(buf.read())
            dialog = QDialog(self)
            dialog.setWindowTitle("二维码 - " + url)
            dialog.setModal(True)
            layout = QVBoxLayout()
            lbl = QLabel()
            lbl.setPixmap(pixmap)
            lbl.setAlignment(Qt.AlignCenter)
            url_lbl = QLabel(url)
            url_lbl.setAlignment(Qt.AlignCenter)
            url_lbl.setStyleSheet("font-size:14px;color:#7f8c8d;margin-top:8px;")
            url_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(lbl)
            layout.addWidget(url_lbl)
            dialog.setLayout(layout)
            dialog.exec_()
        except Exception as e:
            QMessageBox.warning(self, "错误", "生成二维码失败（需安装 segno）: {}".format(str(e)))

    def open_browser(self):
        if self.start_result:
            import webbrowser
            webbrowser.open(self.start_result['local_url'])

    def update_log(self):
        if not self.log_queue:
            return
        new_logs = []
        try:
            while True:
                new_logs.append(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        if not new_logs:
            return

        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        prefix_fmt = self._log_format("muted")
        for entry in new_logs:
            timestamp, client_ip, message, status = self._unpack_log(entry)
            cursor.insertText("[%s] %s " % (timestamp, client_ip), prefix_fmt)
            cursor.insertText("%s\n" % message, self._log_format(self._tone_of(status)))
        self.log_text.setTextCursor(cursor)
        self._trim_log()
        self.log_text.ensureCursorVisible()

    LOG_MAX_LINES = 2000

    def _trim_log(self):
        """长时间开着时截掉最老的日志，避免 QTextEdit 越吃越多内存。"""
        doc = self.log_text.document()
        extra = doc.blockCount() - self.LOG_MAX_LINES
        if extra <= 0:
            return
        cursor = QTextCursor(doc)
        cursor.movePosition(QTextCursor.Start)
        cursor.movePosition(QTextCursor.Down, QTextCursor.KeepAnchor, extra)
        cursor.movePosition(QTextCursor.StartOfBlock, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()

    @staticmethod
    def _unpack_log(entry):
        """兼容 3 元组（旧队列）与 4 元组（带状态码）。"""
        if len(entry) >= 4:
            return entry[0], entry[1], entry[2], entry[3]
        return entry[0], entry[1], entry[2], None

    @staticmethod
    def _tone_of(status):
        try:
            code = int(status)
        except (TypeError, ValueError):
            return "muted"
        if code >= 500:
            return "err"
        if code >= 400:
            return "warn"
        if code >= 300:
            return "info"
        if code >= 200:
            return "ok"
        return "muted"

    def _log_format(self, tone):
        colors = style.tone_colors(self.mode)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(colors.get(tone, colors["muted"])))
        return fmt

    def update_status(self, message, is_running):
        self.status_updated.emit(message, is_running)

    def on_status_updated(self, message, is_running):
        if is_running:
            port = getattr(self.sharer, 'port', None)
            suffix = " · 端口 %s" % port if port else ""
            self.set_pill("● %s%s" % (message, suffix), "ok")
        else:
            self.set_pill("● %s" % message, "muted")

    def toggle_context_menu_integration(self):
        if self.is_context_menu_installed():
            self.remove_context_menu()
        else:
            self.add_context_menu()
        self._menu_installed = self.is_context_menu_installed()
        self.update_menu_btn_text()

    def is_context_menu_installed(self):
        if platform.system() != "Windows" or not winreg:
            return False
        try:
            winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,
                           r"Directory\Background\shell\{}".format(self.reg_key_name),
                           0, winreg.KEY_READ)
            return True
        except FileNotFoundError:
            return False
        except Exception:
            return False

    def add_context_menu(self):
        if platform.system() != "Windows":
            QMessageBox.information(self, "提示", "此功能仅支持 Windows 系统")
            return
        try:
            exe_path = str(Path(sys.executable if IS_FROZEN else sys.argv[0]).resolve())
            key_path = r"Directory\Background\shell\{}".format(self.reg_key_name)
            with winreg.CreateKey(winreg.HKEY_CLASSES_ROOT, key_path) as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "📂 在此处启动云盘")
                winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, exe_path)
            with winreg.CreateKey(winreg.HKEY_CLASSES_ROOT, key_path + r"\command") as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, '"{}" "%V"'.format(exe_path))
                QMessageBox.information(self, "成功", "已添加右键菜单")
        except PermissionError:
            QMessageBox.warning(self, "权限不足", "请以管理员身份运行程序")
        except Exception as e:
            QMessageBox.critical(self, "失败", "添加右键菜单失败: {}".format(str(e)))

    def remove_context_menu(self):
        if platform.system() != "Windows":
            return
        try:
            key_path = r"Directory\Background\shell\{}".format(self.reg_key_name)
            winreg.DeleteKey(winreg.HKEY_CLASSES_ROOT, key_path + r"\command")
            winreg.DeleteKey(winreg.HKEY_CLASSES_ROOT, key_path)
            QMessageBox.information(self, "成功", "已移除右键菜单")
        except FileNotFoundError:
            pass
        except PermissionError:
            QMessageBox.warning(self, "权限不足", "请以管理员身份运行程序")
        except Exception as e:
            QMessageBox.critical(self, "失败", "移除右键菜单失败: {}".format(str(e)))

    def update_startup_btn_text(self):
        """把按钮文案更新成当前状态。

        注意：`is_startup_enabled()` 要跑一次 `schtasks /query`（200~500ms），
        启动时不能同步调用——窗口会晚出来半秒。走 `refresh_system_checks()`
        在后台线程里查，查完用信号回到 UI 线程。
        """
        enabled = self._startup_enabled
        if enabled is None:                 # 还没查出来，先显示"检测中…"
            self.startup_btn.setText("检测自启状态…")
            self.startup_btn.setEnabled(False)
            return
        self.startup_btn.setEnabled(True)
        self.startup_btn.blockSignals(True)
        self.startup_btn.setText("已开启开机自启" if enabled else "开机自启")
        self.startup_btn.setToolTip("已在计划任务里注册，可再次点击关闭" if enabled
                                    else "登录 Windows 后自动在托盘启动共享")
        self.startup_btn.blockSignals(False)
        self._set_toggle_state(self.startup_btn, enabled)

    def refresh_system_checks(self):
        """后台查「右键菜单 / 开机自启」两个开关的真实状态，不阻塞窗口显示。"""
        def worker():
            startup = False
            menu = False
            try:
                startup = self.is_startup_enabled()
            except Exception as exc:
                print("[启动自检] 查询计划任务失败:", exc)
            try:
                menu = self.is_context_menu_installed()
            except Exception as exc:
                print("[启动自检] 查询右键菜单失败:", exc)
            self.system_checks_ready.emit(startup, menu)

        threading.Thread(target=worker, daemon=True).start()

    def on_system_checks_ready(self, startup, menu):
        self._startup_enabled = startup
        self._menu_installed = menu
        self.update_startup_btn_text()
        self.menu_btn.setText("已添加到右键菜单" if menu else "添加到右键菜单")
        self.menu_btn.setToolTip("在文件夹空白处右键即可把该文件夹设为共享目录"
                                 if menu else "在资源管理器里加一条右键菜单")
        self._set_toggle_state(self.menu_btn, menu)

    def update_menu_btn_text(self):
        menu = self._menu_installed
        if menu is None:
            self.menu_btn.setText("检测右键菜单…")
            return
        self.menu_btn.setText("已添加到右键菜单" if menu else "添加到右键菜单")
        self.menu_btn.setToolTip("在文件夹空白处右键即可把该文件夹设为共享目录"
                                 if menu else "在资源管理器里加一条右键菜单")
        self._set_toggle_state(self.menu_btn, menu)

    @staticmethod
    def _set_toggle_state(btn, enabled):
        """让「已开启」的开关按钮显示成绿色，需要重新走一遍样式计算。"""
        btn.setProperty("on", "true" if enabled else "false")
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def toggle_startup(self):
        print("toggle_startup called")
        if getattr(self, "_startup_toggling", False):
            return

        self._startup_toggling = True
        self.startup_btn.setEnabled(False)

        try:
            if self.is_startup_enabled():
                self.disable_startup()
            else:
                self.enable_startup()
        finally:
            self._startup_enabled = self.is_startup_enabled()
            self.update_startup_btn_text()
            self.startup_btn.setEnabled(True)
            self._startup_toggling = False

    def _run_schtasks(self, args, capture=True):
        """
        运行 schtasks，隐藏黑窗口。
        """
        creationflags = 0
        if platform.system() == "Windows":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        return subprocess.run(
            ["schtasks"] + args,
            capture_output=capture,
            text=True,
            encoding="mbcs",
            errors="ignore",
            creationflags=creationflags
        )


    def _get_startup_command(self):
        """
        生成计划任务启动命令。
        """
        if IS_FROZEN:
            # 打包成 exe 后
            exe_path = str(Path(sys.executable).resolve())
            command = f'"{exe_path}" --startup'
        else:
            # 源码运行时，尽量使用 pythonw.exe，避免开机弹出黑窗口
            python_path = Path(sys.executable).resolve()

            if python_path.name.lower() == "python.exe":
                pythonw_path = python_path.with_name("pythonw.exe")
                if pythonw_path.exists():
                    python_path = pythonw_path

            script_path = str(Path(sys.argv[0]).resolve())
            command = f'"{python_path}" "{script_path}" --startup'

        return command


    def is_startup_enabled(self):
        """
        检查计划任务是否存在。
        """
        print("\n=== is_startup_enabled called, 使用计划任务检测 ===")

        if platform.system() != "Windows":
            return False

        try:
            task_name = getattr(self, "startup_task_name", self.startup_reg_key_name)

            result = self._run_schtasks([
                "/query",
                "/tn", task_name
            ])

            enabled = result.returncode == 0

            if enabled:
                print("计划任务存在:", task_name)
            else:
                print("计划任务不存在:", task_name)
                if result.stderr:
                    print("schtasks stderr:", result.stderr.strip())
                if result.stdout:
                    print("schtasks stdout:", result.stdout.strip())

            return enabled

        except Exception as e:
            print("检查计划任务异常:", repr(e))
            return False


    def enable_startup(self):
        """
        创建登录时启动的计划任务。
        """
        print("\n=== enable_startup called, 使用计划任务 ===")

        if platform.system() != "Windows":
            QMessageBox.information(self, "提示", "此功能仅支持 Windows 系统")
            return

        try:
            task_name = getattr(self, "startup_task_name", self.startup_reg_key_name)
            command = self._get_startup_command()

            print("准备创建计划任务:")
            print("任务名:", task_name)
            print("命令:", command)

            result = self._run_schtasks([
                "/create",
                "/tn", task_name,
                "/sc", "onlogon",
                "/tr", command,
                "/f"
            ])

            print("schtasks returncode:", result.returncode)
            print("schtasks stdout:", result.stdout.strip() if result.stdout else "")
            print("schtasks stderr:", result.stderr.strip() if result.stderr else "")

            if result.returncode == 0:
                if self.is_startup_enabled():
                    QMessageBox.information(self, "成功", "已启用开机自启")
                else:
                    QMessageBox.warning(self, "异常", "计划任务创建后检测不到")
            else:
                msg = result.stderr.strip() or result.stdout.strip() or "未知错误"
                QMessageBox.critical(self, "失败", "创建开机自启计划任务失败:\n{}".format(msg))

        except Exception as e:
            print("设置开机自启异常:", repr(e))
            QMessageBox.critical(self, "失败", "设置开机自启失败: {}".format(str(e)))


    def disable_startup(self):
        """
        删除计划任务。
        """
        print("\n=== disable_startup called, 删除计划任务 ===")

        if platform.system() != "Windows":
            return

        try:
            task_name = getattr(self, "startup_task_name", self.startup_reg_key_name)

            result = self._run_schtasks([
                "/delete",
                "/tn", task_name,
                "/f"
            ])

            print("schtasks returncode:", result.returncode)
            print("schtasks stdout:", result.stdout.strip() if result.stdout else "")
            print("schtasks stderr:", result.stderr.strip() if result.stderr else "")

            if result.returncode == 0:
                QMessageBox.information(self, "成功", "已关闭开机自启")
            else:
                # 如果本来就不存在，也当作关闭成功
                if not self.is_startup_enabled():
                    QMessageBox.information(self, "成功", "已关闭开机自启")
                else:
                    msg = result.stderr.strip() or result.stdout.strip() or "未知错误"
                    QMessageBox.critical(self, "失败", "删除开机自启计划任务失败:\n{}".format(msg))

        except Exception as e:
            print("关闭开机自启异常:", repr(e))
            QMessageBox.critical(self, "失败", "关闭开机自启失败: {}".format(str(e)))


    def _port_from_settings(self):
        """端口可能是命令行给的、也可能是上次存的；QSettings 读回来是字符串。"""
        raw = self.initial_port if self.initial_port else self.settings.value("port", DEFAULT_PORT)
        try:
            port = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_PORT
        return port if 1 <= port <= 65535 else DEFAULT_PORT

    def save_settings(self):
        self.settings.setValue("path", self.path_entry.text())
        self.settings.setValue("port", self.port_entry.value())
        self.settings.setValue("password", self.password_entry.text())
        self.settings.setValue("admin_password", self.admin_password_entry.text())
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())

    def load_settings(self):
        self.path_entry.setText(self.initial_path or self.settings.value("path", str(DEFAULT_SHARE_PATH)))
        self.port_entry.setValue(self._port_from_settings())
        self.password_entry.setText(self.settings.value("password", ""))
        self.admin_password_entry.setText(self.settings.value("admin_password", ""))
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        window_state = self.settings.value("windowState")
        if window_state:
            self.restoreState(window_state)

    def auto_start_share(self):
        path = self.path_entry.text().strip()
        if path and Path(path).exists():
            self.start_share()


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════
