# 第六版 · 变更说明

基于 `第五版/lanshare.py` 改造，`第五版` 未做任何修改。

## 一、安全与正确性（P0）

| # | 问题 | 处理 |
|---|---|---|
| 1 | `GET /delete` 可被任意网页 CSRF 删除文件 | `/delete` 只允许 POST，GET 返回 405；所有 POST 增加同源校验（`Origin`/`Referer` 与 `Host` 比对），跨站返回 403 |
| 2 | 「访问密码留空 + 管理密码非空」时管理员永远解不开 | `/verify_admin` 在无会话时自动创建会话并下发 Cookie；密码比较改为 `hmac.compare_digest` |
| 3 | 断点续传只看文件大小 → 同名不同内容被拼坏 | `/check_exists` 需要客户端上报 `size + mtime`，服务端用 `UPLOAD_STATE` 记录可信偏移，指纹一致才允许续写；`offset > 已写入长度` 直接 400；新增 `/upload/done` 清理记录 |

## 二、顺手修掉的

- **同名文件上传**：分片上传遇到同名文件不再静默覆盖，弹「覆盖 / 另存副本 / 跳过」；副本名由 `/upload/unique` 给出（`a.txt → a (2).txt`）。
- **安全响应头**：`nosniff` / `X-Frame-Options` / `Referrer-Policy` / `Permissions-Policy`；`Server` 头不再暴露 Python 版本。
- **预览不再能执行脚本**：只有白名单后缀允许 `inline`，其余强制 `attachment`；预览响应带 `CSP: default-src 'none'`（SVG/HTML 里的脚本不会跑）。
- **聊天室原子写**：先写 `.tmp` + `fsync` + `os.replace`，并保留 `.bak`；主文件损坏时自动从备份恢复。
- **图标**：删掉 179 KB 内嵌 base64 与 2048×2048 逐像素抠图（实测 ≈4 秒启动开销），改为加载 `icon.ico`（内含 6 档尺寸）；单文件 exe 旁边没有 ico 时，用 `webassets.ICON_ICO_B64` 里内嵌的那份兜底。
- **日志**：按你的要求保留「打印用户输入的口令」（仅本机自用）。

## 三、Web 端改版（玻璃拟态 + 可换配色）

- 新增 `web/` 目录：`tokens.css`（6 套配色 × 明暗 × 三档玻璃）、`app.css`、`app.js`、`pages/*.html`。
- `tools/build_assets.py` 把它们打包成 `webassets.py`，运行时由 `lanshare.py` 直接 import，**打包仍是单 exe，无需 include-data**。
- 新增路由：`/static/app.css`、`/static/app.js`（带 ETag/304）、`/favicon.ico`、`/api/list`（列表 JSON，供无刷新刷新用）。
- 页面结构：统一外壳（侧栏 / 移动端顶栏 / 底部导航），文件页、聊天室列表、聊天室、登录页全部换新。
- 交互：Toast / Dialog / 下拉菜单替代 `alert/confirm/prompt`；上传队列（并发 2 路、逐文件进度、可取消）；列表排序/搜索/网格视图/多选操作条；聊天按天分组、悬停操作、回到底端、图片灯箱。
- 外观：左下角「外观」面板选择配色 / 主题 / 玻璃强度，存 `localStorage`。

## 四、开发时怎么改

```powershell
# 改完 web/ 下的 css/js/html 后重新生成
python tools/build_assets.py
# 语法检查
python -m py_compile lanshare.py
```

## 五、打包（单 exe，方式不变）

CSS / JS / 页面模板 / 图标都已经进 `webassets.py`，所以**不需要**任何 `--include-data-file`：

- 入口脚本：`lanshare.py`
- 同目录必须一起带上：`webassets.py`（Nuitka / PyInstaller 都会顺着 `import webassets` 自动收进来）
- `icon.ico` 可选：打包后 exe 旁边没有它也能显示图标（走 `webassets.ICON_ICO_B64`）

缺 `webassets.py` 时启动会弹一个明确的提示框，而不是打开一个白页面。

Nuitka 参考命令：

```powershell
nuitka --onefile --enable-plugin=pyqt5 --windows-console-mode=disable ^
       --output-filename=局域网共享.exe lanshare.py
```

> 说明：本机试用 Nuitka 4.1.1 时卡在 `pyqt5: failed to detect PyQt5 plugin directories`，
> 原因是这台机器的路径含中文（`E:\code\局域网\...`）且 Qt 环境被别处注入过插件路径，
> 与代码无关；你现有打包工具能产出 exe，按原方式打即可。

## 六、验证记录

| 项 | 方式 | 结果 |
|---|---|---|
| GET /delete | curl | 405，文件未被删 |
| 跨站 POST | `Origin: http://evil.example` | 403 |
| 同源 POST（含端口不同） | curl | 302 / 200 正常 |
| 管理员解锁（访问密码空 + 管理密码） | curl | 200 + 下发 Cookie，随后上传 200 |
| 续传指纹 | 传 5 字节 → 再查 | 一致 `resume=true offset=5`；改指纹 `resume=false`；`/upload/done` 后清空 |
| 同名上传 | 传 4 字节副本 | 生成 `keep (2).txt`，原文件保留 |
| 空文件上传 | 0 字节 | 成功建 0 字节文件 |
| 移动端横向溢出 | 390 CSS px 渲染 | 无溢出，左右边距对称 |
| `webassets` 导入 | 换工作目录导入 | 正常，不依赖 cwd |
| 图标兜底 | 删掉 icon.ico | 仍取到 91463 字节内嵌图标 |

## 七、尚未做（下一轮）

HTTP/1.1 + gzip + 静态长缓存、并发线程上限、ZIP 流式打包、聊天 SSE 增量渲染、缩略图与列表虚拟化。

## 八、第二轮：路由表化 + 接口再分家 + 并发闸门

上一轮把「一个大文件」拆成了包，但 `do_GET` / `do_POST` 还是两个 500 多行的 if 链，
`files_api.py` 一个文件也塞了 690 行。这轮继续：

| 变化 | 前 | 后 |
|---|---|---|
| 路由 | 两个 if 链，共 558 行 | 三张表（GET / POST / 前缀）+ `_dispatch()`，`do_GET`/`do_POST` 各约 20 行 |
| 聊天接口 | 散在 do_GET / do_POST 的 10 个分支里 | `server/chat_api.py`，一接口一方法（10 个） |
| 文件接口 | `files_api.py` 15 个方法 690 行 | 拆成 `listing.py` / `transfer.py` / `archive.py` / `fs_ops.py` |
| 并发 | `ThreadingMixIn` 无限起线程 | `FastBindHTTPServer` 32 个名额，排队超 10 秒回 503 |
| 重复代码 | 6 处 `send_response(302) + Location + end_headers` | `self.redirect('/login')` |
| 取参数 | 到处 `query_params.get('x', [d])[0]` | `self.q('x', d)` |

鉴权从分支里挪进表里，五种标记（`AUTH_PUBLIC` / `AUTH_REDIRECT` / `AUTH_403` /
`AUTH_ADMIN` / `AUTH_ADMIN_JSON`），加接口时不用再想「这条要不要判权限」。

新增两个测试工具：

| 工具 | 覆盖 | 结果 |
|---|---|---|
| `tools/auth_matrix_test.py` | 未登录 / 登录非管理员 / 管理员 / 跨站 四组共 33 项 | 全部通过 |
| `tools/limits_test.py` | 名额为 0 时 503、默认名额下 20 并发、串行 | 全部通过 |

`tools/smoke_test.py` 也补了 ZIP 打包下载（现在 24 项）。

## 九、第三轮：前端 1781 行拆成 11 个分片

`web/app.js` 是一个 IIFE，函数之间互相调用、共享 `fsState` / `chat` / `CTX` 等变量，
所以没有做「命名空间 + 模块」那套（那会改动上百处调用点），而是：

**把源码按功能切片，仍然拼进同一个 IIFE。**

| 分片 | 内容 | 行 |
|---|---|---|
| `10-icons.js` | 图标 sprite | 60 |
| `20-appearance.js` | 配色 / 明暗 / 玻璃 | 160 |
| `30-ui.js` | Toast、弹窗、菜单 | 185 |
| `40-api.js` | 请求封装、管理员流程 | 59 |
| `50-files.js` | 文件列表与工具栏 | 423 |
| `60-upload.js` | 上传队列 / 分片 / 冲突 | 280 |
| `70-lightbox.js` | 图片预览 | 53 |
| `80-chat-list.js` | 聊天室列表 | 104 |
| `85-chat-room.js` | 聊天室 | 395 |
| `88-login.js` | 登录页 | 32 |
| `90-boot.js` | 启动 | 24 |

`tools/build_assets.py` 负责按文件名排序拼接，骨架（注释 / IIFE / `'use strict'`）写死在
`js_source()` 里。拼接规则只有一条：**分片文件以换行结尾，拼接时既不补也不减换行**——
一开始我用了 `rstrip("\n")` + `"\n".join()`，结果每两个分片之间的空行被吃掉，
`JS` 少了 10 个字节；改成原样 `''.join()` 后逐字节还原。

怎么证明没改坏：把新生成的 `webassets.JS` 与拆分前的 `app.js` 直接比对，
65679 字节完全一致；CSS、5 个页面模板、内嵌图标也都一致。

## 十、第四轮：HTTP/1.1 长连接 + gzip

局域网带宽不是瓶颈，但「打开一页要 4 个文件、每个都从头下载」是真的慢。
这轮把传输层补上：

| 改动 | 位置 | 效果 |
|---|---|---|
| 开 `HTTP/1.1` 长连接 | `handler.py`（`protocol_version`） | 一页的 html/css/js/favicon 复用同一 TCP 连接 |
| 空闲连接 60 秒超时 | `handler.py`（`timeout`） | 挂死的浏览器连接不会占着并发名额 |
| 三个出口 gzip | `http_util.py`（`_maybe_gzip`） | 首屏 131 KB → 31.3 KB |
| `Vary: Accept-Encoding` | 同上 + 304 响应 | 让中间缓存知道内容随编码变化 |
| 302 / 416 补 `Content-Length: 0` | `http_util.redirect` / `auth._issue_session` / `transfer` | 长连接的硬前提 |

实测压缩比（同一份源码）：

| 文件 | 原始 | gzip | 省 |
|---|---|---|---|
| `app.css` | 57.3 KB | 12.4 KB | 78% |
| `app.js` | 66.9 KB | 16.9 KB | 75% |
| 首页 HTML | 6.8 KB | 2.0 KB | 71% |
| **一页首屏合计** | **131.0 KB** | **31.3 KB** | **76%** |

长连接的坑就在「响应必须带 Content-Length」——少了这一个头，浏览器会一直等响应体，
表现成页面卡住不渲染，从功能测试里完全看不出来。所以加了
**`tools/protocol_test.py`**（31 项）：把 20 个接口全打一遍逐条检查响应头，
再验证同一条连接连发 4 个请求、gzip 解压后与明文一致、ETag 命中回 304。

顺手修的小问题：聊天室里图片文件被删掉后，`<img>` 会 404 并显示破图。
现在在捕获阶段接住 `error` 事件，换成「图片已失效」的虚线占位（`app.css` 里
`.msg-img-broken`）。控制台那行 404 是浏览器自己记的网络日志，脚本无法消除。

## 十一、第五轮：聊天改 SSE 推送

原来前端每 2 秒拉一次 `/chat/history?after=...`：消息延迟最多 2 秒，每次还得重复下载
整个 JSON（历史越长越慢）。这轮换成服务端推送：

