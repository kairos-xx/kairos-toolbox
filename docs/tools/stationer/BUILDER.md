# Stationer — Extended Documentation and API Reference

`tools.stationer` builds release artifacts for a Python project on the
three target environments instead of cross-compiling them from one OS:
macOS is built natively, Linux inside a Docker container, and Windows ARM64
inside an SSH-accessible QEMU guest. Each platform produces a bare frozen
executable plus a native installer, SHA-256 digests for both, and a
machine-readable `artifacts.json` manifest.

A second output format, `--format pyz`, builds one self-contained
`<name>-<version>-<tag>.pyz` per platform/architecture/ABI cell: dependencies
are resolved against the target's wheel platform on the host, so Windows,
musl and x86_64 archives are produced without a VM. It needs
Python on the target (the native installers do not) and is the format that
self-updating tools download; see [`.pyz` archives](#pyz-archives).

## Contents

- [Model](#model)
- [Manifest reference](#manifest-reference)
- [Remote pull workers](#remote-pull-workers)
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
The implementation lives in `tools/stationer/` (with an example manifest
and the guest bootstrap scripts), its tests in `tests/tools/stationer/`.
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
| `python` | string | Interpreter used for the build; defaults to the interpreter running `builder.py`. |
| `signing_identity` | string | Optional `Developer ID Installer` identity passed to `pkgbuild --sign`. |

### `[linux]` — required when building `linux`

| Key | Type | Description |
| --- | --- | --- |
| `docker_platform` | string | `docker buildx` platform, e.g. `linux/arm64`; also sets the DEB architecture. |
| `docker_platforms` | list | Every platform to build, e.g. `["linux/amd64", "linux/arm64"]`; defaults to `[docker_platform]`. |
| `image` | string | Base image for glibc builds, e.g. `python:3.13-slim-bookworm`. |
| `musl_image` | string | Optional musl (Alpine) image, e.g. `python:3.13-alpine`; adds musl cells that produce `.apk` packages via `apk mkpkg` (unsigned, installable with `apk add --allow-untrusted`). |
| `maintainer` | string | DEB `Maintainer` field. |
| `description` | string | DEB `Description` field. |

The container needs no preinstalled tooling: the build script detects `apk`
(Alpine/musl) vs `apt` (Debian/glibc), installs `binutils` (and `dpkg-dev`)
itself, bootstraps Python when the image lacks one, and records the image's
glibc floor for glibc builds. The container is removed when the build ends.

### `[windows]` — required when building `windows`

| Key | Type | Description |
| --- | --- | --- |
| `host` | string | QEMU guest SSH host (`127.0.0.1` with `hostfwd`). |
| `port` | int | Guest SSH port (`2222` with `hostfwd=tcp:127.0.0.1:2222-:22`). |
| `user` | string | Guest SSH user. |
| `identity_file` | string | Private key for the guest SSH connection. |
| `python` | list | Interpreter launcher, e.g. `["py", "-3.13"]`. |
| `remote_root` | string | Work directory inside the guest, e.g. `C:/stationer`. |
| `publisher` | string | `AppPublisher` in the generated Inno Setup script. |

`tools/stationer/example/stationer.example.toml` is a complete annotated
manifest. `tools/stationer/guest/windows-guest-setup.ps1` bootstraps the
Windows guest (Python, PyInstaller, Inno Setup prerequisites).

### `[linux.guest]` / `[macos.guest]` — optional SSH guest builds

When present, a `<platform>.guest` table replaces that platform's default
builder (Docker cells for Linux, the native build for macOS) with a single
build inside an SSH-reachable guest — typically a QEMU/UTM VM prepared
with `tools/stationer/guest/linux-guest-setup.sh` or
`guest/macos-guest-setup.sh`. Both tables take the `[windows]` keys except
`publisher`: `host`, `port`, `user`, `identity_file`, `python` (the guest's
Python launcher, default `["python3"]`) and `remote_root` (default
`/tmp/stationer`).

`PosixQemuBuilder` probes the guest over SSH — `uname -m` and, on Linux,
`command -v apk` — so the guest's own architecture and C library decide the
package folder, the `.pyz` release tag and the artifact names: a Debian
guest yields `{name}_{version}_{arch}.deb`, an Alpine guest
`{name}-{version}-linux-{arch}-musl.apk`, a macOS guest
`{name}-{version}-macos-{arch}.pkg` built with `pkgbuild`. The worker
script is `templates/linux_build.sh.tmpl` or `templates/macos_build.sh.tmpl`,
rendered through the same `@@NAME@@` engine as the Windows worker.

Guest builds transfer source through the content-addressed partition
engine rather than a whole-tree zip: `source_packs.py` scans the staged
project into a catalog + zstd packs under `build/.partition/` (persistent,
so unchanged blocks are packed once), uploads to the guest only the packs
its `<remote_root>/.partition` store is missing, and the worker rebuilds
the tree with `python -m stationer.partition materialize`.

## CLI reference

```bash
python -m tools.stationer \
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
| `--remote` | `windows`, `linux` or `macos`: build on a remote pull worker ([details](#remote-pull-workers)). Also `--bind`, `--port`, `--worker-host`, `--expose`, `--short`, and `--vm`, `--vm-user`, `--vm-identity`, `--vm-port` to run the worker in an existing VM ([details](#running-the-worker-in-an-existing-vm)). |
| `--release-config` | `stationer` configuration; defaults to `./release.toml`. |
| `--dry-run` | With `--publish`, plan the release without running it. |
| `--platform` | Repeatable `macos`/`linux`/`windows` selector; default builds all three. With `--format pyz` it narrows the archive matrix. |
| `--arch` | `arm64`, `amd64`, `native` or `all`; narrows the macOS and remote builds and the pyz matrix. |
| `--format` | `native` (default) builds the platform installers; `pyz` builds one `.pyz` per platform/architecture/ABI cell; `pypi` builds sdist+wheel for `local_dependencies` then the project ([details](#pypi-distributions)). |

Build only some targets by repeating the option:

```bash
python -m tools.stationer --project /path/to/project \
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

`--publish PROJECT` hands the result to `tools.stationer`: it checks that
the built version equals the project's version in `release.toml`, that the tag
is pushed, then **creates** the release or, when it already exists (CI usually
creates it first), **uploads** to it, replacing same-named assets. Attached
files are the executables, installers, every `.pyz` under a `pyz/` artifact
folder, `SHA256SUMS` and `BUILD.md`.

When the release-config root contains a `wiki/` directory, `--publish` also
synchronizes it with the repository's GitHub Wiki (`<repo>.wiki.git`) after
the release plan executes: the wiki checkout is cloned to a temporary
directory, fully replaced by the local files, committed, and pushed. Missing
wiki directories are skipped silently; `WikiPublisher` is also usable
standalone (`python -m tools.stationer.wiki owner/repo --dry-run`).

## Remote pull workers

PyInstaller cannot cross-compile, so each operating system needs a machine of
its own. `--remote windows|linux|macos` builds on such a machine **without SSH
access or a pre-configured worker**: the host serves one authenticated job and
prints a single command; run it on a fresh VM, container or laptop and the
worker pulls the job, builds, and returns the result.

```bash
python -m tools.stationer --project tools/district \
    --manifest tools/district/templates/stationer.toml --remote windows
# Run this on the windows worker:
#   curl.exe -fsSL http://10.0.2.2:8765/<secret>/install.ps1 | powershell.exe -NoProfile -Command -
```

`--remote` is its own mode (not combinable with `--format` or `--platform`),
but it shares everything else: the manifest, `BUILD.md`, `artifacts.json` and
`--publish`. The worker also builds the target's installer — Windows workers
download Inno Setup and compile an injected `installer.iss` (silently
installed under the job directory, removed with it), Linux workers run
`dpkg-deb` against an injected `DEBIAN/control`, and macOS workers run
`pkgbuild` — so `pyinstaller/` and `installer/` folders land under
`<output>/<target>/<arch>/<abi>/<project>/`.

### How a job runs

1. The host archives the project and its `local_dependencies` (the same
   `SourceArchive` the SSH backends use), computes its SHA-256 and creates
   fresh credentials.
2. The worker downloads the payload with the bearer token and checks its
   digest, writes any host-defined **files** into its job directory (the spec
   carries an optional `files` map — rendered `installer.iss`,
   `DEBIAN/control` and the like, with the same `{python}`/`{dist}`/`{work}`/
   `{architecture}` placeholders as steps), then runs the host-defined
   **steps** in order: create a venv, upgrade pip, `pip install pyinstaller`
   plus the dependencies and the project (dependencies first), run
   PyInstaller, and package the installer. Both worker flavours
   (PowerShell for Windows, a standard-library Python program started by Bash
   for Linux and macOS) only interpret the step list, so all build logic lives
   on the host. Live progress is shown as `[worker] …` lines.
3. The worker uploads every file under PyInstaller's dist directory plus a
   manifest of sizes and SHA-256 digests. The host accepts the job only after
   the transport digest, ZIP structure, every manifest path (absolute and
   `..` paths are refused), size and per-artifact digest verify; nothing on
   disk changes before that, and a previous good output survives a bad upload.
4. A failing step is reported to the host with its traceback and fails the
   build; a worker that never connects times out.

Windows workers install Python themselves when none is found (x64 and ARM64,
`python_bootstrap`); Linux and macOS workers need `python3` for the bootstrap,
and always build in a fresh virtual environment.

### The `[remote]` manifest table

Precedence is built-in defaults, then `[remote]`, then command-line options.

| Key / option | Default | Meaning |
| --- | --- | --- |
| `bind` / `--bind` | `127.0.0.1` | Interface the job server listens on. Use `0.0.0.0` or a charter only when the worker is on another machine. |
| `port` / `--port` | `8765` | Port; `0` picks a free one. |
| `worker_host` / `--worker-host` | `10.0.2.2` | Address the worker uses to reach this host (`10.0.2.2` is QEMU user-mode networking's host address). |
| `expose` / `--expose` | `direct` | `direct`, `ngrok`, `pinggy`, `tunnl` or `runlocal`. |
| `short` / `--short [ALIAS]` | off | Shorten the bootstrap URL; an empty value asks for a random code. |
| `ngrok_authtoken` | — | Prefer `NGROK_AUTHTOKEN` or `ngrok config add-authtoken`. |
| `python_version` | `3.13.9` | Interpreter installed on Windows workers that have none. |
| `python_bootstrap` | `true` | Whether a Windows worker may install Python. |
| `timeout_seconds` | `1800` | Maximum wait for the worker. |
| `vm` / `--vm` | — | Path of an existing `qcow2` VM disk to boot as the worker ([details](#running-the-worker-in-an-existing-vm)). Relative manifest paths are relative to the manifest. |
| `vm_user` / `--vm-user` | `Administrator` | SSH user inside the VM. |
| `vm_identity_file` / `--vm-identity` | `id_ed25519` beside the disk | SSH private key. |
| `vm_ssh_port` / `--vm-port` | `spec.json`, else `2222` | Host loopback port forwarded to the VM's SSH server. |
| `vm_boot_timeout_seconds` | `1800` | Longest wait for the VM's SSH server. |

### Running the worker in an existing VM

Instead of pasting the bootstrap command yourself, point the build at a VM
disk you already own:

```bash
python -m tools.stationer --project tools/district \
    --manifest tools/district/templates/stationer.toml \
    --remote windows --vm ~/.windows-vm-studio/vms/default/windows.qcow2
```

`QemuVm` (`qemu_vm.py`) then:

1. reads `spec.json` beside the disk (`cpus`, `memory_mb`, `machine`,
   `accel`, `cpu_model`, `ssh_port`) and defaults the SSH key to the
   `id_ed25519` next to it;
2. starts `qemu-system-x86_64` with the disk attached through `-snapshot`, so
   **the disk file is never modified** (writes go to a throwaway overlay), SSH
   forwarded on `127.0.0.1` only (`hostfwd=tcp:127.0.0.1:<port>-:22`), and QMP
   on a private Unix socket;
3. polls SSH until the guest answers (an emulated guest can need many
   minutes; `vm_boot_timeout_seconds` bounds it);
4. runs the same bootstrap command over `ssh`, so the worker pulls the job from
   the host at `10.0.2.2` exactly as in the pasted flow. A launch command that
   dies fails the build immediately with its output instead of waiting for the
   timeout;
5. stops the SSH session and powers the VM off through QMP `quit` (terminate
   and kill are the fallbacks), however the build ends.

The guest needs an SSH server that accepts the key, `curl.exe` (Windows 10
1803+) and internet access for Python/pip. The disk is attached as IDE with an
`e1000` NIC (`pc`/`q35` machines) so Windows boots without extra drivers. The
architecture is whatever the guest is: an x64 VM yields an `amd64` executable.

Set it permanently in `[remote]` (`vm = "…"`) or, for bare runs, in
`pyproject.toml`:

```toml
[tool.stationer.builder]
remote = ["windows"]          # built after the formats, one VM boot each
vm = "~/.windows-vm-studio/vms/default/windows.qcow2"
```

`remote` lists operating systems built on pull workers after the `formats`;
`vm` is passed as `--vm`. A `dry_run` skips remote builds so no VM is booted.
Precedence: `--vm` on the command line, then `[remote] vm` in the manifest;
`pyproject.toml` feeds the bare run only.

### Exposure backends

- **direct** — workers reach `worker_host:port` themselves (QEMU, a LAN VM).
- **ngrok** — starts the ngrok agent, reads the HTTPS endpoint from its local
  API, and stops it with the server. The free plan needs an account authtoken.
  ngrok is installed with Homebrew, winget or the official Linux tarball.
- **pinggy** — registration-free: `expect` drives OpenSSH to `free.pinggy.io`
  on port 443 and the first `https://*.free.pinggy.net` URL is used.
- **tunnl** — `ssh -R 80:localhost:PORT proxy.tunnl.gg`, URL `*.tunnl.gg`.
- **runlocal** — `npx --yes runlocal PORT`, URL `*.runlocal.eu`.

The host keeps each backend's process and stops it in the same cleanup path as
the server. `--short` shortens only the bootstrap URL (callbacks use the real
public URL), and the shortening service can see that URL.

### Live progress

Guests never interpret build output; they only report it, and the host works
out what it means (`progress.py`). One parser and one display serve every
guest type:

| Guest | How progress reaches the host |
| --- | --- |
| Remote worker (VM, Windows, Linux, macOS) | Posts `{"step": N, "message": …}` to `/v1/progress` when a planned step starts, and streams the step's output to `/v1/output` in batches (at most every 0.5 s or 8 kB, newest 12 kB kept). |
| Docker container (Linux) | The container script prints `@@district-step@@ N message` before each phase; the host streams `docker exec` line by line (`DockerRunner.run(on_output=…)`, stderr merged) into the same tracker. |

`ProgressTracker` turns those into `BuildProgress` updates. The overall
percentage is **weighted by finished steps** (the install and PyInstaller
steps weigh most) and stays below 100 until the build completes: pip reports
nothing measurable for resolving, wheel building, freezing or packaging, so
no byte fractions are invented for them. The one measurable thing, a
download, is shown beside the bar with exact bytes: the install step runs
`pip install --progress-bar raw`, whose `Progress N of M` lines the host
decodes (it needs a pip that knows `raw`, so pip is upgraded in the
environment first).

`ProgressDisplay` writes one permanent line per step
(`[ 29%] step 5/7 installing …`) and, on a terminal, a live bar below it
(`[#######---] 29% step 5/7 … · numpy 8.0 MB/16.0 MB`) that is erased before
any other line is printed. Pipes and CI logs get the permanent lines only.
Without a display (`progress=None`) workers still report plain step messages.

### Security model

- The server binds to loopback by default.
- The scripts embed the bearer token, so they are served only under an
  unguessable per-job path (`/<secret>/install.ps1`, `install-linux.sh`,
  `install-macos.sh`); every other path is a 404.
- `GET /v1/payload`, `PUT /v1/result`, `POST /v1/progress`, `POST /v1/output`
  and `POST /v1/error` require the bearer token (compared in constant time); bodies are size-limited
  and checked against `Content-Length` before being read; request logging is
  suppressed so URLs and tokens never reach a log.
- The transport is plain HTTP. Use it on a trusted host/VM network or behind
  one of the HTTPS tunnels.
- The job ends, and the server closes, after one validated result or one error.

### Relationship to the SSH backends

`WindowsQemuBuilder` pushes to a guest that already runs an SSH server;
`--remote windows` needs only a way to paste one command into the guest, and
also covers Linux and macOS workers. Guests that cannot run an SSH server use
`--remote windows`.

## `.pyz` archives

`python -m tools.stationer … --format pyz` builds a self-contained
archive for the machine it runs on. Compiled dependencies make an archive
platform-specific, so one archive is built per platform/architecture/ABI
cell: macOS arm64 and amd64, Linux amd64 and arm64 in both glibc and musl
flavours, and Windows amd64 and arm64. Foreign cells are resolved on the
host with `pip --platform`, so no VM is needed for them.

```toml
[pyz]
name = "my-app"            # file name prefix; defaults to project.name
bootstrap = "pyz_main.py"  # becomes the archive's __main__.py
```

`bootstrap` must be a file inside the project root. Each build

1. copies the project to a throwaway directory (so `pip` never leaves
   `build/` or `*.egg-info` in the source tree),
2. for the host cell, runs `pip install --target <stage>
   --only-binary :all:` on that copy plus any `project.pip_args`; for a
   foreign cell, builds the project's pure wheels first and installs them
   with `pip --platform <cell wheel tags> --python-version …
   --implementation cp --abi cpXY|abi3|none`,
3. drops console scripts and `__pycache__`, ships `bootstrap` as
   `__main__.py`, and writes `<name>-<version>-<tag>.pyz` into the cell's
   `pyz/` folder with `zipapp` (compressed, `/usr/bin/env python3` shebang),
4. smoke-tests host-compatible archives by running `--version` with an
   isolated home directory and fails with `BuildError` unless the output
   contains the project version; foreign archives skip the smoke test.

The tag is `<os>-<arch>` (`windows-amd64`, `macos-arm64`, …) plus the ABI on
Linux (`linux-amd64-glibc`, `linux-arm64-musl`).

## PyPI distributions

`python -m tools.stationer … --format pypi` builds the **sdist and
wheel** for every `local_dependencies` entry and then for the project itself,
into `output`. Dependencies first matters: `pip install <project>` resolves
declared siblings (like `bowman`) from PyPI, so a project is only
installable from the index once every dependency it declares exists there.
Building them in the same run keeps their versions in lock-step with the
release.

Like the other formats the builds run on staged copies — `python -m build`
never writes `build/` or `*.egg-info` into the checkout. The PyPA `build` tool
comes from a throwaway virtual environment and runs in isolated mode (`-I`):
the default output folder is itself called `build`, which would otherwise
shadow the tool when run from the repository root, and the host needs no
preinstalled tooling. The returned paths are in build order, dependencies
first. Upload the result
with the stationer helper:

```bash
python -m tools.stationer.upload build/pypi \
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

Runs inside `docker run --platform {docker_platform} -v … {image}` — one
container per `(image, docker_platform)` cell, so glibc and musl builds and
both architectures are covered. The
generated worker script installs `binutils`/`dpkg-dev`, creates a venv,
installs the project, freezes it with PyInstaller, copies the binary to
`{name}-{version}-linux-{arch}` (or `…-musl` on Alpine; architecture derived
from `docker_platform`), and assembles `{name}_{version}_{arch}.deb` through
`dpkg-deb --build --root-owner-group` — or an unsigned `.apk` through
`apk mkpkg` on musl — with the configured maintainer and description.

### Windows ARM64 (`WindowsQemuBuilder`)

Targets an ARM64 Windows guest reachable over SSH/SCP (typically QEMU with
`hostfwd`). The builder `scp`s the project and a generated PowerShell worker
to `remote_root`; the worker creates a venv, freezes the entrypoint, ensures
Inno Setup via `winget`, compiles `installer.iss`
(`PrivilegesRequired=lowest`, ARM64-only) into `setup.exe`, and the host
pulls back `{name}-{version}-windows-arm64-setup.exe` plus the bare
executable. Quoting goes through `ps_quote`; the guest bootstrap lives in
`guest/windows-guest-setup.ps1`.

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
- `LinuxConfig` — `docker_platform`, `docker_platforms`, `image`,
  `musl_image`, `maintainer`, `description`.
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
  .build(project, config, output, target)` — the `.pyz` format; `PyzTarget`
  describes one platform/architecture/ABI cell, `PyzTarget.cells()` lists the
  matrix, and `platform_tag()`/`archive_name(name, version, tag)` name the
  result.
- `RemoteBuilder(say, open_vm?, job_settings?)` — serves one authenticated
  job to a pull worker; `remote_steps(project, target)` plans the worker's
  commands, `remote_files(project, target, packaging)` renders injected
  installer inputs, and `RemotePackaging` carries maintainer/description/
  publisher metadata.
- `PypiBuilder(runner).build(project, output)` — the sdist+wheel format;
  builds `local_dependencies` before the project, returns the produced
  distributions.
- `WikiPublisher(repository, wiki_directory, runner?, commit_message?,
  dry_run?)` — synchronizes a local wiki directory with the GitHub Wiki Git
  repository; `wiki_repository_url(repository)` normalizes
  `owner/repo`, HTTPS, or SSH forms into `<repo>.wiki.git`.
- `ReleasePublisher` — bridges built artifacts to `tools.stationer` and
  triggers the wiki sync when `wiki/` exists at the release-config root.
- `Cli.main(arguments)` — the `python -m tools.stationer` entrypoint.
- `QemuVmSettings.from_disk(disk, user?, identity_file?, ssh_port?,
  boot_timeout_seconds?)` / `QemuVm(settings, directory, say)` — boot an
  existing VM disk read-only, run commands over SSH (`start`, `launch`,
  `failure`, `close`); `RemoteBuilder(say, vm_factory?)` accepts any
  `VmSession` factory.

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

`tests/tools/stationer/` covers manifest loading (all tables and
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
  machine). Skipped unless `STATIONER_WINDOWS_HOST` is set;
  `…_PORT`, `…_USER`, `…_KEY`, `…_PYTHON`, and `…_ROOT` tune the SSH
  target.

- **PyPI** — a real `python -m build` of a project and its sibling, run from a
  directory that contains a `build/` folder; needs network access.
- **Windows VM** — `STATIONER_WINDOWS_VM` set to a `qcow2` disk boots it
  with `QemuVm` and builds through `--remote windows`.

Each real build installs a sibling dependency from a staged copy, so the
`local_dependencies` install path is exercised end to end.
