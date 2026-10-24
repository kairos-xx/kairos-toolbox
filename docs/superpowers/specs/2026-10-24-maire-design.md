# maire and evocator — design

Date: 2026-10-24. Status: maire approved by the owner; the rest drafted.
Scope **[decided]**, one plan covering all of:
1. `tools/maire` (installer engine, wizard, Python provisioning).
2. `tools/evocator` (updates for releases and for non-bundled dependencies).
3. Stationer installer integration (the four native package formats built
   around a frozen `maire`).
4. A partition engine inside `tools/stationer`, ported from the owner's
   Exactus package and made agnostic (see "Partitioned tool bundles").
5. The `build/bin` + `build/lib` layout, for all 11 projects: the 5 existing
   tools (bowman, charter, conjure, district, stationer), the 2 new tools
   (`maire`, `evocator`) and the 4 plugins.

Marking convention: **[decided]** was stated by the owner. **[call]** is a
design call made while writing this and open to change. **[verify]** needs a
spike before it is relied on.

## Why

Every tool and plugin in this repo is meant to ship as a native installer
(`.pkg`, `.deb`, `.apk`, `-setup.exe`), and today each format carries its own
ad-hoc logic. Stationer's installers already bundle the frozen executable, the
`.pyz`, a shim and an ensure-python script. The four plugin `install.py` files
are byte-identical 360-line copies that already discover VS Code-compatible
editors. District has its own 1,150-line updater.

The owner wants installers that:

1. Install and **uninstall**.
2. Put the `.pyz` built by PyInstaller in a safe place for the OS.
3. Create the CLI aliases.
4. Make the tool's modules importable on a Python interpreter the user picks.
5. For plugins, find the VS Code-compatible editors on the machine and let the
   user choose which ones receive the extension.
6. Present all of this as a wizard, native on each OS.

`maire` is the single engine behind all of that. The package formats become
thin shells that carry a frozen `maire` and call it.

## Goals

- One typed, UI-free core that performs install and uninstall for tools and
  plugins and records what it did.
- A wizard described as **data** (pages, fields, conditions, transitions),
  independent of any toolkit. **[decided]**
- A small widget vocabulary (checkbox, path input, ...) that renderers
  implement. **[decided]**
- Renderers: PySide6 wizard (Linux), terminal, answers-file (silent), test
  fake. **[decided]** for PySide6; the rest **[call]**.
- Windows and macOS native wizards are **generated and driven by maire**
  from the same page tree. **[decided]**
- `.deb` and `.apk` install silently through a thin layer that hosts the real
  installer; the wizard runs afterwards. **[decided]**
- `maire` ships as a full tool: tests, docs, builds, its own installer,
  matching `tools/conjure` and `tools/district`.

## Non-goals

- Not a package manager. No dependency resolution, no repositories.
- No release fetching. Finding and downloading new tool or plugin releases
  is `evocator`'s job; it calls `maire` to install what it downloaded. The
  only network access `maire` has is downloading a Python interpreter when
  none qualifies (see "Python-free host and Python provisioning").
- Does not replace `dpkg`/`apk`/`installer`; it is called by them.
- No custom theming or branding system for the wizard.

## Anti-goals (a technical success that is still a failure)

- An install that leaves a half-finished state when it fails midway.
- An uninstall that leaves an alias, a `.pth` entry or an editor extension
  behind.
- The core importing any GUI toolkit.
- PySide6 landing in the `.pyz` of tools that do not ship the wizard.

## Constraints

- Python 3.13+. `AGENTS.md` applies in full: Ruff and BasedPyright at zero
  diagnostics, keyword-only calls, typed locals, no suppressions, top-level
  imports only.
- Repo license is MIT. PyQt6 (GPLv3) was rejected; **PySide6 (LGPL)** is the
  toolkit. **[decided]**
- PySide6 is an **optional extra** (`maire[gui]`), included only in the
  frozen build that ships the Linux wizard and absent from every other
  build. **[decided]** Because `AGENTS.md` forbids function-local imports
  and requires optional imports to expose capability state (PY-IMP-0010), the
  Qt code lives in one module with top-level PySide6 imports, located with
  `importlib.util.find_spec` and loaded with `importlib.import_module`, as
  district already does for other modules. If PySide6 is missing, `maire`
  falls back to the terminal renderer. **[call]**
