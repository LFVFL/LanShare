# 第七版首轮验证与交付（历史记录）

> 以下 EXE 哈希、构建目录和 138 项结果属于启动报错前的首轮交付，不是当前文件的哈希。
> 最新单文件启动修复已另行记录在 [2026-10-28-startup-fix](../2026-10-28-startup-fix/README.md)。
> 当时的 EXE 测试把 TEMP/TMP 都设为有效的自有目录，未覆盖无效 TMP 的启动条件；
> 本次补上启动矩阵、实际解包路径与退出回收检查，保留本目录原始证据，不冒称重跑全部套件。

> 后续路径问题与界面专项复测见 [2026-10-28-path-and-chat](../2026-10-28-path-and-chat/README.md)。
> 新候选哈希与原路径验收分开记录；本文首轮哈希和 138 项结果保持历史含义，不据此宣称当前原路径已修复。

完成日期：2026-10-28（Asia/Shanghai）。本轮修改在独立的第七版工作区进行；第六版保留不动。未提交、未推送 GitHub，未安装真实右键菜单，也未控制用户正在运行的服务。

## 单文件 EXE

| 项目 | 首轮结果（历史） |
| --- | --- |
| 交付位置 | `E:\code\局域网\第七版\lanshare.exe` |
| 文件大小 | 63,929,736 字节，60.97 MiB |
| SHA-256 | `ad5e408f42d8ddd3e302073e234c05d069da75d7188c6a1866e80d7b0e3eea17` |
| 构建类型 | Windows x64，PyInstaller onefile/windowed |
| 最终 staging | `E:\LanShare-builds\build-2lqxw_37` |
| 源码一致性 | 29 个运行源码、嵌入资源及图标文件与 staging 逐项 SHA-256 一致 |
| 嵌入前端版本 | `fe144e9977` |
| 打包结果 | 退出码 0，Qt/平台插件/VC 运行时归档检查通过，隔离 EXE 启动冒烟通过 |
| 交付文件复验 | 8/8，通过；正常退出码 0、监听关闭、独立 INI |

只需 EXE，不需要安装 Python，也不需要在旁边放源码、Qt 或图片解码器。单文件运行时会在临时目录解包；正常使用仍按原设计保存设置和 `chat_data`，并不是“永不产生数据文件”。本次只在当前 Windows x64 主机实测，不宣称所有 Windows 版本或干净虚拟机都已覆盖。

独立验证将交付文件复制到专属空目录，把目标进程 PATH 限制为 Windows 系统目录，并移除外部 Python/Qt 路径。验证实际当前 CSS/JS、PNG/TXT 的 inline 预览与 attachment 下载、Range、退役的 `/open`、默认群改名、正文空白、发送幂等，以及九类真实图片往返。21,870,054 字节 BMP 原图上传、下载逐字节一致。EXE 旁只有它自身，测试设置和聊天数据在独立 profile 中。

## 实际构建环境与命令

| 依赖 | 本次版本 |
| --- | --- |
| Python | 3.12.1 x64 |
| PyQt5 / Qt / sip | 5.15.11 / 5.15.2 / 12.20.0 |
| Pillow | 13.0.0 |
| PyInstaller / hooks-contrib | 6.22.3 / 2026.9 |
| pillow-heif / pillow-avif-plugin | 1.8.1 / 1.6.0 |
| brotli / segno | 1.2.1 / 1.6.6 |
| Node | v24.14.1 |

实际构建使用系统目录之外的 E 盘独立 venv。先前 C:\Windows\Temp 环境被 PyInstaller 误当系统库位置，造成漏 Qt DLL；已在新 E 盘环境重建，旧构建保留，没有删除或搬动。最终还补齐了普通 DLL 扫描不覆盖的 `concrt140.dll` 延迟依赖，并排除宿主 Codex PATH 带入的无关库。

在第七版工作区执行的最终命令：

```powershell
Set-Location -LiteralPath 'E:\code\局域网\第七版'
$python = 'E:\LanShare-builds\build-22jznups\env\Scripts\python.exe'
& $python -B tools/build_exe.py --work 'E:\LanShare-builds' --venv 'E:\LanShare-builds\build-22jznups\env' --extra-images
& $python -B tools/v7_exe_test.py --exe 'E:\code\局域网\第七版\lanshare.exe'
```

