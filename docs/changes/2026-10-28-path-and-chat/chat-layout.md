# 群聊列表对齐与输入区精简

日期：2026-10-28。仅记录本轮聊天页面修改与隔离浏览器验证；EXE 启动路径问题由本轮
其他记录说明。本阶段未修改设置、真实聊天数据或右键注册表，也未替换 EXE。

## 修改

### 群聊表头对齐

实际表头为“消息”“创建时间”。文件页增加预览按钮后，共用的 `.col-more` 被
`files-search.css` 扩为 112px，但群聊每行仍只有一个 32px 的“更多”按钮。于是消息和
创建时间表头比对应内容向左错了 80px。

在 `web/pages/chat_list.html` 的群聊列表容器加入局部 `room-list` 类，并在 `web/app.css`
用 `.room-list .col-more` 把此页操作表头设为 32px。该规则不改变文件页三个操作的位置。
手机继续使用无表头的卡片，消息数和创建时间仍显示在群名下方。

### 删除输入框下方常驻说明

从 `web/pages/chat_room.html` 删除整个键盘/纯文本/原图说明、连接状态文字和图片格式说明块，
同时移除无用 CSS 与对应 JS 的 DOM 写入。保留输入框、图片按钮、发送按钮、必要的上传进度/
失败提示、图片预览和手机改群名按钮。

`web/js/85-chat-room.js` 中的 SSE、断线轮询兜底、重连后的轮询停止逻辑未删。
图片能力接口仍调用；禁用图片按钮不再依赖已删除的说明元素。当服务端不能上传图片时，
按钮禁用并提供悬停说明。发送失败保留草稿、图片失败可重试等原有错误处理均保留。

## 验证

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| 修改前复现 | 1280/1024/901px 下表头均错位 80px；提示块仍可见，针对性断言按预期失败 | `test-results/chat-layout-before.json` |
| 聊天 Node VM 回归 | 9/9，原 6 个方法加 3 个无提示块/图片能力/SSE 兜底回归 | `test-results/chat-frontend.json` |
| 新增真实 Chrome 回归 | 3/3 | `test-results/chat-layout.json` |
| 搜索范围补充浏览器验证 | 2/2，详见搜索修改记录 | `test-results/file-scope-browser.json` |

聊天 Chrome 检查覆盖：

- 1280/1024/901px 下每行的消息、创建时间及更多按钮与表头右边缘一致；390/320px 卡片
  保留消息数和创建时间，无横向溢出。
- 1280/390/320px 输入框下不再包含被删除的说明节点或文字。
- 没有连接状态元素时仍能建立 SSE 并接收另一个 HTTP 客户端的消息。
- 使用 Chrome 实际键盘事件验证 Shift+Enter 换行、Enter 发送，正文空格、Tab 和换行不丢失。
- 实际选择合成 PNG、上传、发送，图片预览和发送流程正常。

8 张最终聊天截图均逐张检查：`chat-layout-list-{1280,1024,901,390,320}.png`、
`chat-layout-room-{1280,390,320}.png`。修改前的 `chat-layout-before-*` 仅作本机诊断，
不要当成最终效果或随公开证据整目录提交。

顺便对搜索修改进行了额外浏览器验证：共享根目录两个范围、子目录三个范围；两套桌面/
手机选择器同步，在 1280/390/320px 选中标签完整、无横溢。复用既有真实浏览器用例确认
当前目录/当前目录及子目录/整个共享目录得到 1/2/3 个结果，慢响应不覆盖新搜索。
对应 6 张 `file-scope-{root,nested}-{1280,390,320}.png` 也逐张检查。两个方法包含上述
循环，不把视口或结果数重复计为独立用例。

## 复测方式与隔离

```powershell
$python = 'E:\LanShare-builds\build-22jznups\env\Scripts\python.exe'
& 'D:\Program Files\nodejs\node.exe' tools/v7_chat_frontend_test.js
& $python -B tools/v7_chat_layout_test.py --results 'docs/changes/2026-10-28-path-and-chat/test-results/chat-layout.json'
```

`tools/v7_chat_layout_test.py` 使用既有 Chrome 测试基础设施，但只运行本轮三个方法。
它在自有临时目录生成最新 `webassets.py` 并用于测试进程，不改工作区生成文件；HTTP
服务器用独占动态端口，Chrome 使用新的独立 profile，全部房间与图片都是合成 fixture。
修改前复现、最终聊天回归、搜索补充检查的测试进程退出后，分别确认自有测试根已删除。
未控制用户浏览器或正在运行的网盘。静态检查包括相关 JS 语法、Python 3.8 AST 语法及
`git diff --check`；不代表已在 Python 3.8 或所有移动浏览器实际运行。

原始 `.log` 继续由 Git 忽略；发布白名单由本轮总记录汇总，不能直接 `git add` 整个证据目录。