| 项 | 原来 | 现在 |
|---|---|---|
| 新消息到达 | 前端每 2 秒轮询一次 | `GET /chat/stream` 长连接，服务端 0.3 秒内推过来 |
| 一次会话的请求数 | 每分钟约 30 个请求 | 1 个连接（25 秒一续） |
| 消息延迟 | ≤ 2 秒 | ≤ 0.3 秒 |
| 断线/不支持 | — | 连续失败 3 次自动退回轮询；切后台断开、回来补历史再重连 |

实现要点：

- **响应不能有 Content-Length**：SSE 是流式响应，靠关连接结束，所以显式
  `Connection: close` + `X-Accel-Buffering: no`，每帧写完立刻 `flush()`。
- **名额要管住**：每条流占一个处理线程，所以限制最多 8 条、每条活 25 秒；
  满了回 503，前端自动退轮询。到期主动断，让浏览器重连时回收线程。
- **重复消息靠 id 去重**：重连时 URL 里的 `after` 是旧的，会重发一段，
  `chatAppend()` 按 `msg.id` 去重，所以不会重复上屏。
- **心跳顺手探活**：10 秒没消息就发 `: ping`，对端没了这里就会抛异常，线程及时退出。

验证（`tools/sse_test.py`，12 项）：连上拿到 `retry:` 帧 → 另一个连接发消息 →
5 秒内收到 `data: {...}` 帧且字段完整 → 不存在的房间 404 → `after` 只回增量。

浏览器实测（headless Chromium，6 秒窗口）：

| 请求 | 次数 |
|---|---|
| `/chat/history?room=default`（首次加载） | 1 |
| `/chat/stream?...`（`resource_type: eventsource`，响应 200，无 Content-Length） | 1 |
| 后续轮询请求 | **0**（换 SSE 之前同样的窗口是 4 次） |

## 十二、第六轮：聊天图片缩略图

以前聊天里发一张 3MB 的图，进房间就要下载 3MB（原图直接当 `<img src>`）。
现在列表只加载几 KB 的缩略图，点开才取原图。

| 项 | 原来 | 现在 |
|---|---|---|
| 进房间的图片流量 | 原图（可能几 MB × N 张） | 缩略图（每张约 5~20 KB） |
| 点开看大图 | 一样 | 一样（`data-img` 存的始终是原图） |
| 缩略图在哪生成 | — | 浏览器 canvas，最长边 320px、JPEG q=0.72 |
| 服务端要装 PIL 吗 | — | **不用**，只做 base64 落盘（`MAX_CHAT_THUMB_BASE64_LEN` 200KB 兜底） |

存储方式：缩略图和原图放同一目录，文件名在原图主体上加 `_t`
（`20261019_xxx.png` + `20261019_xxx_t.jpg`）。这样删消息时按主体名 glob 一次，
原图和缩略图一起删，不留孤儿文件。

三个兼容性决定：

- **老客户端**只发 `image`、不发 `thumb` → 服务端照旧只存原图，前端 `m.thumb || m.image` 回退。
- **缩略图超限或不是合法图片** → 静默丢掉缩略图，原图仍保存成功（不因为缩略图失败而发不出消息）。
- **老消息没有 `thumb`** → 渲染时用原图，行为跟以前一致。

验证（`tools/thumb_test.py`，16 项）：发图 → 两个 URL 都能取且内容不同 → 历史里带 `thumb`
→ 删消息后原图和缩略图都 404 → 缩略图超限/非法时只存原图。

浏览器实测（`--dump-dom`）：聊天室里渲染出的 `<img src="..._t.png" data-img="...png">` —
`src` 是缩略图、`data-img` 是原图，点开走原图 ✓

## 十三、第七轮：brotli（可选依赖）

浏览器发的 `Accept-Encoding` 里本来就带 `br`，服务端装了 `brotli` 就一直没用上。
这轮补上，**装了才生效，没装自动退回 gzip**（`pip install brotli`，纯 Python 包）。

| 文件 | 原始 | gzip | brotli |
|---|---|---|---|
| `app.css` | 57.7 KB | 12.5 KB | 12.1 KB |
| `app.js` | 71.1 KB | 18.5 KB | 17.3 KB |
| 首页 HTML | 6.8 KB | 2.0 KB | 1.7 KB |
| **合计** | **135.6 KB** | **33.0 KB** | **31.0 KB** |

**如实说：brotli 只比 gzip 再省 5.9%**（约 2 KB/次）。我之前在文档里写“再省 15~20%”
是照一般经验估的，在这个项目的 CSS/JS 上没达到。而且因为静态资源是 `no-cache + ETag`
（改完源码立刻生效），浏览器正常浏览时走 304 不下载正文，所以这 2 KB 只在首次加载
或刚重新构建后才有意义。

代码上没什么可挑的：`_maybe_gzip` 改名 `_encode_body`，按 `br > gzip > 不压` 挑一个，
`Vary: Accept-Encoding` 本来就带了。质量参数用 `q=5`（比 q=11 快一个数量级，体积只差几个百分点）。

验证：`tools/protocol_test.py` 新增 6 项，且**两种解释器都跑了**——

| 环境 | br 支持 | 结果 |
|---|---|---|
| 全局 python（没装 brotli） | 未安装 | 只给 `br` 时不压、同时给 `gzip,br` 时用 gzip ✓ |
| 项目 `.venv`（`brotli 1.2.0`） | 支持 | br 解压后与明文一致、比 gzip 小、带 Vary、长度正确、同时接受时优先 br ✓ |

浏览器实测（headless Chromium）：html/css/js 三个响应全是 `Content-Encoding: br`，
首屏总计约 **32.2 KB**（原始 135.6 KB）。

另外补了 `requirements.txt`，把「必装 / 可选 / 只在构建时需要」三类依赖写清楚。

## 十四、第八轮：ZIP 流式打包

以前导出文件夹：先写一个完整临时 zip，写完再发。
一个 2GB 的目录就是「占 2GB 额外磁盘 + 干等几十秒才开始下载」。

现在分两步：

| 步骤 | 干什么 | 耗时 |
|---|---|---|
| `POST /zip` | 只把「要打包哪些文件」展开成清单（`os.walk`），登记进内存 | 毫秒级 |
| `GET /zip_download` | 一边 `zipfile.write()` 一边往连接里写 | 立刻开始下载 |

技术上靠两点：

- `zipfile` 支持写**不可 seek 的流**：发现 `seek` 不可用就改用 data descriptor
  记录大小和 CRC，所以不需要提前知道总长度、也不需要回退重写。我加了个
  `_ZipSink` 把 `wfile` 包一层（只实现 write/flush/tell，seek 抛
  `UnsupportedOperation`），实测 3MB 随机文件打出来 `testzip()` 无损坏。
- 流式响应不能带 `Content-Length`，所以显式 `Connection: close` 靠关连接结束——
  跟 SSE 一个套路。

配套改动：

- `ZIP_DOWNLOAD_STORE` 里存的是清单而不是文件路径，`cleanup_zip_store()`
  只丢过期清单（老版本留下的 `path` 字段还在时顺手删文件）。
- 打包也限流：最多 4 条同时进行（`ZIP_STREAM_MAX`），满了回 503，
  免得导大目录把 32 个处理名额全占住、页面都打不开。

验证（`tools/zip_test.py`，16 项）：单文件 / 整个子目录（含 3MB 随机文件）都能
解开、CRC 正确、内容一致、子目录结构保留；响应无 `Content-Length`、
带 `Connection: close`；系统临时目录里**没有新增 zip**；不存在的 id 404、
越权路径被拒、空选择 400。

## 十五、一个环境问题（顺手记一笔）

这轮跑测试时冒烟测试突然挂了几项，上传回 **507**。查下来不是代码问题：
`transfer.py` 里有「剩余空间不足 512MB 就拒绝上传」的保护，而这台机器的
**C: 盘只剩 232MB**（`C:\Windows\TEMP` 里堆了 40 多个 93MB 的 `_MEI*`
PyInstaller 残留、若干 125MB 的临时文件、`vscode-stable-system-x64` 等）。

处理：

- 只清了我自己造成的那部分（`C:\Windows\TEMP\lan6` 里历次 Chrome 配置目录，约 204MB），
  **别人的残留没动**——那些要清得你自己确认为准（磁盘清理或 `%TEMP%` 手动筛）。
- 改了测试工具：临时共享目录建在**项目所在盘**（E:，剩 423GB）而不是系统临时目录，
  这样测试结果不再受 C: 盘空间影响。

507 这个保护本身是对的，别关掉。

## 十六、第九轮：共享内搜索

之前搜索框只能过滤**当前目录已加载的**条目——想找一个不知道放哪的文件，只能一层层翻。
这轮补上服务端递归搜索。

| 项 | 之前 | 现在 |
|---|---|---|
| 搜索范围 | 当前目录（内存过滤） | 整个共享（或指定起点目录），递归 |
| 触发方式 | 边打边过滤 | 打回车才搜（`Esc` 退出），输入框占位符已写明 |
| 结果 | 直接筛列表 | 每条显示文件名 + 所在目录，点整行跳过去 |

接口：`GET /api/search?q=关键词&path=起点`

- **三重上限**：最多 80 条结果、最多扫 20000 项、最多 4 秒；超了就把 `truncated`
  置 true，前端显示「结果过多已截断」。有人对着几万文件的目录按回车也不会把服务端卡死。
- **跳过 `.git` / `node_modules` / `__pycache__` 和隐藏目录**，这些地方搜出来也没意义。
- **不越界**：起点目录和每个命中项都要过 `is_path_within`，越权直接 403。
- 中文关键词、大小写不敏感都支持（`str.lower()` 比对）。

验证（`tools/search_test.py`，16 项）：递归找到 3 个 report（含子目录里的）、
不扫 `node_modules`、结果带 `dir` / `dirHref` / `encPath`（点开能跳）、
中文命中、指定起点只搜那棵子树、空关键词 400、越权 403、无结果也是 200。

浏览器实测：在搜索框输入 `rooms` 回车 → `.search-note` 与结果行在 40ms 内出现，
两条结果（`rooms.json`、`rooms.json.bak`）的 `href` 都指向
`/dir?path=...\chat_data`，无 console 报错。

## 十七、第十轮：静态资源长缓存 + SSE 真唤醒

这轮不加功能，只收尾两笔「已经写好但没榨干」的性能账。

**1. 静态资源长缓存（带版本号的 URL）**

以前 CSS/JS 是 `no-cache + ETag`，每打开一页都要两次 304 往返（本地 1ms，但也是两个请求）。
现在页面里引用的是 `/static/app.css?v=<CSS+JS 的内容摘要>`：

| 请求 | 响应头 | 效果 |
|---|---|---|
| 带对 `v`（页面自己生成的） | `public, max-age=31536000, immutable` | 第二次开始浏览器**完全不发请求** |
| 不带 `v` / `v` 对不上 | `no-cache` + ETag | 手敲地址或旧页面留着的老 URL 不会拿到过期内容 |

改完 `web/` 重新 `build_assets.py` → 摘要变了 → URL 变了 → 浏览器自然取新的。
`ASSET_VERSION` 在 `pages.py` 里算一次（sha1(CSS+JS)[:10]），页面渲染时填进
`{{ASSET_V}}`。

**2. 聊天推送改成真唤醒**

SSE 原来是「每 0.3 秒查一次内存」——够用，但闲着也在空转。现在
`chat_store` 里挂了一个 `threading.Condition`，发消息时 `notify_all()`，
连接立刻被唤醒；没消息时就在条件变量上等，最多等 0.3 秒兜底。

实测推送延迟（连发 5 条，从 POST 到 SSE 收到）：

