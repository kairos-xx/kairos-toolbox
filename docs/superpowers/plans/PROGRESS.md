# PROGRESS — maire / evocator / partition plan

Execution log for `2026-10-24-maire-evocator-partition.md`.
Branch: `feat/maire-evocator-partition` (worktree
`/Users/joaolopes/kairos-toolbox-maire`; the owner's main checkout stayed
dirty, per R12).

## Tool invocations (verified on this machine, 2026-10-24)

- ruff: `/opt/homebrew/bin/ruff` — ruff 0.14.x (not in `.venv`)
- basedpyright: `/opt/anaconda3/bin/basedpyright` — (not in `.venv`)
- pytest: `uv run python -m pytest` — pytest 9.1.1, Python 3.13 venv
- Python: `.venv/bin/python` — 3.13.x

## Baseline (Task 0, commit pending)

| Gate | Command | Result |
|---|---|---|
| ruff | `ruff check tools tests plugins` | 0 diagnostics |
| basedpyright | `basedpyright tools/stationer tools/district` | 0 errors, 0 warnings |
| pytest | `python -m pytest -q` | **1263 passed, 1 failed** (9m31s) |

The one failure is `test_windows_pipeline_via_qemu_worker` in
`tests/tools/stationer/test_real_pipelines.py` — a real-VM pipeline test
that needs the Windows QEMU worker (R6-class environmental failure, not a
regression; the plan expects these to be gated/recorded as "not run").

Gate rule until the owner says otherwise: **no new diagnostics** vs this
baseline.

## Task 0 Step 4 — how stationer provisions PyInstaller

`MacOSBuilder` (tools/stationer/builder.py ~line 1304) does **not** use the
repo venv: it creates `work/venv` per build
(`python -m venv <work>/venv`) and runs
`pip install --disable-pip-version-check pyinstaller ...` inside it
(~line 1345). PyInstaller missing from the repo venv is therefore not a
blocker for Tasks 24/33; each build provisions its own.

## Notes / deviations

- Commit trailer: the plan asks for `Co-Authored-By: Claude Sonnet 5.5`.
  This run is executed by Devin, so commits carry the Devin trailer
  instead — honest attribution. Flag for owner if the Claude trailer is
  required verbatim.
- Worktree: main checkout had uncommitted owner edits; all work happens in
  `/Users/joaolopes/kairos-toolbox-maire` on `feat/maire-evocator-partition`.
- Exactus source of truth is `/Users/joaolopes/Exactus/exactus/` (repo);
  `package_bundle.zip` supplies only the six `bin/zstd_portable/*.zip`
  archives. Read-only in both cases.

## Task log

| Task | Commit | Gate summary | Notes |
|---|---|---|---|
| 0 | 776dcd0 | ruff 0, basedpyright 0 | baselines above |
| 1 | 95ec727 | ruff 0, basedpyright 0, 26 tests, 96% cov | notes below |
| 2 | 6d3a726 | ruff 0, basedpyright 0, 38 tests, 96% cov, stationer 337 pass | notes below |
| 3 | pending | ruff 0, basedpyright 0, 45 partition tests | notes below |

### Task 1 — zstd_portable port

- Module: `tools/stationer/partition/zstd_portable/__init__.py` (~700 lines)
  ported from `/Users/joaolopes/Exactus/exactus/packages/zstd_portable/`.
- Dropped: `export_package` / `directory_inventory` dependency (per plan;
  installer staging now belongs to maire).
- Dropped: `Path.parents[3]` fallback payload lookup — payloads only resolve
  from `zstd_portable/bin/` inside the package.
- Added: `bin/SHA256SUMS` (pinned digests from plan) verified before any
  extraction or export; missing manifest / missing entry / digest mismatch
  all raise `RuntimeError` before the archive is opened.
- Payloads: six zips in `bin/` — `shasum -a 256 -c SHA256SUMS` → all `OK`.
- Tests: `tests/tools/stationer/partition/test_zstd_portable.py`, 26 cases —
  normalization, validation, round-trips (incl. 1 MiB random on host),
  cache/digest-marker behavior, invalid/tampered payloads, manifest tests,
  export staging. Fake-package tests write their own matching `SHA256SUMS`.
- Gates: ruff clean, basedpyright 0 errors, coverage 96% on the module.
- Deviation: helper `_call` uses `helper=` kwarg (source `name=` collides
  when forwarding `name=` to `_expected_digest`).

### Task 2 — content-addressed store port

- Module: `tools/stationer/partition/store.py` (2,277 lines) ported from
  `Exactus/exactus/packages/package/partition_fs.py`.