- Stationer's `.pyz` bootstrap unpacks once because compiled dependencies
  cannot be imported from a zip; the Qt build relies on that.

## Architecture

Three layers, no upward imports.

### 1. Data model

Typed, frozen dataclasses. **[call]** (PY-DAT-0001/0002.)

- `Answers`: map of field id to typed value.
- `InstallRequest`: built from `Answers`; names the payload, scope, chosen
  interpreter, aliases and editors.
- `Receipt`: ordered, invertible record of every effect, stored as JSON with a
  schema version.

### 2. Actions (the core)

Each action is a small reversible unit with `apply`, `invert` and a receipt
entry. **[call]**

| Action | Effect | Inverse |
|---|---|---|
| `EnsurePython` | find an interpreter that qualifies (3.13+); if none, download and verify a private one into the install directory | remove only what `maire` itself downloaded |
| `PlacePayload` | copy the `.pyz` (and frozen exe) to the per-OS safe location | remove it |
| `CreateAlias` | shim for each CLI command on `PATH` | remove shim |
| `RegisterModules` | make the modules importable on the chosen interpreter by writing a `.pth` into its `site-packages` | remove `.pth` |
| `EnableExtension` | copy the extension into a chosen editor's extensions directory (behaviour of today's `install.py`) | remove it |

Requirements: idempotent, supports dry run, rolls back already-applied actions
if a later one fails, and uninstall replays the receipt in reverse.

Receipt location depends on install scope. **[call]** Per-user:
`~/.local/share/maire` (Linux), `~/Library/Application Support/maire`
(macOS), `%LOCALAPPDATA%\maire` (Windows). System-wide (root/admin):
`/var/lib/maire`, `/Library/Application Support/maire`, `%ProgramData%\maire`.

Discovery providers (also UI-free): installed Python interpreters (PATH,
uv, pyenv, conda, system) and installed VS Code-compatible editors. The
editor table from the current `install.py` moves here once, replacing the four
copies.

### 3. Wizard tree (decision tree + widgets)

The wizard is data. **[decided]**

- `Page`: id, title, ordered `Field`s, and transition rules.
- `Field`: id, kind, label, default, validators, `visible_when` /
  `enabled_when` conditions over earlier `Answers`.
- Field kinds (the widget abstraction): `Checkbox`, `CheckList`, `Choice`,
  `Path` (file or directory, must-exist flag), `Text`, `Info`, `Summary`,
  `Progress`.
- Transitions are pure functions over `Answers`, so any path through the
  wizard is testable without a UI.
- Choices that depend on the machine come from discovery providers at
  run time.
- Tool wizard pages (**[call]**): Welcome, Scope, Interpreter (pick a
  detected one, browse to one, or download a private Python), Aliases,
  Summary, Progress, Done. Plugin wizard pages: Welcome, Scope, Editors
  (checklist), Summary, Progress, Done. Uninstall: Confirm, Progress, Done.

### Renderers

One small Protocol owned by the core (PY-PRO-0001): show a page and return
`Answers`; report progress; finish.

- `terminal`: prompts; the CLI fallback.
- `answers`: reads a file or flags; the silent path. It fails clearly when a
  required answer is missing instead of guessing. **[call]**
- `fake`: deterministic, for tests.
- `qt`: PySide6, optional, Linux wizard. **[decided]**

### Native wizard generation (Windows, macOS) **[decided]**

`maire` exports, from the same page tree:

- **Windows:** an Inno Setup script with generated Pascal custom pages.
  Run-time choices (interpreters, editors) come from the frozen `maire`
  extracted to a temp directory at wizard start (`maire discover --json`);
  the install itself runs `maire install --answers ...`. **[verify]** that
  Inno can extract and run the helper before the file-copy step in the way
  needed.
- **macOS:** a `distribution.xml` plus a `postinstall` script. Installer
  JavaScript is much more limited than Inno, so **[call]**: static choices and
  file-existence checks (for example one optional component package per
  detected editor, whose marker file the postinstall script reads). Dynamic
  interpreter choice cannot be shown on the page; the package uses default
  interpreter detection and `maire configure` changes it later. **[verify]**
  with a spike before committing to this.
- `.pkg` has no native uninstall; `maire` ships an uninstaller driven by the
  receipt.

