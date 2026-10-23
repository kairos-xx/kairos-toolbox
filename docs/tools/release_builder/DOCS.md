# Release Builder — Extended Documentation and API Reference

`tools.release_builder` builds release artifacts for a Python project on the
three target environments instead of cross-compiling them from one OS:
macOS is built natively, Linux inside a Docker container, and Windows ARM64
inside an SSH-accessible QEMU guest. Each platform produces a bare frozen
executable plus a native installer, SHA-256 digests for both, and a
machine-readable `artifacts.json` manifest.

A second output format, `--format pyz`, builds one self-contained
`<name>-<version>-<platform>.pyz` for the current machine instead. It needs
Python on the target (the native installers do not) and is the format that
self-updating tools download; see [`.pyz` archives](#pyz-archives).

## Contents

- [Model](#model)
- [Manifest reference](#manifest-reference)
- [`.pyz` archives](#pyz-archives)
- [CLI reference](#cli-reference)
- [Per-platform pipelines](#per-platform-pipelines)
- [Outputs and artifact manifest](#outputs-and-artifact-manifest)
- [Python API](#python-api)
- [Source archives](#source-archives)
- [Errors and determinism](#errors-and-determinism)
- [Testing](#testing)

## Model

A build is driven by one TOML manifest describing the project plus one
optional table per platform. `ManifestLoader.load` validates the manifest
into a `BuildConfiguration`; `ReleaseBuilder` dispatches each selected
platform to its builder sequentially and finally writes `artifacts.json`.
The implementation lives in `tools/release_builder/` (with an example manifest
and the Windows guest bootstrap), its tests in `tests/tools/release_builder/`.
Every external command flows through the `Runner` protocol — the production
implementation is `CommandRunner` — so builds are deterministic, captured,
and fail fast through `BuildError` on any non-zero exit.

## Manifest reference

### `[project]` — required

`local_dependencies` lists sibling project directories (relative to the project
root, each with a `pyproject.toml`) that are installed before the project in
every pipeline — from throwaway copies, so builds never write into your
checkout.

| Key | Type | Description |
| --- | --- | --- |
| `name` | string | Product/executable name used in artifact filenames. |
| `version` | string | Release version (or see `version_file`). |
| `version_file` | string | Path to a file whose `version = "X.Y.Z"` literal supplies the version instead of `version`. |
| `entrypoint` | string | Python entrypoint, relative to the project root. |
| `identifier` | string | Reverse-DNS identifier (PKG `CFBundle`-style id, Inno `AppId`). |
| `console` | bool | `true` builds a console executable; `false` adds `--windowed`. |
| `pyinstaller_args` | list | Extra PyInstaller arguments (e.g. `--collect-submodules`). |
| `pip_args` | list | Extra arguments appended to the worker's `pip install`. |

### `[macos]` — required when building `macos`

| Key | Type | Description |
| --- | --- | --- |
| `python` | string | Interpreter used for the build; defaults to the interpreter running `release_builder.py`. |
| `signing_identity` | string | Optional `Developer ID Installer` identity passed to `pkgbuild --sign`. |

### `[linux]` — required when building `linux`

| Key | Type | Description |
| --- | --- | --- |
| `docker_platform` | string | `docker buildx` platform, e.g. `linux/arm64`; also sets the DEB architecture. |
| `image` | string | Base image, e.g. `python:3.13-slim-bookworm`. |
| `maintainer` | string | DEB `Maintainer` field. |
| `description` | string | DEB `Description` field. |

### `[windows]` — required when building `windows`

| Key | Type | Description |
| --- | --- | --- |
| `host` | string | QEMU guest SSH host (`127.0.0.1` with `hostfwd`). |
| `port` | int | Guest SSH port (`2222` with `hostfwd=tcp:127.0.0.1:2222-:22`). |
| `user` | string | Guest SSH user. |
| `identity_file` | string | Private key for the guest SSH connection. |
| `python` | list | Interpreter launcher, e.g. `["py", "-3.13"]`. |
| `remote_root` | string | Work directory inside the guest, e.g. `C:/release-builder`. |
| `publisher` | string | `AppPublisher` in the generated Inno Setup script. |

`tools/release_builder/release-builder.example.toml` is a complete annotated
manifest. `tools/release_builder/windows-guest-setup.ps1` bootstraps the
Windows guest (Python, PyInstaller, Inno Setup prerequisites).

## CLI reference

```bash
python -m tools.release_builder \
  --project /path/to/project \
  --manifest /path/to/release.toml \
  --output /path/to/release
```

| Option | Description |
| --- | --- |
| `--project` | Project root to build (required). |
| `--manifest` | TOML manifest path (required). |
| `--output` | Output root; defaults to `./build` and is resolved to an absolute path. |
| `--publish PROJECT` | After building, create the GitHub release for `PROJECT` (a `release.toml` project) or add the builds to it ([details](#build-report-and-github-releases)). |
| `--release-config` | `release_publish` configuration; defaults to `./release.toml`. |
| `--dry-run` | With `--publish`, plan the release without running it. |
| `--platform` | Repeatable `macos`/`linux`/`windows` selector; default builds all three. Ignored by `--format pyz`/`pypi`. |
| `--format` | `native` (default) builds the platform installers; `pyz` builds one `.pyz` for this machine; `pypi` builds sdist+wheel for `local_dependencies` then the project ([details](#pypi-distributions)). |

Build only some targets by repeating the option:

```bash
python -m tools.release_builder --project /path/to/project \
  --manifest /path/to/release.toml --output /path/to/release \
  --platform linux --platform windows
```

The `[windows]` table (and its SSH key) is only required when `windows` is
selected. Exit status is `0` on success; any builder failure raises
`BuildError`.

## Build report and GitHub releases

Every run updates two files in the output directory (`build/` by default):

- `artifacts.json` — the machine-readable manifest. Runs **merge**: building
  only one platform keeps the other platforms' earlier entries while their
  files still exist; rebuilding a platform/architecture replaces its entry.
  Paths are relative to the output directory.
- `BUILD.md` — generated, never hand-edited: project and version, git commit,
  generation time and host, a table of every executable and installer with
  build time and duration, any `.pyz` archives found, a `SHA-256` block
  (`shasum -a 256 -c`), and the commands that rebuild each platform.
  `.gitignore` tracks `build/BUILD.md` while the binaries stay local.

`--publish PROJECT` hands the result to `tools.release_publish`: it checks that
the built version equals the project's version in `release.toml`, that the tag
is pushed, then **creates** the release or, when it already exists (CI usually
creates it first), **uploads** to it, replacing same-named assets. Attached
files are the executables, installers and `BUILD.md`. `.pyz` archives are never
published from here: CI builds one per platform and checksums them, and a
locally built copy would replace the CI asset and break verification.

When the release-config root contains a `wiki/` directory, `--publish` also
synchronizes it with the repository's GitHub Wiki (`<repo>.wiki.git`) after
the release plan executes: the wiki checkout is cloned to a temporary
directory, fully replaced by the local files, committed, and pushed. Missing
wiki directories are skipped silently; `WikiPublisher` is also usable
standalone (`python -m tools.release_builder.wiki owner/repo --dry-run`).

## `.pyz` archives

`python -m tools.release_builder … --format pyz` builds a self-contained
archive for the machine it runs on. Compiled dependencies make an archive
platform-specific, so one is built per target machine rather than
cross-compiled.

```toml
[pyz]
name = "my-app"            # file name prefix; defaults to project.name
bootstrap = "pyz_main.py"  # becomes the archive's __main__.py
```

`bootstrap` must be a file inside the project root. The build

1. copies the project to a throwaway directory (so `pip` never leaves
   `build/` or `*.egg-info` in the source tree),
2. runs `pip install --target <stage> --only-binary :all:` on that copy plus
   any `project.pip_args`,
3. drops console scripts and `__pycache__`, ships `bootstrap` as
   `__main__.py`, and writes `<name>-<version>-<platform>.pyz` with
   `zipapp` (compressed, `/usr/bin/env python3` shebang),
4. smoke-tests it by running `--version` with an isolated home directory and
   fails with `BuildError` unless the output contains the project version.

The platform tag is `macos-arm64`, `macos-x86_64`, `linux-x86_64`,
`linux-aarch64`, `windows-amd64` or `windows-arm64`.

## PyPI distributions

`python -m tools.release_builder … --format pypi` builds the **sdist and
wheel** for every `local_dependencies` entry and then for the project itself,
into `output`. Dependencies first matters: `pip install <project>` resolves
declared siblings (like `arrow-local-uri`) from PyPI, so a project is only
installable from the index once every dependency it declares exists there.
Building them in the same run keeps their versions in lock-step with the
release.

Like the other formats the builds run on staged copies — `python -m build`
never writes `build/` or `*.egg-info` into the checkout. Upload the result
with the release-publish helper:

```bash
python -m tools.release_publish.upload build/pypi \
  --repository-url https://test.pypi.org/legacy/   # optional
```

`--publish` does not apply to this format; PyPI upload is a separate,
explicit step.

## Per-platform pipelines

### macOS (`MacOSBuilder`)

Runs natively on macOS. Creates a `venv`, installs `pyinstaller` plus
`pip_args` and the project, freezes the entrypoint with
`--noconfirm --clean --onefile`, copies the frozen binary to
`{name}-{version}-macos-{arch}`, stages a `pkg-root`, and invokes
`pkgbuild --identifier … --version … --install-location /` (plus
`--sign` when `signing_identity` is configured) to produce
`{name}-{version}-macos-{arch}.pkg`.

### Linux (`LinuxDockerBuilder`)

Runs inside `docker run --platform {docker_platform} -v … {image}`. The
generated worker script installs `binutils`/`dpkg-dev`, creates a venv,
installs the project, freezes it with PyInstaller, copies the binary to
`{name}-{version}-linux-{arch}` (architecture derived from
`docker_platform`), and assembles `{name}_{version}_{arch}.deb` through
`dpkg-deb --build --root-owner-group` with the configured maintainer and
description.

### Windows ARM64 (`WindowsQemuBuilder`)

Targets an ARM64 Windows guest reachable over SSH/SCP (typically QEMU with
`hostfwd`). The builder `scp`s the project and a generated PowerShell worker
to `remote_root`; the worker creates a venv, freezes the entrypoint, ensures
Inno Setup via `winget`, compiles `installer.iss`
(`PrivilegesRequired=lowest`, ARM64-only) into `setup.exe`, and the host
pulls back `{name}-{version}-windows-arm64-setup.exe` plus the bare
executable. Quoting goes through `_ps_quote`; the guest bootstrap lives in
`windows-guest-setup.ps1`.

## Outputs and artifact manifest

Output is grouped per platform under `{output}/{platform}/`. Every
`BuildResult` records `platform`, `architecture`, `executable`, `installer`,
`executable_sha256`, `installer_sha256`, and `duration_seconds`; all results
are serialized to `{output}/artifacts.json` (sorted, indented JSON) for CI
consumption.

## Python API

- `ManifestLoader.load(*, manifest, project_root, platforms)` — parse and
  validate the manifest; injects `version_file` values, enforces
  platform-required tables, returns `BuildConfiguration`.
- `ProjectConfig` — `root`, `name`, `version`, `entrypoint`, `identifier`,
  `console`, `pyinstaller_args`, `pip_args`.
- `MacOSConfig` — `python`, `signing_identity | None`.
- `LinuxConfig` — `docker_platform`, `image`, `maintainer`, `description`.
- `WindowsConfig | None` — `host`, `port`, `user`, `identity_file`,
  `python` tuple, `remote_root`, `publisher`.
- `Runner` — protocol boundary; `execute(*, arguments, cwd,
  timeout_seconds) -> CompletedProcess` and raises `BuildError` on failure.
  `CommandRunner` is the production subprocess implementation.
- `MacOSBuilder`, `LinuxDockerBuilder`, `WindowsQemuBuilder` — constructed
  with a `Runner`; `build(project, config, output) -> BuildResult`.
- `ReleaseBuilder(runner).build(configuration, output, platforms)
  -> tuple[BuildResult, ...]` — orchestrates selected builders and writes
  `artifacts.json`.
- `ArtifactUtilities.result`/`digest` — result assembly and SHA-256
  helpers.
- `PyzConfig.load(manifest, project_root, default_name)` / `PyzBuilder(runner)
  .build(project, config, output, tag)` — the `.pyz` format; `platform_tag()`
  and `archive_name(name, version, tag)` name the result.
- `PypiBuilder(runner).build(project, output)` — the sdist+wheel format;
  builds `local_dependencies` before the project, returns the produced
  distributions.
- `WikiPublisher(repository, wiki_directory, runner?, commit_message?,
  dry_run?)` — synchronizes a local wiki directory with the GitHub Wiki Git
  repository; `wiki_repository_url(repository)` normalizes
  `owner/repo`, HTTPS, or SSH forms into `<repo>.wiki.git`.
- `ReleasePublisher` — bridges built artifacts to `tools.release_publish` and
  triggers the wiki sync when `wiki/` exists at the release-config root.
- `Cli.main(arguments)` — the `python -m tools.release_builder` entrypoint.

## Source archives

`SourceArchive.create(project, destination)` writes a deflated ZIP of the
project tree, excluding `__pycache__`, `build`, `dist`, `node_modules`,
`.venv`, and version-control internals — the same source payload shipped to
remote builders.

## Errors and determinism

`BuildError` (a `RuntimeError`) wraps every external failure with the
offending command and its captured output. Windows builds requested without
a `[windows]` table fail fast with a descriptive error. All command
invocations are argv tuples — no shell interpolation — and generated worker
scripts quote every interpolated value, so manifests cannot inject shell
syntax into Docker or PowerShell workers.

## Testing

`tests/tools/release_builder/` covers manifest loading (all tables and
`version_file`), each platform builder via a `FakeRunner` that fabricates
artifacts, the multi-platform orchestrator, CLI argument selection, source
archive exclusions, checksum computation, and failure propagation. Run them
with the repository's normal `pytest` invocation.

`test_real_pipelines.py` runs the pipelines **for real** where the host
supports it — the copy-based install path end to end, not faked:

- **Linux** — inside a real Docker container; runs automatically when a
  working Docker daemon answers `docker info`. Asserts the produced `.deb`
  is a real `ar` archive.
- **macOS** — natively on macOS hosts: venv, `pip install` of the staged
  dependency and project, PyInstaller, `pkgbuild`. Asserts a real `xar`
  `.pkg`.
- **Windows** — on a real Windows guest (QEMU or any SSH-reachable Windows
  machine). Skipped unless `RELEASE_BUILDER_WINDOWS_HOST` is set;
  `…_PORT`, `…_USER`, `…_KEY`, `…_PYTHON`, and `…_ROOT` tune the SSH
  target.

Each real build installs a sibling dependency from a staged copy, so the
`local_dependencies` install path is exercised end to end.
