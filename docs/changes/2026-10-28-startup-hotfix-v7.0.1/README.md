# v7.0.1 启动兼容性修复候选

## 故障与责任

朋友电脑运行 v7.0 时提示 `LOADER: failed to create runtime-tmpdir path`，
目标为当前用户的 `AppData\Local\LanShare\Temp`，随后显示“系统找不到指定的路径”。
这是新增自定义解包策略引入的兼容性依赖；此前仅在受控开发环境通过，不足以证明其他
电脑可用。该错误发生在 Python 入口之前，不能在应用入口补 `mkdir` 修复。

匹配构建实际使用的 PyInstaller 6.22.3 官方
[Windows bootloader 源码](https://github.com/pyinstaller/pyinstaller/blob/v6.22.3/bootloader/src/pyi_utils_win32.c#L129)
显示：自定义路径会展开环境变量并尝试逐级创建；任何最终路径创建失败都会直接终止，
不会回退至系统临时目录。祖先目录的创建错误被忽略，因此截图不能单独证明是哪一级
权限、重定向或路径状态导致失败，也不能归咎为“没有递归创建目录”。

## 最小改动

- 移除 `--runtime-tmpdir`，恢复 PyInstaller 默认 Windows 临时目录机制，不再额外依赖
  `%LOCALAPPDATA%\LanShare\Temp`；保留 Qt、VC 运行时及图片解码器收集。
- 构建后明确拒绝任何 `pyi-runtime-tmpdir` 归档选项，防止误用旧策略。
- 测试使用全新 bootloader 环境和隔离数据，补充首次运行、不存在或不可用的
  `LOCALAPPDATA`、中文与空格临时路径、重复运行和最终原路径验收。
- 窗口、网页和 Qt 应用版本统一为 `7.0.1`；不更改文件、聊天室等业务功能。
- 不修改第六版、真实设置、聊天数据、密码日志、系统环境变量或目录权限。

## 验证与交付状态

已在本机重新构建独立单文件 EXE，构建时使用原有依赖环境，没有升级 Python、Qt、
PyInstaller 或图片解码器。构建后归档选项只有 `pyi-contents-directory _internal`，
没有 `pyi-runtime-tmpdir`；独立启动冒烟已通过。

本目录 `test-results/` 记录本轮实跑结果，不覆盖此前任何验收证据：

| 记录 | 实测范围 | 结果 |
|---|---|---|
| `build-safety.json` | 构建目录保护、bootloader 环境隔离、归档契约、进程归属 | 28 个方法通过 |
| `startup-temp-candidate.json` | 10 个启动方法、12 次启动；首次运行、LOCALAPPDATA 异常、中文/空格、TMP 优先与 TEMP 回退、重复与原路径 | 全部通过，解包正常清理 |
| `exe-verification.json` | 预览/下载及 Range、9 种图片格式、20MiB 原图、群名与文本格式 | 8 个方法通过 |
| `version.json` | 窗口标题、窗口内版本、Qt、网页及冻结归档对应性 | 9 个方法通过 |

计数规则：以上为 55 个不同的测试方法；图片格式子测试及重复启动不额外计数。
最终原目录交付及普通用户令牌检查另行记录，不能把先前的 116 项历史验收当成本轮重跑。

新包先作为本地修复候选交付，不覆盖 GitHub 的 v7.0 标签或附件。

边界：默认机制仍要求 Windows 选中的临时目录可用；撤销覆盖不代表无效 `TMP`、权限
拦截、安全软件或任意损坏的系统环境也一定能启动。受控环境测试不是全新 Windows
用户、干净虚拟机或朋友电脑的真实首次双击测试，后者仍需单独确认。