### Linux packages **[decided]**

`.deb` and `.apk` install silently: their maintainer scripts call the frozen
`maire` with defaults. The PySide6 wizard is launched afterwards from a
desktop entry or `<tool> --configure`. On a machine with no display it
degrades to the terminal renderer / CLI flags. **[call]** Alpine (`.apk`,
musl): PyQt/PySide wheels for musl may not be on PyPI, so `.apk` is treated
as silent + terminal first and the GUI there is not promised. **[verify]**

## Package layout **[call]**

```
tools/maire/
  __init__.py  __main__.py  cli.py
  model/        answers, request, receipt
  actions/      place_payload, alias, modules, extension
  discovery/    interpreters, editors
  tree/         page, field, conditions, tools_wizard, plugin_wizard
  render/       protocol, terminal, answers, fake, qt   (qt optional)
  export/       inno, pkg
  templates/stationer.toml
docs/tools/maire/       README.md, DOCS.md
tests/tools/maire/      unit tests, tree-walk tests, offscreen Qt tests
```

CLI sketch: `maire install`, `maire uninstall`, `maire configure`,
`maire discover --json`, `maire export inno|pkg`, `maire wizard`.

## Testing and quality

- pytest; the tree is tested by walking answer sets. Actions run against
  temporary directories and a fake interpreter. Follow the existing coverage
  gates style (`tests/tools/conjure/test_coverage_gates.py`).
- Qt tests use the offscreen platform and are gated on PySide6 being
  installed.
- Ruff and BasedPyright at zero; JS/CSS rules do not apply (no web UI).

## Python-free host and Python provisioning **[decided]**

`maire` must not need Python on the target machine, because installing
Python is one of its jobs. So the thin layer and its frontend are compiled
(PyInstaller), and the frozen `maire` is what every package carries. The
tools' own `.pyz` archives are the part that needs Python; `maire` makes sure
one exists.

`EnsurePython` replaces the ensure-python scripts in
`tools/stationer/installer_support.py`:
- Accept an existing interpreter that is 3.13+ (the wizard offers detected
  ones, a browse field, or "download a private Python").
- Otherwise download a python-build-standalone build (Linux and macOS) or the
  NuGet package (Windows) into the install directory, using the pinned
  versions already in `InstallConstants`. **[call]**
- The download follows the same rules as `evocator`: HTTPS only, timeouts and
  size bounds, and a SHA-256 check against a digest `maire` already knows
  before anything is unpacked. Digests are pinned in `maire` and bumped with
  its releases. **[call]** **[verify]** that the upstream release publishes
  digests that can be pinned this way.
- Offline machines get a clear failure and a path field to point at a
  pre-downloaded archive instead of a silent fallback. **[call]**
- If a private Python was provisioned, tool shims and `RegisterModules`
  target it. Uninstall removes it only if `maire` installed it.

**Two frozen flavors.** **[call]** `maire` (core, terminal and
answers-file renderers) is small and is what `.deb`/`.apk` maintainer
scripts, the Windows and macOS native wizards, and `evocator` call.
`maire-wizard` adds PySide6 and exists only for the Linux GUI. This keeps
PySide6 out of every package that does not show a Qt window. `evocator` is
frozen the same way, so it too runs without Python.

**Linux libc.** Frozen binaries are per libc, as stationer already builds
them (glibc and musl). The Qt flavor on musl is unproven (see Linux
packages), so `.apk` ships the small flavor first.

## Stationer installer integration **[decided]**

Today `tools/stationer` builds native, `.pyz` and PyPI formats and its
installers (DEB, APK, PKG, Inno Setup) share the payload in
`installer_support.py`. Changes:

- Stationer builds `maire` (both flavors where needed) and `evocator` first,
  because every other package embeds them. Build order follows
  dependencies. **[call]**
- Each package contains the frozen `maire`, the generated `install.json`, and
  the staged payload. The ensure-python scripts are removed in favor of
  `EnsurePython`.
- `.deb` / `.apk` maintainer scripts call `maire install` silently and
  `maire uninstall` on removal. The Windows `.exe` and macOS `.pkg` are built
  from `maire export inno|pkg`. Plugins get the same installers; their
  payload is the extension (`vsix`/`src`) instead of a `.pyz`.
