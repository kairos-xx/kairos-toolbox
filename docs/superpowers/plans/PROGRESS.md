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
| 2 | pending | ruff 0, basedpyright 0, 38 tests, 96% cov, stationer 337 pass | notes below |

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
