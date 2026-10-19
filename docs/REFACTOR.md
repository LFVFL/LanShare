# 第六版 · 拆分重构方案（下一轮）

目标：把 3336 行的单文件拆成职责清晰的模块，**不改行为、不改界面、不改打包方式**。

## 一、现状（实测）

| 区块 | 行数 | 说明 |
|---|---|---|
| 常量 / 全局状态 | ~130 | 端口、TTL、限制、锁、上传状态、会话 |
| 聊天持久化 + 图片 | ~300 | rooms.json 读写、原子写、旧数据迁移、图片落盘 |
| 路径与格式化工具 | ~110 | `sanitize_relative_path`、`is_path_within`、`format_size` |
| 页面渲染 | ~40 | `render_template`、`page_shell` |
| **`FileShareHandler`** | **1631** | 占全文件 49%；其中 `do_GET` 226 行、`do_POST` 327 行 |
| `FastBindHTTPServer` + `FileSharer` | ~290 | 服务生命周期、访问日志 |
| Qt 界面 | ~785 | `FileShareGUI` 等 |
| 入口 | ~50 | `main()`、argparse |

`do_GET` / `do_POST` 是路由大杂烩：鉴权、静态资源、文件操作、聊天接口全挤在 if-elif 里。

## 二、目标结构（全部平铺在同一目录，入口不变）

```
第六版/
├─ lanshare.py        入口：argparse + main()（~60 行）
├─ ui.py              ClickableLabel / build_app_icon / FileShareGUI（~800）
├─ server.py          FastBindHTTPServer / FileSharer / 访问日志（~290）
├─ handler.py         FileShareHandler：只剩 __init__、log_message、路由表（~250）
├─ filesapi.py        FilesMixin：列举/下载/上传/删除/重命名/新建/zip/预览（~420）
├─ chatapi.py         ChatMixin：房间列表/房间页/收发/删除/清空（~160）
├─ webresp.py         HtmlMixin：模板渲染、send_html/json、静态资源、安全头（~150）
├─ auth.py            AuthMixin：会话、Cookie、管理员、同源校验（~110）
├─ storage.py         会话表、上传锁、上传偏移、ZIP 临时文件与清理线程（~180）
├─ chatstore.py       聊天持久化（原子写 + 备份）、图片落盘、旧数据迁移（~200）
├─ pathutil.py        路径清洗与越界校验、大小格式化、扩展名分类（~90）
├─ appconfig.py       常量：端口、TTL、上限、图片/预览白名单、基础目录（~100）
├─ webassets.py       （生成物，保持不动）
├─ web/               （CSS/JS/模板源码，保持不动）
├─ tools/build_assets.py
└─ tools/smoke_test.py  ← 新增：把验收项做成可重复运行的脚本
```

**为什么用 Mixin 而不是拆成独立函数模块**：`handler` 里的方法大量依赖 `self.self.headers`、`self.wfile`、`self.shared_path`，改成函数得层层传 self，改动面和出错概率都更大。Mixin 只做「物理搬移」，方法体几乎不用改。

## 三、依赖方向（禁止反向与平级互引）

```
lanshare ─→ ui, server
server   ─→ handler, appconfig
handler  ─→ auth, filesapi, chatapi, webresp      （均为 Mixin，无相互 import）
filesapi ─→ storage, pathutil, appconfig, webresp
chatapi  ─→ chatstore, storage, pathutil, appconfig
webresp  ─→ appconfig, webassets
auth     ─→ storage, appconfig
storage / chatstore / pathutil / appconfig        （叶子，只依赖标准库）
```

全局状态只放在 `storage.py` 与 `chatstore.py`，其他模块通过函数访问，**不 import 对方的 dict**。

## 四、分阶段执行（每阶段独立可验证）

| 阶段 | 内容 | 验证 |
|---|---|---|
| P0 | 备份当前版本（`_backup/` 或 git init）；新增 `tools/smoke_test.py` | 脚本能跑通现有代码 |
| P1 | 抽 `appconfig.py` + `pathutil.py` | 编译 + 冒烟 |
| P2 | 抽 `storage.py`（会话/上传锁/上传偏移/ZIP） | 冒烟（含续传指纹） |
| P3 | 抽 `chatstore.py`（含原子写与图片） | 冒烟 + 聊天收发 |
| P4 | 抽 `webresp.py` + `auth.py` | 冒烟（含 CSRF/管理员） |
| P5 | 抽 `filesapi.py`（最大一块，逐方法搬） | 冒烟 + 上传/下载/打包 |
| P6 | 抽 `chatapi.py` | 冒烟 + 聊天全流程 |
| P7 | `handler.py` 改成路由表 + `server.py` | 冒烟 + 日志/启动 |
| P8 | 抽 `ui.py`，入口瘦身；加 `--web-only` | 手动点一遍 GUI |
| P9 | 全量回归 + 你那边打一次包 | 验收表 + exe |

每阶段结束都跑：`python -m py_compile *.py` + `python tools/smoke_test.py`。

## 五、smoke_test.py 覆盖的断言

1. `GET /delete` → 405，文件仍在
2. 跨站 `POST /delete`（Origin 为外站）→ 403
3. 同源 `POST /verify_admin` → 200 且下发 Cookie
4. 分片上传 5 字节 → `check_exists` 指纹一致时 `resume=true`
5. 改指纹后 `resume=false`
6. `/upload/done` 后记录清空
7. 同名上传 → `/upload/unique` 返回 `x (2).txt`
8. 空文件上传成功
9. `/api/list` 返回条目且 path 为原始路径
10. `/static/app.css|js` 200 且带 ETag；重复请求 304
11. `/favicon.ico` 200（无 ico 文件时走内嵌）
12. 页面占位符 0 残留、`data-page` 正确

## 六、风险与对策

| 风险 | 对策 |
|---|---|
| 循环 import | 严格按上面的依赖方向；Mixin 之间互不 import |
| 全局状态被复制成两份 | 只在 storage/chatstore 定义，其他地方 `from storage import SESSIONS_STORE` 会共享同一对象；禁止再写一次赋值 |
| 打包漏文件 | 入口仍是 `lanshare.py`，所有模块同目录；启动时对 `webassets`/关键模块做缺失提示 |
| 搬移过程引入笔误 | 逐阶段 + 冒烟脚本；每阶段前后各跑一次 |
| Nuitka 在这台机器上检测不到 Qt | 打包验证放你机器上做（本机路径含中文，Nuitka 4.1.1 误判） |

## 七、工作量

P1–P7 约 4–6 小时（机械搬移，风险低），P8 约 1 小时，P9 视你打包情况。
建议一次做完 P1–P7 再让验证，避免中途半成品。
