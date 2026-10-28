# 第七版单文件 EXE 启动修复

> 当前 v7.0 已接续本次修复；最新哈希、原路径验收和发布状态见
> [v7.0 发布记录](../2026-10-28-release-v7/README.md)。本文及其 JSON 保留当轮历史状态。

> 后续用户反馈：此包在特定原目录仍然报错，移动目录后表现不同。本记录保留当时的
> 修复与隔离验证事实，不表示已经解决精确 EXE 路径的问题。后续调查及界面修正见
> [路径启动排查与界面修正](../2026-10-28-path-and-chat/README.md)。

完成日期：2026-10-28（Asia/Shanghai）。本轮处理双击 `lanshare.exe` 时出现的
`Could not create temporary directory!`。第六版保持不动；没有提交或推送 GitHub，
没有修改真实设置、聊天记录、系统环境变量或注册表，也没有结束用户已有的服务。

## 当时交付（已被后续包替换）

| 项目 | 结果 |
| --- | --- |
| EXE | `E:\code\局域网\第七版\lanshare.exe` |
| 大小 | 63,929,292 字节（60.97 MiB） |
| SHA-256 | `e283857ab18dc7533b735ede1ae017cb63a8691791d58f5c1dc70e1aab03ef41` |
| 构建 | Windows x64，PyInstaller onefile/windowed，含 HEIC/HEIF 和 AVIF 解码器 |
| 构建产物 | `E:\LanShare-builds\build-9k4ezesh\dist\lanshare.exe` |
| 交付校验 | 与完成本轮测试的候选逐字节哈希一致；校验原文件与备份后原子替换 |

此处记录的是当时交付的单个 EXE，不需要把 Python、Qt 或源码放在旁边。当前文件及
原路径验收请看顶部后续记录。这个阶段没有在实际资源管理器账户配置下启动应用或读写
聊天数据来冒充验证，后续用户反馈也说明仅凭当时的隔离副本测试还不够。

旧包备份保存在工作区外层的诊断目录：
`E:\code\局域网\_diag\lan7-temp-startup-a4ccexbc\previous\lanshare.exe`。
原包 SHA-256 为 `ad5e408f42d8ddd3e302073e234c05d069da75d7188c6a1866e80d7b0e3eea17`；
没有把旧包、构建目录或 EXE 加入 Git。

## 原因与确认边界

这个提示由 PyInstaller 单文件引导程序在 Python 启动前发出，所以在 `lanshare.py`
中创建临时目录或添加 Python runtime hook 都来不及解决。

使用同版本 PyInstaller 的最小 console/debug 探针得到：

| 条件 | 原选择方式 | 指定用户解包目录后 |
| --- | --- | --- |
| 工具进程继承的有效临时目录 | 正常 | 完整 EXE 启动回归正常 |
| 中文及空格临时路径 | 正常 | 正常 |
| TMP 指向不存在目录，TEMP 有效 | 相同错误，Python 未启动 | 正常 |
| TMP 指向普通文件，TEMP 有效 | 相同错误，Python 未启动 | 正常 |
| TMP/TEMP 都指向普通文件 | 未作为原探针独立用例 | 修复探针正常 |

这确认了一类可复现原因，不等于证明用户当时的资源管理器 TMP 就是这个值。
工具进程与资源管理器的环境不相同，中文用户名本身也没有被证实是问题原因。
此前的 EXE 验证把 TEMP/TMP 都覆盖成有效测试目录，漏掉了这个启动条件；这次补上。
选取后的探针结果见 `test-results/diagnosis.json`，原始诊断日志留在本机。

## 修改内容

- `tools/build_exe.py`：Windows onefile 添加 `--runtime-tmpdir %LOCALAPPDATA%\LanShare\Temp`，
  保留变量字面量，让引导程序在目标机器按当前用户展开，而不是写死构建者的路径。
- 构建后核验归档中的 `pyi-runtime-tmpdir %LOCALAPPDATA%/LanShare/Temp`；斜杠规范化是
  PyInstaller 的正常行为。构建缓存改为本次 staging 下的独立目录。
- 冒烟测试使用自有 `LOCALAPPDATA`，保留继承的 TMP/TEMP，并检查正常退出后的解包回收。
- `tools/v7_build_test.py`：原 23 个方法增加 4 个参数、归档与隔离相关方法，共 27 个。
- `tools/v7_exe_test.py`：保留 8 个方法，增加参数门禁、中文/空格用户路径、真实 Python/Qt
  解包文件和 fixture 清理前的回收验证。
- `tools/v7_startup_temp_test.py`：新增 4 个方法，覆盖继承环境、中文/空格路径、无效 TMP
  两种情况及重复启动；共进行 6 次独立启动，不把循环再计为额外方法。

