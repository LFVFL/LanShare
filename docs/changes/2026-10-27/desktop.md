# 第七版：桌面生命周期与右键目录切换

## 本轮范围

- 只修改第七版的入口、共享控制器、桌面主窗口及两个新的桌面支持模块。
- 第六版不修改；不注册真实右键菜单、不创建或修改真实计划任务。
- 原密码日志及 QSettings 中的密码保存行为保持原样。
- 不包含托盘开机重试、schtasks 异步化、AppData 迁移或 CI。

## 已实现

1. Windows HTTP 监听使用 `SO_EXCLUSIVEADDRUSE`，不再使用允许重复监听的
   `SO_REUSEADDR`；其他平台保留快速重新绑定的行为。
2. `ui/single_instance.py` 使用真实 Windows 用户 SID 的哈希构造实例名。
   Windows 通过每用户全局命名 mutex 抢占主实例；IPC 使用限制为当前用户的
   `QLocalServer/QLocalSocket`，不开放任何 TCP 控制端口。
3. 主实例锁在聊天加载、迁移、清理之前取得。第二实例只发送本地命令，不启动
   HTTP 服务，也不初始化聊天数据。协议只允许 `activate`、`status` 和 `open_here`，
   验证绝对目录，限制 16 KiB 帧、16 个待处理连接及 5 秒超时。
4. `FileSharer` 使用 stopped / starting / running / stopping 四态和锁；启动可以
   取消，失败时释放监听资源。`stop()` 异步关闭监听，`wait_stopped(timeout)` 供
   测试或非 GUI 调用者等待真实停止。窗口和托盘的起停入口一起禁用，启动结果
   带代次校验，退出等待监听关闭后才结束 Qt 事件循环。
5. `--open-here DIRECTORY` 独立于隐藏启动的 `--startup`：没有主实例时在此目录
   启动服务并显示窗口；已有已停止实例时设置目录并启动；运行中的实例直接切换
   目录，不重启 HTTP 服务。启动/停止过渡期间拒绝切换并提示重试。
6. 右键集成只写当前用户 `HKCU\Software\Classes`，覆盖文件夹背景 `%V` 和选中
   文件夹 `%1`。服务停止时显示“在此处启动网盘”，运行时显示“切换到此目录”。
   参数加 `\.` 后缀，避免盘符根路径末尾反斜线破坏 Windows 命令行引号。
7. 共享根的发布复用 `server.storage.set_shared_root()`；每请求快照和 ZIP
   generation 失效机制由服务端改动提供。失败的第二次绑定不修改共享根或清除
   原实例会话。聊天初始化调用幂等的 `initialize_chat_store()`。

## 边界与使用说明

- Windows 注册表菜单标签是静态值：正常启动、停止、退出和下次打开软件会更新。
  进程被强制结束或崩溃后，标签可能暂时保留“切换到此目录”；点击仍通过
  `--open-here` 检查主实例并在需要时启动，实际行为正确。要在菜单展示时也实时
  查询状态需要 Shell 扩展，本轮明确未加入未经验证的复杂扩展。
- 第六版可能在管理员的机器级 HKCR/HKLM 中留下旧菜单。本轮不自动修改或删除
  机器级注册表。安装第七版当前用户条目会覆盖同名的旧背景条目；移除当前用户
  条目后旧机器级条目可能再次出现，需要用户明确选择清理旧版注册项。
- 一个 Windows 用户只允许一个第七版主实例，与程序所在文件夹、端口无关。
  第六版不支持此 IPC，不会被远程控制；若它已占用端口，第七版安全地报告
  端口占用，不会宣称在同一端口成功共享新目录。
- `wait_stopped()` 确认停止接收新连接并释放监听，不承诺已建立的文件下载立刻
  完成；不对真实用户数据执行迁移、删除或自动复制。
- `tools/desktop_test.py` 原来的即时停服断言需要等待
  `gui.sharer.wait_stopped(5)` 后调用 `app.processEvents()`，再检查按钮和二维码。
