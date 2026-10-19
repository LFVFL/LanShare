"""桌面端配色、字号与样式表。

三层 token：
  MODES    明/暗两套中性色（背景、卡片、描边、文字）
  PALETTES 6 套主色（和 Web 端同一组）
  SIZES    字号档位（中/大/特大），乘一个系数得到具体 px

改外观只动这里的常量；`QSS` 模板里不出现裸色值，也不出现裸字号。
"""

# ── 6 套主色（brand / brand_dark / soft 用同色系浅底）────────────────
PALETTES = {
    "indigo": {"name": "靛蓝", "brand": "#4f6ef7", "brand_dark": "#3d59d8"},
    "blue": {"name": "海蓝", "brand": "#2563eb", "brand_dark": "#1d4ed8"},
    "emerald": {"name": "青翠", "brand": "#0f9d58", "brand_dark": "#0b7c45"},
    "rose": {"name": "玫红", "brand": "#e11d48", "brand_dark": "#be123c"},
    "amber": {"name": "琥珀", "brand": "#d97706", "brand_dark": "#b45309"},
    "violet": {"name": "紫罗兰", "brand": "#7c3aed", "brand_dark": "#6d28d9"},
}
DEFAULT_PALETTE = "emerald"

# ── 明 / 暗 ──────────────────────────────────────────────────────────
MODES = {
    "light": {
        "bg": "#f4f6f9", "card": "#ffffff", "line": "#e3e7ee",
        "text": "#1f2430", "muted": "#6b7280",
        "ghost": "#eef1f6", "ghost_hover": "#e2e7f0", "ghost_text": "#1f2430",
        "log_bg": "#fbfcfe", "ok": "#0d7a43", "ok_bg": "#e7f7ee",
        "ok_line": "#c3e8d5", "warn": "#b45309", "warn_bg": "#fdf1e0",
        "danger": "#dc2626", "danger_dark": "#b91c1c", "danger_bg": "#fdeaea",
        "disabled": "#b6bdc9", "scroll": "#cfd6e2", "scroll_hover": "#b8c2d2",
        "menu_bg": "#ffffff", "sel_bg": "#eef1f6",
    },
    "dark": {
        "bg": "#14161c", "card": "#1c1f27", "line": "#2b303c",
        "text": "#e7eaf1", "muted": "#98a1b2",
        "ghost": "#252a35", "ghost_hover": "#2f3542", "ghost_text": "#e7eaf1",
        "log_bg": "#171a21", "ok": "#4ade80", "ok_bg": "#16301f",
        "ok_line": "#245234", "warn": "#fbbf24", "warn_bg": "#332612",
        "danger": "#f87171", "danger_dark": "#ef4444", "danger_bg": "#3a1c1c",
        "disabled": "#5c6472", "scroll": "#3a414f", "scroll_hover": "#4b5468",
        "menu_bg": "#22262f", "sel_bg": "#2f3542",
    },
}
DEFAULT_MODE = "light"

# ── 字号档位（乘在基准 px 上）──────────────────────────────────────
SIZES = {"中": 1.0, "大": 1.15, "特大": 1.32}
DEFAULT_SIZE = "大"

# 基准字号（px，未乘档位）
BASE_PX = {
    "label": 15, "title": 16, "brand": 21, "small": 14,
    "input": 16, "btn": 15, "tiny": 14, "mono": 14, "pill": 14,
}

FONT = "'Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI', sans-serif"
MONO = "'Cascadia Mono', 'Consolas', 'Courier New', monospace"

# 日志里按状态码着色（明暗两套都够对比度）
TONE_TONES = {
    "light": {"ok": "#0d7a43", "info": "#2563eb", "warn": "#b45309",
              "err": "#dc2626", "muted": "#6b7280"},
    "dark": {"ok": "#4ade80", "info": "#7aa2ff", "warn": "#fbbf24",
             "err": "#f87171", "muted": "#98a1b2"},
}


def tone_colors(mode="light"):
    return TONE_TONES.get(mode, TONE_TONES["light"])


def px(key, scale=DEFAULT_SIZE):
    """基准字号 × 档位，四舍五入到整数 px。"""
    return int(round(BASE_PX[key] * SIZES.get(scale, 1.0)))


def tokens(palette=DEFAULT_PALETTE, mode=DEFAULT_MODE, scale=DEFAULT_SIZE):
    tok = dict(MODES.get(mode, MODES[DEFAULT_MODE]))
    pal = PALETTES.get(palette, PALETTES[DEFAULT_PALETTE])
    tok["brand"] = pal["brand"]
    tok["brand_dark"] = pal["brand_dark"]
    for key in ("label", "title", "brand_px", "small", "input", "btn", "tiny",
                "mono", "pill"):
        tok["f_" + key.replace("_px", "")] = px("brand" if key == "brand_px" else key, scale)
    tok["font"] = FONT
    tok["mono_family"] = MONO
    return tok