```
13ms, 12ms, 11ms, 11ms, 11ms   中位数 11ms
```

改之前最坏要等一个 0.3 秒的轮询周期。

验证（`tools/protocol_test.py` 新增 5 项）：

| 检查 | 结果 |
|---|---|
| 页面里的 CSS 带 `?v=` | ✓ |
| 带对 `v` → `immutable` + `max-age=31536000` | ✓ |
| 不带 `v` → `no-cache` | ✓ |
| `v` 对不上 → `no-cache` | ✓ |

浏览器实测：CSS/JS 请求 URL 都带 `?v=7322c185df`，聊天页仍是
「1 次 history + 1 次 stream + 0 次轮询」，无 console 报错。

## 十八、修一个我自己引入的 bug：发一条消息显示两条

**现象**：聊天室点一次「发送」，列表里出现两条一样的消息。

**排查**：用浏览器抓网络 —— 只发了 **1 次** `POST /chat/send`，但 DOM 里有两行
`data-idx`。也就是说服务端只存了一条（查历史确认 `DUP-TEST-1` 只出现 1 次），
重复是**前端本地多插了一条**。

**原因**：上一轮把 SSE 从「0.3 秒轮询」改成「条件变量即时唤醒」后，时序反了过来：

| 顺序 | 之前（轮询） | 现在（即时推送） |
|---|---|---|
| 1 | POST 发出，响应先回来 → 本地 push | POST 发出，**SSE 先推回来** → `chatAppend` 插入 |
| 2 | 0.3 秒后 SSE 才把同一条推来 → 被 id 去重丢掉 | POST 响应随后到 → `chatSend` 里 `chat.msgs.push()` **又插一条** |

`chatSend` 的成功回调绕过了去重函数、直接 `push`；之前靠「响应总比推送快」侥幸没暴露，
推送变快之后必然翻车。

**修法**（3 处，都在 `85-chat-room.js`）：

1. `chatSend` 成功后改走 `chatAppend(d.msg, true)`——自己发的消息即使已被推送插过，
   也只补一次滚动，不再插第二条；
2. `chatAppend(m, mine)` 加第二个参数：自己发的消息强制滚到底、不计未读数；
3. `chatPoll`（轮询兜底路径）也改走 `chatAppend`，避免 SSE 与轮询短暂并存时插重。

顺手修了同一处的显示问题：顶部「N 条消息」原来只在页面渲染时算一次，发完消息数字不动；
现在 `chatRender` 里跟着更新（实测发完后从 7 变 8）。

**验证**：同样的操作（填 `DUP-TEST-3` → 点发送 → 等 1.2 秒），无障碍树里
`DUP-TEST-3` 只出现 **1 次**，顶部计数变 8，无 page-error；测试期间插进房间的
3 条 `DUP-TEST-*` 已通过接口删掉。

> 这个 bug 只有在**重启程序**后才会消失：`webassets.py` 是启动时 `import` 的，
> 老进程内存里还是旧 JS。改完前端必须「重新 build_assets + 重启程序 + 刷新页面」。

## 十九、第十一轮：测试补强 + 打包验证 + 三个功能

按「A→B→C→D」的顺序做，逐项说结果。

### A. 前端行为测试（补上最大的测试缺口）

上一轮那个「点一次发送出现两条」的 bug，当时的八套测试一个都没抓到——它们都在 HTTP 层，
碰不到前端。这轮新增 `tools/ui_test.py`：

- 用 **Chrome 自带的调试协议**驱动，不装 Playwright/Selenium；
- 自己实现了一个极简 WebSocket 客户端（握手 + 文本帧，约 80 行）跟 CDP 说话；
- 现在有 **18 条断言**：一次发送只多一条、顶部计数跟着变、聊天记录能过滤、
  开的是 SSE 不是轮询、文件页搜索回车出结果、无 JS 异常。

**验证这个测试有牙**：把 bug 代码注回去（`push` + 关掉渲染兜底）再跑，它准确红在
「仍是 1 条（没有重复）实际 2 条」，退出码 1；恢复后全绿。

顺手加了渲染前的**按 id 去重兜底**：不管从哪条路进来，画面上都不会重复。

### B. 打包（终于跑通了，根因不是 Adobe）

| 尝试 | 结果 |
|---|---|
| Nuitka 4.1.1 + 项目 venv | ❌ `failed to detect PyQt5 plugin directories` |
| PyInstaller + 项目 venv | ❌ `Qt plugin directory '...Qt5/plugins' does not exist`（日志里路径变成了 `???`） |
| **PyInstaller + ASCII 路径的新 venv** | ✅ **成功，39.7 MB 单文件 exe** |

**根因是路径里有中文**：项目在 `E:\code\局域网\第六版`，PyInstaller 的 Qt 钩子拿到的路径
变成 `E:/code/???/...`，于是"插件目录不存在"。Nuitka 的检测失败大概率同源
（之前一直以为是 Adobe 往 `QT_PLUGIN_PATH` 里塞路径，其实清掉也没用）。

打包步骤（已验证）：

```bat
:: 1) ASCII 路径的独立环境
D:\Program Files\python\python.exe -m venv E:\lanvenv
E:\lanvenv\Scripts\python.exe -m pip install PyQt5 pyinstaller brotli segno

:: 2) 源码复制到 ASCII 目录再打包
xcopy /E /I 第六版 E:\lanbuild
cd /d E:\lanbuild
E:\lanvenv\Scripts\python.exe -m PyInstaller --onefile --windowed ^
  --name juyuwang --hidden-import webassets --add-data "E:\lanbuild\icon.ico;." lanshare.py
```

**实测 exe 能跑**（`--startup -p 6330`）：

| 请求 | 结果 |
|---|---|
| `/`、`/login` | 200（显示登录页，因为本机 QSettings 里存着访问密码） |
| `/static/app.css` | 200，59685 字节（资源在 exe 内部） |
| `/favicon.ico` | 200，91463 字节（内嵌图标生效） |

新增 `tools/preflight.py`：打包前在**目标解释器**里自检 PyQt5 插件目录、项目模块能否
import、可选依赖、`webassets` 版本、磁盘空间。它顺手解决一个坑：PyQt5 找不到自己的
platforms 目录时要显式设 `QT_QPA_PLATFORM_PLUGIN_PATH`。

### C. 两个小收尾

1. **启动打印资源版本**：`[Web] 前端资源版本 xxxxx（改动 web/ 后需 build_assets.py + 重启）`
   —— 以后"改了没生效"能一眼对照页面里的 `?v=`。
2. **老消息补缩略图**：读聊天历史时，对只有原图的消息用 Pillow 补 320px 缩略图
   （每次最多补 20 张，避免首屏卡住）；没装 Pillow 就静默跳过。

### D. 功能

1. **聊天记录搜索**（纯前端）：顶栏加搜索框，输入即过滤，顶部显示「找到 N 条」。
   顺带把消息行的定位从 `data-idx` 改成 `data-id`，过滤时点删除不会删错消息。
2. **批量移动 / 复制**：选中多项后操作栏多了「移动」「复制」，目标写相对路径
   （`docs/2024`），不存在会自动创建；同名自动加序号；不支持 `..` 往上跳。
3. **上传暂停 / 继续 / 重试**：暂停在「当前块传完」时生效，并且跳过 `/upload/done`，
   所以服务端偏移还在，点继续就从断点接着传。

### 验证

十套测试全绿，其中三套是本轮新增/扩充：

| 工具 | 覆盖 | 结果 |
|---|---|---|
| `tools/ui_test.py` | 18 条前端行为断言（含那个重复消息 bug） | ✓ |
| `tools/fsops_test.py` | 移动/复制：自动建目录、同名序号、搬进自己、`..` 拒绝 | ✓ |
| `tools/smoke_test.py` | 新增「暂停后继续」的协议级验证（不调 done 偏移仍在） | ✓ |
| `tools/preflight.py` | 打包环境自检 | ✓ |

## 二十、第十二轮：exe 图标 + 一键打包脚本

### 为什么 exe 还是 PyInstaller 的默认图标

上一轮打包只加了 `--add-data icon.ico;.`——那是把 ico **当数据文件塞进 exe**，给程序运行时
自己用（窗口图标、favicon 兜底）。**exe 在资源管理器/任务栏里显示的那个图标**由另一个参数决定：
`--icon`。当时没写，于是 PyInstaller 用了它自带的 `icon-windowed.ico`。

改法就是加一个参数：

```bat
PyInstaller ... --icon icon.ico --add-data "icon.ico;." ...
```

两个参数各管一件事，都要有：

| 参数 | 管什么 |
|---|---|
| `--icon` | exe 文件本身的图标（资源管理器、任务栏、快捷方式） |
| `--add-data` | 程序运行时读得到的 ico（窗口图标 / favicon 数据） |

**怎么确认改对了**：直接解析 PE 资源。改之前图标组是 PyInstaller 默认的 7 张
（16/32/48 各有一张 8bpp，再加 32bpp 的 256/48/32/16）；改之后变成我们 `icon.ico` 里的
6 张，全 32bpp，字节数与 ico 条目一一对应：

| 尺寸 | 字节 |
|---|---|
| 16×16 | 713 |
| 32×32 | 2141 |
| 48×48 | 4092 |
| 64×64 | 6631 |
| 128×128 | 21285 |
| 256×256 | 56499 |

改完 exe 仍能正常跑（`/login` 200、`/static/app.css` 60264 字节、`/favicon.ico` 91463 字节）。

### 顺手把打包做成一键脚本

一开始写成 `tools/build_exe.ps1`，结果在 **Windows PowerShell 5.1** 上踩了三次：

| 问题 | 现象 |
|---|---|
| 脚本编码 | 没有 BOM 时按 ANSI 读，中文全变乱码 + 语法错误 |
| 子进程 stderr | `$ErrorActionPreference='Stop'` 把 PyInstaller 的正常日志当成异常终止 |
| `2>$null` 重定向 | 同样是 stderr 触发终止，脚本根本走不到下一步 |

换成 `tools/build_exe.py`（Python 里 subprocess + 编码都是确定的），一次通过：

```
=== 检查路径        [OK] 源码含中文但只是复制，打包目录 E:\lanbuild 是 ASCII
=== 准备打包环境    [OK] PyQt5 / PyInstaller / Pillow 就绪
=== 复制源码        [OK] lanshare.py / webassets.py / icon.ico / server / ui
=== 打包前自检      [OK] 自检通过
=== 开始打包        [OK] 生成 juyuwang.exe（45.7 MB）
=== 启动 exe 实测   [OK] /login 200 · /static/app.css 200(60264) · /favicon.ico 200(91463)
```

里面固化了这几件事：ASCII 路径检查、venv 自动创建与依赖安装、只复制运行需要的文件、
用目标解释器跑 preflight、`--icon` + `--hidden-import webassets`、最后**启动 exe 真的访问三个
URL 全 200 才算成功**（避免"打包成功但跑不起来"）。

顺带给 `tools/*.py` 全加了 stdout UTF-8 保护——Windows 控制台默认 GBK，脚本里打印 `✓`
会直接 `UnicodeEncodeError` 崩掉（preflight 就这样崩过一次）。

### 图标核对（最终版 exe）

用 `pefile` 读最终 exe 的 `RT_ICON`：

```
6 张：713 / 2141 / 4092 / 6631 / 21285 / 56499 字节
```

与我们 `icon.ico` 的 16/32/48/64/128/256 六档完全对应（91463 = 91361 + 6 字节头 + 6×16 目录）。
改之前是 PyInstaller 默认的 7 张（含 8bpp 的 16/32/48），现在是自己的图标。

