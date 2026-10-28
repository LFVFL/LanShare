# 第七版路径启动排查与界面修正

> 当前 v7.0 的版本号、移动端改群名入口、重新构建与原路径验收已另记于
> [最新发布记录](../2026-10-28-release-v7/README.md)。本文旧产物哈希与测试记录保留不变。

本轮接续用户反馈：同一 `lanshare.exe` 在第七版目录仍提示
`Could not create temporary directory!`，换目录似乎可以运行；同时处理搜索范围冗余、
群聊表头错位和聊天室输入区说明过多的问题。此前的临时解包修复及测试作为历史记录保留，
不把它们描述为已经覆盖这个精确路径问题。

## 界面修改

### 搜索范围

- “仅当前目录”只查当前目录中的项目。
- “当前目录及子目录”从当前浏览位置向下查找。
- “整个共享目录”从服务器共享根目录查找，包含当前目录之外的其他分支。

后两项只在浏览共享根目录时等价。因此根目录只显示前两项，进入子目录后再显示第三项。
桌面和手机同步；服务端切换或扩大共享根时也会更新选项。默认仍然递归搜索，没有改变
服务端的搜索边界、时限或截断提示。实现和验证见 `files-scope.md`。

### 群聊列表

实际表头是“消息 / 创建时间”。文件页为三个操作按钮把操作列扩为 112px，群聊每行
只有 32px 的更多按钮，旧表头因此错开 80px。本次在群聊列表加局部样式，让表头与行
使用相同操作列宽，不缩窄文件页的预览、下载按钮空间。

### 聊天输入区

移除输入框下整块常驻文字：快捷键/纯文本/原图说明、实时连接状态文字、可用格式和
HEIC/AVIF 补充说明。发送、Shift+Enter 换行、SSE、断线轮询、能力检测、图片上传进度
与错误提示仍然保留。实现和真实浏览器验证见 `chat-layout.md`。

## 原路径启动问题

修复前原交付文件为 `e283857ab18dc7533b735ede1ae017cb63a8691791d58f5c1dc70e1aab03ef41`，
不是仅凭文件名推测版本。目录不是 Junction，EXE 没有 Zone.Identifier；只读检查实际
Windows 用户的 AppCompat Layers，没有该精确路径的兼容性标记。

此次对照区分了 EXE 路径、工作目录、执行账户及临时目录归属。跨账户复用 fixture 的
早期结果不能用于推断原因；在同一个获批上下文中新建自有 fixture 后，原路径仍复现同款
提示，而等字节副本可以启动。受控 API 观察确认变量展开正确，失败发生在创建隔离测试
解包目录时：`CreateDirectoryW` 返回拒绝访问，而不是中文路径转码失败。

### 已确认原因

第七版目录中的旧 EXE 继承了 Windows **Low（低完整性）**标记。即使调用者为普通
Medium 用户，启动的旧 EXE 进程仍为 Low（RID 4096）；同一账户、同字节的普通目录副本
进程为 Medium（RID 8192）。Low 进程不能向 Medium 临时目录写入，因而引导程序在
Python 启动前失败。把解包目录改为 `LOCALAPPDATA` 不能单独解决这项文件标记问题，
修改 Python 启动逻辑也来不及；不能把“中文目录”当成已证实原因。

### 实际处理

先在新建的自有小文件上验证：普通构建目录的 Medium 文件，使用同卷 `os.replace`
替换第七版内确实继承 Low 的自有目标后，保留源文件的安全描述符、不继承旧目标 Low。
前两个没有满足 Low 前置条件的探针没有执行替换，作为诊断历史保留。

随后校验旧 EXE、旧包备份和新包哈希，把新构建包复制到 E 盘普通构建目录的新专属位置，
再用相同的同卷替换方式交付到 `E:\code\局域网\第七版\lanshare.exe`。只更换这个 EXE；
前后核对第七版和上级工作区的安全描述符完全不变。没有执行目录级改权限、管理员启动、
注册表/环境变量/安全软件修改，没有迁移真实聊天或设置，也没有结束用户已有的服务。

### 原路径验收

- 替换后在新的工具调用中复查，文件仍无 Low 标记，字节哈希保持一致。
- 普通 Medium 用户直接从**第七版原路径、以第七版为工作目录**启动；就绪前及就绪后，
  引导进程与应用子进程均为 Medium，没有借助管理员提权或运行副本冒充原路径。
- 交付包启动矩阵 6/6 通过，共 8 次启动：原路径 1 次、隔离副本 7 次；另外单独进行
  原路径进程令牌验收 1 次。所有进程正常退出码均为 0，监听关闭，`_MEI*` 在测试
  fixture 删除前已自行清理，没有把副本启动次数算成原路径启动次数。