- Every tool and plugin gets a stationer manifest; today only
  `tools/district/templates/stationer.toml` exists. **[call]**
- `[tool.stationer.builder]` in `pyproject.toml` moves from a single project
  (`district`) to all eleven by default. `release.toml` gains the other
  projects so every one publishes with the existing `<project>/v<version>`
  tag, which `evocator` uses to find its releases.
- The hand-written plugin `install.py` files and `installer_support.py`'s
  python-provisioning logic are removed once the new path is verified.
- Output reports (`artifacts.json`, `SHA256SUMS`, `BUILD.md`) are produced in
  the new layout.

## Build layout **[decided]**: `build/bin` and `build/lib`

```
build/
  bin/
    <os>/<arch>/<abi>/tools/<name>/{pyinstaller,installer,pyz}/
    <os>/<arch>/<abi>/plugins/<name>/{pyinstaller,installer,pyz}/
    pypi/{tools,plugins}/<name>/
  lib/
    tools/<name>/<version>/
    plugins/<name>/<version>/
```

- `<os>` is `linux`, `macos` or `windows`; `<abi>` is `glibc`, `musl` or
  `native`, as stationer already uses. Example:
  `build/bin/linux/amd64/glibc/tools/conjure` (the owner's "conjuring" is read
  as the existing `conjure` tool). **[call]**
- `build/bin` holds the per-platform installers for all seven tools and all
  four plugins. **[decided]**
- `build/lib` holds, per project and per version, the **frozen release
  snapshot**: the immutable set of artifacts of one release with its
  `SHA256SUMS.txt` and `artifacts.json`. It is the local source that
  publishing uploads from; `evocator` reads what publishing put on GitHub
  Releases, not `build/lib` itself. It is written at release time, not on
  every build, and holds copies of the `build/bin` outputs. Rebuilding an
  existing version fails if any digest would differ (it is immutable) unless
  the owner forces it. **[call]**
- Everything is driven by stationer. The legacy `build/` contents (the old
  `lake` and `arrow` outputs and the stray `artifacts 2.json`) are removed
  as part of the migration. **[call]**
- The `build/pypi` destination moves under `build/bin/pypi`. **[call]**

## Design calls made after the builder check **[call]**

These settle the questions a builder would otherwise have to guess.

**Conditions and transitions are declarative.** `visible_when`,
`enabled_when` and page transitions are a small expression AST (equals, in,
not, and, or, has-value, over answer ids), not Python callables. This is
required so the Inno and `distribution.xml` exporters can translate them. The
AST is the first thing built; the model, tree tests and both exporters
depend on it.

**Package versus maire.** The package is a thin shell. It carries the frozen
`maire`, the install manifest and the staged payload (`.pyz`, frozen exe),
and `maire` places every file and records it, so the receipt covers the whole
install. Removing the package runs `maire uninstall` first (`prerm`,
`pre-deinstall`, the Inno uninstaller, the macOS uninstaller). The frozen
`maire` lives in the per-scope `maire` data directory so uninstall and
`configure` can always find it.

**Who runs what.** Silent `.deb`/`.apk` steps run as root, so they do the
system-scope part only: payload, system shims, system interpreter modules,
and for plugins a shared copy of the extension. Per-user parts (the user's
editors, the user site-packages) are done by `configure` or the wizard,
per user, and recorded in that user's receipt.

**Install manifest.** Stationer generates an `install.json` inside each
package from the existing stationer manifest: name, version, CLI commands,
module path, extension folder, payload file names, and a `dependencies`
list for everything the install fetches that is not in the release (see
"Dependencies not bundled in a release" under evocator). `InstallRequest` is built
from that manifest plus the user's `Answers`.

**RegisterModules.** The `.pth` points at the directory the `.pyz` bootstrap
unpacks to (compiled dependencies cannot import from a zip); registering
triggers that unpack once. User scope writes to the interpreter's user site
(`site.getusersitepackages()`); system scope writes to the interpreter's own
`site-packages`. A `.pth` in the user site is allowed on PEP 668
externally-managed interpreters (PEP 668 restricts `pip`, not `.pth`). If the
target is unwritable or user site is disabled, that action fails with a clear
message and the run rolls back; the user can re-run without the module step.