## 二十一、第十三轮：前端命名空间 + 断点续传 + 缩略图清理

三件事一起做，逐条说。

### 1. 前端对外命名空间 `window.Lan`

内部还是共享闭包（同一 IIFE，互相能调），但对外只暴露一层：

```js
Lan.version            // '6.0'
Lan.util               // toast / openModal / openMenu / copyText / downloadUrl / esc / icon
Lan.appearance         // state / palettes / apply()
Lan.api                // fetch / write / json / isAdmin() / ensureAdmin
Lan.files              // state / render / refresh / search / zip / move / copy
Lan.chat               // state / render / append / send / filter(q)
Lan.upload             // queue / pump / render
```

好处很实际：调试时能在控制台直接 `Lan.chat.state.msgs.length`、`Lan.files.render()`；
以后想把某个分片真正拆成独立模块，也是从这里换实现，不用再动调用点。
`ui_test` 加了 5 条断言守住这个契约（存在、6 个模块、`esc` 行为、state 是对象、render 是函数）。

### 2. 打包下载：断点续传

上一轮是纯流式（边压边发、零临时文件），但**没法续传**——因为流式 zip 用的是
data descriptor 布局（大小/CRC 写在数据后面），同一份内容生成到文件时布局不同，
两者的字节流对不上，按 Range 续传会拿到错位内容。

所以做成两种模式：

| 请求 | 行为 | 能续传 |
|---|---|---|
| `GET /zip_download?id=…`（默认） | 先生成到缓存文件，再按 `Content-Length` 发；带 `Accept-Ranges` + `ETag` | ✅ 支持 `Range` / `206` / `416` / `If-Range` |
| `GET /zip_download?id=…&stream=1` | 边压边发，零临时文件、立刻开始 | ❌ 不宣告 `Accept-Ranges`（避免浏览器按错位的字节续传） |

默认走可续传，因为"下到一半断了只能重来"比"晚几秒开始"更让人难受。
文件变了 `ETag` 会变（按 路径+大小+mtime 算），`If-Range` 对不上就回 200 整包。
缓存文件挂在 `ZIP_DOWNLOAD_STORE` 上，过期后由清理线程删掉。

`zip_test` 从 16 项扩到 20 项，新增：默认带 `Content-Length`/`Accept-Ranges`/`ETag`、
`bytes=mid-mid+999` 返回的字节与整包切片**逐字节相同**、开放式 Range、越界 416、
`If-Range` 过期回 200、`stream=1` 仍是流式且不产生临时文件。

### 3. 聊天图片的存储清理

以前删消息会删原图（连带 `_t` 缩略图），但两种孤儿没人管：原图被外部删掉后残留的
缩略图、以及消息被手动改过之后没人引用的图片。新增 `cleanup_chat_images()`：

1. `xxx_t.*` 找不到对应原图 → 删；
2. 原图没被任何房间的任何消息引用 → 连 `_t` 一起删；
3. 只处理 mtime 早于 10 分钟的文件（`grace`），避免删掉刚上传/正在发的。

触发方式两个：**启动时自动清一次**；以及管理员接口 `POST /chat/cleanup[?grace=0]`。
`thumb_test` 新增 9 条断言：三类孤儿都会删、被引用的图片一定保留、`grace` 宽限期生效。

## 二十二、第十四轮：文件行加「直接下载」按钮

以前下载一个文件要「点最右边 ⋯ → 菜单里选下载」。现在每行（`⋯` 左边）多了一个下载图标，
点一下就走 `/download`。

| 项 | 处理 |
|---|---|
| 文件行 | 原生 `<a download>`，不写 JS：右键「另存为」可用，也不干扰行的点击/勾选 |
| 文件夹行 | 同一位置留**空占位**（`visibility:hidden`），保持列对齐，避免误点压整个目录 |
| 表头 | `.col-more` 从 32px → 76px（32 按钮 + 12 间距 + 32 更多），跟行尾对齐 |
| 手机（≤900px） | 跟 `⋯` 一样放大到 36×36，保留可点 |
| 搜索结果行 | **不加**：那整行是跳转链接，不能再嵌 `<a>`（会产生非法嵌套） |

实现上只动了三个地方：`50-files.js::rowHtml` 插入一个元素、`app.css` 加 `.row-dl` 规则、
`.col-more` 改宽度。权限、路由、下载逻辑都没碰。

验证（`tools/ui_test.py` 新增 8 条断言）：

| 断言 | 结果 |
|---|---|
| 每个文件行恰好一个 `a.row-dl` | ✓ |
| `href` 含 `/download?file=`，且带 `download` 属性 | ✓ |
| 真的按这个 href 取一次，拿到 `keep`（5 字节） | ✓ |
| 文件夹行是占位且不可点 | ✓ |
| 表头 `.col-more` 计算宽度 = 76px | ✓ |
| 390px 视口无横向溢出、按钮仍在 | ✓ |

截图（只传一次）：`截图\首页-行内下载.png`

## 二十三、本轮验证记录

| 项 | 方式 | 结果 |
|---|---|---|
| 冒烟测试 | `python tools\smoke_test.py` | 30 项 ✓（含搜索、暂停后继续） |
| 鉴权矩阵 | `python tools\auth_matrix_test.py` | 33 项 ✓ |
| 协议层（gzip 路径） | `python tools\protocol_test.py` | 36 项 ✓ |
| 协议层（brotli 路径） | 同上，用 `.venv` 的 python | 42 项 ✓ |
| SSE 推送 | `python tools\sse_test.py` | 12 项 ✓ |
| **图片缩略图 + 清理** | `python tools\thumb_test.py` | **26 项** ✓（补生成 + 孤儿清理 + 宽限期） |
| **打包 + 断点续传** | `python tools\zip_test.py` | **20 项** ✓（Range / 416 / If-Range / stream=1） |
| 共享内搜索 | `python tools\search_test.py` | 16 项 ✓ |
| 移动 / 复制 | `python tools\fsops_test.py` | 13 项 ✓ |
| **前端行为 + Lan 契约** | `python tools\ui_test.py` | **32 项** ✓（含行内下载 8 条、`window.Lan` 5 条） |
| 并发闸门 | `python tools\limits_test.py` | 503 / 20 并发 / 串行 ✓ |
| 打包环境自检 | `python tools\preflight.py` | 可以打包 ✓ |
| 单文件 exe | `python tools\build_exe.py` | 45.7 MB，`/login`·`/static/app.css`·`/favicon.ico` 全 200 ✓ |
| exe 图标 | `pefile` 读 RT_ICON | 6 张 713/2141/4092/6631/21285/56499，与 icon.ico 一致 ✓ |
| **行内下载按钮** | 浏览器 1280×900 截图 | 文件行有 ↓、文件夹行留空占位、表头对齐 ✓ |
| 行内下载（390px） | CDP 设窄屏 | 无横向溢出、按钮仍在 ✓ |
| SSE 推送延迟 | 连发 5 条量时间 | 中位数 **11ms** ✓ |
| 浏览器实测（聊天） | 一次发送 | 只多 1 条、0 次轮询 ✓ |
| 浏览器实测（搜索） | 输入 `rooms` 回车 | 40ms 内出结果 ✓ |
| 实际传输量 | 首屏 html+css+js | 约 **32 KB**（原始 135.6 KB）✓ |
| 原版未改动 | `第五版\lanshare.py` 时间戳 | 2026/6/12 13:39:30 ✓ |

## 二十四、下一轮建议

1. **把分片真正拆成独立模块**：`window.Lan` 已经是对外接口了，下一步是让每个分片只通过
   `Lan.*` 互相调用（现在还是共享闭包），拆一个跑一次 `tools/ui_test.py`。
2. **`85-chat-room.js` / `50-files.js` 再切**：按「渲染 / 事件 / 网络」分成三个文件。
3. **打包缓存的磁盘配额**：现在 `stream=1` 不落盘、默认可续传会落一份缓存（过期自动删），
   如果要支持超大目录，可以加"缓存超过 N GB 就只允许 stream"的策略。
4. **上传队列的并发数可调**（现在固定 2），以及**限速**（一个下载把带宽占满时）。


## 二十六、第十五轮：前端分片真正独立 + 桌面端改版方案

### 26.1 之前那层"假模块化"

上一轮把 1781 行拆成 11 个分片，但拼接时外套的是**同一个 IIFE**，
所以 11 个文件其实共享一个闭包：`50-files.js` 里定义的 `esc`，
`85-chat-room.js` 直接就能用，谁也不知道自己依赖谁；改一个名字，
要到运行时才炸，而且报的是"某某 is not defined"，看不出是谁在用。

这一轮把这层壳拆了：**每个分片自己是 IIFE**，只在 `window.Lan` 上相遇。

| 之前 | 现在 |
|---|---|
| 11 个分片共享闭包 | 12 个分片各自 IIFE，顶层零声明 |
| 依赖关系靠人记 | 文件头 `var esc = Lan.util.esc;` 是显式契约 |
| 漏了依赖 → 静默 `undefined` | 漏了依赖 → 第一次调用就 ReferenceError |
| 跨文件状态是裸变量 `fsState` | `Lan.files.state` / `Lan.chat.state` |
| 启动逻辑散在 `90-boot` 的 DOMContentLoaded 里，直接调 11 个函数名 | `90-boot` 只查 `PAGES[data-page]` 表，调 `Lan.pages.*` |

新增 `00-core.js`（77 行）：建命名空间骨架 + 把通用工具 `esc` / `readJson` /
`downloadUrl` / `copyText`（含 `execCommand` 兜底）从 `50-files.js` 搬过来，
因为 5 个分片都要用它们，放在文件页里位置就不对了。

现在 `50-files.js` 从 549 行变成 573 行（去掉 49 行工具函数，加上文件头依赖和导出），
`85-chat-room.js` 从 498 行变成 588 行（同上；聊天室的函数本来就多）。

### 26.2 迁移是怎么做的（避免手抖）

正文一行没改，只用一次性脚本做三件事：加文件头（注释 + IIFE + 依赖别名）、
正文整体缩进 +2、末尾挂导出。脚本跑完即删，仓库里不留迁移工具。

改完立刻暴露了两个真问题，正好说明这套契约是有用的：

| 报错 | 原因 | 修法 |
|---|---|---|
| `Object.assign(Lan.icons, {ICONS: None, ...})` | 迁移脚本的"同名导出"用了 `None` 占位 | 手写这段导出 |
| 聊天室页白屏，`Uncaught TypeError: Lan.chat.init is not a function` | `85` 的导出表漏了 `init`（以前是闭包里直接调 `initChatRoom`，不需要导出） | 补 `init` / `streamStop` |

第二个错误很典型：**共享闭包时会漏掉的导出，独立之后一定会漏**，
但只要漏了就是启动即炸，而不是某个操作点下去没反应——这就是这一轮的价值。

### 26.3 顺手加的护栏

`tools/preflight.py` 新增 `check_module_isolation()`：

- 每个分片必须以 `(function (Lan) {` 开头、以 `})(window.Lan = window.Lan || {});` 结尾；
- 分片内部**不允许**出现 2 空格缩进的声明（那意味着又漏回了顶层）。

故意塞一个坏分片验证过，会打印 `[FAIL] 99-bad.js:3 顶层声明泄漏`。

`tools/build_assets.py` 也去掉了公用 IIFE 外壳，`js_source()` 现在就是纯拼接 +
两行文件头注释，docstring 里写清了 12 个分片各自提供什么。

### 26.4 桌面端改版方案（本轮只出方案与样稿）