- 这些是同账户原路径的受控启动，使用独立 INI、聊天目录、`LOCALAPPDATA` 和端口；
  不是在用户正常聊天数据上验收，也不冒充实际资源管理器双击或可见桌面 UI 的人工验收。

原因摘要见 `test-results/startup-diagnosis.json`；发布元数据见 `release-artifact.json`；
原路径证据见 `startup-temp-delivered.json` 和 `startup-token-delivered.json`。
Windows 文件哈希不包含完整性标记，后续若再次复制覆盖交付文件，应同时核对文件标记和
原路径启动，不能仅核对 SHA-256 或只测试另一个目录的副本。

## 本轮历史交付（已被 v7.0 替换）

| 项目 | 结果 |
| --- | --- |
| EXE | `E:\code\局域网\第七版\lanshare.exe` |
| 构建源 | `E:\LanShare-builds\build-7ihfiggx\dist\lanshare.exe` |
| 大小 | 63,929,817 字节 |
| SHA-256 | `023f3143d43733eeb7528be44703ee93dc5d2e185ef454dcc3ce8ed6ae0f9a24` |
| 资源版本 | `98bfe0bd8e` |
| 状态 | 已替换，原路径启动、Medium 进程及退出清理验收通过 |

标准构建复用原独立 venv，没有安装新依赖。构建前检查、Qt/运行时参数核验、隔离 EXE
冒烟均通过。119 个原生依赖在完整归档路径下与前一包逐字节一致。29 个运行源码、资源
和图标与 staging 一致，其中仅生成的 `webassets.py` 随本轮界面改变；Python 产品逻辑
没有变化。详情见 `test-results/build.json`、`native-dependencies.json` 和
`build-source-manifest.json`。

原交付文件已校验并备份到本地诊断目录，信息见 `test-results/previous-artifact.json`。

## 界面及功能验证

| 验证 | 结果 | 记录 |
| --- | --- | --- |
| 搜索范围 Node VM | 22/22 | `test-results/files-frontend.json` |
| 聊天 Node VM | 9/9 | `test-results/chat-frontend.json` |
| 聊天真实 Chrome | 3/3 | `test-results/chat-layout.json` |
| 搜索范围真实 Chrome | 2/2 | `test-results/file-scope-browser.json` |
| 聊天 HTTP | 16/16 | `test-results/root-v7_chat_test.json` |
| 文件 HTTP | 9/9 | `test-results/root-v7_files_test.json` |
| 嵌入资源版本 | 5/5 | `test-results/root-v7_assets_test.json` |
| 构建安全与隔离 | 27/27 | `test-results/root-v7_build_test.json` |
| 独立 EXE 功能 | 8/8 | `test-results/exe-verification.json` |
| 交付包启动矩阵 | 6/6（原路径 1 次、副本 7 次） | `test-results/startup-temp-delivered.json` |

本轮以上合计 **107 个测试方法全部通过**，无失败或跳过。构建候选的 `startup-temp.json`
也是同一套 6 方法复测，不重复计数；视口循环、17 项移动探针检查和 12 项令牌检查也不
另算独立测试方法。总核验见 `test-results/verification.json`；63 个 Python 文件和 16 个
JavaScript 文件的静态检查见 `static-checks.json`，89 个源码/资源/测试文件哈希见
`source-manifest.json`。Python 3.8 检查仅为 AST 语法检查，不等同于该版本的运行验证。

真实 Chrome 覆盖 1280/1024/901px 桌面群聊表头，390/320px 手机布局，输入区文字移除，
真实键盘换行/发送、SSE 和图片发送；搜索覆盖根目录两选项、子目录三选项及对应 1/2/3
结果和过期响应处理。14 张最终合成截图已逐图检查，没有把 viewport 循环再计成测试方法。

57 项 Python 功能回归由四个独立进程执行，绕过原测试脚本的历史输出路径；旧 JSON/log
前后哈希不变。相关回归明细见 `test-results/root-regressions.json`。测试全部使用自有
聊天目录、INI、浏览器 profile 和动态端口；没有在真实聊天数据上验收。

尚未专项覆盖真实手机软键盘/IME、Safari/WebView、其他 Windows 机器或所有主题组合。

## 保留范围

- 第六版不修改；正常聊天、设置和用户要求保留的密码日志/明文 QSettings 不迁移、不脱敏。
- 没有自动执行 Git add、commit、push 或创建 Release。
- `chat-layout-before*` 和启动调查的中间结果只作本机排查，不作为最终截图或交付证据。
- 原始 `.log` 继续被 Git 忽略；仅审核后的 JSON、文档和合成截图适合列入推送白名单。
- 原始 `test-results/diagnosis/` 包含本机账户与权限信息，仅留本机；公开采用无账户 SID
  的原因、同卷替换探针和最终令牌摘要。推送准备见上一轮的 `github-push.md`（已补本轮白名单）。
