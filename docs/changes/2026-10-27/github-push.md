# 第七版 GitHub 推送准备

> 历史操作草案：本文旧哈希、测试数量和待手动推送状态仅适用于当轮，不能用作当前发布指令。
> 当前 v7.0 产物、精确文件白名单及实际推送状态以
> [最新发布记录](../2026-10-28-release-v7/README.md)为准；旧 JSON 不改写。

本轮仅准备修改和记录，**没有运行 add、commit 或 push**。第六版保持不动；第七版保留原 Git 历史，基线 `131c39c8e3133c458b316df39faacd9b275bce27`，分支 `fix/v7-review`，远端 [LFVFL/LanShare](https://github.com/LFVFL/LanShare)。

以下命令由你审核后手动执行。若这期间又修改了文件，请先辨别并拆分自己的改动，不要盲目提交整目录。

## 1. 审核改动与交付文件

```powershell
$repo = 'E:\code\局域网\第七版'
$safeRepo = $repo.Replace('\', '/')
git -c "safe.directory=$safeRepo" -C $repo status --short
git -c "safe.directory=$safeRepo" -C $repo diff --stat
git -c "safe.directory=$safeRepo" -C $repo diff --check
git -c "safe.directory=$safeRepo" -C $repo diff -- README.md lanshare.py requirements.txt server ui web tools
$expectedSha256 = '023f3143d43733eeb7528be44703ee93dc5d2e185ef454dcc3ce8ed6ae0f9a24'
$expectedBytes = 63929817
$delivery = Get-Item -LiteralPath "$repo\lanshare.exe"
$deliveryHash = (Get-FileHash -LiteralPath $delivery.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
if ($delivery.Length -ne $expectedBytes -or $deliveryHash -ne $expectedSha256) {
  throw '原交付路径与本轮验收产物不符，停止发布并核对最新验收记录。'
}
```

本轮已交付 EXE SHA-256 为 `023f3143d43733eeb7528be44703ee93dc5d2e185ef454dcc3ce8ed6ae0f9a24`，63,929,817 字节，
构建位置为 `E:\LanShare-builds\build-7ihfiggx\dist\lanshare.exe`。
**已在第七版原交付路径通过受控启动、Medium 进程完整性及正常退出清理验收。**
字节哈希相同不代表文件元数据、启动进程完整性相同，其他目录或等字节副本启动成功不能替代原路径验收。
最新进展见 [路径启动与界面修正记录](../2026-10-28-path-and-chat/README.md)；此前临时解包修复见
[启动修复记录](../2026-10-28-startup-fix/README.md)，首轮范围见 [verification.md](verification.md)。旧哈希和 JSON 保留为各轮历史证据。
Git diff 不会显示未跟踪文件内容，新增文件也需要单独打开审核。

`safe.directory` 仅用于当前命令，没有自动更改全局 Git 配置。

## 2. 暂存已经审核的源码和测试

先逐项审核，再只暂存列出的文件；不使用目录级 `git add`。以下白名单不包含真实聊天、EXE、环境及构建目录：

```powershell
$trackedSources = @(
  'README.md'
  'lanshare.py'
  'requirements.txt'
  'server/archive.py'
  'server/chat_api.py'
  'server/chat_store.py'
  'server/config.py'
  'server/fs_ops.py'
  'server/handler.py'
  'server/http_util.py'
  'server/listing.py'
  'server/pages.py'
  'server/paths.py'
  'server/routes.py'
  'server/runner.py'
  'server/storage.py'
  'server/transfer.py'
  'tools/admin_flow_test.py'
  'tools/auth_matrix_test.py'
  'tools/build_assets.py'
  'tools/build_exe.py'
  'tools/desktop_test.py'
  'tools/fsops_test.py'
  'tools/limits_test.py'
  'tools/mobile_shot.py'
  'tools/origin_test.py'
  'tools/paging_test.py'
  'tools/preflight.py'
  'tools/protocol_test.py'
  'tools/screenshots.py'
  'tools/search_test.py'
  'tools/share_switch_test.py'
  'tools/shot.py'
  'tools/smoke_test.py'
  'tools/sse_test.py'
  'tools/theme_flash_test.py'
  'tools/thumb_test.py'
  'tools/ui_detail_test.py'
  'tools/ui_test.py'
  'tools/zip_test.py'
  'ui/main_window.py'
  'web/app.css'
  'web/js/50-files.js'
  'web/js/72-files-page.js'
  'web/js/80-chat-list.js'
  'web/js/85-chat-room.js'
  'web/pages/chat_list.html'
  'web/pages/chat_room.html'
  'web/pages/files.html'
)
git -c "safe.directory=$safeRepo" -C $repo add -u -- $trackedSources

$newSources = @(
  'server/chat_media.py'
  'ui/context_menu.py'
  'ui/single_instance.py'
  'web/files-search.css'
  'tools/desktop_lifecycle_test.py'
  'tools/test_support.py'
  'tools/windows_job.py'
  'tools/v7_assets_test.py'
  'tools/v7_browser_test.py'
  'tools/v7_build_test.py'
  'tools/v7_chat_frontend_test.js'
  'tools/v7_chat_layout_test.py'
  'tools/v7_chat_test.py'
  'tools/v7_exe_test.py'
  'tools/v7_files_frontend_test.js'
  'tools/v7_files_test.py'
  'tools/v7_security_test.py'
  'tools/v7_startup_temp_test.py'
)
git -c "safe.directory=$safeRepo" -C $repo add -- $newSources
```

## 3. 只暂存公开记录白名单

不要执行 `git add .`、`git add docs/changes`、按目录暂存源码或 `git add -f *.log`。
早期诊断、原始日志和过时的 `v7-preview-initial-oopif.json` 继续留本机；下面只加入明确审核过的记录和最终合成截图。

```powershell
$record = 'docs/changes/2026-10-27'
$documents = @(
  'README.md', 'backend.md', 'chat.md', 'desktop.md',
  'files-build-tests.md', 'verification.md', 'github-push.md'
) | ForEach-Object { "$record/$_" }

$jsonNames = @(
  'assets.json'
  'build-environment.json'
  'build-source-manifest.json'
  'dll-closure-build-E.json'
  'exe-verification.json'
  'files-frontend.json'
  'files-http.json'
  'final-root-regressions.json'
  'final-suite-results.json'
  'packaging-dependencies.json'
  'password-policy-unchanged.json'
  'release-artifact.json'
  'security-build-venv.json'
  'static-checks.json'
  'v7-browser-artifacts.json'
  'v7-browser-large-image-evidence.json'
  'v7-pdf-browser-evidence.json'
)
$json = $jsonNames | ForEach-Object { "$record/test-results/$_" }

$imageNames = @(
  'v7-files-320.png', 'v7-files-390.png', 'v7-files-1280.png'
  'v7-chat-320.png', 'v7-chat-390.png', 'v7-chat-1280.png'
  'v7-preview-pdf.png', 'v7-chat-large-image.png', 'v7-chat-bmp-lightbox.png'
)
$images = $imageNames | ForEach-Object { "$record/test-results/$_" }
git -c "safe.directory=$safeRepo" -C $repo add -- $documents $json $images

$startupRecord = 'docs/changes/2026-10-28-startup-fix'
$startupJsonNames = @(
  'diagnosis.json', 'build.json', 'build-safety.json', 'build-source-manifest.json'
  'exe-verification.json', 'startup-temp.json', 'native-dependencies.json'
  'static-checks.json', 'release-artifact.json', 'verification.json'
)
$startupJson = $startupJsonNames | ForEach-Object { "$startupRecord/test-results/$_" }
git -c "safe.directory=$safeRepo" -C $repo add -- "$startupRecord/README.md" $startupJson

$pathRecord = 'docs/changes/2026-10-28-path-and-chat'
$pathDocumentNames = @('README.md', 'chat-layout.md', 'files-scope.md')
$pathDocuments = $pathDocumentNames | ForEach-Object { "$pathRecord/$_" }
$pathJsonNames = @(
  'build.json'
  'build-source-manifest.json'
  'native-dependencies.json'
  'previous-artifact.json'
  'chat-frontend.json'
  'chat-layout.json'
  'files-frontend.json'
  'file-scope-browser.json'
  'root-regressions.json'
  'root-v7_chat_test.json'
  'root-v7_files_test.json'
  'root-v7_assets_test.json'
  'root-v7_build_test.json'
  'exe-verification.json'
  'startup-temp.json'
  'startup-diagnosis.json'
  'integrity-replace-probe.json'
  'release-artifact.json'
  'startup-temp-delivered.json'
  'startup-token-delivered.json'
  'static-checks.json'
  'source-manifest.json'
  'verification.json'
)
$pathJson = $pathJsonNames | ForEach-Object { "$pathRecord/test-results/$_" }
$pathImageNames = @(
  'chat-layout-list-1280.png'
  'chat-layout-list-1024.png'
  'chat-layout-list-901.png'
  'chat-layout-list-390.png'
  'chat-layout-list-320.png'
  'chat-layout-room-1280.png'
  'chat-layout-room-390.png'
  'chat-layout-room-320.png'
  'file-scope-root-1280.png'
  'file-scope-root-390.png'
  'file-scope-root-320.png'
  'file-scope-nested-1280.png'
  'file-scope-nested-390.png'
  'file-scope-nested-320.png'
)
$pathImages = $pathImageNames | ForEach-Object { "$pathRecord/test-results/$_" }
git -c "safe.directory=$safeRepo" -C $repo add -- $pathDocuments $pathJson $pathImages
git -c "safe.directory=$safeRepo" -C $repo diff --cached --stat
git -c "safe.directory=$safeRepo" -C $repo diff --cached --check
git -c "safe.directory=$safeRepo" -C $repo status --short
```

JSON 中记录实际本机构建路径和临时 fixture 路径，但没有真实分享、聊天或浏览器 profile 内容。发布前如果不希望公开这些路径，可另行编辑文档副本；不要把修改过的路径当作原始运行证据。首轮九张与本轮十四张图片均为隔离合成数据，已逐图检查。`.log` 可能包含本机网卡地址，即使密码日志明确保留，也不意味着应公开原始诊断日志。

启动修复的 `test-results/initial-candidate/` 是未交付中间包的本机调查记录，不在白名单内。
`diagnosis.json` 只选取探针结果字段，不包含原始 bootloader 日志中的账户 SID。
本轮 `test-results/diagnosis/` 的原始账户 SID/令牌调查、`integrity-move-probe.json`、
`chat-layout-before*`、原始 `.log` 和临时 runner 不在白名单中。
原路径发布元数据、原因摘要、同卷替换探针和专门验收 JSON 已按准确文件名加入白名单；
不能为省略审核而暂存整个目录。候选的 `startup-temp.json` 和原路径的
`startup-temp-delivered.json` 均为同一套 6 个方法、8 次启动，不重复计为新的方法。
交付包的 8 次启动包含原路径 1 次及隔离副本 7 次；另有原路径令牌验收 1 次。
本轮方法去重后共 107 项，详见新的 `verification.json`；原路径验收使用隔离数据，
并非在用户真实聊天与设置中执行资源管理器双击验收。

## 4. 审核后提交并推送分支

```powershell
git -c "safe.directory=$safeRepo" -C $repo commit -m "fix: harden LanShare and improve files chat and desktop flows"
git -c "safe.directory=$safeRepo" -C $repo push -u origin fix/v7-review
```

若远端分支已存在且 push 被拒绝，先检查远端变化，再决定如何合并；不要直接 force-push。可在 GitHub 创建该分支到 main 的 Pull Request，说明已修复的审查项、按要求保留的密码行为、本轮分项回归及原路径验收状态，以及未覆盖范围。首轮 138 项和上一轮 39 项启动相关复测保留为历史；各轮有重叠，不相加宣称为不同测试。

## EXE 发布

`lanshare.exe`、`webassets.py`、`chat_data/`、venv、build/dist 和 `.log` 已被忽略，不 force-add。
原交付路径启动、实际进程完整性及隔离退出回收验收已通过；可在你审核后手动上传
`E:\code\局域网\第七版\lanshare.exe` 到 GitHub Release，发布前再次核对本轮产物的
63,929,817 字节及 `023f3143d43733eeb7528be44703ee93dc5d2e185ef454dcc3ce8ed6ae0f9a24`，并附最新验收说明。
重新复制覆盖 EXE 后须复查文件完整性标记和原路径启动，不能仅凭字节哈希判断；不要把
61 MiB 构建产物直接塞进源码提交，也不要为启动临时解包而降低整个目录的权限或要求管理员运行。

本轮没有自动创建 Release、PR 或 CI，也没有替你决定版本标签。