软件本体的 Qt 界面从第一版起没动过：标题 + 一堆 `QLineEdit` 竖排 + 4 个
等宽 emoji 按钮（🚀 启动 / 🌐 网络 / ⚙️ 设置 / ⏰ 定时）+ 上下分割的两个只读
`QTextEdit`（服务信息、访问日志），QSS 只有 `#f8f9fa` / `#dfe4ea` / `#3498db` 三个色值。

写了两个新文件：

- `docs/DESKTOP-UI.md`：现状逐条分析（含每个控件的行数出处）、设计目标、
  新布局分区、配色/字号/间距规范、P1–P5 分期（P1 只动样式表，P5 才加托盘气泡）；
- `样稿/桌面端.html` + `截图/桌面端-样稿.png`：1040×720 的实际效果，含
  顶部状态胶囊（运行中 · 5995 端口）、共享设置卡片、并排的"访问方式 + 二维码"、
  按状态码着色的访问日志。

二维码那块样稿里是真图（`segno` 生成），实现时也用同一个库，
没装就退化成"显示地址文本"，不影响启动。

### 26.5 本轮验证记录

| 检查 | 结果 |
|---|---|
| `python tools\ui_test.py` | 32 项全部通过（含 `Lan.*` 契约 5 项、无 JS 异常） |
| `smoke_test` / `protocol_test` / `search_test` / `fsops_test` / `zip_test` | 全绿 |
| `auth_matrix_test` / `sse_test` / `thumb_test` / `limits_test` | 全绿 |
| `node --check web/js/*.js` | 12 个分片语法均通过 |
| `python tools\preflight.py` | 可以打包 ✓（新增分片隔离检查通过） |
| 前端产物体积 | `webassets.py` 284.9 KB（js 87.4 KB / css 56.5 KB） |

### 26.6 下一轮建议

1. 按 `docs/DESKTOP-UI.md` 的 P1 落地：先只换 QSS（配色 + 圆角 + 状态胶囊），不碰布局；
2. `50-files.js`（573 行）按「渲染 / 事件 / 请求」再切三片——现在有 `Lan.files` 这层契约，切开是安全的；
3. 上传并发数与限速（一个下载把带宽占满时）；
4. 打包缓存的磁盘配额（`stream=1` 不落盘，默认落一份可续传缓存）。


## 二十七、第十六轮：桌面端界面改版（P1–P4）+ 二维码指向真正的局域网地址

这轮动的是软件本体（Qt 窗口），Web 端没有改。

### 27.1 改版前是什么样

`ui/main_window.py` 的界面部分从第一版起没动过：

```
📂 局域网云盘共享            ← 一行大标题 + 分割线
[共享路径输入框] [📁 浏览]
端口 [ ]  访问密码 [ ]
管理密码 [......]
[🚀 启动服务] [🌐 打开浏览器] [⚙️ 右键菜单] [⏰ 开机自启]
╔ 服务信息 ═══════════════════╗   ← 大 QTextEdit
╠ 访问日志 ═══════════════════╣   ← 另一个大 QTextEdit
╚════════════════════════════╝
● 已停止
```

四个按钮权重完全一样（主操作「启动」混在里面），二维码藏在别处（要双击服务信息
里的地址才弹出），所有地址挤在一个大文本框里想复制某一个很别扭，
QSS 只有一层浅色、圆角 6px、全 emoji。

### 27.2 改成什么样

新布局（`截图\桌面端-实际.png` 是真实窗口抓的图）：

| 区域 | 内容 |
|---|---|
| 顶栏 | 图标 + 产品名 + 版本 + 状态胶囊（灰「已停止」/ 绿「运行中 · 端口 5995」）+ 打开浏览器 + 启动/停止（主按钮，起停时在 primary/danger 间切换） |
| 共享设置卡 | 目录 + 浏览；端口 / 访问密码 / 管理密码 一行三个；**服务运行中整卡只读** |
| 访问方式卡 | 每个地址一行：`推荐 / 本机 / 其他网卡 / 异常` 标签 + 等宽地址 + 复制按钮；推荐那条加粗 |
| 手机扫码卡 | 168px 二维码 + **地址下拉框**（默认选中路由器那条）+ 提示 |
| 访问日志卡 | 等宽、按状态码着色（2xx 绿 / 3xx 蓝 / 4xx 橙 / 5xx 红 / 系统消息灰）+ 暂停·继续 / 清空 / 导出… |
| 底栏 | 提示文字 + **添加到右键菜单** + **开机自启**（已开启时变绿） |

配色/圆角/字号全部收到 `ui/style.py`（`QSS` 里不出现裸色值），
判断"哪个 IP 才是路由器那条"的逻辑单独放 `server/netinfo.py`，
二维码渲染放 `ui/qr.py`（没装 segno 就退化成文字提示，不影响启动）。

### 27.3 二维码到底该指向谁

这是这轮最关键的一个判断。原来点出来的二维码用的是「服务信息里双击的那一行」，
默认往往就是 `http://localhost:5995`——**别人扫码只会打开他自己手机上的
localhost，必然是打不开的**。

`server/netinfo.py` 的做法：

1. UDP `connect` 到 `8.8.8.8 / 1.1.1.1 / 223.5.5.5`：内核只查路由表、不发包，
   `getsockname()` 就是默认路由那块网卡的地址；
2. **但不能盲信**——装了 Clash / v2ray（TUN 模式）或 VPN 时，默认路由会指向
   虚拟网卡。开发机实测拿到的就是 `198.18.0.1`（RFC 2544 保留段，Clash 常用），
   手机根本连不上。所以把 `198.18/15`、`100.64/10`（CGNAT/Tailscale）、
   `169.254/16`（APIPA）直接判为虚拟网段，排到最后；
3. 再按网段可信度排：`192.168.*` > `10.*` > `172.16–31.*`；
4. 同档次里，以 `.1` 结尾的多半是路由器本身或 VMware/Hyper-V 的 host-only 网卡，
   自家机器很少是 `.1`，所以往后放。

实测（本机 4 个地址）：`192.168.1.100`（真网卡，推荐）→ `192.168.56.1`、
`192.168.99.1`（虚拟网卡，标「其他网卡」）→ `198.18.0.1`（Clash，标「异常」）。
二维码默认就是第一条，下拉框可以切到别的地址（比如机器接了两条网线）。

### 27.4 顺手修掉一个一直哑着的日志 bug

要给日志按状态码着色，就得知道状态码。翻代码发现
`server/http_util.py::log_message` 上面挂着一个多余的 `@staticmethod`，
导致 `self` 实际收到的是 format 字符串，方法体第一行就抛异常、
又被自己的兜底 `except` 吞掉——**控制台的访问日志其实一直是空的**，
只是没人从控制台看它。桌面端日志之所以有内容，是因为
`do_GET / do_POST` 在分发前另外调了一次 `access_logger`，而那一路根本没有状态码。

改法：

- 去掉 `@staticmethod`（并加注释说明为什么不能加）；
- 日志统一由 `send_response` 触发，一次请求一行，带真实状态码；
- `send_error` 会先 `log_error` 再 `send_response`，跳过前者，避免 404 打两行；
- `runner.access_logger` 只负责把消息塞进队列（4 元组），顺手删掉那段
  已经不可达的旧解析代码（136 行，URL 解码现在由 `log_message` 做）。

### 27.5 测试

新增 `tools/desktop_test.py`（离屏跑，56 项断言）：窗口能建、控件齐全、
起停时按钮/胶囊/地址/二维码跟着变、二维码地址能切换、日志色档都对、
网段排序与标签正确（含 `198.18` / `100.64/10` 判虚拟、192.168 排第一）。

```bat
python tools\desktop_test.py
```

### 27.6 本轮验证记录

| 检查 | 结果 |
|---|---|
| `python tools\desktop_test.py` | 56 项全部通过 |
| `ui_test` / `smoke` / `protocol` / `search` / `fsops` / `zip` / `auth_matrix` / `sse` / `thumb` / `limits` | 全绿（服务端与 Web 端未回归） |
| `python tools\preflight.py` | 可以打包 ✓ |
| exe | 已按新界面重打，启动实测 `/login` 200 |

### 27.7 下一轮建议

1. P5：暗色模式 + 6 套配色（token 已集中在 `ui/style.py`，加一套色值即可）；
2. 端口改用 `QSpinBox`（现在还是文本框 + 手写范围校验）；
3. 日志行数上限（现在无上限，长时间开着会越来越占内存）；
4. 访问方式卡里的「已连接 N 台 / 本次传输 X GB」统计（需要服务端埋计数）。


## 二十八、第十七轮：手机打不开 / 发不出消息的真凶，字号与二维码放大

这三件事都是手机上用出来的：字号偏小、二维码太小扫不动、以及一个只在
老内核浏览器上才炸的 500。

### 28.1 那个 500：`_hostname` 漏了 `@staticmethod`

报错原文：

```
Error code: 500
Message: Error: AuthMixin._hostname() takes 1 positional argument but 2 were given
```

`server/auth.py` 里这个方法是这么写的：

```python
def _hostname(netloc):        # ← 少了一行 @staticmethod
    ...
```

而调用处是 `self._hostname(netloc)`。不带装饰器时 `self` 会被当成第一个位置参数，
于是变成"两个位置参数给了一个形参"，直接 TypeError → 500。

**为什么桌面浏览器一直没事**：`_check_same_origin()` 的判断顺序是
①`Sec-Fetch-Site` → ②`Origin/Referer` 比对 → ③都没有就放行。
现代浏览器（Chrome/Edge/Safari 新版）都会发 `Sec-Fetch-Site`，第 ① 步就返回了，
**根本走不到 `_hostname`**。而手机上那些老内核 / App 内置 WebView（微信、QQ 浏览器、
部分安卓系统浏览器）只发 `Origin`，正好落在第 ② 步——一提交登录表单就 500。

同理，进页面之后发消息走的是 `POST /chat/send`，也过 `_reject_cross_site()`，
所以第二个浏览器"能进但发不出消息"，是同一个 bug。

修法就是补上 `@staticmethod`（并写清为什么不能省）。

### 28.2 加了两道护栏，防止同一个坑再踩

这已经是**第二次**同类事故了——上一轮修日志时发现 `log_message` 被多了个
`@staticmethod`，这轮又发现 `_hostname` 少了它。两次都极其隐蔽：
一次异常被自己的兜底 `except` 吞掉，一次只在老浏览器上才走到。

1. **`tools/preflight.py` 新增 `check_method_signatures()`**：
   扫 `server/` 和 `ui/` 里所有类方法，第一个参数不是 `self/cls` 又没标
   `@staticmethod` / `@classmethod` 的一律报错。故意删掉装饰器验证过会红。
2. **新增 `tools/origin_test.py`（20 项）**：用只发 `Origin`、不发
   `Sec-Fetch-Site` 的请求（还带 iPhone UA）把整条链路跑一遍——
   登录、建聊天室、发消息都要 200，跨站要 403，`_hostname` 的端口/userinfo/
   IPv6/大小写/空值五种输入各测一条。

```bat
python tools\origin_test.py
```

### 28.3 字号调大

原来基础字号 13px，在 100% 缩放的 1080P 屏上偏小（截图里能看出来）。
这一轮整体上调：

| 位置 | 原 | 现 |
|---|---|---|
| 正文标签 | 13px | 14px |
| 卡片标题 | 13px | 15px |
| 输入框 | 13px | 15px |
| 按钮 | 13px | 14px |
| 小按钮 / 下拉框 / 提示 | 12px | 13px |
| 产品名 | 17px | 19px |
| 日志（等宽） | 12px | 13px |

窗口默认从 940×780 放大到 1000×850，最小 900×720，行距和内边距同步放宽。

