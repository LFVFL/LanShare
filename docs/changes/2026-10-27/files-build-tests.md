# 第七版：搜索、预览、构建和测试隔离

## 文件浏览与搜索

- `server/listing.py` 统一首屏、刷新和 API 的分页合同：首屏最多 400 项，过滤和名称 / 大小 / 修改时间排序先作用于整个目录，再分页；目录优先、同值稳定排序，返回总数、offset、hasMore、根路径及面包屑。
- `server/routes.py` 的列表路由传递 query / sort / order，前端不再把“已加载的部分文件”当作全部目录搜索结果。直接查询 URL 的首屏也恢复对应查询状态。
- 文件页和手机端提供可见搜索按钮、范围选择：当前目录、包含子目录、全部共享；默认包含子目录。递归结果有相对位置和“打开所在目录”。后端保留 80 个结果、20000 扫描项及 4 秒保护，并显示是否截断，不承诺已经搜完。
- 320ms 防抖、AbortController 和请求序号共同避免较慢的旧结果覆盖新输入。当前目录搜索使用服务端完整目录过滤及分页；排序会重新请求服务器。切换共享根后加载更多从新目录 offset=0 开始，不拼接旧根条目。
- 批量删除同时校验 HTTP 和 JSON，区分成功 / 失败 / 未执行，权限拒绝、网络失败及取消管理员验证时停止后续操作，保留失败项选择，不再误报全部成功。

对应文件：`server/listing.py`、`server/routes.py`、`web/js/50-files.js`、`web/js/72-files-page.js`、`web/pages/files.html`、`web/files-search.css`。`tools/build_assets.py` 一并打入新增 CSS。

## 预览旁邻下载

- 列表、网格、递归搜索结果中的预览按钮紧邻下载按钮。预览链接使用 `preview=1`，不设置 `download` 属性，新标签打开并带 noopener。
- 后端以固定 MIME 白名单区分预览与下载，不依赖 Windows 注册表的 MIME 猜测；PNG / JPEG / GIF / WebP / BMP / ICO、PDF、纯文本及常见音视频可以请求 inline，普通下载始终 attachment。Range 保持可用。
- HTML、SVG、Office、压缩包、未知格式保持附件下载；前端禁用其预览并解释原因，而不是把“点击预览”偷偷当下载。聊天室 TIFF / HEIC 等的 JPEG 灯箱预览属于另一条图片转换路径，不表示普通文件页支持直接浏览这些格式。
- Chrome 实际点击验证 PNG、UTF-8 TXT、PDF 在预览页打开，没有下载事件；旁邻的下载按钮仍产生原图 / 原文下载。PDF 按 Chrome extension OOPIF 检查实际成功加载与渲染，未把顶层空白 DOM 当作失败。

对应后端：`server/paths.py`、`server/transfer.py`；同步列表元数据使用 `previewable`。

## 构建保护及本轮打包排错

1. 每次新建带归属标记的 ASCII staging，不删除旧目录或旧 EXE；拒绝源码、源码祖先、源码内部、卷根和未标记的既有容器。
2. 构建环境必须为独立 venv，验证 pyvenv.cfg、sys.prefix、sys.base_prefix 后才安装依赖；默认解释器使用 sys.executable。先从目标源码生成 webassets.py，干净 clone 不需要旧生成文件。
3. Windows 构建 venv 必须位于 SystemRoot 之外。初次环境位于 C:\Windows\Temp，PyInstaller 的系统 DLL 排除规则漏掉 Qt 主 DLL 和 HEIF 的依赖；console 诊断确认 QtCore 导入失败。旧环境和失败构建保留，新环境在专属 E 盘容器重新创建，并未搬动或删除原文件。
4. 构建时清除外部 PYTHONHOME / PYTHONPATH / Qt 路径，并对白名单 PATH 做 DLL 扫描，避免把宿主工具的 libheif / UCRT 等 DLL 混入 EXE。静态资源 / 自检 / 编译均在隔离环境运行；Pillow 缺失属于自检错误，不再提示为可选功能。
5. 打包完成先读取 CArchive，核验主 Qt DLL、平台插件和 VC 运行时，缺失即失败。PyInstaller 不自动收集 MSVCP140 的 delay-import `concrt140.dll`，本轮显式从目标 venv 的 Qt bin 收集并校验它，再运行离屏隔离 EXE。`--test-profile` 仅允许离屏及 profile 内的聊天目录，使用独立 mutex、IPC 和 INI，不触碰正式实例、右键注册表或计划任务。
6. 只管理本次 Popen 句柄，并分配私有 Windows job。通过 stop.request 正常退出，等待监听关闭及 bootloader 清理；没有 `taskkill /IM` 或任何按名称结束正式进程的操作。job 的启动后 assign 存在理论时间窗口，因此仍以正常退出和残留核查作为证据。

独立验证脚本 `tools/v7_exe_test.py` 把 EXE 复制到空目录，移除 Python / Qt 外部路径，只给目标进程 Windows PATH；通过当前前端资源、预览 / 下载 / Range、9 类真实图片、20.86MiB 原图、默认群改名和纯文本草稿完整性验证其自包含运行。

## 测试数据边界

- `tools/test_support.py` 在应用 import 前建立独占临时根、归属 token 和 LANSHARE_DATA_DIR；清理前验证解析后的绝对路径仍在该根内，拒绝卷根、链接或外部路径。
- GUI fixture 始终使用临时 INI，显式 settings=None 也不能退回真实 QSettings，并禁用系统集成。新版桌面 / 安全 / 聊天测试采用同等专属 fixture，不导入后才切换路径。
- 旧 HTTP 测试只在自己成功排他绑定并打印 READY 后联系端口，还核对期望共享路径；失败启动不会把已有网盘当测试服务。Chrome 使用自己的临时 profile、随机调试端口及下载目录，只等待或结束自己创建的进程。
- thumb_test 不再枚举清空项目真实图片；服务及聊天模块 import 不会加载、迁移或清理用户数据。停服测试等待真实监听释放后才删除临时 fixture。

## 回归命令

```text
python -B tools/v7_build_test.py
python -B tools/v7_files_test.py
python -B tools/v7_assets_test.py
node tools/v7_files_frontend_test.js
python -B tools/v7_exe_test.py --exe <本次独立EXE路径>
```

构建保护及隔离 23 项、文件 HTTP 9 项、资源版本 5 项、真实前端模块 Node VM 15 项；独立 EXE 8 项全部通过。最终交付为 `E:\code\局域网\第七版\lanshare.exe`，构建暂存位于 `E:\LanShare-builds\build-2lqxw_37`；完整环境和所有既有脚本结果统一以 `verification.md`、`test-results/final-suite-results.json` 及 `release-artifact.json` 为准。循环中的图片格式和断言不另增测试方法数。原始 `.log` 保留本机，默认不提交公开仓库。
