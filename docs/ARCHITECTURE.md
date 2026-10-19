# 模块说明（第六版目录结构）

原来的 `lanshare.py` 一个文件 3400 行，改起来要来回滚。
现在按「一个文件只干一件事」拆成两个包，入口只剩 79 行。

## 一、目录树

```
第六版/
├── lanshare.py          ← 入口：解析参数、建 Qt 应用、开主窗口（79 行）
├── webassets.py         ← 生成产物（CSS/JS/页面/图标），运行时被 import
│
├── server/              ← HTTP 服务端，不依赖 Qt
│   ├── config.py           常量：端口、上限、目录、安全头、预览白名单
│   ├── paths.py            路径清理、包含判断、体积格式化、口令比较
│   ├── storage.py          运行期状态：会话、上传锁/偏移、ZIP 临时文件
│   ├── chat_store.py       聊天持久化：rooms.json 原子写 + .bak、图片落盘、旧数据迁移
│   ├── pages.py            页面模板渲染 + 图标字节（封装 webassets）
│   ├── auth.py         [M] 同源检查、Cookie 会话、管理员口令
│   ├── http_util.py    [M] 响应输出、日志、静态资源、各类页面发送、q()/redirect()
│   ├── routes.py       [M] 页面 + 文件/系统接口（列表、下载、改名、删除…）
│   ├── chat_api.py     [M] 聊天接口：页面、房间、历史、图片、增删改
│   ├── listing.py      [M] 目录项分类、面包屑、同名改名
│   ├── netinfo.py          网卡地址发现与排序（默认路由优先，虚拟网段垫底）
│   ├── transfer.py     [M] 下载（Range）、上传、断点续传、指纹校验
│   ├── archive.py      [M] 打包下载（zip）
│   ├── fs_ops.py       [M] 本机文件操作：打开、删除、改名、新建目录
│   ├── handler.py          路由表 + 分发表（GET/POST/前缀三张表）
│   └── runner.py           FastBindHTTPServer（带并发闸门）+ FileSharer
│
├── ui/                  ← 桌面界面
│   ├── icon.py             应用图标（磁盘 icon.ico 优先，回退到内嵌）
│   ├── widgets.py          小控件（可点击标签）
│   ├── style.py            明/暗 + 6 套配色 + 3 档字号的 token 与 QSS 生成
│   ├── qr.py               二维码渲染（segno 缺失时退化成文字）
│   └── main_window.py      主窗口：状态胶囊、设置卡、地址清单+二维码、彩色日志、托盘
│
├── web/                 ← 前端源码
│   ├── js/                 分片各自独立 IIFE，只通过 window.Lan 互调（共 12 个）
│   │   ├─ 00-core.js           命名空间骨架 + Lan.util（esc/readJson/下载/复制）
│   │   ├─ 10-icons.js          图标 sprite 与注入          → Lan.icons
│   │   ├─ 20-appearance.js     配色 / 明暗 / 玻璃           → Lan.appearance
│   │   ├─ 30-ui.js             Toast、弹窗、菜单            → Lan.ui
│   │   ├─ 40-api.js            请求封装与管理员流程         → Lan.api
│   │   ├─ 50-files.js          文件列表渲染与工具栏         → Lan.files
│   │   ├─ 60-upload.js         上传队列、分片、冲突处理     → Lan.upload
│   │   ├─ 70-lightbox.js       图片预览 + 文件页启动        → Lan.lightbox / Lan.pages.files
│   │   ├─ 80-chat-list.js      聊天室列表                  → Lan.pages.chatList
│   │   ├─ 85-chat-room.js      聊天室                      → Lan.chat
│   │   ├─ 88-login.js          登录页                      → Lan.pages.login
│   │   └─ 90-boot.js           启动编排（只调用，不实现）
│   ├─ pages/               各页面 HTML 片段
│   ├─ tokens.css           设计变量（配色/圆角/阴影）
│   └─ app.css              组件样式
├── tools/
│   ├── build_assets.py     把 web/ 打进 webassets.py（含 js 拼接）
│   ├── smoke_test.py       冒烟测试（无密码常态，30 项）
│   ├── auth_matrix_test.py 鉴权矩阵（有密码部署，33 项）
│   ├── protocol_test.py    协议层：长连接 / 压缩 / 缓存头（36 项）
│   ├── sse_test.py         聊天 SSE 推送（12 项）
│   ├── thumb_test.py       聊天图片缩略图（21 项）
│   ├── zip_test.py         流式打包下载（16 项）
│   ├── search_test.py      共享内搜索（16 项）
│   ├── fsops_test.py       批量移动 / 复制（13 项）
│   ├── ui_test.py          前端行为（CDP 驱动真浏览器，32 项）
│   ├── desktop_test.py     桌面端界面与网段排序（离屏，56 项）
│   ├── origin_test.py      同源校验：只有 Origin 的老浏览器（20 项）
│   ├── admin_flow_test.py  管理密码全流程：上传/清空/手机端入口（29 项）
│   ├── shot.py             本地 HTML 样稿截图（CDP，中文路径可用）
│   ├── mobile_shot.py      手机尺寸(390x844)打开 Web 端，查溢出并截图
│   ├── preflight.py        打包前自检（分片隔离/别名/类方法签名）
│   └── limits_test.py      并发闸门（503 + 20 并发）
└── docs/CHANGELOG.md / docs/REFACTOR.md / docs/ARCHITECTURE.md / docs/DESKTOP-UI.md / requirements.txt
```