### 28.4 二维码双击放大

二维码缩略图从 168 缩到 152（给日志腾地方），双击弹出一个独立窗口里的
大图（`segno` 以 scale=8 重画，约 640px），手机举远一点也能扫。
地址下拉框里只显示「推荐 · 192.168.1.100」这种短标签，完整 URL
放在下面一行、可选中复制。

### 28.5 本轮验证记录

| 检查 | 结果 |
|---|---|
| `python tools\origin_test.py` | 20 项全部通过（含老内核登录/发消息） |
| `python tools\desktop_test.py` | 56 项全部通过 |
| 其余 10 套（ui/smoke/protocol/search/fsops/zip/auth/sse/thumb/limits） | 全绿 |
| `python tools\preflight.py` | 可以打包 ✓（含新的方法签名检查） |
| exe | 已重打，启动实测 `/login` 200 |

### 28.6 下一轮建议

1. 手机端登录页/聊天页在窄屏下的可用性（这次只修了崩溃，没有专门适配小屏）；
2. P5：暗色模式 + 6 套配色；
3. 端口改 `QSpinBox`、日志行数上限；
4. `_hostname` 这类"只在某些浏览器才走到"的代码，考虑补一份请求头矩阵测试
   （现在 `origin_test` 只覆盖了 Origin / Referer / Sec-Fetch-Site 三种组合）。


## 二十九、第十八轮：字号再放大 + 桌面端外观（P5 暗色/六配色）+ 手机端窄屏修复

### 29.1 字号：这次做成可调的

上一轮把基础字号从 13px 提到 14px，还是偏小。这轮不再一个个硬编码，
而是把字号收进 `ui/style.py` 的 `BASE_PX`，再乘一个档位系数：

| 档位 | 系数 | 正文 | 输入框 | 产品名 |
|---|---|---|---|---|
| 中 | 1.00 | 15px | 16px | 21px |
| **大（默认）** | 1.15 | **17px** | **18px** | **24px** |
| 特大 | 1.32 | 20px | 21px | 28px |

右上角「外观 ▾ → 字号」可以随时切，选择存进 `QSettings`。
另外窗口默认尺寸跟着放大到 1020×900，最小 900×720；行首小标签的宽度
按字号算（`4 个汉字宽 + 8px`），所以切到特大也不会把「共享目录」挤掉。

### 29.2 P5：暗色模式 + 6 套配色（桌面端）

`ui/style.py` 重写成三层 token：

```
MODES     明/暗两套中性色（bg / card / line / text / muted / ok / warn …）
PALETTES  6 套主色：靛蓝、海蓝、青翠、玫红、琥珀、紫罗兰（和 Web 端同组）
SIZES     中 / 大 / 特大
build_qss(palette, mode, scale) → 整份 QSS
```

右上角「外观 ▾」菜单里三组可选项，勾选状态跟着当前值走，选择写进
`QSettings`（`theme/palette`、`theme/mode`、`theme/size`），下次启动还原。

**踩到的坑**：地址清单里的「推荐」那行原来是把颜色写在内联 `setStyleSheet` 里，
切暗色时这些行不会重算样式，结果深色字直接看不见——截图里第一行只剩一个「推荐」。
改成 `objectName + 动态属性`（`addrTag[tone="best"]`），QSS 管颜色，
切主题时 Qt 会自动重刷所有子控件。日志颜色本来就是逐行 `QTextCharFormat`，
切暗色只影响之后新写入的行（旧行保留原色，属于预期）。

### 29.3 顺手做的两件小事

- **端口改 `QSpinBox`**：范围 1–65535 由控件保证，不再手写 `try/except` 校验；
  隐藏上下箭头，看起来还是个输入框。`QSettings` 里存的可能是字符串，
  统一走 `_port_from_settings()` 兜底。
- **日志行数上限 2000**：超出就从头删最老的（`_trim_log`），
  长时间开着不再无限吃内存。

### 29.4 手机端（390×844）实测修了两个布局 bug

用 `Emulation.setDeviceMetricsOverride` 按 iPhone 尺寸打开真实页面，量了
`scrollWidth` 和每个元素的位置，发现两处：

| 现象 | 原因 | 修法 |
|---|---|---|
| 长文件名占 3 行，把下面的「大小 · 时间」盖住了 | `.row-name` 用了 `-webkit-line-clamp: 2` 但同时写着 `overflow: visible`，截断直接失效 | 改回 `overflow: hidden` + `word-break: break-all` |
| 多选工具条里「取消」被挤出屏幕（right=449 > 390） | 6 个按钮 + 「已选 N 项」在 390px 下排不下，容器又是 `nowrap` | 小屏按钮收窄（`padding: 0 7px`、12px 字），≤420px 时隐藏计数文案；容器仍保留横向滚动兜底 |

修完实测：视口 390 / 内容 390（无横向滚动），多选条最右 369 < 390，
5 个按钮全在屏内。顺手把这件事做成工具 **`tools/mobile_shot.py`**：

```bat
python tools\mobile_shot.py
```

它自己起服务、用 390×844 打开首页、打印几何数据、更新
`截图\手机端-文件页.png` 和 `截图\手机端-多选条.png`，有溢出就返回非 0。

### 29.5 本轮验证记录

| 检查 | 结果 |
|---|---|
| `python tools\desktop_test.py` | 78 项全部通过（新增端口/外观/暗色/字号/日志上限 22 项） |
| `python tools\mobile_shot.py` | 无横向溢出、多选条不出屏 |
| 其余 11 套（ui/origin/smoke/protocol/search/fsops/zip/auth/sse/thumb/limits） | 全绿 |
| exe | 已重打，启动实测 `/login` 200 |

### 29.6 下一轮建议

1. 手机端继续打磨：聊天页输入框在键盘弹起时的行为、图片预览手势；
2. 桌面端「服务信息」里可以补上真实统计（在线连接数、累计流量），需要服务端埋计数；
3. 日志支持按状态码/文件过滤；
4. 暗色模式下托盘图标、`QMessageBox` 的图标是否需要换一套浅色版本。


## 三十、第十九轮：修「设了管理密码后上传/删消息全废」——四个真 bug

用户报的三件事：上传点按钮没反应、删聊天记录只说"失败"、手机端没地方输管理密码。
查下来不是一个 bug，是**四个**，其中一个和"管理密码"其实没关系。

### 30.1 bug ①：上传按钮是坏的（和密码无关）

`60-upload.js` 里调了 `openMenu(btn, [...])`，但这个分片文件头**没有**
`var openMenu = Lan.ui.openMenu;`。上一轮"分片真正独立"把共享闭包拆掉之后，
这里一调用就是 `ReferenceError: openMenu is not defined`——点「上传」什么都不会发生。

只要设过管理密码才会被发现，是因为：设密码之前很多人是拖拽上传（走的是
另一条 `drop` 路径，不经过菜单），或者根本没点过这个按钮。

**为什么没测出来**：`ui_test` 只验证列表渲染、下载按钮和 `Lan.*` 契约，
从没点过上传按钮。这也是这一轮最重要的教训——**分片独立之后，
"用了但没声明"的符号只会在真正点下去的那一刻炸**。

修完顺手加了个静态检查（`tools/preflight.py::check_undeclared_calls`）：
把每个分片里"调用的裸函数名"和"本地声明 / 浏览器全局 / 从 Lan 取的别名"对一遍，
对不上就红。故意删掉别名验证过：

```
[FAIL] 60-upload.js 调用了未声明的 openMenu()（漏了 Lan.* 别名？）
```

### 30.2 bug ②：打开文件选择框被异步鉴权挡住

原来是：

```js
onSelect: function () { ensureAdmin(function () { fileInput.click(); }); }
```

`ensureAdmin` 一旦需要密码，`fileInput.click()` 就跑到 Promise 回调里了。
浏览器规定**打开文件选择框必须发生在用户激活（user activation）期间**，
跨了一个网络往返之后手势已经失效，`input.click()` 被静默忽略——
表现就是"输完密码，什么都没发生"。

改成：**先同步打开选择框**，等用户真的选了文件，再要管理密码；

```js
onSelect: function () { pickFiles(fileInput); }        // 同步 click
function onPicked(e) {                                  // change 之后
  var picked = [...e.target.files];
  ensureAdmin(function () { addUploads(picked); });
}
```

拖拽路径同时修了一个隐患：`dataTransfer.files` 在异步回调里可能已经失效，
现在先抓成数组再进回调。

### 30.3 bug ③：删聊天记录走的是裸 `apiFetch`

`/chat/delete`、`/chat/clear` 这类接口是**自己在 handler 里判管理员**的
（返回 401 JSON），但前端 `chatDeleteMsg` 用的是 `apiFetch`，401 只会走到
`else toast('删除失败')`——不弹密码框、也不说为什么。

改成和文件操作一致的 `writeFetch`（401 → 弹管理密码 → 自动重试一次），
并且把响应体里的 `Admin Required` 翻译成人话：

```
需要管理密码：点右上角「管理员权限」解锁后再试
```

顺手把 `/upload/unique` 也从 `apiFetch` 换成 `writeFetch`
（它在冲突改名时用，401 会导致改名失败、静默覆盖同名文件）。

### 30.4 bug ④：手机端根本没有管理员入口

`[data-admin-open]` 只存在于左侧栏，而侧栏在 `max-width: 900px` 下是
`display: none`——手机上既没有入口，也没有任何提示。

改法：

- 顶栏（`.mobile-bar-actions`）加一个锁形按钮，和侧栏那个共用 `[data-admin-open]`；
- `/is_admin` 多返回一个 `needs_admin`（服务端有没有设管理密码），
  前端据此决定：没设密码 → 整条入口隐藏；设了没解锁 → 显示锁头；
  已解锁 → 文案变「已解锁」；
- 顺带补了一条全局 `[hidden] { display: none !important; }`——
  否则 `hidden` 会被 `display:flex` 顶掉，藏不住。

### 30.5 顺手修的两处噪音

| 现象 | 原因 | 处理 |
|---|---|---|
| 切换页面时日志里出现 `GET 500 /api/list` | 浏览器切页/关标签会中途断开连接，`do_GET` 的兜底 `except` 把它当服务器错误 | 捕获 `ConnectionError/BrokenPipeError`，只记一行「客户端提前断开」，不再报 500 |
| `hidden` 属性对 flex 元素无效 | CSS 里没有 `[hidden]` 规则 | 全局补 `[hidden] { display: none !important; }` |

### 30.6 新增回归测试：`tools/admin_flow_test.py`（29 项）

真浏览器 + 真服务，按用户的场景一步步走：

| 分组 | 验的东西 |
|---|---|
| ① 入口 | `/is_admin` 报 `needs_admin`；设了密码时侧栏/顶栏入口可见、文案正确 |
| ② 上传按钮 | 点菜单立刻触发 `input.click()`，且**此时还没发过 `/verify_admin`**（就是 30.2 那个 bug 的守门断言） |
| ③ 选文件后 | 才弹管理密码框 → 输对 → `/upload` 真的成功、文件出现在列表中 |
| ④ 清空聊天记录 | 清掉 Cookie 重来（模拟"没解锁的会话"）→ 401 → 弹框 → 输对 → 消息被清空 |
| ⑤ 输错密码 | 提示"密码不对"，且**不会误删** |
| ⑥ 手机端 | 390×844 下顶栏有锁形按钮、不出屏、点它能弹框 |
| ⑦ 没设管理密码 | 两个入口都隐藏 |
| ⑧ 两个密码同时开 | 先登录 → 仍需管理密码 → 上传弹框 → 输对 → 文件落地 |

