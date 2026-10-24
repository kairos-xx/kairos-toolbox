# maire, evocator, partition engine, installer integration and build layout — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `tools/maire` (installer engine and wizards), `tools/evocator` (updater), a partition engine inside `tools/stationer`, native installers for all 11 projects built around a frozen `maire`, and the `build/bin` + `build/lib` layout.

**Architecture:** A UI-free `maire` core runs reversible install actions and writes a receipt; wizards are declarative data rendered by pluggable renderers; native Windows/macOS wizards are exported from the same tree. Stationer gains a content-addressed partition engine (ported from the owner's Exactus package) used for Python provisioning and incremental updates, builds everything in dependency order, and emits the new layout. `evocator` extracts and generalizes `tools/district/update.py`.

**Tech Stack:** Python 3.13+, PyInstaller (existing stationer builders), PySide6 (only in the Linux wizard flavor), pytest, Ruff, BasedPyright, Inno Setup, `productbuild`/`pkgbuild`, `dpkg-deb`, `abuild`/`apk`.

**Spec:** `docs/superpowers/specs/2026-10-24-maire-design.md`. Read it first; this plan only records what the spec leaves for the implementer to wire.

## Global Constraints

Every task includes these.

- Python **3.13+**; `AGENTS.md` applies in full: Ruff and BasedPyright at **zero** diagnostics, keyword-only calls (positional-only parameters excepted), typed locals, **top-level imports only**, no `noqa`/`type: ignore`/suppressions, line length 79, Google-style docstrings, no mutable module state.
- Repo license is **MIT**. **PySide6 only** (never PyQt6), and only in the `maire-wizard` Linux flavor and the `maire[gui]` extra. Nothing else may import it. Optional import = top-level import in one module, located via `importlib.util.find_spec`, loaded via `importlib.import_module`, with terminal fallback.
- Network code: **HTTPS only** with a scheme allowlist, explicit timeouts, size bounds, HTTP status checked before use, **no live network in unit tests** (use a local fake server).
- Receipt locations: user `~/.local/share/maire` (Linux), `~/Library/Application Support/maire` (macOS), `%LOCALAPPDATA%\maire` (Windows); system `/var/lib/maire`, `/Library/Application Support/maire`, `%ProgramData%\maire`.
- Release identity: tag `<project>/v<version>`; asset `<name>-<version>-<platform>.pyz`; checksum asset `SHA256SUMS.txt`; repo `kairos-xx/kairos-toolbox`.
- 11 projects: tools `bowman charter conjure district stationer maire evocator`; plugins `close_deleted_editors devin_remote_control open_selected_editor_panel simple_web_server`.
- Layout: `build/bin/<os>/<arch>/<abi>/{tools,plugins}/<name>/{pyinstaller,installer,pyz}/`, `build/bin/pypi/{tools,plugins}/<name>/`, `build/lib/{tools,plugins}/<name>/<version>/`. `<abi>` is `glibc`, `musl` or `native`.
- Python provisioning stays on the declared minor series (3.13.x); never auto-move to 3.14.
- Work on branch `feat/maire-evocator-partition`. Commit messages end with the trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- **Everything that runs in Docker, QEMU, UTM, a VM or a remote worker goes through `tools.conjure`** (`DockerRunner`, `UtmRunner`, `LocalRunner`, `AttachedVm`, `VmRegistry`, `CommandResult`, `Settings`). New code, tests and verification steps never call `docker`, `utmctl`, `qemu-system-*` or `subprocess` for that purpose directly. Existing direct calls in stationer are migrated in Task 22A. Where a task below says "in a Docker container" or "in the Windows VM", it means "through conjure".
- Never modify `/Users/joaolopes/Exactus`; it is read-only source material, including `package_bundle.zip` (extract it only into a scratch directory, and never run anything from it other than the pinned `zstd` binaries under test).

## Review Focus

Spec-implied failure modes no task's happy path covers; each has a pinned test in the owning task.

1. **Crash midway through install** (power loss, kill): uninstall must still remove everything done so far. → Task 11.
2. **Paths with spaces and non-ASCII characters** (`/Users/joão lopes/`, `C:\Users\João`): shims, `.pth` files and PATH entries must round-trip. → Task 12.
3. **Interrupted or corrupted downloads** (partial file, wrong digest, offline): nothing is installed and nothing is left half-written. → Tasks 13 and 30.
4. **Two runs at once** (scheduler tick plus manual `evocator update`, or two installers): serialized, no corrupted receipt. → Tasks 14 and 29.
5. **Hostile or odd release data** (tags with another prefix, malformed versions, prereleases on the stable channel, GitHub rate-limit 403, empty release list, missing `SHA256SUMS.txt`): ignored or refused, never installed. → Task 28.

---

## Needs, Forecast and Operating Rules

This section exists so a fresh session (or agent) can start without rediscovering anything. Facts marked *(probed 2026-10-24)* were measured on the owner's Mac, not assumed.

### A. Environment readiness *(probed 2026-10-24)*

| Need | Status here | Used by | Action |
|---|---|---|---|
| Python 3.13.9 venv `.venv`, `pytest`, `websockets`, `psutil` | present | all | none |
| `ruff` | `/opt/homebrew/bin/ruff` (not in the venv) | every task | invoke by that path; record in PROGRESS.md |
| `basedpyright` | `/opt/anaconda3/bin/basedpyright` | every task | invoke by that path |
| `PySide6` | **missing** | Task 17, 24 | `uv pip install --python .venv/bin/python "PySide6>=6.8"` at Task 17 only |
| `PyInstaller` | **missing from the venv** | Tasks 24, 33 | stationer builds in its own environments (Docker / venv); confirm in Task 0 how `MacOSBuilder` provisions it before relying on it |
| `pyyaml` | **missing** | Task 26 validation | use `uv run --with pyyaml python -c ...` (no permanent install) |
| Docker 29.8 (Linux **aarch64** engine); images `python:3.13-alpine`, `python:3.13-slim-bookworm`, `debian:12`, `debian:13` | present | Linux builds, Tasks 23, 24, 29, 33 | amd64 builds run emulated: slow (see R5) |
| `dpkg-deb`, `fakeroot`, `abuild`, `apk` | **missing on the host** | Tasks 23, 33 | only used inside Docker containers; never required on the host |
| `pkgbuild`, `productbuild`, `pkgutil` | present | Tasks 19, 23 | none |
| Inno Setup (`ISCC`) | not on the host; `wine` is present | Tasks 18, 23 | stationer's Windows worker fetches `ISCC.exe` itself (`remote_builder.py:545`); the spike runs there |
| UTM.app with `Windows 2.utm`, `Windows.utm`, `Linux*.utm`; `utmctl` not on PATH | present | Tasks 18, 29, 33 | stationer drives UTM through the guest agent (`vm = "utm:Windows 2"`); reuse that, don't invent a new path |
| `gh`, `node`, `npm`, `tmux`, `uv`, `git` | present | release, plugin tests | none |
| Disk: **33 GB free**; RAM 64 GB; 18 cores | measured | Docker images, frozen PySide6 builds, VM disks | prune Docker between phases (R11) |
| Signing: only `Apple Development` identity | measured | Task 19, 23 | no `Developer ID Installer`; see R3 |

### B. Dependency forecast

- Runtime of `maire`/`evocator`: standard library only, plus the committed native `zstd` archives (Task 1). No new PyPI runtime dependency.
- Optional: `PySide6` (the `gui` extra), never imported outside `tools/maire/render/qt.py`.
- Dev/test only: `PySide6` (Task 17 tests), `pyyaml` via `uv run --with` (Task 26).
- Build: Inno Setup (fetched on the worker), `pkgbuild`/`productbuild`, `dpkg-deb`, `abuild`; python-build-standalone and NuGet (downloaded at run time by `maire`, digests pinned in `versions.py`).

### C. Risk register

| ID | Risk | Likelihood / impact | Mitigation | Early signal | Task |
|---|---|---|---|---|---|
| R1 | Inno cannot extract and run `maire` at wizard start | medium / high | Task 18 spike first; fallback = static pages + post-install `maire configure` | spike fails in the Windows VM | 18 |
| R2 | macOS Installer JavaScript too limited for dynamic choices | high / medium | static choices + file-existence checks; `maire configure` for the rest | `distribution.xml` rejects a condition | 19 |
| R3 | No `Developer ID`: `.pkg` is unsigned or dev-signed; other Macs' Gatekeeper blocks it | high / high for distribution, none for local use | build anyway; document right-click-open; owner decision whether to enrol in the Developer ID program | install on a second Mac is blocked | 19, 23 |
| R4 | Windows installers unsigned: SmartScreen warns | high / medium | document; signing is out of scope | first Windows download | 23 |
| R5 | Docker on an arm64 Mac runs amd64 images under emulation: slow, sometimes flaky | high / medium | develop on native arm64; run amd64 on the GitHub `ubuntu-latest` runner in `release.yml`; give long timeouts; run amd64 only at Task 33 | builds exceed 10 min | 23, 24, 33 |
| R6 | UTM/Windows VM unavailable or flaky (SSH transient seen earlier) | high / medium | gate VM tests like `test_real_pipelines.py`; record **not run**, never mark passed | guest agent timeout | 18, 29, 33 |
| R7 | Frozen PySide6 is large; musl unproven | medium / medium | `maire-wizard` glibc only; small flavor everywhere else | build >300 MB | 24 |
| R8 | python-build-standalone publishes no checksum manifest | medium / medium | digests pinned in `versions.py`; evocator reads upstream manifest only where it exists | Task 13 step 4 finding | 13, 30 |
| R9 | Ported Exactus code (~6k lines) fails this repo's Ruff/BasedPyright at zero | medium / medium | fix root causes in place; budget a full extra pass for `store.py` | first lint run | 1–4 |
| R10 | The plan is large (35 tasks) | high / high | phase gates; each phase leaves working software; PROGRESS.md checkpoints; stop-safe boundaries (section F) | a phase exceeds its forecast by 50% | all |
| R11 | Disk pressure (33 GB free) | medium / medium | `docker system prune` and delete `build/` scratch after each phase; never keep more than one frozen PySide6 build | `df` below 15 GB | 24, 33 |
| R12 | The owner edits the same repo concurrently (earlier editor conflicts) | medium / high | check `git status` before each task; commit only the task's own paths (`git add <paths>`, never `-A`); use a worktree if the tree is dirty | unexpected diff in `git status` | all |
| R13 | Replacing `district-release.yml` breaks District's release | medium / high | keep the old workflow until `release.yml` has a green dry run; delete it in a later commit | dry run differs | 26 |
| R14 | Auto-install is a remote-code-execution path | low / high | repo allowlist, HTTPS only, SHA-256 required, no unverified fallback, kill switch (`EVOCATOR_DISABLE=1` and `policy check=never`); no signing is a **documented residual risk** | any bypass found in review | 28–30 |
| R15 | The full test suite is slow, hiding regressions | medium / low | focused runs per task; full run at phase gates only | suite >10 min | all |
| R16 | Stationer manifest loader rejects unknown keys | high / medium | extend `ProjectTable`, `ProjectConfig` and loader together with tests (Task 21) | `ManifestLoader` raises on `kind` | 21 |

### D. Effort and session forecast (estimates, recalibrate after Phase 1)

"Session" = one full working context. Native execution; subagent-driven costs roughly 1.5–2.5× the tokens for the same scope.

| Phase | Tasks | Main cost driver | Forecast |
|---|---|---|---|
| 0 Preflight | 0 | baselines | 0.2 session |
| 1 Partition engine | 1–5 | porting and linting ~5.4k lines | 1–1.5 |
| 2 maire core | 6–16 | 11 tasks, all greenfield, heavy tests | 2–2.5 |
| 3 GUI and native wizards | 17–19 | spikes need VM/Mac hands-on | 1 |
| 4 Stationer and layout | 20–27 | `builder.py` (3.5k lines) coupling | 2–2.5 |
| 5 evocator | 28–32 | extraction plus scoped apply | 1.5 |
| 6 Verification and docs | 33–34 | VM/Docker waits | 0.7 |
| 4 addition: reuse (5A, 14A, 14B) | 3 | lazy `__init__`, architecture guard, bowman seam | +0.8 |
| 5 addition: district installer (32A analysis, 32B migration) | 2 | 2,809-line file onto maire | +1.2 |
| optional 32C: retire remaining duplicates (owner-gated) | 1 | touches four tools | +0.7 |
| **Total** | 41 | | **≈ 10.5–12 sessions** (the earlier 8.5–9.5 plus the additions) |

Recommended slicing into session-sized work packages, each with entry and exit criteria (exit = phase gate in section E):
1. Task 0 + Phase 1. 2. Tasks 6–11. 3. Tasks 12–16. 4. Tasks 17–19. 5. Tasks 20–23. 6. Tasks 24–27. 7. Tasks 28–30. 8. Tasks 31–32. 9. Tasks 33–34.

### E. Phase gates (definition of done per phase)

Run from the repo root; every gate must print the stated result before the checkpoint tag is created.

- **After every task:** `ruff check <paths touched>`, `basedpyright <paths touched>` → 0 diagnostics; the task's tests pass; `git status --short` shows only the task's paths.
- **Phase 1:** `python -m pytest tests/tools/stationer -q` all pass; `stationer partition build/verify/update/rollback/gc` round-trips a sample tree.
- **Phase 2:** `python -m pytest tests/tools/maire -q` all pass; `python -m tools.maire install --silent` then `uninstall` leaves a temp `HOME` identical.
- **Phase 3:** `docs/tools/maire/SPIKES.md` records the Inno and `.pkg` results (including "could not run" where true); exporters match their golden files.
- **Phase 4:** `python -m tools.stationer --projects all --dry-run` lists 11 projects in `build_order` under `build/bin/...`; one DEB built in Docker contains `install.json` and the frozen `maire`.
- **Phase 5:** `python -m pytest tests/tools/evocator tests/tools/district -q` all pass; `tools/district/update.py` no longer exists.
- **Phase 6:** `ruff check .`, `ruff format --check .`, `basedpyright`, `python -m pytest -q` → 0 / 0 / 0 / all pass, with every skip listed in `docs/tools/maire/VERIFICATION.md`.

### F. Checkpoint and resume protocol

- **Branch and tags.** Work on `feat/maire-evocator-partition`; after each phase gate create the tag `checkpoint/phase-N` (local only; do not push without the owner's say-so).
- **Progress file.** After each task, update `docs/superpowers/plans/PROGRESS.md` with: task id, commit SHA, gate output summary (counts), blockers, anything deferred. Commit it with the task. A stop after any commit therefore loses at most the task in flight.
- **Resume.** A new session reads, in order: the spec, this plan's header, `PROGRESS.md`, then the next unchecked task. Each task's Interfaces block is sufficient context for that task; do not reread other tasks.
- **Rollback.** `git reset --hard checkpoint/phase-N` is the escape hatch for a failed phase; never rewrite commits that were pushed.
- **Never leave a half state.** If a task cannot finish in the session, revert its uncommitted changes (`git restore -- <task paths>`) and record the blocker in PROGRESS.md.

### G. Critical path and parallelism (for subagent-driven execution)

- Serial spine: 0 → 1 → 2 → 3 → 4(fetch) → 13 → 14 → 23 → 29 → 30 → 32 → 33 → 34.
- Safe to run in parallel once their inputs exist: Task 4 alongside Task 2; Tasks 7, 8, 10 together after Task 6; Tasks 20, 21 (stationer layout/manifests) alongside all of Phase 2 because they do not import `maire`; Task 28 (extraction) alongside Phase 3; Tasks 18 and 19 together.
- Parallel agents must touch disjoint paths (R12): never two agents editing `tools/stationer/builder.py`; Tasks 20, 21, 22, 23, 24 all edit it and therefore run **serially** in that order.

### H. Decision log (carried from the spec; do not reopen without the owner)

PySide6 not PyQt6; PySide6 only in the Linux wizard flavor; frozen `maire` hosts everything; native Windows/macOS wizards are exported from the page tree; `.deb`/`.apk` install silently, wizard runs afterwards; one plan for all five parts; layout and `conjure` reading confirmed; partition engine ported agnostic and Exactus stays read-only; native zstd archives pinned by SHA-256; `charter` stays a separate tool (conjure and stationer use it, nothing is folded in); tests run in a hermetic sandbox (Task 6A).

### I. Still-open items that gate work (each with an owner and fallback)

| Item | Resolved by | Fallback if unresolved |
|---|---|---|
| python-build-standalone checksum source | Task 13 step 4 | pinned digests in `versions.py` |
| Inno helper extraction | Task 18 spike | static pages + `maire configure` |
| macOS `.pkg` dynamic choices | Task 19 spike | postinstall defaults + `maire configure` |
| Unsigned `.apk` system installs | Task 29 step 4 | Alpine system scope is notify-only |
| Developer ID / code signing | **owner decision** | build unsigned; document install friction |
| Whether baseline lint is already at zero | Task 0 | gate on "no new diagnostics", record baseline counts |

---

## Ownership and Reuse Map (no redundancy)

**Principle.** Every capability has exactly one owner module. Every other tool imports it. A task that appears to write one of these means "import the owner". Findings below come from an audit of the repo on 2026-10-24.

### Layers (imports point downward only)

| Layer | Modules | May import |
|---|---|---|
| L0 foundation | `tools.stationer.partition.*`, `tools.stationer.versioning`, `tools.stationer.naming` | standard library and each other only. Must be importable **without** importing `tools.stationer.builder` |
| L1 | `tools.bowman` (leaf), `tools.charter`, `tools.conjure` | L0; conjure may use charter |
| L2 | `tools.maire` | L0, L1 (`bowman` for elevation) |
| L3 | `tools.evocator` | L0, L2 |
| L4 | `tools.stationer` (builders, publisher), `tools.district` | everything below; district must never be imported by a lower layer |

Forbidden edges include: `maire` → `evocator`/`district`/stationer builders; `evocator` → `district`; any L0 module → anything outside L0. An AST import-graph test enforces this (Task 5A).

**Blocking finding.** `tools/stationer/__init__.py` eagerly imports `builder`, `engine`, `publisher`, `remote_builder`, so today `import tools.stationer.partition` loads the whole build tool (and `conjure`, creating a cycle once `conjure` reuses L0). Task 5A therefore turns the package `__init__` into a lazy public API (module `__getattr__` + `importlib.import_module`, with `TYPE_CHECKING` imports for the type checker), keeping every existing `from tools.stationer import X` working.

### Owners

| Capability | Single owner | Reused by (new work) | Duplicates to retire (task) |
|---|---|---|---|
| Bounded HTTPS fetch: bytes, JSON, retries, size cap, atomic write | `partition/fetch.py` (`fetch`, `fetch_bytes`, `fetch_json`) | maire Python sources (13), evocator source, dependencies, verify (28–30) | `district/update.py` `_request/_read/fetch_json/fetch_bytes` (32B); `charter/http.py` is a different job (provider API calls) — evaluated in 32C |
| File digests and checksum manifests (`SHA256SUMS`) | `partition/checksums.py` (`write_sums`, `parse_sums`, `expected_digest`, `verify_file`) on top of `store.hash_file` | stationer artifacts/report/publisher, zstd loader (1), maire EnsurePython (13), evocator verify (29) | `district/update.expected_digest` (32B); ad-hoc `sha256(` in stationer `builder/engine/build_report/artifacts` (32C) |
| Platform identity (`os/arch/abi`, release tag) | `partition/target.py` (`Platform`, `host_platform`, `release_tag`) | maire Environment and discovery, evocator asset selection, zstd loader, stationer layout | `district/update.platform_tag` (32B); `zstd_portable._normalize` (Task 1); ad-hoc `platform.machine()` in `pyz_builder`, `builder` (32C); `district/platform.py` reviewed in 32A |
| Version parsing and ordering (incl. prerelease) | `stationer/versioning.py` (`Version`, add `is_newer`) | evocator, maire shared-copy rule, release planner | `district/update.parse_version/is_newer` (32B) |
| Release asset, checksum-asset and tag names | `stationer/naming.py` (`asset_name`, `checksum_name`, `tag_name`, `parse_tag`) | `pyz_builder.archive_name`, publisher, planner, evocator, `install.json` | f-strings in `district/update.select_asset` (32B); `remote_builder.installer_name` (32C) |
| Atomic and private file writes | `partition/store.atomic_bytes` (add `private: bool = False`) | maire receipt and journal, evocator state and policy | `district/product.secure_private_file`, `pairing` 0o600 writes, `json_config` writes (32C) |
| Cross-process lock | `partition/launcher.cache_lock` | maire services, evocator apply | locking in `district/installer.py` (32A) |
| JSON typing | `partition/json_types.py` (`JsonValue`, `JsonObject`) | maire, evocator, partition | ~12 private copies in charter, conjure, stationer, district (32C) |
| Placing payloads, aliases, `.pth`, extensions, uninstall by receipt | `maire` actions | stationer package scripts (23), evocator apply (29), every plugin, **district's installer (32B)** | `plugins/*/install.py` ×4 (27); placement, manifest, launcher and wizard code in `district/installer.py` (32B); ensure-python scripts and pins in `installer_support` (23) |
| Marker-delimited blocks in text files | `maire` `ManagedBlock` action (Task 12) | PATH opt-in (12), district shell-profile and routing blocks (32B) | `install_block/remove_block/routing_block` in `district/installer.py` (32B) |
| Interpreter and editor discovery | `maire/discovery` | maire wizards | the four plugin copies (27) |
| Privilege escalation for system scope | `bowman` `PrivilegeBroker` / `ElevationGrant` | maire system-scope actions (14A decides the exact seam) | none new; maire must not hand-roll `sudo`/UAC |
| Execution on Docker, QEMU, UTM, VM, remote | `conjure` | stationer (22A), every verification step | direct `subprocess_run` in stationer (22A) |
| Tunnels and exposure | `charter` | conjure backends, stationer remote | none |
| Progress reporting | `stationer/progress.py` | evaluated for maire/evocator in 14A (reuse if it is import-light, else define the event type once in L0) | n/a |
| Wizard rendering and prompts | `maire` renderers | district's installer wizard (32B) | `wizard/read_key/wizard_frame/prompt_alias` in `district/installer.py` (32B) |

### Enforcement (Task 5A)

`tests/tools/test_architecture.py` has two parts: (1) the layer table above as an allowed-edge list checked against the real import graph (AST scan of `tools/`); (2) a **duplication guard** that fails on `urlopen(`, `sha256(` on files, `platform.machine(`, `os.replace(` for config writes, `type JsonValue =` and `def parse_version` outside their owners. Existing offenders are listed once in `tests/tools/architecture_allowlist.toml`, each tagged with the task that retires it. The allowlist may only shrink: a test fails if an entry no longer matches anything, or if a new offender appears.

### New risks

| ID | Risk | Mitigation |
|---|---|---|
| R17 | Making `tools/stationer/__init__.py` lazy breaks `from tools.stationer import ...` consumers or type checking | keep a `__all__`, use `TYPE_CHECKING` imports, test every name previously exported |
| R18 | Asset-name drift: stationer writes `SHA256SUMS`, District's updater requires `SHA256SUMS.txt`; the platform tag in asset names may differ between `PyzTarget` cells and `platform_tag()` | Task 5A reconciles with golden tests from real names before `evocator` is written |
| R19 | Migrating District's 2,809-line installer onto maire changes behavior users rely on | Tasks 32A/32B keep District's component list and CLI flags, move mechanisms only, and run District's existing installer tests unchanged |

---

## File Structure

```
tools/stationer/partition/        zstd_portable/  store.py  launcher.py  model.py  fetch.py  target.py  cli.py
tools/stationer/layout.py         snapshot.py  projects.py
tools/maire/                      __init__ __main__ cli  model/ tree/ discovery/ actions/ render/ export/ services/ templates/
tools/evocator/                   __init__ __main__ cli  policy/ state/ source/ verify/ apply/ deps/ schedule/ templates/
docs/tools/{maire,evocator}/      README.md DOCS.md
tests/tools/{maire,evocator}/     mirror the packages
tests/tools/stationer/partition/  ported + new tests
```

---

# Phase 0 — Preflight

### Task 0: Branch, baselines and progress file

**Files:**
- Create: `docs/superpowers/plans/PROGRESS.md`

**Interfaces:**
- Produces: the recorded baseline (test count, ruff and basedpyright diagnostic counts, tool paths) that every later gate compares against.

- [ ] **Step 1: Check the tree.** Run `git status --short`. If it prints anything, use `superpowers:using-git-worktrees` to work in a separate worktree (R12); otherwise run `git switch -c feat/maire-evocator-partition`.
- [ ] **Step 2: Record the tool invocations** that work here: `/opt/homebrew/bin/ruff --version`, `/opt/anaconda3/bin/basedpyright --version`, `.venv/bin/python -m pytest --version`. Write them at the top of `PROGRESS.md`.
- [ ] **Step 3: Measure the baseline:** `ruff check tools tests plugins 2>&1 | tail -3`, `basedpyright tools/stationer tools/district 2>&1 | tail -3`, `.venv/bin/python -m pytest -q 2>&1 | tail -3`. Record the counts. Expected: the owner's last verified state was ruff clean and 1232 tests passing; if today's numbers differ, record today's and gate on "no new diagnostics" until the owner says otherwise.
- [ ] **Step 4: Confirm how stationer provisions PyInstaller:** `grep -n "pyinstaller" tools/stationer/builder.py | head -20`; note whether `MacOSBuilder` creates its own venv. Record the answer in `PROGRESS.md`.
- [ ] **Step 5: Commit** `chore: plan progress file and baselines`, then `git tag checkpoint/phase-0`.

---

# Phase 1 — Partition engine in stationer

### Task 1: Port `zstd_portable` with its native binaries

`zstd_portable` is a wrapper that extracts a prebuilt native `zstd` CLI from a per-platform zip on first use (it is not pure Python). The owner supplied the binaries in `/Users/joaolopes/Exactus/package_bundle.zip` under `bin/zstd_portable/`. The six archives and their SHA-256 digests (zstd v1.5.7 where the version string is readable; Linux builds are statically linked, so they run on glibc and musl):

```
a487e65b284ade11bc168ee10990539269b1026b05281fd8b3557711d6dcc2f3  linux-arm64.zip
f41eb87f99b78a284ac54fbb0d4214c70834b5ee59c984b5de28179a77e39a3a  linux-x86_64.zip
96b5cc46e87e6cc5ab1d87db1fc03e53afe229b2b93fb3f955d0cc4eb01a93a1  macos-universal2.zip
50289631fd602eab8e05cd67d55f270df9c3914d13715c547152c6f885ff2bab  windows-arm64.zip
acec2f0ceb31642186c22ce713d830b91f984734f279ffc1fd2ccdad6093db75  windows-x86.zip
ab351ff0f5eb1875ce353a29a55f2a46db68fe3cf85067bd758afbeecc577eac  windows-x86_64.zip
```

**Files:**
- Create: `tools/stationer/partition/__init__.py`, `tools/stationer/partition/zstd_portable/__init__.py`, `.../py.typed`, `.../bin/*.zip` (the six archives), `.../bin/SHA256SUMS`
- Test: `tests/tools/stationer/partition/test_zstd_portable.py` (port from `/Users/joaolopes/Exactus/exactus/tests/packages/zstd_portable`)

**Interfaces:**
- Produces: `Zstd`, `ZstdOptions`, `export_binary(*, os_name: str, machine: str, target: Path) -> Path` (same names as the source, importable from `tools.stationer.partition.zstd_portable`). `export_package` and its `directory_inventory` / README generation are **not** ported.

- [ ] **Step 1: Copy** the source package and its tests, dropping `__main__.py`, `examples/`, `README.md`, `.DS_Store`, `export_package` and the `directory_inventory` import. Rewrite any `exactus.` import to a relative import. Payload lookup uses only the sibling `bin/` directory (remove the `parents[3]/bin` fallback).
- [ ] **Step 2: Copy the six archives** from the owner's bundle into `bin/` and write `bin/SHA256SUMS` from the digests above. Verify with `cd tools/stationer/partition/zstd_portable/bin && shasum -a 256 -c SHA256SUMS`. Expected: six `OK`.
- [ ] **Step 3: Tests (add to the ported ones):** `test_archives_match_sha256sums` (hash each zip, compare to `SHA256SUMS`); `test_every_platform_has_its_expected_member` (`zstd` or `zstd.exe` present in each archive); `test_loader_refuses_archive_with_wrong_digest` (a tampered copy raises before extraction); `test_round_trip_on_host` (compress then decompress 1 MiB of random bytes using the host's binary and compare).
- [ ] **Step 4: Make the loader verify** the archive digest against `bin/SHA256SUMS` before extracting. Run the tests. Expected: pass.
- [ ] **Step 5: Run** `ruff check tools/stationer/partition && basedpyright tools/stationer/partition`; fix root causes until both report 0 (no suppressions).
- [ ] **Step 6: Commit** `feat(stationer): port zstd_portable with pinned native binaries`.

### Task 2: Port the content-addressed store

**Files:**
- Create: `tools/stationer/partition/store.py` (from `Exactus/exactus/packages/package/partition_fs.py`, 2,310 lines)
- Test: `tests/tools/stationer/partition/test_store.py` (from `Exactus/exactus/tests/packages/package/test_partition_fs.py`, 1,688 lines)

**Interfaces:**
- Consumes: Task 1 `Zstd`, `ZstdOptions`.
- Produces: same public names as the source: `Catalog`, `Recipe`, `PackRecord`, `ObjectRecord`, `ChunkingConfig`, `FormatError`, `ObjectStore`, `RecipeScanner`, `PackCodec`, `PartitionStore`, `Materializer`, `write_delta`, `apply_delta`, `rollback`, `garbage_collect`, `deep_verify`, `apply_update`, `verify_tree`, `load_catalog`, `load_recipe`.

- [ ] **Step 1: Port the test file first**, replacing the import root with `tools.stationer.partition.store`. Run it. Expected: FAIL (module missing).
- [ ] **Step 2: Port `partition_fs.py`.** Replace the conditional `exactus.packages.zstd_portable` import blocks (source lines 45–52) with one top-level `from .zstd_portable import Zstd, ZstdOptions`. Remove the four `exactus` mentions from names, docstrings and schema ids. Behavior is otherwise unchanged.
- [ ] **Step 3: Run** the ported tests. Expected: all 1,688 lines' worth pass. If a test depended on an Exactus path, change the path, not the assertion.
- [ ] **Step 4: Run** ruff and basedpyright on the module and tests; fix to 0.
- [ ] **Step 5: Add** `test_catalog_has_no_exactus_strings`: build a tiny catalog, serialize with `canonical`, assert `b"exactus" not in encoded`.
- [ ] **Step 6: Commit** `feat(stationer): port the content-addressed partition store`.

### Task 3: Port the launcher runtime

**Files:**
- Create: `tools/stationer/partition/launcher.py` (from `partition_launcher.py`, 946 lines)
- Test: `tests/tools/stationer/partition/test_launcher.py` (new; the source has no tests)

**Interfaces:**
- Consumes: Task 2 store API.
- Produces: `closure(*, components: Mapping[str, tuple[str, ...]], requested: tuple[str, ...]) -> tuple[str, ...]` (dependency-first order, deterministic), `cache_lock(*, root: Path)` context manager, `Runtime`, `main(*, arguments: tuple[str, ...]) -> int`.

- [ ] **Step 1: Write tests** (they fail first):
  - `test_closure_orders_dependencies_first`: `a→b→c` returns `("c","b","a")`.
  - `test_closure_is_deterministic_and_rejects_cycles`: a cycle raises `FormatError`.
  - `test_cache_lock_serializes_two_processes`: two `multiprocessing` workers append to a file under the lock; lines never interleave.
  - `test_cache_root_falls_back_when_shared_root_unwritable`: unwritable shared root selects a per-owner root.
  - `test_main_replaces_process_via_injected_boundary`: using the existing `ProcessReplacement` seam, `main` calls it with the materialized tool path.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Port** `partition_launcher.py`. Remove the `exactus.tools.common.conflict_copies` dependency (conflict-copy cleanup is Exactus-specific; delete `_bundle_root` and its caller). Remove `exactus` strings.
- [ ] **Step 4: Run** tests, ruff, basedpyright. Expected: pass, 0, 0.
- [ ] **Step 5: Commit** `feat(stationer): port the partition launcher runtime`.

### Task 4: Generic tool model, fetch and build target

**Files:**
- Create: `tools/stationer/partition/model.py`, `fetch.py`, `target.py` (generic parts of `bundle.py`)
- Test: `tests/tools/stationer/partition/test_model.py`, `test_fetch.py`

**Interfaces:**
- Produces:
  - `ToolSpec(name: str, entrypoint: str, component: str)`, `ComponentSpec(name: str, dependencies: tuple[str, ...], tools: tuple[ToolSpec, ...])` (frozen, slots, kw_only).
  - `resolve_components(*, requested: tuple[str, ...], components: Mapping[str, ComponentSpec]) -> tuple[ComponentSpec, ...]`.
  - `FetchOptions(timeout_seconds: int = 30, max_bytes: int = 1 << 30, retries: int = 3)`, `FetchError`, `fetch(*, url: str, destination: Path, options: FetchOptions) -> Path`.
  - `BuildTarget(os: str, arch: str, abi: str)`.

- [ ] **Step 1: Write tests:**
  - `test_resolve_components_includes_transitive_dependencies`.
  - `test_fetch_rejects_non_https_scheme`: `http://` and `file://` raise `FetchError`.
  - `test_fetch_enforces_size_cap`: a local HTTPS-allowlisted test server (scheme check bypassed only through an injected `allowed_schemes` seam used by tests) streaming more than `max_bytes` raises and leaves no file.
  - `test_fetch_retries_then_fails`: server returns 503 twice then 200 → succeeds; always 503 → `FetchError` after `retries`.
  - `test_fetch_writes_atomically`: the destination never exists half-written (interrupted stream leaves no destination file).
- [ ] **Step 2: Run** (FAIL), **Step 3: Implement** by porting `ToolSpec`, `ComponentSpec`, `resolve_components`, `FetchOptions`, `FetchError`, `fetch`, `BuildTarget`, `ResponseReader` from `bundle.py`, dropping Node/Playwright/Exactus types, adding the scheme allowlist and atomic write.
- [ ] **Step 4: Run** tests, ruff, basedpyright. **Step 5: Commit** `feat(stationer): generic tool model and bounded fetch`.

### Task 5: `stationer partition` commands

**Files:**
- Create: `tools/stationer/partition/cli.py`
- Modify: `tools/stationer/cli.py` (register the `partition` subcommand beside the existing ones)
- Test: `tests/tools/stationer/partition/test_cli.py`

**Interfaces:**
- Consumes: Tasks 2 and 4.
- Produces: `main(*, arguments: tuple[str, ...]) -> int` with subcommands `build <source-dir> --out <dir>`, `verify <catalog>`, `update --from <catalog> --to <catalog> --out <dir>`, `rollback <store> <catalog>`, `gc <store>`.

- [ ] **Step 1: Tests** run each subcommand on a temp tree: `build` produces a catalog and packs; `verify` returns 0 and returns nonzero after one pack byte is flipped; `update` writes only new packs (assert pack count equals the changed count); `rollback` restores the earlier catalog; `gc` removes unreachable packs only.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement** `main` as a thin argparse layer over `PartitionStore`/`write_delta`/`rollback`/`garbage_collect`/`deep_verify`; register in `tools/stationer/cli.py`.
- [ ] **Step 4: Run** `python -m pytest tests/tools/stationer -q` (whole suite, to catch CLI regressions) and the linters. **Step 5: Commit** `feat(stationer): partition build/verify/update/rollback/gc`.

---

### Task 5A: Foundation primitives, lazy stationer API, architecture guard

Makes the shared owners real before anything else consumes them. Runs after Task 5 and before Phase 2.

**Files:**
- Modify: `tools/stationer/__init__.py` (lazy public API), `tools/stationer/versioning.py` (add `is_newer`), `tools/stationer/artifacts.py` (`checksum_directory` delegates to `checksums.write_sums`), `tools/stationer/pyz_builder.py` (`archive_name` delegates to `naming.asset_name`), `tools/stationer/partition/store.py` (`atomic_bytes(..., private: bool = False)`), `tools/stationer/partition/fetch.py` (add `fetch_bytes`, `fetch_json`), `tools/stationer/partition/target.py` (add `Platform`, `host_platform`, `release_tag`)
- Create: `tools/stationer/partition/checksums.py`, `tools/stationer/partition/json_types.py`, `tools/stationer/naming.py`, `tests/tools/test_architecture.py`, `tests/tools/architecture_allowlist.toml`
- Test: `tests/tools/stationer/partition/test_checksums.py`, `test_target.py`, `test_json_types.py`, `tests/tools/stationer/test_naming.py`, `test_versioning.py` (extend), `test_lazy_init.py`

**Interfaces:**
- Produces:
  - `checksums.write_sums(*, directory: Path, names: Sequence[str] | None = None, output_name: str = naming.CHECKSUM_NAME) -> Path`; `parse_sums(*, text: str) -> Mapping[str, str]`; `expected_digest(*, text: str, name: str) -> str | None`; `verify_file(*, path: Path, text: str, name: str | None = None) -> bool`.
  - `target.Platform(os: Literal["linux","macos","windows"], arch: Literal["amd64","arm64","x86"], abi: Literal["glibc","musl","native"])`; `host_platform(*, system: str, machine: str, libc: str | None) -> Platform` (all ambient values injected); `release_tag(*, platform: Platform) -> str` preserving today's tags exactly (`macos-arm64`, `linux-x86_64`, `windows-amd64`) as golden values.
  - `naming.CHECKSUM_NAME: Final[str]`, `asset_name(*, project: str, version: str, tag: str, suffix: str = "pyz") -> str`, `tag_name(*, project: str, version: str) -> str` (`<project>/v<version>`), `parse_tag(*, tag: str) -> tuple[str, str] | None`.
  - `versioning.is_newer(*, candidate: str, current: str, allow_prerelease: bool = False) -> bool`.
  - `json_types.JsonValue`, `JsonObject`.

- [ ] **Step 1: Reconcile the naming contract (R18).** Run `grep -rn "SHA256SUMS" tools tests .github | head -30` and `grep -n "platform_tag\|PyzTarget\|archive_name" tools/stationer/*.py tools/district/update.py`; run `gh release list --repo kairos-xx/kairos-toolbox --limit 5` and, if a release exists, `gh release view <tag> --json assets` to read the **real** published asset names. Write the findings into `PROGRESS.md` and choose one checksum name and one tag form; encode them as golden tests in `test_naming.py` and `test_target.py`. If real assets and the updater disagree, the real assets win and the updater is the thing to change.
- [ ] **Step 2: Lazy `__init__` tests first.** `test_lazy_init.py`: (a) every name in the old `__all__` still imports via `from tools.stationer import X`; (b) `subprocess` check: `python -c "import tools.stationer.partition, sys; sys.exit(int('tools.stationer.builder' in sys.modules))"` exits 0; (c) same for `tools.stationer.versioning` and `tools.stationer.naming`. Run: (b) and (c) FAIL.
- [ ] **Step 3: Make `tools/stationer/__init__.py` lazy:** keep `__all__`; resolve each name on first access with a module `__getattr__` that maps name → submodule and calls `importlib.import_module`; keep the real imports under `if TYPE_CHECKING:`. Run the tests; then `python -m pytest tests/tools/stationer -q` to prove no consumer broke (R17).
- [ ] **Step 4: Primitive tests first, then implement** `checksums`, `target`, `naming`, `json_types`, `is_newer`, `atomic_bytes(private=True)` (file mode `0o600` on POSIX, asserted in a test), `fetch_bytes/fetch_json` (reuse the Task 4 transport; status checked before use). Tests include prerelease ordering (`1.2.0 > 1.2.0rc1`, `rc` ignored unless `allow_prerelease`), malformed versions return `False`, and checksum parsing of `sha256  name` and `sha256 *name` forms.
- [ ] **Step 5: Re-point existing code at the owners:** `artifacts.checksum_directory`, `pyz_builder.archive_name`. Existing tests must pass unchanged.
- [ ] **Step 6: Architecture guard.** Implement `test_architecture.py`: (1) parse every `.py` under `tools/` with `ast`, build the module import graph, and assert it only uses edges allowed by the layer table; (2) the duplication guard from the Ownership map. Generate `architecture_allowlist.toml` from the current offenders (this audit's list), each entry tagged with its retiring task. Also assert allowlist entries still match something.
- [ ] **Step 7: Run** the full stationer suite, `tests/tools/test_architecture.py`, ruff and basedpyright. Expected: pass, 0, 0.
- [ ] **Step 8: Commit** `refactor(stationer): shared foundation primitives, lazy API, architecture guard`.

---

# Phase 2 — maire core

### Task 6: Scaffold `tools/maire`

**Files:**
- Create: `tools/maire/__init__.py`, `__main__.py`, `cli.py` (stub `main(*, arguments) -> int` returning 0), `tools/maire/README.md`, `docs/tools/maire/README.md`, `docs/tools/maire/DOCS.md`, `tests/tools/maire/__init__.py`, `tests/tools/maire/README.md`, `tools/maire/templates/stationer.toml`
- Test: `tests/tools/maire/test_main.py`

- [ ] **Step 1: Test** `test_module_runs`: `python -m tools.maire --help` exits 0 and prints `maire`.
- [ ] **Step 2: Create** the files, mirroring `tools/conjure` structure and README conventions. `templates/stationer.toml` follows `tools/district/templates/stationer.toml` (name `maire`, entrypoint `maire_entry.py`, `console = true`, identifier `dev.kairos-xx.maire`).
- [ ] **Step 3: Run** the test and linters. **Step 4: Commit** `feat(maire): scaffold the tool`.

### Task 6A: Hermetic safety net (tests can never touch the real machine)

`maire`, `evocator`, the scheduler action and District's installer write to shell profiles, agent configs, `PATH`, launch agents and `site-packages`: the same places the removed `lake` install polluted. Every test must run against a throwaway world, enforced by a fixture rather than by discipline.

**Files:**
- Create: `tests/tools/hermetic.py`, `tests/tools/conftest.py` (autouse fixtures), `tests/tools/test_hermetic.py`
- Modify: `tests/conftest.py` (import the fixtures)

**Interfaces:**
- Produces: autouse fixture `sandbox_machine` that sets `HOME`, `USERPROFILE`, `XDG_*`, `APPDATA`, `LOCALAPPDATA`, `TMPDIR` to a temp tree and `PATH` to a temp `bin` directory plus the Python directory; a `CommandGuard` that makes any real call to `launchctl`, `systemctl`, `schtasks`, `dpkg`, `apk`, `installer`, `pkgbuild` (outside tests that opt in), `sudo`, `reg`, `tmux`, `docker` raise `RealMachineAccess`; `real_machine` pytest marker (default-skipped) for the few end-to-end tests in Task 33; `machine_snapshot(*, root: Path) -> Mapping[str, str]` (path → SHA-256) used by install/uninstall round-trip tests to prove byte-identical restoration.

- [ ] **Step 1: Tests (`test_hermetic.py`):** `test_home_is_sandboxed` (`Path.home()` is under the temp tree); `test_real_command_is_refused` (`subprocess.run(["launchctl", "list"])` raises `RealMachineAccess`); `test_marker_opt_in_allows_real_machine` (a test marked `real_machine` is skipped unless `--real-machine` is passed); `test_snapshot_detects_a_single_changed_byte`; `test_sandbox_never_leaks_between_tests` (write in one test, absent in the next).
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement** using `monkeypatch` for the environment and a patched `subprocess` entry point for the guard; the guard allows the exact interpreters and tools the suite needs (`python`, `git`).
- [ ] **Step 4: Dogfood:** run `python -m pytest tests/tools/stationer/test_installer_support.py -q` under the guard; any real-machine call it surfaces is a bug to fix now, not to allow.
- [ ] **Step 5: Add a gate** to section E: after any phase that adds installer-like code, `git status --short ~` style checks are replaced by `machine_snapshot` assertions inside the tests, and the full suite must pass with `HOME` unset to the real value (run once with `env -i`-style minimal environment and record the result).
- [ ] **Step 6: Linters; commit** `test: hermetic sandbox so tests cannot touch the real machine`.

Every later task that installs, schedules, edits a profile or a config file must use `sandbox_machine` and, for reversible actions, assert `machine_snapshot` equality after apply-then-invert. Task 33 is the only place `real_machine` is used, and only with the owner's explicit `--real-machine` flag.

### Task 7: Data model

**Files:**
- Create: `tools/maire/model/__init__.py`, `answers.py`, `request.py`, `receipt.py`
- Test: `tests/tools/maire/test_model.py`

**Interfaces:**
- Produces (frozen, slots, kw_only dataclasses; JSON-serializable):
  - `type AnswerValue = str | bool | tuple[str, ...]`; `Answers(values: Mapping[str, AnswerValue])` with `get(*, field_id: str) -> AnswerValue | None`.
  - `type Scope = Literal["user", "system"]`.
  - `DependencySpec(kind: Literal["python", "release"], name: str, constraint: str, source: str)`.
  - `InstallRequest(name: str, version: str, kind: Literal["tool", "extension"], scope: Scope, payload_dir: Path, commands: tuple[str, ...], module_path: str | None, extension_dir: str | None, interpreter: Path | None, editors: tuple[str, ...], create_aliases: bool, download_python: bool, dependencies: tuple[DependencySpec, ...])`.
  - `Effect(action: str, data: Mapping[str, str])`; `Receipt(schema: int, name: str, version: str, scope: Scope, effects: tuple[Effect, ...], pending: tuple[str, ...])`; `Receipt.load(*, path: Path) -> Receipt`, `Receipt.save(*, path: Path) -> None` (atomic), `Constants.schema_version: Final[int] = 1`, `ReceiptSchemaError`.

- [ ] **Step 1: Tests:** `test_receipt_round_trips`; `test_receipt_save_is_atomic` (a failure during write leaves the old file intact); `test_newer_maire_reads_older_schema` (schema 0 fixture upgrades on load); `test_older_maire_refuses_newer_schema` (schema 99 raises `ReceiptSchemaError` with a message containing `upgrade maire`); `test_install_request_from_install_json` (parse the spec's `install.json` fields).
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.** **Step 4: Run** tests and linters. **Step 5: Commit** `feat(maire): answers, request, receipt model`.

### Task 8: Declarative expression language

**Files:**
- Create: `tools/maire/tree/__init__.py`, `tools/maire/tree/expr.py`
- Test: `tests/tools/maire/test_expr.py`

**Interfaces:**
- Produces: nodes `Eq(field: str, value: AnswerValue)`, `In(field: str, values: tuple[str, ...])`, `HasValue(field: str)`, `Not(operand)`, `And(operands)`, `Or(operands)`; `type Expr`; `evaluate(*, expr: Expr, answers: Answers) -> bool`; `to_json(*, expr: Expr) -> JsonValue`; `from_json(*, data: JsonValue) -> Expr` (raises `ExprError` on unknown node).

- [ ] **Step 1: Tests:** one per node; `test_json_round_trip` for a nested expression; `test_from_json_rejects_unknown_node`; `test_missing_field_is_false_not_error` (`Eq("x", True)` on empty answers → False).
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement** with `match` on node type, exhaustive. **Step 4: Run** tests and linters. **Step 5: Commit** `feat(maire): declarative condition language`.

### Task 9: Wizard tree

**Files:**
- Create: `tools/maire/tree/field.py`, `page.py`, `wizard.py`, `tool_wizard.py`, `plugin_wizard.py`, `uninstall_wizard.py`
- Test: `tests/tools/maire/test_wizard.py`

**Interfaces:**
- Consumes: Task 7 `Answers`, Task 8 `Expr`.
- Produces: `FieldKind = Literal["checkbox","checklist","choice","path","text","info","summary","progress"]`; `Field(id, kind, label, default, visible_when: Expr | None, enabled_when: Expr | None, choices: str | None, path_kind: Literal["file","dir"] | None, must_exist: bool, required: bool)`; `Transition(to: str | None, when: Expr | None)`; `Page(id, title, fields, transitions)`; `Wizard(start: str, pages: tuple[Page, ...])` with `next_page(*, current: str, answers: Answers) -> str | None`, `visible_fields(*, page_id: str, answers: Answers) -> tuple[Field, ...]`, `validate(*, page_id: str, answers: Answers) -> tuple[str, ...]`; builders `tool_wizard() -> Wizard`, `plugin_wizard() -> Wizard`, `uninstall_wizard() -> Wizard`.
- Pages per the spec: tool = Welcome, Scope, Interpreter, Aliases, Summary, Progress, Done; plugin = Welcome, Scope, Editors, Summary, Progress, Done; uninstall = Confirm, Progress, Done. The Interpreter `choice` has a `download` option and a `path` field visible only when "browse" is chosen.

- [ ] **Step 1: Tests (walk the trees):** `test_tool_wizard_default_path_ends_at_done`; `test_interpreter_path_field_only_when_browse`; `test_plugin_wizard_skips_interpreter`; `test_validate_reports_missing_required_and_nonexistent_path`; `test_every_page_reachable_and_terminates` (graph check over all three wizards, no unreachable page, no cycle without exit).
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.** **Step 4: Run** tests and linters. **Step 5: Commit** `feat(maire): wizard tree and the three wizards`.

### Task 10: Discovery

**Files:**
- Create: `tools/maire/discovery/__init__.py`, `environment.py`, `interpreters.py`, `editors.py`
- Test: `tests/tools/maire/test_discovery.py`

**Interfaces:**
- Produces: `Environment(home: Path, path_entries: tuple[Path, ...], system: Literal["linux","macos","windows"], variables: Mapping[str, str])` (all ambient access injected); `Interpreter(path: Path, version: tuple[int, int, int], source: str)`; `discover_interpreters(*, environment: Environment, probe: Callable[[Path], tuple[int,int,int] | None]) -> tuple[Interpreter, ...]` (3.13+ only, deduped, sorted newest first; sources `path`, `uv`, `pyenv`, `conda`, `system`); `Editor(identifier: str, label: str, command: str | None, extensions: Path | None)`; `discover_editors(*, environment: Environment) -> tuple[Editor, ...]`.

- [ ] **Step 1: Move the editor table** (`products` in `plugins/simple_web_server/install.py`) into `editors.py` unchanged; the four install.py copies stay until Task 27.
- [ ] **Step 2: Tests** on a temp home with fake directories and fake probe results: finds uv/pyenv/conda layouts; rejects 3.12; finds editors by extension directory; reports an editor with no CLI as `command=None`.
- [ ] **Step 3: Implement** to pass. **Step 4: Run** tests and linters. **Step 5: Commit** `feat(maire): interpreter and editor discovery`.

### Task 11: Action runner (journal first, rollback)

**Files:**
- Create: `tools/maire/actions/__init__.py`, `base.py`, `runner.py`
- Test: `tests/tools/maire/test_runner.py`

**Interfaces:**
- Consumes: Task 7 model, Task 10 `Environment`.
- Produces: `Context(request: InstallRequest, environment: Environment, paths: Paths, registry: PathRegistry)`; `Paths(payload_root: Path, shim_dir: Path, receipt_dir: Path, shared_maire_dir: Path)` and `paths_for(*, scope: Scope, name: str, environment: Environment) -> Paths` (locations from the spec); `PathRegistry` Protocol (`add(*, directory: Path) -> None`, `remove(*, directory: Path) -> None`); `Action` Protocol with `name: str`, `apply(self, *, context: Context) -> Effect`, `invert(self, *, effect: Effect, context: Context) -> None`; `Runner(journal: Path)` with `run(self, *, actions: Sequence[Action], context: Context) -> Receipt` and `uninstall(self, *, receipt: Receipt, actions: Mapping[str, Action], context: Context) -> None`.
- Behavior: write each `Effect` to the journal file **before** returning from `apply` is considered complete (journal entry = intent, then outcome); on failure invert applied effects in reverse; `dry_run` flag produces a receipt with no side effects.

- [ ] **Step 1: Tests:** `test_failure_midway_rolls_back_in_reverse`; `test_dry_run_changes_nothing`; `test_apply_twice_is_idempotent`; **review-focus** `test_kill_between_actions_is_recoverable`: run the runner in a subprocess that is SIGKILLed after action 2 of 4; then `Runner.recover(journal=...)` (add this method) inverts actions 1–2 and leaves the filesystem as before.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.** **Step 4: Run** tests and linters. **Step 5: Commit** `feat(maire): reversible action runner with journal`.

### Task 12: File, alias, module and extension actions

**Files:**
- Create: `tools/maire/actions/place_payload.py`, `alias.py`, `modules.py`, `extension.py`, `path_registry.py`
- Test: `tests/tools/maire/test_actions.py`

**Interfaces:**
- Consumes: Task 11 `Action`, `Context`.
- Produces: `PlacePayload`, `CreateAlias`, `RegisterModules`, `EnableExtension` (each implements `Action`); `ManagedBlock(Action)` (a generic marker-delimited block in any text file: install, replace and remove by marker; the one implementation, reused by district's shell blocks in Task 32B) and `PosixPathRegistry` built on it (opt-in), `WindowsPathRegistry` (injected `RegistryBackend` Protocol; default uses `winreg`).
- Behavior from the spec: shims point at the frozen exe when no interpreter exists; `RegisterModules` writes a `.pth` in `site.getusersitepackages()` (user scope) or the interpreter's site-packages (system scope), pointing at the directory the `.pyz` unpacks to, and **fails the action with a clear message** if unwritable (run rolls back); `EnableExtension` copies into the editor's extensions directory exactly as `plugins/*/install.py` does today.

- [ ] **Step 1: Tests (temp dirs, fake registry backend):** each action applies and inverts to the original tree; `test_register_modules_unwritable_site_fails_clearly`; `test_posix_profile_not_edited_by_default`; `test_windows_path_added_and_removed_via_backend`; **review-focus** `test_paths_with_spaces_and_non_ascii_round_trip` parametrized over `"/tmp/jo\u00e3o lopes/"` and `"C:\\Users\\Jo\u00e3o Lopes"` for shim content, `.pth` content and the registry value.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.** **Step 4: Run** tests and linters. **Step 5: Commit** `feat(maire): payload, alias, module and extension actions`.

### Task 13: `EnsurePython` and Python sources

**Files:**
- Create: `tools/maire/actions/ensure_python.py`, `tools/maire/python/__init__.py`, `source.py`, `standalone.py`, `nuget.py`, `versions.py`
- Test: `tests/tools/maire/test_ensure_python.py`

**Interfaces:**
- Consumes: Task 2 store (`Materializer`, `load_catalog`), Task 4 `fetch`.
- Produces: `PythonSource` Protocol (`catalog_for(*, system: str, arch: str, version: str) -> Path` returning a verified partition catalog directory); `StandaloneSource` (python-build-standalone) and `NugetSource` implementing it; `PINNED: Mapping[str, PinnedPython]` in `versions.py` (version and release moved from `InstallConstants`: `3.13.17`, `20261021`, NuGet `3.13.9`, with SHA-256 digests); `EnsurePython(Action)` with the order: accept existing 3.13+ interpreter → else provision a private one into `<payload_root>/python-<version>/` and switch the `current` pointer (symlink; junction on Windows) after a smoke test (`python -c "import sys; print(sys.version_info[:3])"`).
- Offline: a missing network and no existing interpreter raises `PythonUnavailable` carrying the hint "pass --python-archive PATH".

- [ ] **Step 1: Tests with a fake `PythonSource` serving a tiny catalog:** existing interpreter is used and nothing is downloaded; missing interpreter provisions side by side and flips the pointer only after the smoke test passes; failed smoke test leaves the previous pointer untouched; inverse removes only what maire created; `--python-archive` path is honored; **review-focus** `test_wrong_digest_installs_nothing` and `test_truncated_download_leaves_no_partial_directory`.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.** **Step 4: Verify the spec's open item:** run `python - <<'PY'` that fetches the python-build-standalone release page listing for tag `20261021` over HTTPS and prints whether a `SHA256SUMS` asset exists. Record the finding in `docs/tools/maire/DOCS.md` ("Digest sources"); if absent, keep digests pinned in `versions.py` and note that evocator must obtain them another way.
- [ ] **Step 5: Run** tests and linters. **Step 6: Commit** `feat(maire): EnsurePython with partitioned interpreters`.

### Task 14: Install and uninstall services, shared maire, scopes

**Files:**
- Create: `tools/maire/services/__init__.py`, `install.py`, `uninstall.py`, `shared.py`
- Test: `tests/tools/maire/test_services.py`

**Interfaces:**
- Consumes: Tasks 7, 11, 12, 13.
- Produces: `install(*, request: InstallRequest, environment: Environment, dry_run: bool = False) -> Receipt`; `uninstall(*, name: str, scope: Scope, environment: Environment) -> None`; `plan_actions(*, request: InstallRequest) -> tuple[Action, ...]` (EnsurePython only when `request.interpreter is None and request.kind == "tool"`; for the silent path without Python, records `pending=("RegisterModules",)`); `shared.ensure_shared_maire(*, source: Path, version: str, paths: Paths) -> Effect` (install if missing or older, never downgrade; reference counted in `shared.refs.json`); `shared.release_shared_maire(*, name: str, paths: Paths) -> None` (removes the copy only at zero references).
- Receipts are named `<name>.receipt.json` in `paths.receipt_dir`; concurrent calls take `cache_lock` from Task 3.

- [ ] **Step 1: Tests:** install then uninstall restores a temp home exactly (compare directory snapshots); `test_silent_without_python_marks_module_step_pending`; `test_shared_maire_not_downgraded`; `test_shared_maire_removed_only_at_zero_refs`; `test_system_scope_uses_system_paths` (paths only, no root needed via injected environment); **review-focus** `test_two_concurrent_installs_serialize` using two processes and asserting a valid receipt file and no leftover temp files.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.** **Step 4: Run** tests and linters. **Step 5: Commit** `feat(maire): install/uninstall services and shared copy`.

### Task 14A: Privilege seam through bowman, progress reuse (decision plus adapter)

**Files:**
- Create: `tools/maire/services/elevation.py`, `docs/tools/maire/REUSE.md`
- Test: `tests/tools/maire/test_elevation.py`

**Interfaces:**
- Consumes: `tools.bowman` `PrivilegeBroker`, `ElevationGrant`, `PrivilegedOperation`, `PrivilegedRegistry`.
- Produces: `Elevator` Protocol with `ensure_privileges(*, scope: Scope, operation: str) -> None` (raises `ElevationDenied`); `RootOnlyElevator` (system scope requires already being root/admin; user scope is a no-op); `BowmanElevator(broker: PrivilegeBroker)` adapting bowman's grant flow.

- [ ] **Step 1: Read, don't guess.** Print the public signatures of `tools/bowman/elevation.py` and `elevation_uri.py`, and the imports of `tools/stationer/progress.py` (is it import-light and L0-compatible?). Write the two decisions in `REUSE.md`: how maire asks bowman for a grant, and whether maire reuses `stationer/progress.py` or defines its event type once in L0.
- [ ] **Step 2: Tests:** `RootOnlyElevator` refuses system scope when not privileged and passes for user scope; `BowmanElevator` with a fake broker requests exactly one grant per run for operation `maire.install` and surfaces denial as `ElevationDenied`; `install()` for system scope calls `ensure_privileges` before any action runs (assert via a recording elevator).
- [ ] **Step 3: Implement** and wire `services.install` to take an `Elevator` (default `RootOnlyElevator`).
- [ ] **Step 4: Linters; commit** `feat(maire): privilege seam through bowman`.

### Task 14B: Extension point for tool-specific actions

Lets other tools (district first) contribute reversible actions without maire knowing about them, and without import-time registration.

**Files:**
- Create: `tools/maire/actions/external.py`
- Modify: `tools/maire/model/request.py` (`InstallRequest.extra_actions: tuple[ActionSpec, ...]`), `tools/maire/services/install.py`
- Test: `tests/tools/maire/test_external_actions.py`

**Interfaces:**
- Produces: `ActionSpec(type: str, args: Mapping[str, str])` where `type` is `"package.module:ClassName"`; `load_action(*, spec: ActionSpec, allowed_prefixes: tuple[str, ...] = ("tools.", "plugins.")) -> Action` using `importlib.import_module`; the receipt stores `Effect.action = spec.type` so uninstall can reload the same class.

- [ ] **Step 1: Tests:** a test action loads and runs through `Runner` and is inverted on uninstall via the stored `type`; `os:system` and `builtins:eval` are refused with `ExtensionRefused`; a missing module or class raises `ExtensionError` naming it; actions run in manifest order; a failing foreign action rolls back the whole run (including earlier native actions).
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.** **Step 4: Linters; commit** `feat(maire): explicit extension point for tool-specific actions`.

### Task 15: Renderers

**Files:**
- Create: `tools/maire/render/__init__.py`, `protocol.py`, `terminal.py`, `answers.py`, `fake.py`, `driver.py`
- Test: `tests/tools/maire/test_render.py`

**Interfaces:**
- Produces: `Renderer` Protocol (`show_page(self, *, page: Page, fields: tuple[Field, ...], answers: Answers, choices: Mapping[str, tuple[Choice, ...]]) -> Answers`, `progress(self, *, label: str, fraction: float) -> None`, `finish(self, *, message: str, success: bool) -> None`); `Choice(value: str, label: str)`; `TerminalRenderer(stdin, stdout)`; `AnswersRenderer(source: Mapping[str, AnswerValue])` (raises `MissingAnswerError` for any required field not in the source — never guesses); `FakeRenderer(script: Sequence[Mapping[str, AnswerValue]])`; `drive(*, wizard: Wizard, renderer: Renderer, providers: Mapping[str, ChoiceProvider]) -> Answers`.

- [ ] **Step 1: Tests:** `drive` with `FakeRenderer` completes each wizard; `AnswersRenderer` raises on a missing required answer; `TerminalRenderer` reads scripted stdin and re-asks on a failed validation message; choices come from providers at run time.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.** **Step 4: Run** tests and linters. **Step 5: Commit** `feat(maire): renderer protocol and terminal/answers/fake renderers`.

### Task 16: maire CLI

**Files:**
- Modify: `tools/maire/cli.py`
- Test: `tests/tools/maire/test_cli.py`

**Interfaces:**
- Produces: `main(*, arguments: tuple[str, ...]) -> int` with `install --manifest install.json [--answers FILE] [--silent] [--scope user|system] [--download-python] [--python-archive PATH] [--dry-run]`, `uninstall NAME [--scope]`, `configure NAME`, `discover --json` (interpreters and editors), `wizard --manifest install.json [--ui auto|terminal|qt]`, `list` (installed receipts), `export inno|pkg` (wired in Tasks 18–19).

- [ ] **Step 1: Tests** run `main` in-process with a temp home: `install --silent` then `list` then `uninstall` round trips; `discover --json` output parses and matches `discover_*`; `--ui qt` without PySide6 falls back to terminal with a one-line notice (monkeypatched `find_spec` seam in the loader).
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.** **Step 4: Run** the whole maire suite and linters. **Step 5: Commit** `feat(maire): command-line interface`.

---

# Phase 3 — GUI and native wizards

### Task 17: PySide6 renderer (optional)

**Files:**
- Create: `tools/maire/render/qt.py`, `tools/maire/render/loader.py`
- Modify: `pyproject.toml` (`[project.optional-dependencies] gui = ["PySide6>=6.8"]`)
- Test: `tests/tools/maire/test_qt.py`

**Interfaces:**
- Produces: `QtRenderer` implementing `Renderer` (one widget class per `FieldKind`); `loader.load_qt_renderer() -> type[Renderer] | None` (uses `find_spec` then `import_module`; `None` when PySide6 is absent). Only `qt.py` imports PySide6.

- [ ] **Step 1: Tests (skipped only via `pytest.importorskip` at module top):** with `QT_QPA_PLATFORM=offscreen`, `QtRenderer` renders each of the three wizards via `drive` with a scripted interaction helper and returns the same `Answers` the `FakeRenderer` returns for the same script; `test_loader_returns_none_without_pyside6` (separate file, no skip).
- [ ] **Step 2: Run** (FAIL where PySide6 is installed). **Step 3: Implement.** **Step 4: Verify the guard:** `grep -rn "PySide6" tools --include='*.py'` lists only `tools/maire/render/qt.py` and `loader.py` (as a string).
- [ ] **Step 5: Linters, then commit** `feat(maire): optional PySide6 wizard`.

### Task 18: Inno Setup spike and exporter

**Files:**
- Create: `tools/maire/export/__init__.py`, `inno.py`, `pascal.py`; `docs/tools/maire/SPIKES.md`
- Test: `tests/tools/maire/test_export_inno.py`

**Interfaces:**
- Produces: `export_inno(*, wizard: Wizard, manifest: InstallManifest, output: Path) -> Path` (writes the `.iss`); `condition_to_pascal(*, expr: Expr) -> str`.
- Spike question (spec `[verify]`): can the generated script extract the bundled `maire` to a temp directory at wizard start and call `maire discover --json` to fill interpreter/editor pages, and run `maire install --answers` before completion?

- [ ] **Step 1: Unit tests (no Windows needed):** `condition_to_pascal` golden outputs for every node; `export_inno` golden `.iss` for the tool wizard contains one custom page per tree page with fields and the `[Run]`/`CurStepChanged` call to `maire install`.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.**
- [ ] **Step 4: Spike on the Windows UTM VM** (as `tests/tools/stationer/test_real_pipelines.py` does): compile with Inno Setup, run silently and with a scripted answers file; record in `SPIKES.md` whether helper extraction at wizard start worked and what the fallback is (spec: static wizard plus post-install `maire configure`).
- [ ] **Step 5: If the spike fails**, change `export_inno` to emit the fallback and add `test_export_inno_fallback_has_static_pages_and_configure_call`.
- [ ] **Step 6: Linters; commit** `feat(maire): export Inno Setup wizard from the tree`.

### Task 19: macOS `.pkg` spike and exporter

**Files:**
- Create: `tools/maire/export/pkg.py`; update `docs/tools/maire/SPIKES.md`
- Test: `tests/tools/maire/test_export_pkg.py`

**Interfaces:**
- Produces: `export_pkg(*, wizard: Wizard, manifest: InstallManifest, output: Path) -> Path` (writes `distribution.xml`, `postinstall`, and an uninstaller script driven by the receipt); `condition_to_js(*, expr: Expr) -> str` (only the subset Installer JavaScript supports: file-existence and choice-selected checks; anything else raises `ExportError` naming the field).

- [ ] **Step 1: Unit tests:** golden `distribution.xml` has one optional choice per detected-editor component; `postinstall` reads component marker files and calls `maire install --answers`; `ExportError` for a condition that needs dynamic discovery; uninstaller script invokes `maire uninstall`.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.**
- [ ] **Step 4: Spike on this Mac:** build with `pkgbuild`/`productbuild` into the scratch directory, inspect with `pkgutil --expand`, and install into a temp root with `installer -pkg ... -target <tmp volume>` only if no sudo is needed; otherwise stop at expansion and record the limit in `SPIKES.md`.
- [ ] **Step 5: Linters; commit** `feat(maire): export macOS pkg wizard from the tree`.

---

# Phase 4 — Stationer integration and layout

### Task 20: Layout module and path routing

**Files:**
- Create: `tools/stationer/layout.py`
- Modify (every output-path site, found by probing): `tools/stationer/builder.py` — `ArtifactUtilities.pyinstaller_folder / installer_folder` and the `pyz` folder helper (lines ~1124–1160), the `output / "pypi"` sites (~3437, ~3542), the `.work` staging dir (~1303), `MacOSBuilder` use of `context.output` (~1524); `tools/stationer/pyz_builder.py:335` (`output / archive_name(...)`); `tools/stationer/pypi_builder.py:93`; `tools/stationer/remote_builder.py` (~1141 `state.output.resolve()`, ~1304 `installer_folder`); `tools/stationer/build_report.py` (~133–136, ~392: relative `executable`/`installer` entries and `artifacts.json`). Re-run `grep -nE 'output ?/|installer_folder|pyinstaller_folder' tools/stationer/*.py` afterwards; it must show every remaining hit goes through `layout`.
- Modify: `tests/tools/stationer/test_layout.py`

**Interfaces:**
- Produces: `bin_cell(*, root: Path, os: str, arch: str, abi: str, kind: Literal["tools","plugins"], name: str) -> Path`; `pypi_cell(*, root: Path, kind, name: str) -> Path`; `lib_snapshot(*, root: Path, kind, name: str, version: str) -> Path`; `kind_of(*, project: Project) -> Literal["tools","plugins"]`.

- [ ] **Step 1: Update `test_layout.py`** to expect `build/bin/linux/amd64/glibc/tools/conjure/` and `build/bin/windows/arm64/native/plugins/simple_web_server/` and `build/lib/tools/district/1.0.1/`. Run. Expected: FAIL.
- [ ] **Step 2: Implement `layout.py`**, then replace each hard-coded path from the grep with the layout call. Existing tests that assert old paths are updated, not deleted.
- [ ] **Step 3: Run** `python -m pytest tests/tools/stationer -q`. Expected: all pass.
- [ ] **Step 4: Linters; commit** `feat(stationer): build/bin and build/lib layout`.

### Task 21: Project kinds and manifests for all 11 projects

**Files:**
- Create: `tools/stationer/projects.py`; `templates/stationer.toml` in `tools/{bowman,charter,conjure,stationer,evocator}/` and `plugins/{close_deleted_editors,devin_remote_control,open_selected_editor_panel,simple_web_server}/` (maire's exists from Task 6)
- Modify: `tools/stationer/builder.py` — extend `ProjectTable` (line ~98, accepted keys today: `name version version_file entrypoint identifier console pyinstaller_args pip_args local_dependencies`), `ProjectConfig` (~542) and `ManifestLoader.load` (~699) with the new keys `kind` (`tool`|`extension`), `payload`, `flavors`, `commands`, `module_path`, `extension_dir`, `dependencies`, `schedule`. The loader rejects unknown keys today (R16), so keys, dataclass fields and validation must change together; unknown-key rejection must still work for genuinely unknown keys (add `test_unknown_key_still_rejected`).
- Test: `tests/tools/stationer/test_projects.py`

**Interfaces:**
- Produces: `ProjectInfo(name: str, kind: Literal["tool","extension"], path: Path, version: str, dependencies: tuple[str, ...])`; `discover_projects(*, root: Path) -> tuple[ProjectInfo, ...]` (the 11 projects); `build_order(*, projects: tuple[ProjectInfo, ...]) -> tuple[ProjectInfo, ...]` (`maire`, `evocator` first, then dependency order, deterministic); extension version read from `src/package.json`.

- [ ] **Step 1: Tests:** `test_discovers_eleven_projects`; `test_build_order_puts_maire_and_evocator_first`; `test_extension_version_from_package_json`; `test_district_depends_on_bowman_bundled_not_a_release_dependency`.
- [ ] **Step 2: Run** (FAIL). **Step 3: Write the manifests** modeled on `tools/district/templates/stationer.toml`; extensions set `kind = "extension"`, `payload = ["vsix", "src"]`, no entrypoint. **Step 4: Implement** `projects.py` and loader support.
- [ ] **Step 5: Run** the stationer suite and linters; commit `feat(stationer): project kinds and manifests for all 11 projects`.

### Task 22: Multi-project build configuration

**Files:**
- Modify: `pyproject.toml` (`[tool.stationer.builder]` gains `projects = ["all"]`; keep `project`/`manifest` working as a single-project override), `tools/stationer/config.py` or the `BuildConfiguration` loader in `builder.py`, `tools/stationer/cli.py`
- Test: `tests/tools/stationer/test_configured.py` (extend)

**Interfaces:**
- Produces: `--projects NAME[,NAME…]|all`; the builder loops `build_order(...)` and calls the existing per-project path for each, stopping at the first failure unless `--keep-going`.

- [ ] **Step 1: Tests:** configured `projects = ["all"]` plans 11 builds in `build_order` using a fake builder; a single `--projects district` behaves exactly as today; `--keep-going` continues after a failure and reports it.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.** **Step 4: Run** the stationer suite and linters; commit `feat(stationer): build all projects in dependency order`.

### Task 22A: Route stationer's execution through conjure

**Files:**
- Modify: `tools/stationer/remote_builder.py` (host-side `subprocess_run` calls at ~1414, ~1455, ~1505, ~1692 and the `CompletedProcess`/`TimeoutExpired` imports at lines 21–22), `tools/stationer/builder.py` (`DockerSteps` / `LinuxDockerBuilder`, ~1716–2128: confirm how `docker` is invoked and move any direct call to `DockerRunner`), `tools/stationer/conjure.py` (keeps only job/result types; no process launching)
- Test: `tests/tools/stationer/test_remote_builder.py`, `test_builder.py` (existing; use `tests/tools/stationer/fake_hosts.py` fakes)

**Interfaces:**
- Consumes: `tools.conjure` `DockerRunner`, `UtmRunner`, `LocalRunner`, `AttachedVm`, `CommandResult`, `SettingsLoader`. Before writing code run `python - <<'PY'` printing `inspect.signature` of each runner's `run`/`exec`/`copy` methods and record them in `PROGRESS.md`, so the adapter below uses real signatures.
- Produces: no new public abstraction unless two call sites need the same wrapper; if so, one `tools/stationer/hosts.py` with `run_on(*, host: HostKind, command: tuple[str, ...], timeout: int) -> CommandResult` where `HostKind = Literal["local","docker","utm","qemu"]`.

- [ ] **Step 1: Tests first (existing suites are the safety net):** run `python -m pytest tests/tools/stationer -q` and record the count. Add `test_no_direct_process_launch_in_stationer`: scans `tools/stationer/*.py` and fails if any imports `subprocess` for Docker/UTM/QEMU purposes, allowing only the `git`/`gh` helpers in `git.py` and `providers.py`.
- [ ] **Step 2: Run** the new test. Expected: FAIL naming `remote_builder.py`.
- [ ] **Step 3: Replace** each direct call with the matching conjure runner; keep timeouts and error mapping (`TimeoutExpired` → `SshTimeoutError`/`RemoteCommandError` from `tools.conjure.errors`). One behavior-preserving change per call site, running the suite after each.
- [ ] **Step 4: Run** the full stationer suite. Expected: same pass count as Step 1 plus the new test; the Windows-VM tests stay gated as today.
- [ ] **Step 5: Linters; commit** `refactor(stationer): run all hosts through conjure`.

Serial after Task 22, before Task 23 (all of 20–24 edit `builder.py`).

### Task 23: `install.json`, frozen `maire` in installers, new package scripts

**Files:**
- Modify: `tools/stationer/installer_support.py` (remove `ensure_python_sh`, `ensure_python_ps1`, `InstallConstants.pbs_*`/`nuget_python` after Task 13 owns them; replace `deb_postinst`, `apk_post_install`, `pkg_postinstall`, the Inno template under `tools/stationer/templates/`), `tools/stationer/builder.py` (installer assembly)
- Create: `tools/stationer/install_manifest.py`
- Modify (installer call sites, found by probing): `tools/stationer/builder.py` — macOS `pkgbuild` assembly and `postinstall` writer (~1396–1522, uses `support.pkg_postinstall`), the Docker DEB script (~1842–1874: `cp postinst`, `dpkg-deb --build`) and `files["postinst"]` (~2124), the Inno `[Run]` flags (~2230); `tools/stationer/remote_builder.py` — worker commands for Inno (`ISCC.exe`, ~545–560), DEB (~583–616), PKG (~644–678), file maps (~757–767) and `_inno_script` (~777). Delete `tools/stationer/templates/ensure-python.sh` and `ensure-python.ps1` only after both builders stop referencing them (`grep -rn ensure-python tools tests` must print nothing).
- Test: `tests/tools/stationer/test_installer_support.py` (update), `tests/tools/stationer/test_install_manifest.py`

**Interfaces:**
- Produces: `build_install_manifest(*, project: ProjectInfo, version: str, platform: str, payload_names: tuple[str, ...]) -> Mapping[str, JsonValue]` (the fields `InstallRequest` reads, plus `dependencies`: a `python` entry for tools, a `release` entry for `maire`); `deb_postinst(*, name: str) -> str` (runs `maire install --manifest /usr/share/<name>/install.json --silent --scope system`), `deb_prerm(*, name: str) -> str`, `apk_post_install`, `apk_pre_deinstall`, `pkg_postinstall`, `pkg_uninstaller`; Inno and pkg wizards come from `maire export`.

- [ ] **Step 1: Update tests first** to assert: scripts call `maire` and no longer contain `ensure-python`; `install.json` validates against `InstallRequest` parsing (Task 7) for a tool and for an extension; the extension manifest has no `commands`.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.** **Step 4: Build one DEB in the existing Docker path** (`LinuxDockerBuilder`) for `maire` itself and inspect with `dpkg-deb -c` / `-I`; expected: contains `install.json`, the frozen `maire`, the payload, `postinst`, `prerm`.
- [ ] **Step 5: Run** the stationer suite and linters; commit `feat(stationer): installers carry a frozen maire and install.json`.

### Task 24: Frozen `maire` flavors

**Files:**
- Modify: `tools/maire/templates/stationer.toml` (two entrypoints), create `tools/maire/maire_entry.py`, `tools/maire/maire_wizard_entry.py`
- Modify: `tools/stationer/builder.py` (a manifest may declare `flavors`; the `wizard` flavor adds `pip_args = ["PySide6"]` and `--collect-submodules PySide6` and is built for Linux glibc only)
- Test: `tests/tools/stationer/test_flavors.py`

**Interfaces:**
- Produces: `ProjectConfig.flavors: Mapping[str, FlavorConfig]`; artifacts named `maire` and `maire-wizard`; `maire-wizard` appears only under `build/bin/linux/*/glibc/…`.

- [ ] **Step 1: Tests:** a manifest with two flavors yields two build jobs; the small flavor's job never includes `PySide6` in its pip arguments; `maire-wizard` is skipped on macOS, Windows and musl; both flavors collect `tools/stationer/partition/zstd_portable/bin/*.zip` as data (frozen builds need the native zstd archives, since `EnsurePython` uses the partition engine) and a frozen build can run `Zstd` round trip.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.** **Step 4: Linux Docker build** both flavors; verify `maire --help` and, for `maire-wizard`, `QT_QPA_PLATFORM=offscreen maire-wizard --ui qt --help`. Record the size difference in `docs/tools/maire/DOCS.md`.
- [ ] **Step 5: Linters; commit** `feat(stationer): maire small and wizard flavors`.

### Task 25: `build/lib` release snapshots

**Files:**
- Create: `tools/stationer/snapshot.py`
- Modify: `tools/stationer/builder.py` or `engine.py` (call at release time), `tools/stationer/build_report.py`
- Test: `tests/tools/stationer/test_snapshot.py`

**Interfaces:**
- Produces: `write_snapshot(*, root: Path, project: ProjectInfo, version: str, force: bool = False) -> Path` — copies the `build/bin` outputs for that project and version, writes `SHA256SUMS.txt`, `artifacts.json`, and the partition `catalog`/`packs` for bundled components (Task 5); raises `SnapshotConflict` if the version exists with any different digest unless `force`.

- [ ] **Step 1: Tests:** snapshot of a fake bin tree contains the copies and a correct `SHA256SUMS.txt`; rewriting the same content succeeds; changing one byte raises `SnapshotConflict`; `force=True` replaces.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.** **Step 4: Run** the stationer suite and linters; commit `feat(stationer): immutable release snapshots in build/lib`.

### Task 26: Release configuration for all projects

**Files:**
- Modify: `release.toml` (a `[projects.<name>]` for each of the other 10, `version_files` per project: tools use their `product.py`/`__init__`-level version file as `tools/district/product.py` does; plugins use `src/package.json`), `pyproject.toml` (`[tool.stationer.publish] projects`)
- Create: `.github/workflows/release.yml` (generalized from `district-release.yml`; matrix over projects and over the six existing runners). **Keep `district-release.yml` in this task** (R13); delete it in a separate follow-up commit only after a dry run of `release.yml` has produced the same assets as District's last release.
- Test: `tests/tools/stationer/test_planner.py` (extend)

**Interfaces:**
- Consumes: `read_version`/`replace_version` in `tools/stationer/versioning.py`; for `package.json` add `read_package_json_version(*, text: str) -> str` and `replace_package_json_version(*, text: str, old: str, new: str) -> str` next to them.

- [ ] **Step 1: Tests:** planning a release for each of the 11 projects produces tag `<project>/v<version>`; JSON version read/replace round-trip preserves formatting of the file.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement the versioning helpers and fill `release.toml`.**
- [ ] **Step 4: Write `release.yml`:** the matrix mirrors the `build` job in `district-release.yml` (macos-14, macos-15-intel, ubuntu-latest, ubuntu-24.04-arm, windows-latest, windows-11-arm), takes the project from the pushed tag prefix, builds in `build_order`, and uploads `build/lib/<kind>/<name>/<version>/` contents as release assets.
- [ ] **Step 5: Validate:** `uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"` exits 0 (pyyaml is not installed here), and run `python -m tools.stationer plan --project maire` (dry) showing tag `maire/v<version>`.
- [ ] **Step 6: Commit** `feat(stationer): release configuration and workflow for all 11 projects`.

### Task 27: Remove legacy

**Files:**
- Delete: `plugins/*/install.py` (4 files; confirm nothing imports them first with `grep -rn "install import\|install.py" plugins tests tools docs`), the ensure-python template files under `tools/stationer/templates/`, legacy `build/` contents (`build/{linux,macos,windows,pypi}`, `build/artifacts 2.json`, old `BUILD.md`/`SHA256SUMS`/`artifacts.json`)
- Modify: plugin `__main__.py` files if they call `install.py`; `tools/stationer/build_report.py` (BUILD.md describes the new layout and current rebuild commands); docs under `docs/plugins/`, `docs/tools/stationer/`

- [ ] **Step 1: Run** the full suite as a baseline: `python -m pytest -q`. Record the pass count.
- [ ] **Step 2: Replace** each plugin's `python install.py` instruction in its README and `__main__` with `maire install`/`maire uninstall`; delete the four copies.
- [ ] **Step 3: Add** `test_build_report_describes_new_layout` asserting `BUILD.md` rebuild commands use `--projects` and paths under `build/bin`.
- [ ] **Step 4: Remove** the legacy `build/` outputs (they are generated; list what is deleted in the commit message).
- [ ] **Step 5: Run** the full suite and linters; expected: same pass count as Step 1 plus new tests, no failures.
- [ ] **Step 6: Commit** `refactor: remove legacy installers and legacy build outputs`.

---

# Phase 5 — evocator

### Task 28: Scaffold and extract releases source, policy, state

**Files:**
- Create: `tools/evocator/` (scaffold like Task 6), `policy/__init__.py` `policy.py`, `state/__init__.py` `state.py`, `source/__init__.py` `github.py`, `templates/stationer.toml`; docs. **No** `version.py`, HTTP or platform code: those are imported from their owners (`stationer.versioning`, `partition.fetch`, `partition.target`, `stationer.naming`).
- Test: `tests/tools/evocator/test_policy.py`, `test_state.py`, `test_github.py` (version ordering and platform tags are tested at their owners in Task 5A); move the update tests from `tests/tools/district/` (find them: `grep -ln "update" tests/tools/district/*.py`) as golden tests

**Interfaces:**
- Produces (extracted from `tools/district/update.py` with behavior preserved): `UpdatePolicy.load(*, home: Path, project: str) -> UpdatePolicy` (cadences `always|hourly|daily|weekly|never`, channels `stable|prerelease`, repository allowlist, `timeout_seconds`); `UpdateState.load/save`; `Release(tag, version, url, prerelease, assets)`; `Asset(name, url)`; `latest_release(*, policy: UpdatePolicy, project: str) -> Release | None` (HTTP through `partition.fetch.fetch_json`, version ordering through `stationer.versioning.is_newer`, tags through `stationer.naming.parse_tag`); `select_asset(*, release: Release, project: str, platform: Platform) -> Asset | None` (name from `stationer.naming.asset_name`, tag from `partition.target.release_tag`). No `parse_version`, `is_newer` or `platform_tag` exist in evocator.

- [ ] **Step 1: Port the district update tests** to the new module paths; add a fake GitHub server fixture (`tests/tools/evocator/fake_github.py`). Run. Expected: FAIL.
- [ ] **Step 2: Add the review-focus tests:** `test_ignores_tags_with_other_prefix`; `test_ignores_malformed_versions`; `test_prerelease_ignored_on_stable_channel`; `test_rate_limit_403_returns_none_and_records_backoff`; `test_empty_release_list_is_not_an_error`; `test_repository_outside_allowlist_is_refused`.
- [ ] **Step 3: Implement** by moving only the release, policy and state logic (the HTTP, version, platform and asset-name code is **not** copied: import `partition.fetch`, `stationer.versioning.is_newer`, `partition.target.release_tag` and `stationer.naming.asset_name`), replacing `District`-specific constants with per-project parameters, removing the `district` names from state paths (`~/.evocator/`).
- [ ] **Step 4: Run** tests and linters; **Step 5: Commit** `feat(evocator): generic release source, policy and state`.

### Task 29: Verification and apply (user and system scope)

**Files:**
- Create: `apply/__init__.py`, `apply/user.py`, `apply/system.py`, `apply/swap.py`
- Test: `tests/tools/evocator/test_verify.py`, `test_apply.py`

**Interfaces:**
- Consumes: Task 28, Task 14 `install`/`uninstall`, Task 3 `cache_lock`.
- Produces (digest parsing is `partition.checksums.expected_digest`, reused, not re-implemented here): `verified_download(*, release: Release, asset: Asset, directory: Path, fetch_bytes: Callable) -> Path | None` (refuses when `SHA256SUMS.txt` is missing or the digest differs, as `district` does today); `apply_user(*, name: str, bundle: Path, environment: Environment) -> Receipt` (new receipt installed through `maire`, old receipt inverted, rollback on failure of a post-update check `<tool> --version`); `apply_system(*, name: str, installer: Path, system: str, runner: CommandRunner) -> None` (invokes `dpkg -i` / `apk add --allow-untrusted` / `installer -pkg` / silent `-setup.exe` through an injected runner).

- [ ] **Step 1: Tests:** wrong digest installs nothing; missing `SHA256SUMS.txt` refuses; failing post-update check restores the previous version and receipt; system apply calls the right command per `system` through a recording runner; **review-focus** `test_manual_update_during_scheduled_update_serializes` (two processes, `cache_lock`, single winner).
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.** **Step 4: Verify the `.apk` open item:** in an Alpine container (`docker run --rm alpine:3 sh -c "apk add --allow-untrusted /pkg.apk"` using a package built by Task 23) record in `DOCS.md` whether it installs; if it cannot, mark system-scope Alpine as notify-only and add `test_apk_system_scope_is_notify_only`.
- [ ] **Step 5: Run** tests and linters; commit `feat(evocator): verified download and scoped apply`.

### Task 30: Dependencies (`python`, `release`) via partition deltas

**Files:**
- Create: `tools/evocator/deps/__init__.py`, `python.py`, `release.py`, `check.py`
- Test: `tests/tools/evocator/test_deps.py`

**Interfaces:**
- Consumes: Task 2 `write_delta`/`apply_delta`/`rollback`, Task 13 `PythonSource`, Task 14 shared maire.
- Produces: `DependencyUpdate(spec: DependencySpec, installed: str, available: str)`; `check_dependencies(*, receipts: tuple[Receipt, ...], sources: Mapping[str, DependencySource]) -> tuple[DependencyUpdate, ...]` (honors `constraint`: a `3.13.x` Python never reports 3.14); `apply_dependency(*, update: DependencyUpdate, environment: Environment) -> None` (new side-by-side directory, smoke test, pointer switch, previous retained until unused); a dependency shared by several packages is applied once and recorded against each receipt.

- [ ] **Step 1: Tests:** constraint blocks a minor jump; update applies as a delta (only new packs fetched, asserted on a recording source); failed smoke test keeps the old pointer; shared dependency updated once; **review-focus** `test_interrupted_download_resumes_clean` and `test_digest_mismatch_aborts_without_touching_current`.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.** **Step 4: Run** tests and linters; commit `feat(evocator): non-bundled dependency updates`.

### Task 31: Scheduler action

**Files:**
- Create: `tools/maire/actions/schedule.py`, `tools/evocator/schedule/__init__.py`
- Modify: `tools/maire/services/install.py` (plan includes `ScheduleTask` when the manifest declares `schedule`)
- Test: `tests/tools/maire/test_schedule.py`

**Interfaces:**
- Produces: `ScheduleTask(Action)` writing a launchd agent (macOS), systemd user timer (Linux), or Task Scheduler entry (Windows) for user scope, and the system-level equivalents for system scope; the command is `evocator check --apply --scope <scope>`; interval from the policy cadence; backends injected (`launchctl`, `systemctl`, `schtasks` through `CommandRunner`).

- [ ] **Step 1: Tests:** golden plist/unit/XML per platform; apply then invert removes the file and calls the unload command; user scope never targets system paths and vice versa.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.** **Step 4: Run** tests and linters; commit `feat(maire): scheduled update task action`.

### Task 32: evocator CLI, self-update, district migration

**Files:**
- Create: `tools/evocator/cli.py`, `apply/self_update.py`
- Modify: `tools/district/cli.py`, `tools/district/core.py` (the update notice/refresh calls), delete `tools/district/update.py`; `tools/district/templates/stationer.toml` (`local_dependencies = ["../bowman", "../evocator"]`)
- Test: `tests/tools/evocator/test_cli.py`; update `tests/tools/district/` update-related tests to assert delegation

**Interfaces:**
- Produces: `main(*, arguments: tuple[str, ...]) -> int` with `check [NAME]`, `update [NAME] [--version V]`, `list`, `policy`, `self-update`, `--apply`, `--scope`; `district --update`/`--check-updates` call `evocator` library functions and keep their existing output text.

- [ ] **Step 1: Tests:** `check` prints available updates for installed receipts using the fake GitHub server; `update` applies and exits 0; `self-update` updates `evocator` through the shared-copy rule; district's `--check-updates` produces the same text as before (golden from the old tests).
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement the CLI; replace district's use of `update.py`; delete `tools/district/update.py`.**
- [ ] **Step 4: Run** `python -m pytest tests/tools/district tests/tools/evocator -q` and linters. Expected: pass, 0, 0, and `wc -l tools/district/update.py` reports no such file.
- [ ] **Step 5: Commit** `refactor(district): delegate updates to evocator`.

---

### Task 32A: Map District's installer onto maire (analysis only, no code)

`tools/district/installer.py` (2,809 lines) already contains a layout, a manifest, launchers, an interactive wizard, shell-profile blocks, agent-hook installers, MCP registration and a URI handler. Most of it duplicates what maire now owns; some of it is genuinely District-specific.

**Files:**
- Create: `docs/tools/district/INSTALLER_MIGRATION.md`

- [ ] **Step 1: Inventory.** `grep -n "^def \|^class " tools/district/installer.py tools/district/platform.py tools/district/startup.py tools/district/mcp_registration.py tools/district/integration.py` and `grep -ln "installer\|platform" tests/tools/district/*.py`. Record test file names.
- [ ] **Step 2: Fill the disposition table** (one row per public function/class) with exactly one of: *moves to maire core*, *becomes a District `Action`*, *stays in District*, *deleted*. Starting hypotheses, to confirm by reading:
  - `Layout`, `ManifestData`, `read_manifest/write_manifest/manifest_layout`, `default_bin_dir/default_lib_dir/windows_root`, `validated_alias`, `write_launcher/install_launcher/remove_launcher/owned_launcher` → maire (`PlacePayload`, `CreateAlias`, `Receipt`).
  - `wizard`, `wizard_frame`, `wizard_toggle`, `wizard_edit`, `read_key`, `prompt_alias`, `Reporter` → maire terminal renderer and the tool wizard.
  - `install_block/remove_block`, `routing_block`, `install_zsh/bash/powershell` + `remove_*`, `install_routing/remove_routing` → maire `ManagedBlock` (mechanism) plus District-owned content.
  - `install_claude/codex/cursor/devin` + `remove_*` + `hook_installed` and `install_mcp/remove_mcp/mcp_installed` and `install_uri_handler/remove_uri_handler` → District `Action` classes (District-specific content), registered through the Task 14B extension point; URI handling continues to use bowman.
  - `install_tmux/install_zsh` (package-manager installs) → District `Action` needing privileges through the Task 14A seam.
  - `components`, `resolve_selection`, `csv`, `resolve_mode`, `Installer`, `main` → District keeps the component names and CLI flags; the table becomes `install.json` actions and wizard checklist items.
  - `platform.py`: check overlap with `partition.target` and keep only what is District-specific.
- [ ] **Step 3: List behavior that must not change:** CLI flags, component names, alias rules, live-vs-copy modes, marker strings in user files (so existing installs still uninstall cleanly). The existing installer tests are the oracle.
- [ ] **Step 4: Estimate** the migration (functions moved, tests affected) and put the estimate in the document. No code in this task; commit the document `docs(district): installer migration map`.

### Task 32B: Migrate District's installer onto maire

**Files:**
- Create: `tools/district/install_actions.py` (the District `Action` classes from 32A)
- Modify: `tools/district/installer.py` (reduced to component definitions and the CLI entry), `tools/district/cli.py` (unchanged flags, now delegating), `tools/district/templates/stationer.toml`
- Test: existing `tests/tools/district/` installer tests (unchanged) plus `tests/tools/district/test_install_actions.py`

**Interfaces:**
- Consumes: Task 12 `ManagedBlock`, `CreateAlias`, `PlacePayload`; Task 14A `Elevator`; Task 14B `ActionSpec`; Task 14 `install`/`uninstall`.
- Produces: `ClaudeHook`, `CodexHook`, `CursorHook`, `DevinHook`, `McpRegistration`, `UriHandler`, `ShellLifecycleBlock`, `AgentRouting`, `TmuxInstall` (each implements `Action`, one test file section each).

- [ ] **Step 1: Baseline:** run `python -m pytest tests/tools/district -q`; record the count.
- [ ] **Step 2: Tests first for each Action** against a temp `HOME` with golden config files: apply produces the same bytes as today's `install_*`; invert restores the pre-install bytes; the marker strings are identical to today's so an **existing** install is recognized and removable.
- [ ] **Step 3: Implement the Actions** by moving the bodies of the matching functions, not rewriting them; mechanism (block editing, JSON entry add/remove) comes from maire.
- [ ] **Step 4: Rewire `Installer`/`main`** to build an `InstallRequest` with `extra_actions` and call `maire.services.install/uninstall`; delete the moved layout, manifest, launcher, wizard and block code. Run `python -m pytest tests/tools/district -q`. Expected: baseline count plus the new tests, no failures.
- [ ] **Step 5: Shrink the allowlist** in `architecture_allowlist.toml` for the entries this task retires; run `tests/tools/test_architecture.py`.
- [ ] **Step 6: Linters; commit** `refactor(district): installer runs on maire`.

### Task 32C: Retire remaining duplicates (owner-gated; do not start without the owner's go-ahead)

Touches `charter`, `conjure`, `stationer` and `district`, so it needs explicit approval.

- [ ] **Step 1: Print the allowlist** (`tests/tools/architecture_allowlist.toml`); each remaining entry is one duplicate.
- [ ] **Step 2: Work one group per commit, running that tool's suite after each:** `JsonValue` copies → `partition.json_types`; file-digest `sha256(` sites → `checksums`/`store.hash_file`; platform sniffing → `target.host_platform`; config writes → `atomic_bytes`; `remote_builder.installer_name` → `naming`; evaluate `charter/http.py` against `partition.fetch` and record the decision (keep if its job is provider API calls with methods and headers that `fetch` should not grow to cover).
- [ ] **Step 3: Done when** the allowlist is empty or every remaining entry has a one-line justification approved by the owner.

---

# Phase 6 — Verification and documentation

### Task 33: End-to-end verification

**Files:**
- Create: `tests/tools/stationer/test_end_to_end.py` (marked to run only when Docker / the UTM VM are configured, following `test_real_pipelines.py`)

- [ ] **Step 1: Local (this Mac, user scope):** build `maire` and `district`, run `maire install --manifest <install.json> --silent --scope user` into a temp `HOME`, run `district --version` through the created alias, then `maire uninstall`; assert the temp `HOME` is byte-identical to its pre-install snapshot.
- [ ] **Step 2: Linux Docker:** build the glibc DEB for `district`; in a clean container run `dpkg -i`, check `district --version`, then `apt-get remove` and verify no files remain under the receipt's recorded paths. Repeat the musl APK with the small flavor.
- [ ] **Step 3: Windows VM:** run the Inno installer silently with an answers file, check the alias on `PATH`, run the uninstaller, check the registry `PATH` value is restored.
- [ ] **Step 4: Update flow:** with the fake GitHub server serving a newer `district`, run `evocator update district --scope user`; assert the new version runs and a forced failing post-check restores the old one.
- [ ] **Step 5: Record results** (commands and outputs) in `docs/tools/maire/VERIFICATION.md`; anything that could not run is listed there as not run, not as passed.
- [ ] **Step 6: Commit** `test: end-to-end installer and update verification`.

### Task 34: Documentation and gates

**Files:**
- Modify: `README.md`, `tools/README.md`, `docs/tools/README.md`, `wiki/` pages for stationer (partition, layout), maire, evocator
- Create: `docs/tools/stationer/PARTITIONS.md`

- [ ] **Step 1: Write** the docs: maire (core, wizard language, renderers, exporters, flavors), evocator (policy, dependencies, scheduling), stationer (layout, partition commands, project kinds).
- [ ] **Step 2: Run the full gates** and capture output: `ruff check .`, `ruff format --check .`, `basedpyright`, `python -m pytest -q`, `node --check` on all JS in `plugins/*/src` and `tools/district` (unchanged files must still pass), `npm test` if defined in `package.json`.
- [ ] **Step 3: Expected:** 0 ruff diagnostics, 0 basedpyright diagnostics, all tests pass (skips only for unavailable Docker/VM/PySide6, each listed in the report).
- [ ] **Step 4: Commit** `docs: maire, evocator, partitions and layout`.

---

## Self-Review

**Spec coverage.** maire layers (Tasks 7–9, 15), actions incl. `EnsurePython` (11–13), shared copy and scopes (14), renderers (15, 17), exporters and spikes (18–19), CLI (16), Linux packages and silent path (14, 23), frozen flavors (24), layout and snapshots (20, 25), manifests and kinds for 11 projects (21–22), release config (26), legacy removal (27), partition engine (1–5, used in 13, 25, 30), evocator source/policy (28), verification and apply, scoped (29), dependencies (30), scheduling (31), CLI/self-update/district migration (32), verification and docs (33–34). Review Focus items map to Tasks 11, 12, 13, 14, 28, 29, 30.

**Open spec items that become explicit tasks, not assumptions:** python-build-standalone checksum availability (Task 13 step 4), unsigned `.apk` installs (Task 29 step 4), Inno helper-extraction and macOS `.pkg` limits (Tasks 18–19).

**Type consistency checked:** `Answers`, `InstallRequest`, `Receipt`, `Context`, `Paths`, `Action`, `Runner`, `Renderer`, `Wizard`, `PythonSource`, `ProjectInfo`, `Release`, `Asset` keep one definition and are referenced by the same names in later tasks.

**Proportion:** the plan states interfaces, test names and assertions, and the specific values the spec pins; implementation bodies are left to the implementer except where the spec fixes behavior.
