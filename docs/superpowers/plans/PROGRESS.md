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
| 0 | (this file) | ruff 0, basedpyright 0 | baselines above |