`[M]` = mixin 类。它们不是独立对象，而是被 `FileShareHandler` 继承的「能力块」：
方法体里照旧用 `self.headers`、`self.shared_path`，所以搬家时一行逻辑都不用改。

## 二、依赖方向（单向，不回头）

```
lanshare.py
   ├── ui/main_window.py ──→ server/runner.py ──→ server/handler.py
   └── server/*               ↑
                              └── {auth, http_util, files_api}
                                     └── {storage, chat_store, pages, paths, config}
```

- 只有 `storage` / `chat_store` 持有可变全局；其它模块只读。
- `server/*` 一律不 import Qt，所以可以脱离桌面单独跑（`smoke_test.py` 就是这么用的）。
- 想换界面（比如以后做 Web-only 或 CLI）只动 `ui/`，服务端不受影响。

## 三、常用命令

```bat
python lanshare.py                 :: 启动（默认 5995 端口）
python lanshare.py D:\共享 -p 6000  :: 指定目录与端口
python tools\build_assets.py       :: 改了 web/ 之后重新打包 webassets.py
python tools\smoke_test.py         :: 冒烟测试，自带临时目录（默认 6199）
python tools\auth_matrix_test.py   :: 有密码部署的鉴权矩阵（默认 6220）
python tools\protocol_test.py      :: HTTP/1.1 长连接 + gzip + Content-Length（6225）
python tools\sse_test.py           :: 聊天 SSE 推送（6229）
python tools\thumb_test.py         :: 聊天图片缩略图（6245）
python tools\zip_test.py           :: 流式打包下载（6263）
python tools\search_test.py        :: 共享内搜索（6278）
python tools\fsops_test.py         :: 批量移动 / 复制（6340）
python tools\ui_test.py            :: 前端行为（CDP 驱动 Chrome，6320 / 9223）
python tools\preflight.py          :: 打包前自检（用目标解释器跑）
python tools\limits_test.py        :: 并发闸门与 20 并发（6230 / 6231）
```

> 测试用的临时目录建在**项目所在盘**（`ROOT` 下），不写系统临时目录——
> 否则 C: 盘快满时上传会因为「剩余空间不足 512MB」直接回 507，看着像代码坏了。

## 三·五、传输层现在是什么样