**Locations and `PATH`.**
- Linux: user payload `~/.local/share/<name>`, shims `~/.local/bin`; system
  payload `/usr/local/lib/<name>` (matches `InstallConstants.libdir`), shims
  `/usr/local/bin`.
- macOS: user payload `~/Library/Application Support/<name>`, shims
  `~/.local/bin`; system shims `/usr/local/bin`.
- Windows: user payload `%LOCALAPPDATA%\Programs\<name>`, shim directory added
  to the user `PATH` (registry `HKCU\Environment`); system payload under
  `Program Files`, machine `PATH`. The `PATH` change is a receipt entry and is
  inverted on uninstall.
- POSIX shell profiles are not edited by default. If the shim directory is not
  on `PATH`, the wizard shows a checkbox to add a clearly marked block
  (opt-in) and the receipt records it. This avoids the profile-hook residue
  that earlier tooling left behind.

**Spike-gated milestones.** The Inno helper-extraction spike and the macOS
`.pkg` spike are early milestones. If Inno fails: static wizard pages from the
tree, with interpreter and editor choice moved to a post-install `maire
configure`. If macOS fails: static choices plus `postinstall` defaults, with
`maire configure` for the rest. The CLI, core, terminal renderer and PySide6
wizard do not depend on either spike.

**Existing installs.** No migration in this plan. The receipt model assumes a
clean slate. The four `plugins/*/install.py` copies are replaced by `maire`
once it ships; machines that used them are not adopted automatically.

# evocator

## Why

District already checks GitHub Releases, verifies a download against
`SHA256SUMS.txt` and swaps its own archive (`tools/district/update.py`,
1,150 lines). The owner wants every tool and plugin to update itself the same
way, and wants District's copy removed so that update management is
package-agnostic **[decided]**.

## What the existing implementation already provides (to extract)

Policy file with cadence (`always`, `hourly`, `daily`, `weekly`, `never`),
channel (`stable`, `prerelease`), repository, tag prefix and timeout; an
environment override; a state cache that throttles checks and repeat
notices; release lookup by project-qualified tag (`district/v<version>`);
asset selection `<name>-<version>-<platform>.pyz`; mandatory checksum
verification against the release's `SHA256SUMS.txt` (refusing to install if
missing); a detached helper that waits for the file lock to clear before the
swap; `platform_tag()`.

## Goals

- A package-agnostic library and CLI (`evocator`) that checks and applies
  updates for any tool or plugin published as releases in the monorepo.
- Auto-install **[decided]**: the default is to download, verify and install;
  the owner can set a cadence or turn it off per project.
- Knows what is installed by asking `maire` for its receipts, not by its own
  registry.
- Applies an update through `maire` (install the new payload, invert the old
  receipt), and rolls back to the previous version if the new one fails.
- Updates itself.
- Also checks and updates **dependencies that are not bundled in the
  release** and downloads them automatically **[decided]**.
- Full tool: tests, docs, builds, its own installer (through `maire`).
- District's `--update` / `--check-updates` become thin calls into
  `evocator`; `tools/district/update.py` is removed.

## Non-goals

- Not a package index or mirror, and not a general dependency resolver: it
  tracks only the dependencies a package declares in its install manifest.
- No signature infrastructure in this plan **[call]**: integrity stays at
  today's level (a checksum published in the same release). Stated plainly
  so it is a conscious limit, not an omission.
- No telemetry.

## Constraints

- Network code follows `AGENTS.md`: HTTPS only with a scheme allowlist
  (PY-SEC-0004), explicit timeouts and size bounds (PY-IO-0008), HTTP
  status checked before use (PY-IO-0009), no live network in unit tests
  (PY-TST-0004). Tests run against a local fake GitHub server.
- Repositories are an allowlist in the policy; a tool cannot redirect itself
  to an arbitrary repository without the owner editing the policy.
- Behavior of the extracted code is preserved; District's current update
  tests move with it.

## Shape **[call]**

```
tools/evocator/
  __init__.py  __main__.py  cli.py
  policy/     policy file, env overrides, per-project overrides
  state/      cache and notice throttling
  source/     GitHub releases client (the extracted fetch/release/asset code)
  verify/     checksum manifest handling
  apply/      update via maire, rollback, detached swap helper
  schedule/   OS scheduler integration
  templates/stationer.toml
docs/tools/evocator/    tests/tools/evocator/
```