`%LOCALAPPDATA%\LanShare\Temp` **只用于单文件临时解包**。正常退出时由引导程序删除
本次 `_MEI*` 目录；父目录可能保留。没有迁移正常设置或 `chat_data`，没有改变用户要求
保留的密码日志、明文 QSettings，也没有实施此前排除的其他改进建议。

## 重新构建与依赖复核

首个修复候选虽通过启动和功能测试，但当前沙箱无法读取 38 个 System32 API-set DLL，
间接导致本应收集的 UCRT 合计 39 个文件未打入包。该候选没有交付。只读授权检查证实
这 39 个系统源文件与原包一致，随后在获批上下文重跑标准构建，恢复完整依赖范围。
没有下载或替换系统 DLL，也没有从 Codex 的 PATH 借用图片库。

在第七版目录实际执行：

```powershell
$python = 'E:\LanShare-builds\build-22jznups\env\Scripts\python.exe'
& $python -B tools/build_exe.py --work 'E:\LanShare-builds' --venv 'E:\LanShare-builds\build-22jznups\env' --extra-images
```

复用原独立 venv，没有安装新依赖。Python 3.12.1、PyInstaller 6.22.3、PyQt5 5.15.11 /
Qt 5.15.2、Pillow 13.0.0、pillow-heif 1.8.1、pillow-avif-plugin 1.6.0；完整信息见
`test-results/build.json`。构建前自检、Qt/运行时选项检查与隔离冒烟都通过。

最终静态复核确认：119/119 个原生 PE 在完整归档路径下与旧包逐字节一致，来源为
22 个基础 Python、58 个构建 venv、39 个 Windows 系统文件，来源路径未变且无外部污染。
最终 Analysis 所列来源也逐项读取并核验哈希。248 个归档成员完整；`base_library.zip`
整体哈希变化只是成员顺序不同，其中 155 个文件内容相同。详情见
`test-results/native-dependencies.json`；按 PE 路径及字节等价复用旧直接/延迟导入的
静态符号闭包证据，不宣称已在所有 Windows 系统实测。

## 本轮验证

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 构建安全与隔离 | 27/27 | `test-results/build-safety.json` |
| 启动临时目录矩阵 | 4/4，6 次独立启动 | `test-results/startup-temp.json` |
| 完整独立 EXE | 8/8 | `test-results/exe-verification.json` |
| 合计 | 39 个测试方法通过，无失败或跳过；循环不重复计数 | `test-results/verification.json` |
| 运行源码一致性 | 29 个源码、嵌入资源及图标与原交付及本次 staging 完全一致 | `test-results/build-source-manifest.json` |
| 静态检查 | 62 个 Python 文件的 3.8 语法 AST、16 个 JS 语法及 Git diff 检查通过 | `test-results/static-checks.json` |
| 交付一致性 | 当前 EXE 与完成测试的候选 SHA-256 一致 | `test-results/release-artifact.json` |

完整 EXE 测试包括页面资源、预览与下载区别、Range、默认群改名、消息空白与幂等、
九类真实图片往返，以及 21,870,054 字节 BMP 原图。所有独立 EXE 正常退出码为 0、
监听关闭、INI 隔离，所有解包目录在测试 fixture 删除前已经由引导程序回收。
测试使用动态端口、自有测试目录和进程句柄，没有按进程名结束服务。

复测命令（显式指定结果位置，避免覆盖首轮历史）：

```powershell
$results = 'docs/changes/2026-10-28-startup-fix/test-results'
& $python -B tools/v7_build_test.py
& $python -B tools/v7_startup_temp_test.py --exe '.\lanshare.exe' --results "$results/startup-temp.json"
& $python -B tools/v7_exe_test.py --exe '.\lanshare.exe' --results "$results/exe-verification.json"
```

本轮没有重新执行首轮全部 138 个新增回归和 13 套既有脚本；产品运行源码未变，历史
结果继续保存在 `../2026-10-27`，但不把历史结果改称为新包的再次实测。
Python 3.8 AST 检查仅代表语法检查，不代表已经在 Python 3.8 运行最新依赖。

## 保留边界与推送

- 只在当前 Windows x64 主机的隔离配置下实测，不代表真实资源管理器环境或干净第二台机器。
- 当前用户的 LOCALAPPDATA 仍需存在有效位置、具备写权限和足够空间；磁盘耗尽、权限或安全软件
  拦截仍可能导致解包失败。本修复不更改 ACL，也不要求修改系统 TEMP/TMP。
- 未签名、未创建 Release 或 PR，未安装右键菜单、计划任务或迁移真实数据。
- `test-results/initial-candidate/` 保存未交付中间包调查；原始 `.log` 仅供本机，继续被 Git 忽略。
  可提交 JSON 的白名单已加入 [GitHub 推送准备](../2026-10-27/github-push.md)，不要整目录 `git add`。
