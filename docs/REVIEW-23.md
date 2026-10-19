# 第二十三轮代码审查报告

> 范围：`server/`（约 2400 行）、`ui/`（约 1300 行）、`web/`（约 2400 行）、`tools/` 14 套测试
> 方法：逐文件通读 + 针对每个疑点写探针脚本实跑验证（不靠"看起来像"下结论）
> 结论：**发现 1 个数据丢失级 bug、1 个数据损坏级 bug、1 个隐私 bug，以及若干健壮性问题。**

---

## P0：数据安全（建议立刻修）

### 1. `POST /delete` 传 `file=.` 会删掉整个共享目录

**已实测复现**，返回 `200 {"success": true}`，共享根整个消失：

```
file='.'  -> 200 b'{"success": true}'  | 根存在=False 剩下=GONE
```

原因：`delete_file` 只做了「在共享目录之内」的判断，却没想到 `.` 解析出来**就是共享根自己**：

```python
target = (Path(current_path) / filename).resolve()   # filename='.' → 就是 current_path
if not is_path_within(target, self.shared_path):     # 根在根里，判定通过
    self.send_error(403); return
...
elif target.is_dir():
    shutil.rmtree(target)        # ← 把共享根删了
```

`..` 和 `/` 会被 `is_path_within` 挡掉，唯独 `.` 能穿过。

**影响**：只要有人（含用户自己误操作、或任何能发 POST 并已解锁管理员密码的会话）
发一次 `POST /delete?path=<共享目录>&file=.`，整个共享盘被清空，且提示"成功"。

**修法**：删除前显式拒绝"目标等于共享根"这一个特例，并顺手拒绝目录自身：

```python
root = Path(self.shared_path).resolve()
if target == root:
    self.send_api_error("不能删除共享根目录", 400)
    return
```

同时建议：批量删除（`files[]`）也走同一个校验。

### 2. 下载 `.gz / .tgz / .svgz` 会给文件打上 `Content-Encoding: gzip`，导致下载下来的是被浏览器解压后的坏文件

**已实测**：`backup.gz`（46 字节，魔数 `1f8b`）原样发出，但响应头带 `Content-Encoding: gzip`，
浏览器会"贴心地"解压 → 用户拿到 850 字节的明文，文件名却还是 `.gz`。

原因在 `download_file`：

```python
content_type, encoding = mimetypes.guess_type(str(file_path))   # .gz → ('...','gzip')
...
if encoding:
    self.send_header('Content-Encoding', encoding)              # 但发的字节是原始未解压内容
```

`guess_type` 返回的第二项是"**这个文件的编码方式**"，不是"**本次响应做过压缩**"。
把它当 `Content-Encoding` 用是语义错误。

**影响**：任何 `.gz/.tgz/.svgz/.bz2/.xz` 文件下载后都无法使用（压缩包打不开）。

**修法**：删掉这三行，或在 `Content-Encoding` 之外改用注释说明。真正需要按压缩响应发的时候，
应该像 `_encode_body` 那样真的去压缩。

### 3. 访问密码与管理密码以明文写进注册表

**已实测**，`HKCU\Software\FileShareTool\App` 里能直接读到：

```
password       : 5555
admin_password : 5555
```

`save_settings()` 用 `QSettings` 原样存明文。同一用户下任何进程、以及注册表导出的备份文件，
都能直接拿到。

**修法**（选一个）：
- 最省事：密码不落盘，只在本次运行内有效（关掉软件就要重输）；
- 折中：用 DPAPI（`CryptProtectData`）加密后再存，绑定当前用户；
- 至少：把 `admin_password` 从设置里去掉（它比访问密码更敏感）。

---

## P1：功能与体验缺陷

### 4. 聊天室短暂断线后，SSE 和 2 秒轮询会**同时永久运行**

`chatStreamStart()` 在 `onerror` 连续失败 3 次时启动轮询兜底：

```js
if (!chat.timer) chat.timer = setInterval(chatPoll, 2000);
```

但 `es.onopen` 只重置了 `failStreak`，**没有停掉这个 timer**。服务端每 25 秒主动断一次是正常设计，
一旦某次断线凑满 3 次失败，之后即使 SSE 恢复正常，轮询也会一直跑下去。

