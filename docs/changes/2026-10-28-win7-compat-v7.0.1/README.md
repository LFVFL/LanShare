# v7.0.1 Windows 7 x64 兼容包

## 结论

用户提供的 Win7 截图先提示 `Failed to load Python DLL ... python312.dll`，随后提示缺少
`api-ms-win-core-path-l1-1-0.dll`。这不是解包目录或单个运行库文件的问题：当前常规包使用
Python 3.12，而 Python 3.9 起已不再支持 Windows 7；`api-ms-win-core-path-l1-1-0.dll`
也是 Win7 不提供的 API Set。继续在 Python 3.12 包上补 DLL 会引出更多依赖，无法形成可靠方案。

本轮因此保留常规 `lanshare.exe` 不动，另做独立 Win7 包：

| 文件 | 目标系统 | 版本 | 大小 | SHA256 |
|---|---|---:|---:|---|
| `lanshare.exe` | 当前 Windows 10/11 常规包 | 7.0.1 | 63,929,453 | `ace121b3504d83c566b1c5b20be69e65e9de97a1b21d7be8b0bf8975df7d3c6b` |
| `lanshare-win7.exe` | **64 位 Windows 7 SP1 候选** | 7.0.1 | 61,342,385 | `bff338db03a2e8e8d2a43c95dd7ad22f41888b38aadcbe32765413db2f665e0e` |

两个文件不能互相替代。`lanshare-win7.exe` 是单文件、无控制台窗口的应用包，不需要安装
Python；设置与聊天数据的处理方式没有改变。

## Win7 包内容

- Python 3.8.10 64-bit
- PyInstaller 5.13.2
- PyQt5 5.15.9 / Qt 5.15.2
- Pillow 10.4.0
- pillow-heif 0.18.0、pillow-avif-plugin 1.4.6

包里同时包含 `ucrtbase.dll` 和 Python/Qt 所需的 38 个 UCRT API Set 转发库，因此不要求
Win7 预先安装 Universal C Runtime 或 VC++ Redistributable。启动壳只导入
`ADVAPI32`、`COMCTL32`、`GDI32`、`KERNEL32`、`USER32` 五个 Win7 原生系统 DLL。

注意：

- 只支持 **64 位 Windows 7 SP1**。32 位 Win7 不能加载这个 x64 文件，需要另做 x86 构建。
- 尚未在真实 Win7 电脑上完成首次双击测试；当前结论来自 PE 静态扫描和 Windows 10
  主机上的隔离启动/功能回归。
- 文件未做数字签名。Windows SmartScreen 或安全软件仍可能提示未知发布者；请先用
  SHA256 核对文件，不要通过关闭系统安全功能来强行运行。
- Python 3.8 已停止安全更新，此包仅用于旧系统兼容，不应视为比常规包更安全。

## 验证摘要

| 记录 | 范围 | 结果 |
|---|---|---|
| `startup-temp.json` | 源候选：10 个启动方法、12 次启动，含首次运行、异常/中文 `LOCALAPPDATA`、`TMP/TEMP` 选择、重复运行和原路径 | 12/12 通过 |
| `startup-final.json` | 最终 `lanshare-win7.exe`：同一套 10 个方法、12 次启动 | 12/12 通过 |
| `exe-verification.json` | 单文件功能：预览/下载、Range、9 种图片格式、HEIC/AVIF、20 MiB 原图、群名与文本空白保留 | 8 个方法通过 |
| `version-final.json` | 窗口标题、窗口内版本、Qt/网页版本及冻结归档对应性 | 9 个方法通过 |
| `pe-imports-final.json` | 归档内 118 个 DLL/PYD/EXE 的导入表、38 个 UCRT API Set 完整性、启动壳导入 | 无禁用项、无缺项 |

完整构建环境、源文件哈希、证据文件哈希和限制见
[`test-results/build.json`](test-results/build.json)。

最终路径首次验收时，沙箱新建文件继承了父目录的 Low 完整性标签，原路径启动返回
`3221225794 (0xC0000142)`；同一文件的临时目录副本则可启动。随后从未带 Low 标签的
同盘暂存目录重新 `File.Move` 交付，最终文件的安全描述符与已知可运行的 `lanshare.exe`
一致，父目录 ACL 未改，再跑完整矩阵后 12/12 通过。该失败记录保留在
`startup-final-low-integrity.json`，没有被覆盖或删除。

受控启动测试不能代替朋友电脑的真实 Win7 首次双击、杀毒软件扫描或域策略环境。

## 复现构建

需要 64 位 Python 3.8：

```powershell
py -3.8 -m venv E:\LanShare-builds\win7-env
E:\LanShare-builds\win7-env\Scripts\python.exe -m pip install -r requirements-win7.txt
E:\LanShare-builds\win7-env\Scripts\python.exe -B tools\build_win7_exe.py `
  --work E:\LanShare-builds --venv E:\LanShare-builds\win7-env
```

再次验收：

```powershell
E:\LanShare-builds\win7-env\Scripts\python.exe -B tools\v7_startup_temp_test.py `
  --exe E:\code\局域网\第七版\lanshare-win7.exe --results <新的结果路径>.json
E:\LanShare-builds\win7-env\Scripts\python.exe -B tools\v7_win7_pe_test.py `
  --exe E:\code\局域网\第七版\lanshare-win7.exe --results <新的PE结果路径>.json
```

所有结果脚本都拒绝覆盖已有 JSON，应使用新的文件名。

## 仍需实机确认

请朋友在 Win7 上提供以下信息：

1. `winver` 截图，确认是否为 Windows 7 SP1；
2. “系统”页面截图，确认是 64 位操作系统；
3. 双击 `lanshare-win7.exe` 后的窗口截图；
4. 若仍报错，提供完整错误文字和安全软件的拦截记录。

如果系统实际是 32 位 Win7，则当前 x64 候选不适用，应停止重复尝试并另做 32 位构建。
