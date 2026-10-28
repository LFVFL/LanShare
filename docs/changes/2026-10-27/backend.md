# V7 Backend Hardening

## Changes

- Retire `GET /open` with HTTP 410; the HTTP service never launches a file on the host.
- Validate lexical paths before resolving them, including Windows junction/reparse attributes.
- Reject linked descendants during copy/move/ZIP and recheck paths during copying/ZIP output.
- Bind ZIP tasks to a shared-root generation; changing roots revokes tasks and cached files.
- Keep legacy multipart upload, with bounded streaming parsing, cross-chunk delimiters,
  exclusive filename creation, complete-body validation, and removal of failed uploads.
- Add security headers to every response on HTTP keep-alive connections.
- Escape HTML delimiters in every embedded JSON data block using `pages.safe_json`.
- Embed only the first 400 file entries and include the pagination/sort/shared-root contract.
- Register chat rename and binary-media routes; their implementations live in the chat modules.
- Use one stable browser-preview MIME allowlist for both server responses and file metadata;
  Windows registry MIME registrations no longer turn supported previews into downloads.
- Include CSS, core/files/chat/login JavaScript, and theme boot content in the immutable
  asset URL version; tag and length-prefix every bundle, including empty bundles.

## Preview Contract

`paths.can_preview(path)` returns whether a file has a supported browser preview;
`paths.preview_mime_type(path)` returns its stable MIME string or `None`.

`GET /download?file=...&preview=1` returns `inline` only for JPEG/PNG/GIF/WebP/BMP/ICO,
PDF, plain-text TXT/LOG/MD/CSV, MP4/WebM/Ogg/MP3/WAV/M4A. Plain text includes
`charset=utf-8`. Supported media previews retain the existing byte-range behavior.

SVG/HTML/JS, Office documents, archives, HEIC, MKV, and unknown formats are not exposed
as browser previews; their direct preview requests remain attachments. Normal downloads
remain attachments even when the file is previewable. Browser codec availability can still
vary within the supported media containers.

## Regression Tests

`python tools/v7_security_test.py` isolates chat/cache/share data with `LANSHARE_DATA_DIR`,
uses a loopback server on a dynamic port, mocks native launching, and creates only disposable
junction fixtures. Run `python tools/build_assets.py` first.

The tests cover native-open retirement, repeated response headers, HTML/JSON round trips,
first-page pagination, multipart split/false delimiters/truncation/zero-byte/duplicate files,
ZIP generation revocation, nested external/internal junction rejection, and path replacement
after ZIP registration. Negative/concurrent cases also cover linked destinations, partial-copy
rollback, declared-body truncation, multiple multipart files, root changes during task registration,
cache construction and active output, and concurrent requests for the same ZIP task.

Preview regressions force `mimetypes.guess_type` to return `application/octet-stream` while
PNG/PDF/TXT/MP4/MP3 still return correct MIME plus `inline`. Active/unsupported types stay
attachments, and normal downloads and preview range requests retain their separate behavior.

### File Listing, Search, and UI State

`python tools/v7_files_test.py` runs an owned loopback server against 1,108 synthetic root
entries. All six name/size/mtime ascending/descending orders are compared with an independent
global sort after concatenating pages at offsets 0/400/800. The tests also cover a 153-result
current-directory filter whose matches lie outside the initial unfiltered page, first-page URL
query preservation, preview metadata, Chinese nested paths, all three search scopes, excluded
private/dependency directories, outside-root rejection/fallback, explicit result/scan/time
truncation, and a Windows junction that must neither be listed nor traversed.

`node tools/v7_files_frontend_test.js` executes the real `40-api.js`, `50-files.js`, and
`72-files-page.js` modules in an isolated VM with a minimal fake DOM and controllable fetch.
It verifies URL query initialization; stale recursive and current-directory responses, including
the input-before-debounce window; HTTP 422/503 error messages; a root switch during append
that must refetch offset zero; mobile scope/Enter behavior; and accurate batch-delete counts
and retained selections for success, 403, 500, network interruption, `success:false`, initial
confirmation cancellation, and real `Lan.api.write` 401 administrator cancellation. The report
records SHA-256 hashes of the actual modules. This is behavioral regression coverage, not
browser rendering or real-network fault injection.