CLI sketch: `evocator check [name]`, `evocator update [name] [--version V]`,
`evocator list`, `evocator policy`, `evocator self-update`.

**Running automatically.** Updates need something to run them. Call: `maire`
installs an OS scheduler entry that runs `evocator check --apply` at the
policy cadence (launchd agent on macOS, systemd user timer on Linux, Task
Scheduler on Windows), recorded in the receipt like any other effect. Tools
may also still trigger a detached refresh when run, as District does today.

## Testing

pytest against a local fake releases server; golden tests that the extracted
policy, version comparison, asset selection and checksum code behave exactly
like District's today; rollback tests that force a failing new version.

## Left to the builder

Policy file location and format details, scheduler entry file formats,
precise rollback mechanics, CLI flag names.

## Dependencies not bundled in a release **[decided]**

Releases bundle their Python code, but installs also fetch things that are
not in the release. Each package lists them in `install.json`, each with a
source, a version constraint and where it was installed:

| Kind | Real example in this plan | Update source |
|---|---|---|
| `python` | the private interpreter `maire` downloaded | python-build-standalone (Linux, macOS), NuGet (Windows) |
| `release` | the shared `maire` and `evocator` copies every package relies on | this repo's releases |

These are the only two kinds, both implemented end to end. **[call]**
Sibling Python packages such as `bowman` for `district` are bundled into the
`.pyz` by stationer's `local_dependencies`, so they are not dependencies in
this sense. PyPI-format installs use `pip` directly and never go through
`maire`, so there is no `pypi` kind. The source interface is small enough
that a new kind can be added later.

`evocator` checks each declared dependency against its source on the same
cadence as releases and downloads and installs newer matches automatically.

Rules **[call]**:
- Version constraints bound the jump. A private Python stays on its declared
  minor series (for example 3.13.x); evocator never moves it to 3.14.
- Sources are an allowlist. This widens trust beyond this repo to the
  upstream projects, so it is stated plainly here.
- Digests for updates come from the upstream's published checksum manifest.
  This replaces the idea of only pinning digests inside `maire`; pinned
  digests remain for the first bootstrap. **[verify]** that each upstream
  publishes a manifest in a form that can be checked.
- An update is applied through `maire` (new receipt, old one inverted) and
  rolls back if the dependent tool fails its post-update check. A failed
  dependency update never leaves a tool without a working interpreter.
- A dependency that other installed packages also use is updated once and
  recorded against every package that declares it.

## Resolutions from the second builder check **[call]**

**Shared `maire`.** One copy lives in the per-scope `maire` data directory.
Each package's frozen `maire` only seeds it. A package installs its copy only
if the shared one is missing or older; it never downgrades. The receipt
schema is versioned, and a newer `maire` reads older receipts (migrating on
write); an older `maire` refuses newer ones with a clear message instead of
guessing. The shared copy is reference-counted: uninstalling a package
removes it only when no other package depends on it. `evocator` updates
`maire` as a `release` dependency, and updates itself the same way.

**Updating a system-scope install.** Scheduling is per scope. User-scope
installs are updated by a user-level timer (launchd agent, systemd user
timer, per-user Task Scheduler entry). System-scope installs get a
system-level timer installed by the system-scope install; a user timer never
escalates. What is applied also depends on scope, so the OS package manager's
records stay correct:
- User scope: `evocator` downloads the payload bundle (`.pyz`, or the
  extension for a plugin) and applies it through `maire` (new receipt, old
  inverted).
- System scope: `evocator` downloads the native installer for the platform
  and installs it with the OS tool (`dpkg`, `apk`, `installer`, or the
  `-setup.exe` in silent mode), so `dpkg`/`apk`/`pkg` keep reporting the real
  version. **[verify]** that unsigned local `.apk` installs (which need
  `--allow-untrusted`) are acceptable.

**Silent installs without Python.** The silent `.deb`/`.apk` path never
downloads Python at package-install time, because that would turn an offline
`apt` run into a failure. It installs the frozen executable, the payload and
the shims that point at the frozen executable (which needs no Python), and
records the module-registration step as pending. A later `configure`,
the wizard, or `evocator` provisions Python and completes it. A
`--download-python` flag opts the silent path into downloading.