```bat
python tools\admin_flow_test.py
```

### 30.7 全面测试结果

| 检查 | 结果 |
|---|---|
| `admin_flow_test`（本轮新增） | 29 项 ✓ |
| `ui_test` / `origin_test` / `desktop_test` | 全绿 |
| `smoke` / `protocol` / `search` / `fsops` / `zip` / `auth_matrix` / `sse` / `thumb` / `limits` | 全绿 |
| `preflight` | 可以打包 ✓（新增"用了但没声明"检查） |
| `mobile_shot`（390×844） | 无横向溢出 |
| exe | 已重打，启动实测 `/login` 200 |

### 30.8 这一轮暴露的流程问题（写给下一轮）

1. **前端交互要有"点一下"的测试**：这次的①和②都只需要"点一次上传按钮"就能发现，
   而 `ui_test` 从来没点过。以后新增按钮，至少补一条 CDP 点击断言。
2. **分片独立 = 每个别名都是契约**：`preflight` 的静态检查已经能兜住漏别名，
   但兜不住"别名写错对象"（比如 `var toast = Lan.api.toast`），那种只能靠运行时测。
3. 管理类操作统一走 `writeFetch`，别再出现第四个裸 `apiFetch` 打管理接口。


## 三十一、第二十轮：深色模式闪白、文件改名、仓库整理

### 31.1 深色模式进子目录会闪一下白

现象：暗色主题下点进子文件夹，整屏先白一帧再变深，很刺眼。

原因在模板写死了初始状态：

```html
<html lang="zh-CN" data-theme="light" data-palette="indigo" data-glass="normal">
```

`data-theme` 永远是 `light`，真正读 localStorage 的 `applyAppearance()` 要等
`app.js` 跑到 `DOMContentLoaded` 才执行。于是每次整页跳转都是
**浅色先画一帧 → JS 再改成深色**，中间那帧就是白闪。

修法是在 `<head>` 里、**样式表之前**放一段同步脚本，首屏之前就把三个属性设好：

```html
<script>
(function () {
  try {
    var d = document.documentElement, s = window.localStorage;
    var theme = s.getItem('lanshare.theme') || 'light';
    if (theme === 'auto') {
      theme = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    d.setAttribute('data-theme', theme);
    d.setAttribute('data-palette', s.getItem('lanshare.palette') || 'indigo');
    d.setAttribute('data-glass', s.getItem('lanshare.glass') || 'normal');
  } catch (e) { }
})();
</script>
```

`layout.html` 和 `login.html` 两个外壳都加了。因为脚本在解析 `<head>` 时同步执行，
浏览器拿到的第一份样式就已经是深色；再加上 `tokens.css` 里
`:root[data-theme="dark"] { color-scheme: dark; }`，导航期间画布也是深色。

新增 `tools/theme_flash_test.py`（19 项）守住它：

- 结构断言：`/`、`/dir`、`/chat`、`/login` 下发的 HTML 里，那段脚本
  **必须存在且排在 `app.css` 之前**（这才是"不闪"的机制保证）；
- 浏览器断言：深色下逐个页面检查 `data-theme`、首帧背景色不是纯白、
  `color-scheme` 是 dark；切回浅色也必须是浅色。

> 为什么不用"截图看闪没闪"来测：闪白只存在一帧（~16ms），截图大概率抓不到；
> 而"脚本是否在样式表之前"是可确定判定的。

### 31.2 文件改名：拼音名换掉

| 旧 | 新 |
|---|---|
| `juyuwang.py` | `lanshare.py` |
| `变更说明.md` | `docs/CHANGELOG.md` |
| `模块说明.md` | `docs/ARCHITECTURE.md` |
| `重构方案.md` | `docs/REFACTOR.md` |
| `桌面端改版方案.md` | `docs/DESKTOP-UI.md` |

打包产物也跟着从 `juyuwang.exe` 变成 `lanshare.exe`。
14 个测试脚本的 `import juyuwang as j` 全部改成 `import lanshare as j`，
文档里的交叉引用也一起更新（历史记录里当年的 `juyuwang.exe` 保持原样，那是史实）。

### 31.3 仓库整理与脱敏

准备推到 GitHub，先做了这些：

- 新增 `.gitignore`：`chat_data/`（聊天内容）、`webassets.py`（构建产物）、
  `截图/`、`样稿/`（含本机网卡地址）、`__pycache__`、`lan6-*/`、构建目录；
- 新增 `.gitattributes`：统一 LF，避免 Windows/Linux 互相改整份文件；
- 新增 `README.md`（功能、快速开始、目录结构、测试清单、隐私说明）和 `LICENSE`（MIT）；
- **脱敏**：文档和测试里的本机真实内网地址统一换成通用示例（`192.168.1.x` 段）；
  仓库里不含作者个人路径、QQ 邮箱；
- git 提交身份用 `LFVFL <LFVFL@users.noreply.github.com>`（noreply 地址），
  不动本机全局配置里的私人邮箱。

提交内容：72 个文件、约 710 KB，只有源码 + 文档 + 图标。

### 31.4 验证

| 检查 | 结果 |
|---|---|
| `theme_flash_test`（本轮新增） | 19 项 ✓ |
| 其余 13 套（admin_flow / origin / desktop / smoke / protocol / search / fsops / zip / auth_matrix / sse / thumb / limits / ui） | 全绿 |
| `preflight` | 可以打包 ✓ |
| 改名后重新打包 | `lanshare.exe` 生成并启动实测 `/login` 200 ✓ |
| `git ls-files` 复查 | 无 chat_data / 截图 / webassets.py / 缓存 |


## 三十二、第二十一轮：按页拆包、启动提速、CSP、分页、SSE 广播

这一轮只做用户点名的那几项（P1 全部 + P2 的拆包 / 静态检查 / 跟随系统暗色），
没有动 P0 的安全项。

### 32.1 按页拆 JS 包

原来 12 个分片拼成一份 89.4 KB 的 `app.js`，登录页也要下 89 KB。现在按"哪个页面用得上"分成四包：

| 包 | 分片 | 体积 |
|---|---|---|
| 核心 `app.js`（每页都有） | 00/10/20/30/40/70 灯箱/90 启动 | 28.4 KB |
| 文件页 `app-files.js` | 50 列表 + 60 上传 + 72 文件页启动 | 35.1 KB |
| 聊天页 `app-chat.js` | 80 列表 + 85 聊天室 | 25.1 KB |
| 登录页 `app-login.js` | 88 登录 | 1.4 KB |

文件页首屏 **89.4 → 63.5 KB**，聊天页 53.5 KB，登录页 29.8 KB。
灯箱（聊天看大图要用）留在核心包；`70-lightbox.js` 里的文件页启动逻辑拆到了新的
`72-files-page.js`。

`ui_test` 的断言也跟着改成"按页看"：文件页里 `Lan.chat.state` 必须不存在
（证明没白下聊天包），聊天页里 `Lan.upload` 不存在。

### 32.2 桌面端不再被 schtasks 拖住

`FileShareGUI.__init__` 里原来同步跑 `schtasks /query` 查开机自启、读注册表查右键菜单，
加起来 200~500 ms，窗口晚出来。现在窗口先显示，后台线程查完用 `system_checks_ready`
信号回填按钮（查的时候按钮显示"检测自启状态…"）。

### 32.3 页面 CSP

新增 `PAGE_CSP`：脚本全部同源、禁止内联、不许被外站嵌。

为了不依赖内联脚本，把"首屏定明暗"那段从模板里搬进了 `/static/theme-boot.js`
（仍然是 `<head>` 里同步加载、排在样式表之前）。用 `Log.entryAdded` 在 5 个页面
上核对过：**0 条 CSP 违规**。

### 32.4 别名静态检查

`preflight` 之前只能发现"用了但没声明"，这轮补上"指错命名空间"：
`var toast = Lan.api.toast` 这种写法不报错、行为却会很怪，现在会直接红：

```
[FAIL] 别名指错命名空间：60-upload.js: toast = Lan.api.toast（应该来自 Lan.ui.）
```

### 32.5 桌面端「跟随系统」暗色

明暗菜单从两项变三项：明亮 / 暗色 / **跟随系统**。跟随系统读注册表
`AppsUseLightTheme`（读不到就退化成看系统调色板），切主题时 QSS 与日志颜色一起变。

### 32.6 超大目录分页

`/api/list` 支持 `limit`/`offset`，返回 `total` 与 `hasMore`；不传就是整份（老行为不变）。
前端每页 400 条，列表底部出现「还有 N 项，点这里继续加载」，点一下追加。
这样几万项的目录不会一次性塞满 DOM，手机上也不至于卡死。

### 32.7 SSE 改广播

原来每条连接各自扫描房间（N 个观众 = N 份扫描）。现在所有连接挂在同一张订阅表上，
由一个广播线程统一扫描后分发到各自的队列；`chat_event` 一响就分发，延迟不变。
客户端读得太慢（队列满 256）会被断开，重连时带 `after` 自动补齐，不会丢消息。

`sse_test` 加了三条断言：三个连接同时在线时都收到、各自只收到一次、`after` 增量仍然正确。

### 32.8 验证

| 检查 | 结果 |
|---|---|
| `paging_test`（本轮新增） | 16 项 ✓ |
| `sse_test`（+3 条广播断言） | 全绿 |
| `desktop_test`（+跟随系统） | 83 项 ✓ |
| `theme_flash_test`（改为断言 theme-boot.js） | 22 项 ✓ |
| 其余 9 套 | 全绿 |
| CSP | 5 个页面 0 违规 |
| 构建产物 | core 28.4 / files 35.1 / chat 25.1 / login 1.4 KB |


## 三十三、第二十二轮：换共享目录立刻生效、上传改成"先密码后选文件"

### 33.1 换共享目录为什么要重启才生效

`FileSharer.start()` 把 `shared_path` 当参数塞进 `handler_factory` 的闭包里：

```python
shared_path = self.shared_path
def handler_factory(*args, **kwargs):
    kwargs['shared_path'] = shared_path      # ← 启动那一刻的值，之后再也不变
```

所以运行中改 `self.shared_path`（或者改桌面端输入框）对已经跑起来的服务毫无影响。
再加上 HTTP/1.1 是长连接，一个 handler 实例会服务很多个请求，
就算把 `shared_path` 改成属性、在构造时读一次，老连接照样是旧值。

修法：把"当前共享目录"提到全局 `storage.SHARE_STATE`，
`FileShareHandler.shared_path` 改成**每次读取都看全局值**的属性：

```python
@property
def shared_path(self):
    return SHARE_STATE.get('path') or self._shared_path
```

`FileSharer.set_shared_path(path)` 负责校验 + 改全局值，下一次请求立刻生效。

桌面端配套改动：

- 运行中不再把路径框设成只读、浏览按钮也不禁用（端口/密码仍然只读）；
- 浏览选择一个目录、或者把文件夹拖进窗口，都会走 `apply_share_path()`：
  校验 → 调 `set_shared_path` → 日志里记一行「共享目录已切换为 …」→ 底部提示一下；
- 传一个不存在的目录会弹错误框，并且保持原目录不变。

Web 端：老页面还指着旧目录的绝对路径，`build_listing` 会把越界路径归到新根，
`loadPage` 发现返回的 `path` 变了就跟着切过去并提示「共享目录已变更，已回到新目录」，
不会满屏 404。

### 33.2 上传：先输管理密码，再选文件

上一轮为了绕开"用户激活"限制，把顺序改成了"先选文件、选完再要密码"。
用户反馈这不合理——确实是，先要权限再动手才符合直觉。