**影响**：每条消息被两条链路重复拉取（`chatAppend` 有 id 去重，所以不会重复上屏，
但流量和手机耗电翻倍，且每 2 秒一次请求会塞满访问日志）。

**修法**：在 `es.onopen` 里清掉 timer：

```js
es.onopen = function () {
  failStreak = 0;
  if (chat.timer) { clearInterval(chat.timer); chat.timer = null; }
};
```

### 5. 分页之后，底部计数仍然只统计"已加载"的条目

目录里有 20000 个文件时，列表底部会显示"400 项 · 12 个文件夹"——用户看不出还有 19600 项
（虽然下面有"还有 N 项"的按钮，但计数本身是错的）。

```js
countEl.textContent = fsState.query
  ? rows.length + ' 个匹配'
  : fsState.entries.length + ' 项 · ' + folders + ' 个文件夹';   // ← 应改用 fsState.total
```

**修法**：`fsState.hasMore` 为真时用 `fsState.total`，并可标注"已加载 X / 共 Y"。

同理 `syncSelectAll()` 的"全选"只选得到已加载的条目，建议文案改成"全选已加载"以免误解。

### 6. `is_path_within` 在 Windows 上对 verbatim 路径会误判

`Path.resolve()` 在某些情况下返回 `\\?\E:\...` 形式（扩展长度路径），而 `relative_to()`
拿它跟 `E:\...` 比较会抛 `ValueError` → 判定为"不在共享目录内" → **合法文件被 403**。

我在测试里构造 `.../a` 时撞到过这个现象。虽然 Windows 不允许创建字面 `...` 目录，
真实触发面很窄，但这是"安全判定依赖字符串前缀"的结构性隐患。

**修法**：比较前统一去掉 `\\?\` 前缀，或用 `os.path.normcase(os.path.realpath(...))` 比较：

```python
def _norm(p):
    s = os.path.realpath(str(p))
    if s.startswith('\\\\?\\'):
        s = s[4:]
    return os.path.normcase(s)
```

---

## P2：可维护性 / 优化方向

| # | 项 | 说明 |
|---|---|---|
| 7 | 删掉 multipart 上传老路径 | `handle_upload` 约 95 行，前端永远不会走到（总是带 `offset`）；实测正常场景能用，但边界解析很脆。留着是负担，删掉能少一个攻击面 |
| 8 | 清理死导入 | `transfer.py` 有 8 个、`fs_ops.py` 有 8 个、`chat_api.py` 有 2 个未使用的导入（`zipfile`/`tempfile`/`platform`/`datetime` 等），是历史遗留 |
| 9 | `SESSIONS_STORE` 无上限 | 目前只有 TTL 清理，没有条数上限；局域网内持续打 `/login` 可以让字典无限增长。加个软上限（比如 500）即可 |
| 10 | 上传队列缺"永不出错"的收尾 | `runUpload` 现在总能 settle（末尾有 `.catch`），但只要以后有人在中间加一个会 reject 的环节，`upRunning` 就再也减不回去、队列永久卡死。建议 `pump()` 用 `.then(..., ...)` 或 `.finally` 兜底 |
| 11 | `static/app.css` 已 57KB | 单文件 CSS 还能接受，但如果继续加，可以按页拆（和 JS 一样），首屏能再省一半 |
| 12 | 访问日志缺筛选 | 桌面端只能暂停/清空/导出，排查时想"只看 4xx"或"只看某个 IP"做不到 |

---

## 建议的修复顺序

1. **P0-1 删根**（10 分钟，加 3 行判断 + 一个回归测试）—— 这个必须有
2. **P0-2 gzip 下载**（5 分钟，删 3 行）
3. **P0-3 密码明文**（半小时，看选哪种方案）
4. **P1-4 SSE 与轮询并存**（5 分钟）
5. **P1-5 计数文案**（10 分钟）
6. 其余按需

每一项都该补一条回归测试：
- 删根 → 断言 `POST /delete?file=.` 返回 400 且根还在；
- gzip → 断言下载 `.gz` 的响应头**不含** `Content-Encoding`，且字节与源文件一致；
- 密码 → 断言设置里不出现明文（或断言用了 DPAPI）；
- SSE 并存 → 在浏览器里断言 SSE 恢复后 `chat.timer === null`；
- 计数 → 造 450 个文件，断言底部写的是 450 而不是 400。
