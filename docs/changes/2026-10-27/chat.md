# 第七版：聊天与图片传输

仅修改第七版副本。本项不改密码日志、QSettings、账号体系或其他扩展功能。

## 群名与同步

- 默认群和自建群均支持改名。列表更多菜单及群内“改群名”按钮使用现有管理员验证；`default` 的 ID 不变，仍不能删除。名称限制为 1-50 个字，保存后重启保留。
- 群名的 HTML 内嵌 JSON 转义由服务端公共模板修复负责；前端标题更新使用 `textContent`，不解释群名里的 HTML。
- 新前端使用 `/chat/history?sync=1` 的快照/变更协议，以及 `/chat/stream?sync=1&since=...`。每个房间的游标是 `epoch:sequence`；SSE 写 `id:`，重连优先读取标准 `Last-Event-ID`。
- 空房间不再跳过第一条消息；游标失效、事件缓存过期、重启换 epoch、500 条保留淘汰均返回 `reset` 快照。`delete`、`clear`、`room_updated`、`room_deleted` 事件使其他已打开页面同步变化。
- 保留旧 `/chat/history` 数组、`after=<message-id>` 和默认 SSE `data:` 消息格式。旧游标丢失时返回现存历史而不是空数组。

## 无固定 5MB 门槛的图片上传

新页面不再使用 `FileReader.readAsDataURL` 把原图塞进 JSON，也不在浏览器复制全尺寸图片做 canvas 缩略图。

1. `/chat/media/start` 创建上传，文件名只作显示元数据，实际落盘文件名由服务器随机生成。
2. `/chat/media/chunk` 接收最大 1MiB 的 `application/octet-stream` 分片，以 64KiB 块写盘；offset 不匹配拒绝，中断回退到上一完整分片边界。
3. `/chat/media/complete` 在有界解码名额内验证真实图片并生成 JPEG 缩略图/预览，原图字节不转码。重复 complete 返回同一结果。
4. `/chat/send` 只提交准备好的媒体 token，token 绑定访问 Cookie、浏览器随机 ID 和房间，不接受任意已有图片 URL。发送后媒体归消息拥有；重试使用 `client_id` 避免重复消息。
5. `/chat/media/cancel` 取消并清除部分文件及未发送的原图、`_t`、`_p` 文件。准备好但未发送的图片受保护，不被聊天孤儿清理误删；过期上传会清理。成功发出的图片不能再通过旧上传 token 删除，删除/清空消息统一回收关联文件。

页面显示上传进度、解码状态、取消及重试；服务器拒绝和网络失败不会清掉文字/待发图片。

### 存储格式和显示格式是两件事

- Pillow 为必装运行时依赖。PNG、JPEG、GIF、WebP、BMP、TIFF、ICO 已实际测试；页面从 `/chat/media/config` 显示当前服务器注册的解码器，不能解码时说明原因而不是悄悄失败。
- HEIC/HEIF 可安装 `pillow-heif`（Python 模块 `pillow_heif`）；AVIF 在 Pillow 内置支持或安装 `pillow-avif-plugin`（模块 `pillow_avif`）时可用。构建专属虚拟环境中使用 Pillow 13.0.0、pillow-heif 1.8.1、pillow-avif-plugin 1.6.0，已真实编码 HEIC/AVIF 并通过上传、JPEG 预览/缩略图解码、原图下载字节一致及消息清空回收测试。系统 Python 缺少这些编码器时，两项可选回归明确跳过；没有安装到全局 Python。打包器按构建环境收集可选插件，最终交付 EXE 也已通过独立 HEIF/AVIF 实际图片往返测试，不是仅据源码环境宣称支持。
- 原图始终保留原字节。列表加载最长边 320px 的 JPEG 缩略图；BMP/TIFF/ICO/HEIC/AVIF 的灯箱使用最长边 1920px 的安全 JPEG 预览。PNG/JPEG/GIF/WebP 灯箱使用原图，保留动画和原始分辨率；下载取原图，不下载转码预览。
- SVG/SVGZ 明确拒绝，绝不内联执行；图片响应有禁止脚本的 CSP。非浏览器原生格式强制 attachment。
- 旧 Base64 API 保持原字节及原有缩略图兼容规则，但仍受旧 JSON/Base64 兼容上限约束。刷新后的第七版页面走二进制传输，不受这一旧路径的 5MB 门槛影响。