重新构建也可以用 `python tools/build_exe.py --work E:\LanShare-builds --extra-images` 创建新的专属环境；每次产物路径会变化。完整已安装包列表、构建目录、产物信息及哈希见 `test-results/build-environment.json`、`build-source-manifest.json`、`release-artifact.json`。最终 DLL 检查见 `dll-closure-build-E.json`。原始构建日志 `final-build-release.log` 仅保存在本机。

## 回归结果

| 新增套件 | 实际通过用例 | 执行命令 | 最终证据（test-results/） |
| --- | ---: | --- | --- |
| 后端安全、ZIP、multipart、预览 | 29/29 | `python -B tools/v7_security_test.py` | `security-build-venv.json` |
| 文件分页与三范围搜索 HTTP | 9/9 | `python -B tools/v7_files_test.py` | `files-http.json` |
| 前端资源缓存版本 | 5/5 | `python -B tools/v7_assets_test.py` | `assets.json` |
| 文件页 Node VM 行为 | 15/15 | `node tools/v7_files_frontend_test.js` | `files-frontend.json` |
| 聊天 HTTP、多客户端、图片 | 16/16 | `python -B tools/v7_chat_test.py` | `final-suite-results.json` |
| 聊天 Node VM 草稿与粘贴 | 6/6 | `node tools/v7_chat_frontend_test.js` | `final-suite-results.json` |
| 真实 Chrome 综合组 | 6/6 | `python -B tools/v7_browser_test.py` | `final-suite-results.json`、浏览器证据及截图 |
| 桌面生命周期与用户隔离实例 | 21/21 | `python -B tools/desktop_lifecycle_test.py` | `final-suite-results.json` |
| 构建保护与测试数据隔离 | 23/23 | `python -B tools/v7_build_test.py` | `final-suite-results.json` |
| 交付的独立 EXE | 8/8 | `python -B tools/v7_exe_test.py --exe <交付路径>` | `exe-verification.json` |
| 合计 | **138/138** | 不重复计算循环、图片格式或 assertion | `final-suite-results.json` |

最终构建环境无失败、无跳过。Chrome 的六组是六个 unittest 方法，不把组内检查再重复计数；PNG/JPEG/GIF/WebP/BMP/TIFF/ICO/HEIF/AVIF 的循环同理。系统 Python 先前缺少可选图片插件而跳过的历史运行，不是最终结果。

另外，`smoke_test`、`auth_matrix_test`、`protocol_test`、`search_test`、`fsops_test`、`zip_test`、`sse_test`、`thumb_test`、`origin_test`、`paging_test`、`share_switch_test`、`limits_test`、`desktop_test` 共 13 套既有回归全部退出码 0。资源构建和 preflight 也通过；完整命令和时间见 `final-root-regressions.json`。

Python 源以 Python 3.8 语法模式做 AST 检查，全部 JS 执行 `node --check`，并执行 `git diff --check`；结果见 `static-checks.json`。语法检查不等于已经在 Python 3.8 运行或证明当前最新第三方依赖兼容 Python 3.8。

## 原审查发现的处理映射

编号对应最初的第六版审查报告。

