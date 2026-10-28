# v7.0.1 发布记录

日期：2026-10-28（Asia/Shanghai）。本版本修复 v7.0 在部分电脑上无法创建
`%LOCALAPPDATA%\LanShare\Temp`、启动壳在 Python 入口前退出的问题，并发布两个独立的
单文件附件。

## 交付附件

| 文件 | 目标系统 | 版本 | 大小 | SHA-256 |
|---|---|---:|---:|---|
| `lanshare.exe` | Windows 10/11 x64 | 7.0.1 | 63,929,453 | `ace121b3504d83c566b1c5b20be69e65e9de97a1b21d7be8b0bf8975df7d3c6b` |
| `lanshare-win7.exe` | 64 位 Windows 7 SP1 候选 | 7.0.1 | 61,342,385 | `bff338db03a2e8e8d2a43c95dd7ad22f41888b38aadcbe32765413db2f665e0e` |

EXE 被 `.gitignore` 排除，只作为 GitHub Release 附件，不进入源码历史。

## 修复范围

- 移除 `--runtime-tmpdir`，不再依赖 `%LOCALAPPDATA%\LanShare\Temp`；恢复 PyInstaller
  在 Windows 上选择默认临时目录的行为。
- 构建后拒绝带有 `pyi-runtime-tmpdir` 的归档，避免旧策略被误带回。
- 窗口标题、窗口内版本、Qt 应用版本和网页版本统一为 `7.0.1`。
- 常规包继续使用 Python 3.12；Win7 候选包使用 Python 3.8 和 PyInstaller 5.13.2，
  附带 Qt 和 38 个 UCRT API Set 转发库。
- 不迁移设置或聊天数据，不修改系统环境变量、整个目录的 ACL 或密码日志行为。

## 验证摘要

常规包的启动矩阵覆盖首次运行、缺失或异常 `LOCALAPPDATA`、中文与空格临时路径、
`TMP/TEMP` 选择、重复运行和最终原路径，共 10 个启动方法、12 次启动，全部通过。
构建安全检查、独立 EXE 功能和版本对应性也通过。

Win7 候选包：

- 最终路径的 10 个启动方法、12 次启动全部通过；
- 独立 EXE 功能 8 个方法、版本检查 9 个方法通过；
- 归档内 118 个 DLL/PYD/EXE 无 Win7 禁用导入，38 个 UCRT API Set 齐全；
- 尚未在真实 Windows 7 电脑上完成首次双击确认。

详细证据：

- [启动修复记录](../2026-10-28-startup-hotfix-v7.0.1/README.md)
- [Win7 x64 兼容包记录](../2026-10-28-win7-compat-v7.0.1/README.md)
- [GitHub Release 文案](release-notes.md)

## 发布结果

源码和文档提交 `b202631b8071b81812c026dd8ba7b2017ca3844a` 已推送到 `main`，
annotated tag `v7.0.1` 指向该提交。GitHub Release `426505756` 于
2026-10-28T10:20:47Z 公开，并确认是 latest 稳定版：

- [LanShare v7.0.1](https://github.com/LFVFL/LanShare/releases/tag/v7.0.1)
- [lanshare.exe](https://github.com/LFVFL/LanShare/releases/download/v7.0.1/lanshare.exe)
- [lanshare-win7.exe](https://github.com/LFVFL/LanShare/releases/download/v7.0.1/lanshare-win7.exe)

两个附件的名称、大小、状态和 GitHub API SHA-256 digest 均已与本地文件逐项核对，
结果见 [github-release.json](test-results/github-release.json)。发布使用本机既有 Git
凭据，凭据没有写入仓库、日志或发布记录。

## 已知边界

- 两个 EXE 均未签名，SmartScreen 或安全软件可能提示未知发布者。
- 常规包不能解决无效 `TMP`、权限拦截、安全软件或任意损坏系统环境导致的全部启动问题。
- Win7 包仅支持 64 位 Windows 7 SP1；32 位系统需要另做 x86 构建。
- Win7 包的结论来自静态扫描及 Windows 10 主机上的隔离验证，不等于真实 Win7 实机
  或域策略环境验收。
- Python 3.8 已停止安全更新。
- 密码日志和明文设置按用户要求保留，日志可能包含完整密码，不能公开。