### Asset Cache Version

`python tools/v7_assets_test.py` verifies that changing only files/chat/login JavaScript changes
the asset version, existing CSS/core/theme changes still invalidate it, empty bundles and
concatenation boundaries are distinct, unchanged content is stable, and missing assets use
the documented zero version. Rebuild `webassets.py` before shipping changed source files.

### Recorded Validation

Validation on 2026-10-28 (Asia/Shanghai), using the actual production build virtualenv
(Python 3.12.1) for all Python suites and Node v24.14.1 for the VM suite. The Python
suites were rerun with the E-drive build environment, not the earlier unsuccessful Windows-directory
build environment; their JSON reports record the actual interpreter in `runtimePath`:

| Suite | Passed | Evidence in `test-results/` |
| --- | ---: | --- |
| Backend security | 29/29 | `security-build-venv.log`, `security-build-venv.json` |
| Files HTTP | 9/9 | `files-http.log`, `files-http.json` |
| Asset cache version | 5/5 | `assets.log`, `assets.json` |
| Files JavaScript VM | 15/15 | `files-frontend.log`, `files-frontend.json` |
| Build safety and test isolation | 23/23 | `final-v7_build_test.log`, `final-root-regressions.json` |

All 81 regressions pass, with no skips. The integration owner's final build suite adds
parent-environment isolation and selected-venv delayed-CRT collection tests; the older
`backend-build-tools.json` records only the historical 21-test run, not the final result.
Eleven Python sources (nine backend modules and two
new tests) pass AST syntax checks; the Node test passes `node --check`; backend changes pass
`git diff --check`. Test data, chat/cache directories, and junction targets are disposable and
owned; no live service, settings, registry, or browser profile is used. V6 Git status remains
unchanged. Browser-level PNG/PDF/TXT rendering and UI button behavior are separately verified
by the integration owner; PDF verification includes the viewer's loaded state and page count
inside Chrome's PDF extension frame rather than assuming a populated top-level document body.

### Packaged Dependency Inspection

`test-results/dll-closure-build-E.json` records an independent inspection of the final build
`build-2lqxw_37`, including its executable SHA-256 and byte size. The former
`packaging-dependencies.json` is also updated to this final artifact. All 119 packaged PE
entries (116 unique basenames) were extracted in memory. Their 729 ordinary and two delay
DLL imports form a complete closure against packaged DLLs and Windows system/API-set
resolution; 35,827 ordinary and 12 delay-import symbols targeting packaged DLLs also match
their exports. There are no missing DLLs, missing packaged-target symbols, unknown non-API
dependencies, actionable parse warnings, or parse errors. No application DLLs or executable
were launched by this inspection.

The E-drive build includes the four main Qt DLLs, platform plugins, `MSVCP140_1.dll`,
`concrt140.dll`, all ten required Qt library DLLs, and all six hashed HEIF wheel DLLs. Native sources are restricted to
the build virtualenv, base Python, and Windows system directory. The former 43 Codex-native
PATH dependencies are absent, and UCRT now comes from Windows `System32`.

The earlier `build-22jznups` omitted `concrt140.dll` from `MSVCP140.dll`'s delay-import table;
that historical gap is fixed. The build tool explicitly collects the DLL from the selected
virtualenv's validated Qt binary directory and requires it in the final archive. Python/Qt
environment cleanup now covers environment creation, validation, dependency installation,
module/image probes, source-resource generation, and packaging, with user-site imports
disabled and a Windows dependency-scanning PATH allowlist.

Qt import/export tables exceed pefile's default 8,192-symbol protection threshold. The
inspection uses bounded 100,000-symbol limits and recognizes harmless zero-entry-point
forwarder DLL notes rather than confusing either case with binary corruption. The final
dependency report is successful. This remains a static DLL/symbol check: it does not test
dynamic `LoadLibrary` paths, loader search order, or compatibility on other Windows versions.

## Scope

No password-log behavior, regular-download ETag policy, ordinary-upload `.part` redesign,
rate limits, registry integration, commits, pushes, or V6 files are changed by this work.