| 编号 | 处理 | 主要验证 |
| --- | --- | --- |
| 1 P1：Web 启动主机文件 | `/open` 返回 410，预览不再调用主机执行 | 安全、独立 EXE |
| 2 P1：测试清理真实聊天图片 | import 无迁移/清理；独占临时数据与边界校验 | 构建隔离、13 套既有回归 |
| 3 P1：危险构建清理/终止全机同名进程 | 新专属 staging，拒绝危险路径，只管理持有的测试句柄 | 构建隔离、最终构建 |
| 4 P1：多实例/重复端口绑定 | Windows exclusive bind、每用户 mutex、受限 IPC | 桌面生命周期 |
| 5 P1：递归 Junction 泄露 | 复制/移动/ZIP 拒绝链接后代，遍历时复核 | 安全、文件 HTTP |
| 6 P2：群名突破内嵌 JSON | 统一安全 JSON 序列化，标题按文本更新 | 安全、Chrome |
| 7 P2：切根后旧 ZIP 有效 | generation 绑定，缓存/待生成/流式旧任务失效 | 安全、既有共享切换 |
| 8 P2：空群/失效游标消息不同步 | epoch:seq、Last-Event-ID、reset 快照 | 聊天 HTTP、Chrome |
| 9 P2：删除/清空不传播 | delete/clear/room_updated/room_deleted 事件 | 聊天 HTTP、Chrome |
| 10 P2：丢草稿/批删误报 | 成功才清对应草稿；校验每项 HTTP 与 JSON | 聊天 Node/Chrome、文件 Node |
| 11 P2：multipart 跨块损坏 | 有界滑动窗口、结束验证、失败只回收本次文件 | 安全 |
| 12 P2：长连接后续缺安全头 | 每个响应独立设置安全头 | 安全、协议回归 |
| 13 P2：密码日志/明文设置 | **按用户要求不修改、不脱敏** | `password-policy-unchanged.json` 的基线 AST 对比 |
| 14 P2：首屏/搜索/排序分页不一致 | 400 项首屏、服务端全目录过滤及排序后分页 | 文件 HTTP/Node、Chrome |
| 15 P2：桌面起停迟到回调 | 四状态、取消 token、真正停止后更新 UI | 桌面生命周期 |
| 16 P2：干净 clone 打包不完整 | 从当前源重建资源、默认解释器、安全环境与 EXE 核验 | 构建隔离、最终构建 |

原报告第四节的账户/限流、正常数据 AppData 迁移、普通传输协议扩展、开机托盘/schtasks 重构、CI/发布自动化等不实施。没有把这些建议写成“已完成”。

## 用户追加需求与浏览器验收

- 图片取消固定 5MB 门槛：新页面独立二进制分片，原图不转码；本次 EXE 包含九类解码支持。仍保留临时配额、剩余磁盘、像素和并发保护，旧 Base64 兼容 API 仍有限额。
- 搜索：可见按钮及当前目录/包含子目录/全部共享，默认包含子目录；4 秒、20000 扫描项、80 结果的递归上限有截断提示，不假装已搜索完整。
- 群名：管理员可修改默认群和自建群，ID、历史和图片保持不变，其他页面更新。
- 右键：无运行实例时在目录启动，已有实例时转交并切目录；正常起停更新菜单标签，不安装动态 Shell 扩展。
- 复制文本：纯文本换行、空格、空行及 Tab 保留，CRLF 归一为 LF。不是 Word/网页富文本编辑器，不保留字体、颜色、表格或 HTML 样式。
- 预览：列表/网格/搜索均紧邻下载；Chrome 实际点击 PNG、TXT、PDF 没有下载事件。PDF 检查扩展 OOPIF 的成功加载状态与实际一页渲染，不用空顶层 DOM 误判。未知/主动格式明确禁用，不伪装下载。

真实 Chrome 在 320/390/1280px 检查文件与聊天页，无横向溢出及 JS exception。九张合成截图及 PDF、大图证据的尺寸、字节数与哈希见 `v7-browser-artifacts.json`；没有真实共享文件或聊天内容。

## 未覆盖与保留边界

- 真正安装/移除右键注册表、开机启动计划任务和真实开机未执行；相关单元测试使用受限 fixture/mock，避免改变用户系统。
- 普通注册表菜单标签在崩溃后可能暂时陈旧，下次正常起停更新；点击行为仍由单实例 IPC 判断，不宣称静态标签时时动态。
- 只验证当前 Chrome 与当前 Windows 主机；未覆盖所有手机内置浏览器、音视频 codec、HEIF/AVIF 编码变体及多系统安装环境。
- 密码明文日志与明文 QSettings 按要求保留。无需为此次交付迁移第六版数据；没有自动读取、复制、删除真实聊天。
- EXE 和原始 `.log` 默认忽略，不直接提交源码仓库。手工推送准备及公开证据白名单见 `github-push.md`。