| 项 | 现状 | 为什么 |
|---|---|---|
| 协议 | `HTTP/1.1`，默认长连接 | 打开一页要 4 个资源（html/css/js/favicon），复用连接省 3 次握手 |
| 空闲超时 | 60 秒 | 浏览器挂着的连接不会一直占着并发名额 |
| 压缩 | `send_html` / `send_json_response` / `serve_asset` 三个出口：有 brotli 就 br，否则 gzip | 一页首屏 135 KB → 31 KB（br）/ 33 KB（gzip） |
| 不压的情况 | 小于 1 KB、或客户端没带 `Accept-Encoding` | 太小压了反而变大 |
| 压缩算法偏好 | `br`（q=5）> `gzip`（q=6）> 不压 | br 比 gzip 再省约 6%，装了才生效 |
| 缓存 | 页面引用 `/static/app.css?v=<内容摘要>`：带对 `v` 就 `max-age=31536000, immutable`，否则 `no-cache` + ETag 校验 | 正常浏览第二次开始一个字节都不重新下载；手敲地址或旧 URL 也不会拿到过期内容 |
| ETag 算法 | 未压缩内容的 sha1 前 16 位 + `Vary: Accept-Encoding` | 同一份源码无论压不压都是同一个版本号 |
| 聊天推送 | `GET /chat/stream`（SSE），发消息时 `notify_all` 立刻唤醒连接；空闲时在条件变量上等 0.3 秒兜底 | 实测推送延迟约 11ms，且空闲不空转 |
| SSE 限额 | 最多 8 条流、每条活 25 秒 | 每条流占一个线程，不能让长连接把并发名额吃光 |
| SSE 兜底 | 连续失败 3 次自动退回轮询；切到后台断开、切回来先补历史再重连 | 老浏览器 / 名额满时照样能收到消息 |
| 聊天图片 | 前端 canvas 生成最长边 320px 的 JPEG 缩略图，随原图一起上传 | 列表只加载几 KB 的缩略图，点开才取原图 |
| 缩略图存储 | 同一目录、文件名加 `_t`（`xxx.png` + `xxx_t.jpg`） | 删消息时按主体名一起删，不留孤儿 |
| 打包下载 | 默认先落缓存再按 Range 发（可断点续传）；`?stream=1` 才边压边发 | 两种 zip 字节布局不同（流式用 data descriptor），不能混着续传 |
| 打包并发 | 最多 4 条流（`ZIP_STREAM_MAX`），满了 503 | 导大目录时不会把页面请求全挤掉 |
| 图片清理 | 启动时 + `POST /chat/cleanup` 清孤儿图片，默认只动 10 分钟前的文件 | 原图删了/消息改了会留下无主文件，不清理只涨不跌 |
| 搜索 | `GET /api/search?q=&path=` 递归找文件名，不分大小写 | 有结果/扫描/时间三重上限，扫不完标 `truncated` |

长连接的硬前提是**每个响应都有正确的 Content-Length**（否则浏览器会一直等响应体）。
`tools/protocol_test.py` 就是守这条的：它把 20 个接口全打一遍，逐个检查响应头。
加新接口时如果忘了设长度，这个测试会直接红。

## 三·六、打包（已验证可用，一条命令）

> ⚠ **打包目录必须是纯 ASCII 路径**。项目在 `E:\code\局域网\第六版`，PyInstaller 的 Qt 钩子
> 会把路径变成 `E:/code/???/...` 然后报「插件目录不存在」；Nuitka 的 pyqt5 检测也是同一原因。
> 源码路径是中文没关系（只是复制过去），真正跑打包的是 `E:\lanbuild`。

```bat
:: 在项目根目录（用系统 python 跑，它会自己准备 venv）
python tools\build_exe.py
```

`tools\build_exe.py` 会依次做：

1. 检查打包目录 / venv 是否是 ASCII；
2. 没有 `E:\lanvenv` 就建一个并装 PyQt5 + PyInstaller + brotli + segno + pillow（首次约 1 分钟）；
3. 复制 `lanshare.py` / `webassets.py` / `icon.ico` / `server/` / `ui/` 到 `E:\lanbuild`；
4. 用**目标解释器**跑 `tools/preflight.py` 自检；
5. `PyInstaller --onefile --windowed --icon icon.ico --hidden-import webassets`；
6. 启动 exe 实测 `/login`、`/static/app.css`、`/favicon.ico`，全 200 才算成功。