### 保留资源保护，不是无限占用服务器

启动前可通过环境变量配置；没有新增桌面设置，也不改变现有 QSettings。

| 环境变量 | 默认 | 含义 |
| --- | --- | --- |
| `LANSHARE_DATA_DIR` | 程序目录的 `chat_data` | 聊天独立数据目录；用于回归测试隔离 |
| `LANSHARE_CHAT_IMAGE_MAX_MB` | `0` | 无固定单图大小门槛；非零时管理员可设单图上限 |
| `LANSHARE_CHAT_TEMP_MB` | `2048` | 尚未发送图片的临时空间配额（MiB） |
| `LANSHARE_CHAT_MIN_FREE_MB` | `512` | 磁盘必须保留的剩余空间（MiB） |
| `LANSHARE_CHAT_MAX_PIXELS` | `80000000` | 解码前像素保护上限 |

分片最大 1MiB，最多同时保留 8 个上传、2 个解码任务；上传 30 分钟过期。大图先缩小再 EXIF 转向/转换，避免额外全尺寸复制；格式、像素、临时配额、磁盘保护超限时返回明确错误。默认没有“5MB”拒绝，但仍可能因这些保护拒绝一张很大的图片。

## 草稿和复制文本

- 服务器不再 `trim` 消息正文，只用空白检测拒绝纯空消息；前后空格、空行、Tab 原样保存。
- textarea 粘贴只读取 `text/plain`，CRLF 归一到 LF，不解释剪贴板 HTML。消息正文和编辑框使用 `white-space: pre-wrap; tab-size: 4`，保留换行和缩进。
- 失败保留草稿，重试复用发送 ID。成功只清掉实际发送的那份草稿，不覆盖用户等待时输入的新文字。

## 初始化与回归

导入 `config`/`chat_store` 不再创建目录、加载真实数据、迁移或清孤儿图。`FileSharer.start` 显式调用幂等的 `initialize_chat_store()`；`CHAT_ROOMS` 保持字典 identity，避免其他模块仍拿着旧字典。设置 `LANSHARE_DATA_DIR` 后只使用这个隔离目录，不访问用户旧聊天迁移文件。

```text
python -B tools/v7_chat_test.py
node tools/v7_chat_frontend_test.js
python -B tools/v7_browser_test.py
```

最终复验使用 `E:\LanShare-builds\build-22jznups\env\Scripts\python.exe`，环境位于 E 盘、Windows 系统目录之外；不依赖之前的 `C:\Windows\Temp` 构建环境。产品源和测试源保持冻结，只更新验证证据和本文档。HTTP 16/16、Node VM 6/6、Chrome 6/6 全部通过，失败和跳过均为 0；上述源码回归共 28 个实际用例，不将循环检查或多个 assertion 重复计数。独立 EXE 的 8 个用例另外记录，不混入这 28 项。

构建虚拟环境 HTTP 回归 16 项及前端 Node VM 回归 6 项通过：空房第一条 SSE、删除/清空/改名跨连接事件、标准游标重放、失效/过期 reset、默认群持久化/权限、文本完整性、重复发送、20.86MiB BMP 原图上传下载字节一致、7 类基础图片和 HEIC/AVIF 可选编解码器、SVG/伪图拒绝、访问会话隔离、跨房间 token、待发/取消/已发图片生命周期、过期分片、中断回滚、像素/解码名额/临时配额/磁盘保护、网络/HTTP 失败草稿、纯文本粘贴/转义及 CSS 排版合同。

真实 Chrome 综合回归 6 组通过。使用专属临时分享目录、独立 Chrome profile、随机 HTTP/CDP 端口、专属下载目录，不连接用户已运行的浏览器或网盘，也不访问真实聊天、注册表或 QSettings。