- The conditional `portable_name`/`import_module` zstd resolution replaced
  with one top-level `from .zstd_portable import Zstd, ZstdOptions`;
  `ZstdType`/`ZstdOptionsType` aliases collapsed to the real classes;
  `ModuleType`/`import_module` imports dropped. Zero `exactus` strings left.
- Tests: `tests/tools/stationer/partition/test_store.py` (1,739 lines, 38
  cases) ported from `test_partition_fs.py`. Helper mapping:
  `expect_equal`→`expect`, `expect_true`→`check` (7 call sites needed a
  `reason` added — kairos `check` requires it), `fail`→local `_fail`,
  `private_result`→local `_private_result` (getattr + isinstance + cast).
- Added `test_catalog_has_no_exactus_strings` per Step 5.
- Known warning: `test_partition_zip_update_rejections` emits
  `UserWarning: Duplicate name: 'catalog.json'` — inherent to building the
  intentionally malformed duplicate-member zip fixture; same warning exists
  upstream in Exactus. Not suppressed.
- Gates: ruff clean, basedpyright 0, coverage 96% on `store.py`, full
  `tests/tools/stationer` suite 337 passed in 6m06s (no regressions).

### Task 3 — launcher runtime port

- Module: `tools/stationer/partition/launcher.py` ported from
  `partition_launcher.py`; conditional import block collapsed to one
  `from .store import ...`; `PackRecord`/`Recipe`/`Catalog`/`Json` stay
  `TYPE_CHECKING`-only.
- `closure` generalized to the plan signature
  `closure(*, components: Mapping[str, tuple[str, ...]], requested:
  tuple[str, ...]) -> tuple[str, ...]`; catalog adaptation via new
  `_requires(catalog)` helper used by `Runtime.ensure`/`environment`.
- `main` is now kw-only `main(*, catalog_path, arguments: tuple |
  None, replace_process)` per plan; `_maintenance` takes the tuple.
- Removed `conflict_copies` dependency, `_bundle_root`, and its call in
  `main` (Exactus-specific cleanup).
- Renames: `EXACTUS_TOOLS_*` env vars → `KAIROS_TOOLS_*`; `exactus-tools`
  cache dirs → `kairos-tools`; help text updated.
- Tests: 7 new cases in `test_launcher.py` — closure order/diamond/cycle/
  ghost, spawn-process lock serialization (line-interleave check),
  per-owner cache-root fallback, `main` dispatch through the injected
  `ProcessReplacement` seam (fake is `NoReturn` raising `SystemExit`,
  matching `execvpe`), ready-tree reuse, dependency-first env merge.
- Reuses `component_tree`/`publish`/`scan_recipe` from `test_store.py`
  rather than duplicating fixtures.
- Gates: ruff clean, basedpyright 0, partition suite 45 passed.

### Task 4 — generic tool model, bounded fetch, build target

- Modules: `partition/model.py` (`ToolSpec`, `ComponentSpec`,
  `resolve_components` over an injected mapping — drops Exactus
  Node/Playwright/GROUPS coupling), `partition/fetch.py` (`FetchError`,
  `FetchOptions(timeout_seconds, max_bytes, retries, allowed_schemes)`,
  `fetch(*, url, destination, options)` streaming to an atomic
  `mkstemp`+`replace` destination), `partition/target.py` (`BuildTarget`
  os/arch/abi triple).
- `fetch` uses the repo's `import_module("urllib.request")` +
  `build_opener` pattern (S310-safe, matches charter/district); HTTPS
  only by default, tests inject `{"http","https"}` for loopback servers.
- Added `Content-Length` reconciliation: CPython `read(amt)` silently
  returns short bodies, so a truncated stream now raises `FetchError`
  (verified mid-body abort leaves no destination or temp file).
- Tests: `test_model.py` (5) + `test_fetch.py` (5, scripted local
  `ThreadingHTTPServer`): scheme rejection, size cap, retry-then-fail,
  atomic write, transitive/tool-name resolution, frozen target.
- Rework repair: `builder.py` `remote_root` default `.stationer`
  (remote-home-relative; `/tmp` trips S108, `~` never expands under the
  argv-quoted remote commands); `PosixBuild` carrier + `_PosixLayout`
  split `build`/`worker_script` under the arg/statement limits;
  `test_real_pipelines.open_agent` widened to the `SshOpener` union.
- Gates: ruff clean, basedpyright 0 on `tools/stationer` +
  `tests/tools/stationer`, 81 tests pass.