产物 `E:\lanbuild\dist\lanshare.exe` 约 **45.7 MB**（多了 Pillow，能做缩略图补生成；
不想要就把 `build_exe.py` 里 `DEPS` 的 pillow 去掉，能省约 6 MB）。
exe 可以复制到任何地方运行（`chat_data` 建在 exe 同级）。

**图标**：`--icon` 决定 exe 在资源管理器/任务栏里的图标；`--add-data icon.ico;.` 只是把 ico
放进去给程序自己用（窗口图标、favicon 兜底）。只加 `--add-data` 不加 `--icon`，exe 就会顶着
PyInstaller 的默认图标。加了 `--icon` 后，PE 里的 RT_ICON 与 `icon.ico` 完全对应：

```
6 张：713 / 2141 / 4092 / 6631 / 21285 / 56499 字节
（ico 文件 91463 = 这 6 段 + 6 字节头 + 6×16 字节目录）
```

核对方法（`pefile` 是 PyInstaller 的依赖，装完就有）：

```bat
E:\lanvenv\Scripts\python.exe -c "import pefile;pe=pefile.PE(r'E:\lanbuild\dist\lanshare.exe',fast_load=True);pe.parse_data_directories(directories=[9]);print([l.data.struct.Size for e in pe.DIRECTORY_ENTRY_RESOURCE.entries if e.id==3 for s in e.directory.entries for l in s.directory.entries])"
```

> 早期版本用的是 `.ps1` 脚本，已放弃：Windows PowerShell 5.1 会把子进程的 stderr 当异常、
> 脚本本身还要求 UTF-8 BOM，折腾了三次才通；Python 里 subprocess + 编码是确定的。

## 四、路由怎么加

`handler.py` 里三张表，加接口就是加一行：

```python
GET_ROUTES = (
    ('/api/list', 'api_list', AUTH_403, None),
    ...
)
# (路径, 方法名, 鉴权, 固定参数)
```

鉴权五种，写在表里而不是散在分支里：

| 标记 | 行为 |
|---|---|
| `AUTH_PUBLIC` | 不判断（静态资源；聊天接口自己判，见下） |
| `AUTH_REDIRECT` | 没登录 → 302 `/login` |
| `AUTH_403` | 没登录 → 403 |
| `AUTH_ADMIN` | 没登录 → 403；不是管理员 → 401 JSON |
| `AUTH_ADMIN_JSON` | 只要不是管理员就 401 JSON（含没登录） |

聊天接口标 `AUTH_PUBLIC` 是历史原因：它们要区分 302 / 403 / 401 三种拒绝形态，
局部判断更直观，所以留在 `chat_api.py` 里。`tools/auth_matrix_test.py` 把 33 种
组合都钉住了，改表时跑一遍就知道有没有漏。

## 五、这一轮补的两件防护

- **并发闸门**：`FastBindHTTPServer` 带 32 个名额的信号量，排队超过 10 秒直接回
  `503 server is busy`。原来 `ThreadingMixIn` 是无限起线程，几十个下载窗口就能把内存吃光。
- **路由表**：两个 500 多行的 `if` 链变成三张表 + 一个 `_dispatch()`，
  `do_GET` / `do_POST` 只剩 20 行。

## 六、前端怎么改

`web/js/` 下 12 个分片，**每个分片自己是独立 IIFE**，彼此不共享闭包，只在 `window.Lan` 上相遇：

| 分片 | 提供什么 | 依赖谁 |
|---|---|---|
| `00-core.js` | 命名空间骨架 + `Lan.util`（esc / readJson / downloadUrl / copyText） | — |
| `10-icons.js` | `Lan.icons`：icon / injectSprite / hydrateIcons | core |
| `20-appearance.js` | `Lan.appearance`：主题、配色、玻璃档位、设置面板 | icons |
| `30-ui.js` | `Lan.ui`：toast / openModal / openMenu | icons |
| `40-api.js` | `Lan.api`：fetch / write（401 自动要密码）/ json / ctx | ui |
| `50-files.js` | `Lan.files`：列表、排序、搜索、多选、ZIP、移动/复制/删除/重命名 | util + ui + api |
| `60-upload.js` | `Lan.upload`：分片上传队列（暂停/重试/冲突改名） | util + ui + api + files |
| `70-lightbox.js` | `Lan.lightbox` + `Lan.pages.files`（文件页启动） | util + files + upload |
| `80-chat-list.js` | `Lan.pages.chatList` | util + ui + api |
| `85-chat-room.js` | `Lan.chat`：消息、SSE、图片/缩略图 | util + ui + api + lightbox |
| `88-login.js` | `Lan.pages.login` | icons |
| `90-boot.js` | 启动编排：装雪碧、初始化外观、按 `body[data-page]` 调对应 init | 全部 |