- 打包冒烟可显式传 `--test-profile ABS_PATH`。仅当
  `QT_QPA_PLATFORM=offscreen` 且 `LANSHARE_DATA_DIR` 的解析路径在此 profile 内时
  允许启动；否则参数校验失败。测试实例使用单独 mutex / pipe 命名空间和 profile
  内的 INI QSettings，并禁用所有真实右键注册表及 schtasks 查询、写入。默认启动
  完全不变；测试不会向用户正在运行的真实实例发 IPC 或切换它的目录。
- 此离屏测试 profile 专属计时器每 100 毫秒查看 `<profile>/stop.request` 文件。
  文件出现后只触发一次既有的正常退出路径，等待服务进入 stopped 后退出 Qt。
  打包冒烟应写此请求并等待自己启动的 onefile EXE 正常退出，让其应用子进程先
  退出、bootloader 再完成清理。生产实例没有这个计时器，也没有新增 IPC 退出命令。

## 回归验证

新增 `tools/desktop_lifecycle_test.py`，使用离屏 Qt、临时目录聊天数据、临时 INI
QSettings 和假 winreg，绝不写真实应用注册表或安装菜单。

覆盖 Windows 排他绑定、真实双进程单实例转发、崩溃后实例锁释放、非法/多帧/
超长命令、空闲连接超时、异常命令安全响应、失败绑定不改根/会话、启动中停止、
取消尚未执行的启动、停止等待与同端口再启动、UI 迟到回调、托盘入口禁用、
运行中切换目录及已停止实例再启、HKCU 双菜单及盘符根参数引用。
另覆盖测试 profile 文件请求必须等待监听真实关闭；资源已构建时还实际启动
`lanshare.py --test-profile` 子进程，验证退出码、监听关闭、实例锁释放及隔离 INI。

```text
python -B tools/desktop_lifecycle_test.py
```

## 2026-10-28 恢复与最终验证记录

- 桌面测试源码曾在 `apply_patch` 的 Update 操作附近两次变为不可用；本次工具
  返回读取权限拒绝后，后续文件检查确认该路径不存在，测试尚未加载。没有证据
  确认具体原因，也未将该现象归因于 ACL 或任何安全软件。
- 原文件完整内容已不可得，不能声称逐字还原。经明确授权，按保留源码片段与原
  回归语义重建原有 19 项覆盖及新增的两项退出测试，共 21 项。采用单文件直接
  `WriteAllText` 写入，不重命名、不删除文件、不修改
  ACL、不禁用安全工具；分段写入仅用于避开 Windows 32767 字符命令行上限。
- 新测试使用 `relative_to()` 做目录边界检查，兼容声明的 Python 3.8；每个 fixture
  清理前确认最终解析目录位于本次专属 TEMP 内、不是卷根或链接、且不包含测试
  源码。执行结束再次核验测试源码存在且 SHA256 与执行前一致。
- GUI fixture helper 对显式 `settings=None` 也强制使用自己的隔离 INI，且在构造
  窗口前拒绝位于该 fixture 外的 QSettings 路径，避免未来测试误用真实设置。
  此保护在现有 integration 用例中追加子断言，测试方法数量保持 21。
- 初次恢复版 21/21 通过，耗时 11.751 秒；仅测试 helper 加固后再次 21/21 通过，
  耗时 11.741 秒。两次都无跳过，产品源在此阶段完全不变。
- 在本轮独立构建虚拟环境内执行全部 21 项，21/21 通过、无跳过，最终加固版耗时 11.741 秒，
  退出码 0。真实 Windows 两进程 IPC、崩溃后 mutex 释放、`stop.request` 等待监听
  实际关闭以及命令行 main 正常退出、监听和实例锁释放、隔离 INI 保存均执行通过。
- 最终测试源码执行前后均为 34627 字节，SHA256 为
  `34B5CD5BAA02DE29284A4526A49472AF0D36CF5232825F66FF5A2BC4C24A45B9`。
  语法用 Python 3.8 AST 规则检查通过，源码无尾随空格；本次 TEMP fixture 已清理，
  只读检查本次 profile 标识没有测试 Python 进程残留。