**Plugins as a project type.** Stationer gains a non-Python project kind,
`extension`. Its version comes from the extension's `src/package.json`, its
payload is the `vsix` plus the extension sources, and it has no frozen
executable of its own: the package is the frozen `maire` plus the extension.
It still gets per-platform cells, because the frozen `maire` is per platform.
Release automation is generalized from `district-release.yml` to cover all
11 projects. Windows builds keep depending on the existing UTM remote worker.

**Swapping a running Python.** Private interpreters are installed side by side
in versioned directories (`python-3.13.17/`), never replaced in place. A
pointer (a symlink, or a junction on Windows) names the current one and is
switched only after the new interpreter passes a smoke test. Shims resolve
through the pointer. The old directory is removed once nothing uses it.
Updates stay inside the declared minor series, as above.

## Partitioned tool bundles **[decided]**

The owner's Exactus package (`/Users/joaolopes/Exactus/exactus/packages/package`,
about 10.5k lines) already has a partition engine for bundling tools. It
becomes a stationer subpackage, `tools/stationer/partition/`, made agnostic:
no `exactus` names, paths or schema ids, and no dependency on Exactus.

**What is ported** **[call]**
- `partition_fs.py` (the content-addressed engine): chunking by content
  (Gear CDC), immutable catalogs and recipes, deterministic zstd packs, an
  object store, a materializer that reuses identical files by hardlink, and
  `write_delta` / `apply_delta` / `rollback` / `garbage_collect` /
  `deep_verify`. It uses only the standard library plus a small portable zstd
  module, which comes with it. Python 3.13 has no stdlib zstd.
- `partition_launcher.py`: dependency-closure resolution, cache locking and
  the runtime that materializes a component and then runs its tool.
- The generic model from `bundle.py`: `ToolSpec`, `ComponentSpec`,
  `resolve_components`, `fetch`, `BuildTarget`. 
- The tests, starting with `tests/packages/package/test_partition_fs.py`
  (1,688 lines), ported to `tests/tools/stationer/`.

**Native zstd binaries.** The portable zstd module is a wrapper around
prebuilt `zstd` CLIs (v1.5.7 where the version is readable), supplied by the
owner in `package_bundle.zip` as six archives (Linux arm64 and x86_64,
statically linked, so they work on glibc and musl; macOS universal2; Windows
arm64, x86 and x86_64). They are committed beside the module with a
`SHA256SUMS` file, and the loader verifies each archive's digest before
extracting. Their upstream provenance is not recorded anywhere, so the pinned
digests are the trust anchor. Frozen builds must collect these archives as
data. **[call]**

**What is not ported** **[call]**: `build.py`, `install.py`,
`process_control.py`, `progress.py` (Exactus's own self-packaging and run
schema; stationer has its own), and the Node.js and Playwright distribution
code in `bundle.py`. Those are examples of "providers"; stationer's own
providers (Python, below) are written against the generic model. The Exactus
tree is not modified.

**Where it plugs in**
- **Python provisioning.** `EnsurePython` treats a Python distribution as a
  partitioned component. Catalogs give verified, offline-capable provisioning;
  the provider that fetches python-build-standalone or NuGet only has to
  produce that component.
- **Updates.** For `evocator`, `write_delta` / `apply_delta` give incremental
  updates (only new packs are downloaded), and the engine's rule of switching
  the catalog last gives the safe-swap behaviour described under "Swapping a
  running Python". `rollback` and `garbage_collect` implement the rollback
  and cleanup the spec calls for. **[call]** The pointer/versioned-directory
  design is realized through catalogs where possible, and kept otherwise.
- **Bundled third-party tools.** A package that bundles other tools (for
  example a Python plus a tool that needs it) ships them as components, so
  identical content is shared across packages and installs.
- **Release snapshots.** Catalogs and packs live inside the `build/lib`
  release snapshot for the project. **[call]**
- **Stationer CLI.** `stationer partition build|verify|update|rollback|gc`.
  **[call]**

**Left to the builder:** whether shims for tools with bundled components go
through the launcher or call the materialized tool directly; the portable
zstd module's exact packaging.

## Confirmed by the owner

- `build/lib` means frozen, versioned release snapshots (above).
- "conjuring" means `conjure`.
- `build/pypi` moves under `build/bin/pypi`.
- The legacy `build/` contents are removed during the migration.