规则：

- 文件头 `var x = Lan.y.x;` 就是**显式依赖**，少一个会在第一次调用时 ReferenceError，
  而不是像以前那样静默拿到 undefined；
- 依赖只能从低编号指向高编号（`50` 不能调 `60`），要反向通信就挂个回调到 `Lan` 上；
- 跨分片共享的**状态**一律挂在 `Lan.<模块>.state`（`Lan.files.state`、`Lan.chat.state`），
  不再有裸变量；
- 每个分片**必须以换行结尾**，拼接时不补也不减；
- 改完跑 `python tools\build_assets.py`，它会打印 css/js/icon 体积。

`tools/preflight.py` 会检查每个分片都确实包了自己的 IIFE、顶层没有声明泄漏——
哪天有人图省事写在顶层，检查直接红。

### 对外接口：`window.Lan`

```js
Lan.version        // '6.1'
Lan.util           // esc / readJson / downloadUrl / copyText
Lan.icons          // icon(name, cls) / injectSprite / hydrateIcons
Lan.appearance     // state / palettes / apply() / openPanel() / init()
Lan.ui             // toast / openModal / closeModal / openMenu / closeMenu
Lan.api            // fetch / write / json / askAdmin / ensureAdmin / ctx
Lan.files          // state / render / refresh / run(q) / zip / relocate / bindList
Lan.upload         // queue / pump / render / init / initFolder
Lan.lightbox       // open(src, name, dlUrl) / init()
Lan.chat           // state / init / render / append / send / load / poll
Lan.pages          // files / chatList / login（各页面唯一的启动入口）
```

控制台里可以直接 `Lan.chat.state.msgs.length`、`Lan.files.refresh()`。
`tools/ui_test.py` 有 5 条断言盯住这个契约，改坏了会红。

## 七、还没做的（下一轮候选）

| 项 | 为什么先留着 | 建议做法 |
|---|---|---|
| 桌面端改版 | Qt 界面还是第一版的样子（四个 emoji 按钮 + 两个只读框） | 按 `docs/DESKTOP-UI.md` 的 P1 先换 QSS，再动布局 |
| `50-files.js` 再切 | 573 行仍偏长（渲染 / 事件 / 请求混在一起） | 有 `Lan.files` 这层契约后可以安全切成三片 |
| 打包缓存的磁盘配额 | 默认可续传会落一份缓存（过期自动删），超大目录可能占不少盘 | 加"缓存超 N GB 就只允许 stream"的策略 |
| 打包路径限制 | 只能在 ASCII 路径下打，中文路径会挂 | 等上游修 Qt 钩子的编码；目前靠"复制到 E:\lanbuild 再打"绕过 |

## 八、功能一览（截至本轮）

| 类别 | 能力 |
|---|---|
| 文件 | 浏览 / 上传（分片 + 断点续传 + 暂停 / 继续 / 重试）/ 下载（Range）/ 改名 / 删除 / 新建目录 / 拖拽上传 |
| 批量 | 打包下载（可断点续传，`?stream=1` 走流式）/ 移动 / 复制 / 删除；共享内递归搜索 |
| 聊天 | 多房间、SSE 实时推送、图片（缩略图 + 点开看原图 + 孤儿清理）、消息搜索、删除/清空 |
| 传输层 | HTTP/1.1 长连接、br/gzip 压缩、带版本号的长缓存、并发闸门、打包流限流 |
| 界面 | 桌面端 Qt（托盘、二维码、开机自启）；Web 端玻璃拟态 + 6 套配色 × 明暗 × 3 档玻璃 |