- 对新增构建私有 Windows job 做只读结构审查：本机 x64 BasicLimits 64 字节、
  IOCounters 48 字节、ExtendedLimits 144 字节，字段布局与 Win32 API 定义相符；
  `JobObjectExtendedLimitInformation=9`、`KILL_ON_JOB_CLOSE=0x2000` 及 HANDLE
  返回类型正确。启动后再 assign 不消除所有理论竞态，因此最终 onefile 冒烟仍需
  采用正常 `stop.request` 退出并检查残留，不仅凭 job 宣称绝不会产生孤儿进程。

实测命令：

```powershell
& 'C:\Windows\Temp\LanShare-builds\build-s4peut1p\env\Scripts\python.exe' -B tools/desktop_lifecycle_test.py
```

## 2026-10-28 正式 E 构建环境生命周期复测

上节 C 盘临时虚拟环境的恢复与验证记录保留为历史记录；它不是最终 EXE 的
构建环境。本轮使用 SystemRoot 之外的真正 E 盘构建环境，测试本身仍只使用
独立 TEMP fixture，不安装系统集成、不访问或改写真实应用设置。

- 解释器：`E:\LanShare-builds\build-22jznups\env\Scripts\python.exe`，实测
  `sys.prefix` 为 `E:\LanShare-builds\build-22jznups\env`。
- Qt 实测 BinariesPath 为
  `E:/LanShare-builds/build-22jznups/env/Lib/site-packages/PyQt5/Qt5/bin`。
  清除外部 Python/Qt 路径环境变量，PATH 仅包含本轮 Scripts 与 Windows 目录，
  不借用宿主 Codex runtime 的 DLL 搜索路径。
- 全部 21 项生命周期测试再次通过，无跳过，耗时 11.756 秒、退出码 0。
  真实 Windows IPC、崩溃后实例锁恢复、监听真实停止、目录切换、测试 profile
  正常退出、实例锁与监听释放以及隔离 INI 保存均执行通过。
- 测试源码未修改，运行前后存在、均为 34627 字节，SHA256 保持
  `34B5CD5BAA02DE29284A4526A49472AF0D36CF5232825F66FF5A2BC4C24A45B9`。
- 本次专属 fixture `C:\Windows\Temp\lanshare-desktop-tests-cesm40g6` 在执行
  完毕后已按测试的边界校验正常清理，`Test-Path` 结果为 False；不手动清理或
  终止任何真实用户目录、进程或应用实例。
- 完整日志：`test-results/desktop-lifecycle-e-build-python.log`。
  此记录是 E 构建环境中的源码生命周期回归，不替代单文件 EXE 独立验证；
  EXE 的正常启动、功能与退出结果由单独的构建和 EXE 验证记录确认。

实测命令（以本轮受控 Python/Qt/PATH 环境运行）：

```powershell
& 'E:\LanShare-builds\build-22jznups\env\Scripts\python.exe' -B tools/desktop_lifecycle_test.py
```

## 最终密码保留约束的静态证据

- 基线提交：`131c39c8e3133c458b316df39faacd9b275bce27`。对比当前第七版
  `RoutesMixin.post_login`、`RoutesMixin.api_verify_admin`、
  `FileShareGUI.save_settings`、`FileShareGUI.load_settings` 的整方法 AST，
  四项均完全相同；忽略的仅是行号、排版和注释，不忽略可执行代码或字符串。
- 这四方法中没有需要解释的非密码变动。访问登录失败与管理员校验失败的日志
  仍直接插入原始 `input_password` / `input_admin_pwd`，未增加脱敏或散列；
  QSettings 仍将两个输入框的原始 `.text()` 直接保存到 `password` 和
  `admin_password`，并从相同 key 读取回填，未增加加密或转换。
- 最终交付构建快照 `E:\LanShare-builds\build-2lqxw_37` 内的这两份源码与当前
  源码字节一致，四个整方法 AST 也均与基线相同。
- 机器可读证据：`test-results/password-policy-unchanged.json`，状态为 pass，
  包含每方法/密码相关语句的 AST 哈希、源语句、基线和 staged 对比及安全范围。
  此验证只读取 Git 和源码、输出证据；不导入或执行应用/EXE、不读取真实设置或
  聊天数据、不操作注册表或计划任务，也不修改产品或测试源码。
- 此证据限定于上述四方法，不将本轮其他路由或调用方的安全改动误称为未变化。
