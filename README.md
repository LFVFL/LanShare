# 局域网云盘共享（LanShare）

一个**不联网也能用**的局域网文件共享工具：Windows 上双击运行，同一个 Wi-Fi / 交换机下的
手机、平板、别的电脑打开浏览器就能传文件、建群聊。不经过任何第三方服务器。

- 桌面端：Python 3 + PyQt5，原生窗口 + 托盘，可打包成单个 exe
- 服务端：只用标准库 `http.server`（多线程），没有 Web 框架
- 前端：手写 HTML/CSS/JS，无构建工具、无 npm 依赖

## 能做什么

| 类别 | 能力 |
|---|---|
| 文件 | 浏览 / 上传（分片 + 断点续传 + 暂停续传）/ 下载（支持 Range 续传）/ 改名 / 删除 / 新建目录 / 拖拽上传 |
| 批量 | 打包下载（可续传，也可选流式）、移动 / 复制、共享内递归搜索、行内「直接下载」按钮 |
| 聊天 | 多房间、SSE 实时推送、图片（自动缩略图）、消息搜索、删除 / 清空 |
| 传输层 | HTTP/1.1 长连接、gzip / br 压缩、带版本号的长缓存、并发闸门（503 保护） |
| 界面 | Web 端玻璃拟态（6 套配色 × 明暗 × 3 档玻璃，深色首屏不闪白）；桌面端 6 套配色 + 明暗 + 3 档字号 |
| 安全 | 访问密码（Cookie 会话）、管理密码（写操作二次校验）、同源校验、安全响应头 |

## 快速开始

### 1. 直接运行（源码）

```bat
pip install -r requirements.txt
python tools\build_assets.py     :: 生成 webassets.py（前端被打包进这个模块）
python lanshare.py               :: 或者：python lanshare.py D:\要共享的目录 -p 5995
```

打开后点「启动服务」，窗口里会显示访问地址和二维码；手机连同一个 Wi-Fi 扫码即可。

> `webassets.py` 是构建产物（不提交进仓库），所以第一次跑必须先执行 `build_assets.py`。

### 2. 打包成单个 exe

```bat
python tools\build_exe.py
```

脚本会先自检环境、把源码复制到一个纯 ASCII 路径下再调 PyInstaller（中文路径会让
Qt 插件钩子失败），最后启动一次 exe 做冒烟验证。产物在 `E:\lanbuild\dist\lanshare.exe`。

## 目录结构

```
lanshare.py          入口：解析参数、建 Qt 应用、开主窗口
server/              HTTP 服务端（不依赖 Qt）
  config.py            常量：端口、上限、目录、安全头
  netinfo.py           网卡地址发现与排序（二维码默认指向路由器那条局域网）
  paths.py             路径清理、体积格式化
  storage.py           会话、上传锁、ZIP 临时表
  chat_store.py        聊天持久化（rooms.json 原子写 + 图片落盘）
  pages.py             模板渲染 + 图标字节
  auth.py              同源检查、Cookie 会话、管理员口令
  http_util.py         响应输出、压缩、静态资源、日志
  routes.py            页面与文件接口
  listing.py           目录项分类、面包屑、同名改名
  transfer.py          下载（Range）、上传、断点续传
  archive.py           打包下载（zip）
  fs_ops.py            本机文件操作
  chat_api.py          聊天接口（含 SSE）
  handler.py           路由表 + 分发
  runner.py            FastBindHTTPServer + FileSharer
ui/                  桌面界面（PyQt5）
  style.py             明暗 / 6 套配色 / 3 档字号的样式生成
  main_window.py       主窗口：状态胶囊、设置、地址清单 + 二维码、彩色日志、托盘
  qr.py                二维码渲染（segno 可选）
  icon.py / widgets.py 图标与小控件
web/                 前端源码（CSS / 分片 JS / 页面模板）
  js/                  12 个分片，各自独立 IIFE，只通过 window.Lan 互相调用
  pages/               页面骨架与片段
  app.css tokens.css   组件样式与设计变量
tools/               构建与测试脚本
docs/                文档（变更记录 / 架构 / 重构 / 桌面端改版）
```

## 文档

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) —— 模块划分、依赖方向、路由表、前端契约
- [docs/CHANGELOG.md](docs/CHANGELOG.md) —— 每一轮改了什么、为什么、怎么验证的
- [docs/REFACTOR.md](docs/REFACTOR.md) —— 最初的拆分方案
- [docs/DESKTOP-UI.md](docs/DESKTOP-UI.md) —— 桌面端改版（P1–P5）

## 测试

全部是自带临时目录、跑完自动收拾的脚本，不需要显示器（GUI 用 offscreen）：

```bat
python tools\smoke_test.py           :: 冒烟：页面 / 上传下载 / 聊天 / 鉴权
python tools\protocol_test.py        :: 协议层：长连接 / 压缩 / 缓存头
python tools\ui_test.py              :: 前端行为（CDP 驱动真 Chrome）
python tools\admin_flow_test.py      :: 管理密码全流程：上传 / 清空 / 手机端入口
python tools\origin_test.py          :: 同源校验（含只发 Origin 的老浏览器）
python tools\desktop_test.py         :: 桌面端界面与网卡排序（offscreen）
python tools\theme_flash_test.py     :: 深色模式首屏不闪白
python tools\preflight.py            :: 打包前自检（含分片别名检查）
```

还有 `auth_matrix_test` / `sse_test` / `thumb_test` / `zip_test` / `search_test` /
`fsops_test` / `limits_test` / `mobile_shot`，覆盖鉴权矩阵、SSE、缩略图、打包下载、
搜索、批量操作、并发闸门和手机尺寸布局。

## 隐私

- 服务只监听本机，数据只在局域网内传输，**不向任何外部服务器发送内容**；
- 聊天记录与图片保存在程序同级目录 `chat_data/`，删掉即可清空；
- 仓库里不包含聊天数据、截图、构建产物，也不包含作者的个人路径与邮箱。

## 许可

MIT，见 [LICENSE](LICENSE)。
