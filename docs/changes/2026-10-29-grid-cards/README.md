# v7.0.2 网格卡片修复

日期：2026-10-29（Asia/Shanghai）。本轮修复文件页网格模式下文件夹无法点击进入的问题，
并重新整理卡片布局和真实浏览器回归。

## 问题原因

- 列表行使用 `.row-name`，旧点击委托也只识别 `.row-name`；网格卡片使用 `.tile-name`，
  因此点击文件夹名称时没有进入目录。
- 网格卡片本身不是链接，也没有键盘焦点，“更多 → 打开”可用但整卡没有明确交互入口。
- 卡片高度由内容自然撑开，标题行和底部操作栏没有固定基准，容易产生参差和多余留白。
- 原卡片最小宽度为 150px，图标区只有 74px，信息层级偏弱。

## 修改内容

- 文件夹卡片增加覆盖整卡的原生链接，点击图标区、名称或卡片空白处均可进入目录。
- 复选框和操作按钮提高层级，避免选择、预览、下载或“更多”操作误触目录跳转。
- 文件夹入口支持键盘 Tab 聚焦和 Enter 打开；列表行原有 Enter 行为保持不变。
- 桌面卡片最小宽度调整为 168px，图标区高度为 96px；手机为 136px 和 80px。
- 卡片使用统一最小高度、两行标题预留和贴底操作栏；副标题增加修改时间。
- 复选框移到右上角，补齐选中、悬停、焦点和文件夹点击样式。
- 窗口、Qt、网页和打包版本统一为 `7.0.2`。

## 验证

| 范围 | 结果 |
|---|---|
| Python/Node 前端行为回归 | 22/22 通过 |
| 真实 Chrome 网格交互 | 文件夹链接、点击进入、复选框防误触、贴底布局全部通过 |
| 响应式布局 | 1280px、390px、320px 通过；手机两列；无横向或卡片内容溢出 |
| 源码版本检查 | 6/6 通过 |
| 最终 EXE 版本与归档对应性 | 9/9 通过 |
| 最终 EXE 功能回归 | 8/8 通过，含预览/下载、Range、9 种图片格式、HEIC/AVIF、20 MiB 原图和群名/文本 |
| 最终路径启动矩阵 | 10/10 方法、12/12 次启动通过，含原路径、中文路径、重复启动和异常 `LOCALAPPDATA` |
| Win7 最终 EXE 版本与归档对应性 | 9/9 通过 |
| Win7 最终 EXE 功能回归 | 8/8 通过 |
| Win7 最终路径启动矩阵 | 10/10 方法、12/12 次启动通过 |
| Win7 PE 依赖扫描 | 118 个归档 DLL/PYD/EXE 无禁用导入，38 个 UCRT API Set 齐全 |
| 构建 | Qt、VC 运行时、默认临时解包及独立启动冒烟通过 |

浏览器证据：

- [桌面 1280px 网格](test-results/grid-desktop-1280.png)
- [手机 390px 网格](test-results/grid-mobile-390.png)
- [前端行为结果](test-results/final/files-frontend.json)
- [最终 EXE 版本结果](test-results/version-exe.json)
- [最终 EXE 功能结果](test-results/exe-functional.json)
- [最终路径启动结果](test-results/startup-final-path.json)
- [Win7 版本结果](test-results/win7-version-exe.json)
- [Win7 功能结果](test-results/win7-functional.json)
- [Win7 最终路径启动结果](test-results/win7-startup-final-path.json)
- [Win7 PE 依赖扫描](test-results/win7-pe-imports.json)

三张视觉截图已逐张检查。真实 Chrome 测试覆盖桌面和移动视口，不等同于所有手机浏览器、
输入法或 Safari/WebView 的完整兼容性验证。

## 最终 EXE

| 文件 | 目标系统 | 版本 | 大小 | SHA-256 |
|---|---|---:|---:|---|
| `lanshare.exe` | Windows 10/11 x64 | 7.0.2 | 63,929,414 | `38a6dcdb1091299f1956e1435f51a648317957a395f4ade110b1cd08c5bc60e` |
| `lanshare-win7.exe` | 64 位 Windows 7 SP1 候选 | 7.0.2 | 61,342,749 | `5e34a6664cf127bf7581e665db065e1396079485ba1fb774f9c59b1ee75c2f1f` |

常规包构建目录为 `E:\LanShare-builds\build-btq5hybu`，使用 Python 3.12.1 和
PyInstaller 6.22.3。Win7 包构建目录为 `E:\LanShare-builds\build-xtm3lpmk`，使用
Python 3.8.10 和 PyInstaller 5.13.2。

两个 EXE 均未做数字签名，Windows SmartScreen 或安全软件可能提示未知发布者。Win7
候选包仅支持 64 位 Windows 7 SP1，32 位系统不支持；结论来自 PE 扫描和 Windows 10
主机上的隔离回归，尚未在真实 Win7 实机完成首次双击确认。

当前修改和 EXE 已进入 `v7.0.2` 发布流程，源码、标签、Release 状态以仓库远端和
GitHub 公开页面为准。