问题在于：打开文件选择框必须发生在用户手势里，而验证密码是网络请求（异步），
等它回来手势已经过期，`input.click()` 会被浏览器静默忽略。

解法是把两者放进**同一个手势**里：

1. 点「上传文件」→ 先弹管理密码框（不发文件选择框）；
2. 用户点「解锁」那一下——仍然是用户激活——同步 `input.click()` 打开选择框，
   同时并行发 `/verify_admin`；
3. 选完文件时验证通常已经回来了：
   - 通过 → 直接开始上传；
   - 没通过（密码错/取消）→ 丢掉这批文件，提示「需要管理密码，这些文件没有上传」。

`askAdmin(desc, onUnlock)` 新增了 `onUnlock` 回调，就是给这个时机用的。

### 33.3 测试

新增 `tools/share_switch_test.py`（14 项）：

- 启动共享 A，切到 B 之后 `/api/list` 立刻看到 B 的内容、A 的消失；
- 拿 A 的绝对路径去请求，会被归到新根 B，不报错；
- 下载也跟着走：B 的文件能下，A 的文件 404；
- 传一个不存在的目录会被拒且保持原样；再切回 A 也正常。

`desktop_test` 补了 6 项（运行中切目录：输入框、服务端路径、仍在运行、
能切回去、路径框可编辑、浏览按钮可用），现在 90 项。

`admin_flow_test` 的上传流程断言按新顺序重写（30 项）：
点上传 → 先出密码框且选择框未打开 → 点解锁 → 选择框才打开 → 选文件时不再要密码。

### 33.5 补修（同一轮反馈）

**「输了错密码还把选择框打开了」。** 上一版把 `input.click()` 塞进了「解锁」按钮的点击回调里
（为了保住用户激活），代价是密码错了也照样弹出选择框——用户会一脸问号。

改成两段式，任何一步都不会"替你点"：

1. 点上传 → 弹管理密码框；
2. **验证通过** → 才出现一个「已解锁 / 选择文件」的小弹窗，点它才真正打开选择框；
   密码错就只提示「管理密码不对」，选择框绝不出现。

代价是多一次点击，但行为完全可预期。

顺手修了一个真 bug：从侧栏「管理员权限」解锁后，`Lan.api.ctx.isAdmin` 没有置为 true
（只有 `writeFetch` / `ensureAdmin` 会置），导致界面显示"已解锁"、程序仍当自己是游客，
下一次点上传又弹密码框。现在 boot 里解锁成功会同步写状态。

`admin_flow_test` 补了 4 条断言（34 项）：错密码不打开选择框、提示"不对"、弹窗关掉、
从管理员入口解锁之后点上传直接打开选择框。

### 33.4 验证

| 检查 | 结果 |
|---|---|
| `share_switch_test`（新增） | 14 项 ✓ |
| `admin_flow_test`（流程重写） | 30 项 ✓ |
| `desktop_test` | 90 项 ✓ |
| 其余 11 套 | 全绿 |


## 三十四、第二十三轮：界面细节与运行排查

这一轮是"看出来的问题 + 跑出来的问题"，全部靠截图与实测数值定位，不靠猜。

### 34.1 图片按钮看起来比发送按钮高一点

用 `getBoundingClientRect()` 量了一遍：**按钮**中线与发送按钮完全一致（都是 737.5），
但**图标**的中线在 735.3——整体偏上 **2.2px**。桌面上只是"感觉有点偏"，放大看才明显。

根因在图标注水（hydration）：

```js
el.innerHTML = icon(...);
el.removeAttribute('data-icon');   // ← 把居中样式一起摘掉了
```

居中是挂在 `[data-icon]` 上的 `display:inline-flex; align-items:center`，
属性一摘，容器退回普通 `block`，里面的 `<svg>` 以 `inline` + `vertical-align:baseline`
参与行盒排版，被基线挤上去 2.2px。

修法：注水时补一个稳定类名，不依赖那个会被摘掉的属性。

```js
el.classList.add('icon-box');
```

```css
[data-icon], [data-theme-icon], .icon-box {
  display: inline-flex; align-items: center; justify-content: center;
  flex: none;
  line-height: 0;          /* 去掉行盒 baseline 余量 */
}
.icon-box > .icon { display: block; }
```

这是**全站**的问题，不是聊天页独有的：修完之后全站 62 个图标，偏移 0 个。

### 34.2 表头"上圆下方"

`.list-head` 原本是 `border-radius: r-lg r-lg 0 0`，只有上半部分圆。
而它下面的行是透明底，所以白条下面直接是空白，看起来像被切了一刀。

改成四角都圆（`border-radius: var(--r-lg)`），并把原来的 `border-bottom`
换成 `box-shadow`，这样吸顶滚动时也不会出现"圆角突然消失"的割裂感。

### 34.3 手机端完全没有排序入口

`.list-head` 在 ≤900px 时是 `display:none`，而排序按钮全在表头里，
所以手机上三个排序（名称/大小/时间）**一个都点不到**。

补了一条移动端专用排序条，放在搜索框下方：

```html
<div class="mobile-sort" data-mobile-sort>
  <span class="mobile-sort-label">排序</span>
  <button class="chip" data-sort="name">名称 <i data-icon="arrow" ...></i></button>
  ...
</div>
```

配套改了两处逻辑：

- 排序监听原来是 `head.addEventListener`，只绑在表头上。改成直接给所有
  `[data-sort]` 绑定，桌面表头和手机排序条共用一套逻辑；
- `updateHead()` 原来只同步表头箭头，改成同步**所有** `[data-sort]`，
  保证两端状态一致（实测 `head=asc / mobile=asc` 同步）。

### 34.4 顺带查出并修掉的两个真 bug

**面包屑完全没有样式。** 服务端输出的是裸 `<a>`：

```python
parts.append('<a href="/dir?path={0}">{1}</a>'.format(...))
```

而 CSS 里定义的全是 `.crumb` / `.crumb-sep` / `.crumb--current`。
结果就是面包屑没有内边距、没有圆角、没有 hover 效果，当前目录也没有加重。
实测 `.crumb` 元素数为 **0**。现在按类名输出，并让最后一级用 `<span>` 而非链接。

**`POST /delete` 缺 `file` 参数时不返回任何响应。** 原来的写法：

```python
def post_delete(self):
    filename = self.q('file')
    if filename:                       # 为空时什么都不做 → 函数直接结束
        self.delete_file(...)
```

函数结束不带响应，连接被关掉，客户端表现为"请求一直挂着"，
`RemoteDisconnected`，日志里也看不出原因。改成明确回 400。

三种情况（无 body / 有 body / 缺参数）现在都稳定返回
`400 {"success": false, "message": "缺少 file 参数"}`。

### 34.5 全量运行排查

除了上面这些，还把 29 个异常输入（非法 offset、路径越界、畸形 JSON、Range 溢出、
超长消息……）和 14 个写接口的参数缺失都跑了一遍：

- 没有 5xx，没有把服务打挂的输入；
- 管理密码校验正确：空密码 / 错密码 / 未登录一律拒绝，只有正确密码通过；
- 全站 3 个页面 62 个图标零偏移、零 `data-icon` 残留；
- 选择/全选/搜索/进入子目录/明暗切换状态机全部符合预期，无 JS 异常。

> 排查过程中有两次是我自己的测试脚本写错导致的误报（把中文路径直接塞进
> HTTP 请求行触发 `UnicodeEncodeError`；以及断言的排序方向写反），
> 都已在确认后排除，没有据此改动产品代码。

### 34.6 验证

新增 `tools/ui_detail_test.py`（21 项），专门守住这几条：

| 断言 | 内容 |
|---|---|
| 图标居中 | 桌面/手机下 `svg` 中线与发送按钮误差 < 0.6px |
| 无残留 | 三个页面 `[data-icon]` 数量为 0，`.icon-box` 全部居中 |
| 表头圆角 | 四角值一致且非 0 |
| 手机排序 | 排序条可见、3 个按钮都有宽度 |
| 排序生效 | 降序后文件顺序为 `c.zip,b.pdf,a.txt`，且两端状态同步 |
| 面包屑 | 有 `.crumb` / `.crumb--current` / `.crumb-sep`，当前项不是链接 |
| 删除缺参 | 返回 400 且响应体提到 file，不是断连 |

17 套测试全绿。

## 三十五、附：第一轮（模块化拆分）对照表

`lanshare.py` 从 3400 行拆成 `server/`（服务端，不依赖 Qt）+ `ui/`（界面），入口只留启动逻辑。
目录职责与依赖方向见 **`docs/ARCHITECTURE.md`**。

| 原位置 | 新位置 | 行数 |
|---|---|---|
| 常量、目录、安全头 | `server/config.py` | 49 |
| 路径/体积/口令工具 | `server/paths.py` | 55 |
| 会话、上传锁、ZIP 临时表 | `server/storage.py` | 91 |
| 聊天持久化与迁移 | `server/chat_store.py` | 289 |
| 模板渲染与图标字节 | `server/pages.py` | 88 |
| Handler 的同源/会话/口令 | `server/auth.py`（Mixin） | 133 |
| Handler 的响应、日志、页面 | `server/http_util.py`（Mixin） | 316 |
| Handler 的文件接口 | `server/files_api.py`（Mixin） | 690 |
| Handler 本体 + 路由 | `server/handler.py` | 605 |
| `FastBindHTTPServer` / `FileSharer` | `server/runner.py` | 306 |
| `ClickableLabel` | `ui/widgets.py` | 11 |
| 图标加载 | `ui/icon.py` | 46 |
| `FileShareGUI` | `ui/main_window.py` | 789 |
| 入口 `main()` | `lanshare.py` | 79 |

搬家时方法体一行没改，只是从类里挪到 mixin 里，靠 `FileShareHandler(...)` 的继承拼回去：

```python
class FileShareHandler(HttpUtilMixin, AuthMixin, FilesApiMixin, BaseHTTPRequestHandler):
```

拆分过程中真实踩到并修掉的问题：

| 现象 | 原因 | 修法 |
|---|---|---|
| `_password_ok` / `_upload_key` 未定义 → 500 | `from x import *` 不导出下划线开头的名字 | 显式 `from server.paths import _password_ok` |
| 上传/登录 500：`SESSION_TTL` 未定义 | `storage.py` 只导入了 `ZIP_EXPIRE_SECONDS` | 补齐导入 |
| 首页 500（`html` 未定义） | `page_shell` 搬到 `pages.py` 后没带 `html` | 补 `import html` |
| `open_file` 里 `FileShareHandler` 未定义 | mixin 反向依赖 handler 会成环 | 改成 `type(self)._open_cache` |
| `APP_ICON` 在窗口里读不到 | 全局变量跟着模块走，跨模块 `global` 不共享 | `ui/icon.py` 提供 `set_app_icon` / `get_app_icon` |

新增 **`tools/smoke_test.py`**：自带临时共享目录和服务进程，跑完自动收拾，
覆盖页面渲染、ETag 304、上传/下载/续传/存在性/另存副本/删除、聊天读写、
只读方法拒绝写入、跨站写入拒绝，共 22 项。改完代码跑一次即可回归：

```bat
python tools\smoke_test.py
```

本轮验证结果（全部通过）：

- `python tools\smoke_test.py` → 22 项 ✓
- 离屏构造 GUI（`QT_QPA_PLATFORM=offscreen`）→ `FileShareGUI()`、图标、托盘 ✓
- `FileSharer().start()` → 返回 `local_url` / `network_urls`，`/api/list` 正常，`stop()` 正常 ✓
- `import juyuwang` 后 `FileShareHandler` / `FileSharer` / `FileShareGUI` 都还在（外部脚本兼容）✓