- 真实点击文件行预览/下载，确认两按钮相邻；PNG、TXT、PDF 实际打开且预览不触发下载，未知格式禁用预览。Chrome 最新 PDF 查看器位于 closed shadow 的 extension OOPIF，按父 target 绑定检查 `loadState=success` 及一页文档尺寸，并有实际渲染截图，不把顶层空白 body 误当产品失败。
- 搜索当前目录、包含子目录、全部共享目录分别命中 1/2/3 个合成文件；延迟旧 `alpha` 搜索响应后发 `beta`，结果不被旧请求覆盖。恶意 `</script>` 群名显示为文本，不插入可执行脚本或样式。
- 真实 textarea 纯文本粘贴保留空格、空行和 Tab；实际消息 DOM、计算样式及服务端历史一致。真实 HTTP 503 不清草稿；两个独立页面同步收到空房首条消息、群改名、删除、清空。
- 在 320/390/1280px 对文件页和聊天页检查无横向溢出，生成六张截图并逐图视觉检查；运行中没有 JavaScript exception。
- 通过真实 file input 选择 2700x2700 的完整 BMP（21,870,054 字节，不是伪造文件填充），浏览器发送 21 个二进制分片，进度到 100%。HTTP 503 保留同一个待发媒体和文字，重试不重新上传；发送成功不覆盖等待期间的新文字。列表实际加载 320px JPEG 缩略图，灯箱实际加载 1920px JPEG 预览，点击保存得到与 BMP 原图逐字一致的文件。正在上传时取消会移除服务器 `.part` 和上传 token，仍保留文字。

初始 HTTP/Node 日志位于本目录的 `chat-http-test.log`、`chat-frontend-test.log`。最终构建环境日志位于 `test-results/v7-chat-build-python.log`、`test-results/v7-browser-build-python.log`，最后一次 Node VM 回归为 `test-results/v7-chat-frontend-final.log`；系统 Python 可选编解码器跳过记录位于 `test-results/v7-optional-codecs-system-python.log`。浏览器截图为 `test-results/v7-files-{320,390,1280}.png`、`test-results/v7-chat-{320,390,1280}.png`、`test-results/v7-preview-pdf.png`、`test-results/v7-chat-large-image.png`、`test-results/v7-chat-bmp-lightbox.png`；PDF 成功状态和大图字节数/分片数/原图 SHA-256 记录在对应 `v7-pdf-browser-evidence.json`、`v7-browser-large-image-evidence.json`。

`.log` 仅供本机复核，受默认 `*.log` 忽略规则保护，不强制加入 Git。公开结果由总任务 aggregate 记录；合成 PNG 和 JSON 可以提交。`test-results/v7-browser-artifacts.json` 记录最终 E 盘环境版本、九张截图的文件名/尺寸/字节数/SHA-256，以及两份浏览器证据 JSON 的元数据；其中没有用户真实分享、聊天或浏览器 profile 内容。

Node VM 回归执行真实 JS 模块和 CSS 合同，但不代替真实浏览器像素验证；真实浏览器证据来自以上 Chrome 测试。这里不宣称所有手机内置浏览器、所有 HEIF/AVIF 编码变体都已验证。

## 最终打包 EXE 验证

交付文件为 `E:\code\局域网\第七版\lanshare.exe`，大小 63,929,736 字节，SHA-256 为：

```text
ad5e408f42d8ddd3e302073e234c05d069da75d7188c6a1866e80d7b0e3eea17
```

独立 `tools/v7_exe_test.py` 已针对该交付文件通过 8/8 回归，失败及错误均为 0；最终交付路径复验报告记录 9.360s。报告位于 `test-results/exe-verification.json`，其中 `exe` 指向交付文件，而不是暂存构建路径。测试将同字节 EXE 复制到专属空 portable 目录，使用 Windows-only PATH、隔离 profile/INI、offscreen Qt；EXE 旁没有 Python 源码，不依赖用户已运行的网盘或真实聊天数据。

- PNG、JPEG、GIF、WebP、BMP、TIFF、ICO 七种常见格式，以及实际编码的 HEIF/HEIC、AVIF 图片均完成原图往返，下载与上传原字节一致；不只是读取格式能力列表。
- 21,870,054 字节 BMP 原图完整上传和下载一致，缩略图/预览可用。默认群改名、文本空白保真、幂等发送重试也通过。
- 关闭时退出码为 0，监听端口关闭；portable 目录仅含 `lanshare.exe`，QSettings 使用独立 INI。

上述结果来自最终 EXE 的真实运行，不用源码或构建成功代替。它仍不是所有 HEIF/AVIF 编码变体、所有 Windows 安装环境或真实手机浏览器的全覆盖测试。