QSS_TEMPLATE = """
QMainWindow, QWidget#root {{ background: {bg}; }}
QLabel {{ color: {text}; font-family: {font}; font-size: {f_label}px; }}
QLabel#brand {{ font-size: {f_brand}px; font-weight: 700; }}
QLabel#ver {{ color: {muted}; font-size: {f_small}px; }}
QLabel#muted {{ color: {muted}; font-size: {f_small}px; }}
QLabel#cardTitle {{ font-size: {f_title}px; font-weight: 700; color: {text}; }}
QLabel#pill {{
    border-radius: 13px; padding: 5px 14px; font-size: {f_pill}px; font-weight: 600;
    background: {ghost}; color: {muted};
}}
QLabel#pill[tone="ok"] {{ background: {ok_bg}; color: {ok}; }}
QLabel#pill[tone="warn"] {{ background: {warn_bg}; color: {warn}; }}
QLabel#pill[tone="err"] {{ background: {danger_bg}; color: {danger}; }}

QLabel#addrTag {{ font-size: {f_small}px; font-weight: 600; color: {muted}; }}
QLabel#addrTag[tone="best"] {{ color: {ok}; }}
QLabel#addrTag[tone="virtual"] {{ color: {warn}; }}
QLabel#addrUrl {{
    font-family: {mono_family}; font-size: {f_small}px; color: {muted};
}}
QLabel#addrUrl[tone="best"] {{ color: {text}; font-weight: 600; }}

QFrame#card {{
    background: {card}; border: 1px solid {line}; border-radius: 11px;
}}

QLineEdit, QSpinBox {{
    background: {card}; border: 1px solid {line}; border-radius: 9px;
    padding: 9px 12px; font-size: {f_input}px; color: {text};
    selection-background-color: {brand}; selection-color: white;
}}
QLineEdit:focus, QSpinBox:focus {{ border: 1px solid {brand}; }}
QLineEdit:read-only {{ background: {ghost}; color: {muted}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}

QPushButton {{
    background: {ghost}; color: {ghost_text}; border: none; border-radius: 9px;
    padding: 9px 16px; font-size: {f_btn}px; font-weight: 600;
}}
QPushButton:hover {{ background: {ghost_hover}; }}
QPushButton:pressed {{ background: {line}; }}
QPushButton:disabled {{ background: {ghost}; color: {disabled}; }}

QPushButton#primary {{ background: {brand}; color: white; }}
QPushButton#primary:hover {{ background: {brand_dark}; }}
QPushButton#danger {{ background: {danger}; color: white; }}
QPushButton#danger:hover {{ background: {danger_dark}; }}
QPushButton#ghost {{ background: transparent; border: 1px solid {line}; color: {ghost_text}; }}
QPushButton#ghost:hover {{ background: {ghost}; }}
QPushButton#ghost[on="true"] {{ background: {ok_bg}; color: {ok}; border: 1px solid {ok_line}; }}
QPushButton#tiny {{ padding: 5px 12px; font-size: {f_tiny}px; font-weight: 500; }}

QToolButton {{
    background: transparent; border: 1px solid {line}; border-radius: 9px;
    padding: 9px 14px; font-size: {f_btn}px; font-weight: 600; color: {ghost_text};
}}
QToolButton:hover {{ background: {ghost}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

QComboBox {{
    background: {card}; border: 1px solid {line}; border-radius: 9px;
    padding: 7px 11px; font-size: {f_small}px; color: {text}; min-height: 20px;
}}
QComboBox:hover {{ border: 1px solid {brand}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {menu_bg}; border: 1px solid {line}; outline: none;
    selection-background-color: {sel_bg}; selection-color: {text};
    font-size: {f_small}px; padding: 3px;
}}

QMenu {{
    background: {menu_bg}; border: 1px solid {line}; border-radius: 8px;
    padding: 6px; font-size: {f_label}px; color: {text};
}}
QMenu::item {{ padding: 7px 22px 7px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {sel_bg}; }}
QMenu::separator {{ height: 1px; background: {line}; margin: 5px 8px; }}

QTextEdit {{
    background: {log_bg}; border: 1px solid {line}; border-radius: 9px;
    padding: 9px; font-family: {mono_family}; font-size: {f_mono}px; color: {text};
}}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {scroll}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {scroll_hover}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


def build_qss(palette=DEFAULT_PALETTE, mode=DEFAULT_MODE, scale=DEFAULT_SIZE):
    return QSS_TEMPLATE.format(**tokens(palette, mode, scale))


# 兼容旧调用：默认明色 + 靛蓝 + 大字号
QSS = build_qss()
