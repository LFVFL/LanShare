# File Search Scope Cleanup

Date: 2026-10-28

## Behavior

- At the shared root, show only `current` (this directory only) and `subtree`
  (this directory and its descendants). `share` would search the same tree and
  is therefore removed from both desktop and mobile selectors.
- Within a subdirectory, retain `share` as a third option for searching the
  entire shared directory, including files outside the current subtree.
- The default remains `subtree`. An existing `share` selection becomes
  `subtree` when the displayed directory becomes the shared root. A `current`
  selection and an existing URL query keep their previous behavior.
- Updates after an API-driven root change use the same synchronization as
  initial rendering. Expanding the shared root so that the displayed directory
  becomes a subdirectory restores the third option.

## Implementation

- `web/js/72-files-page.js`: compare the server's resolved `path` and `root`
  using equality, with Windows drive/UNC case and separator normalization.
  Do not infer root membership from a string prefix or alter server security
  checks. POSIX path case and literal backslashes remain significant.
- `web/js/50-files.js`: add only one optional `syncSearchScopes` hook at the
  start of `renderFiles`, including before the active-search early return.
- `web/pages/files.html`: use more explicit Chinese scope labels on desktop
  and mobile. Remove the redundant option from the DOM rather than relying
  on browser-specific hiding behavior for native select options.
- `web/files-search.css`: reserve enough width for the longer labels and
  prevent flex shrinking. Existing preview/download styling is unchanged.
- `tools/v7_files_frontend_test.js`: derive select options from the actual
  page markup, simulate the option operations, and accept `--results`.
  The default output directory is this change record's `test-results` folder.

The backend, recursive search limits, request semantics, preview/download
behavior, and user data/settings are not changed by this scope cleanup.

## Verification

Run from the version 7 checkout:

```powershell
node --check web/js/50-files.js
node --check web/js/72-files-page.js
node --check tools/v7_files_frontend_test.js
node tools/v7_files_frontend_test.js --results docs/changes/2026-10-28-path-and-chat/test-results
```

- Node VM: 22/22 tests pass, including all 15 existing regressions.
- New coverage: two root options, three nested options, both selectors,
  unchanged recursive/default and whole-share request contracts, Windows
  drive/UNC and Unicode paths, slash/case handling, exact root equality,
  active-search rendering, root switches, root expansion, and an absent hook.
- The report records the actual JavaScript, page, and stylesheet SHA-256
  hashes in `test-results/files-frontend.json`; a text log is alongside it.
- These are synthetic isolated VM tests, not browser layout or Explorer
  double-click validation. Browser checks are recorded separately by the
  chat/layout verification task. The overall release process rebuilds the
  web assets and standalone EXE after source changes are finalized.

No previous change-record results are overwritten. No running user service,
registry entry, real shared directory, or account configuration is touched.
