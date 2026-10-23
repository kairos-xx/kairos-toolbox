# Release Builder

`tools.release_builder` turns one TOML manifest into real platform
artifacts: a **macOS `.pkg`**, a **Debian `.deb`**, a **Windows ARM64 setup
`.exe`**, or — with `--format pyz` — a **self-contained
`name-version-platform.pyz`**. Every build also emits SHA-256 digests and a
machine-readable `artifacts.json`.

```bash
python -m tools.release_builder \
  --project /path/to/project \
  --manifest /path/to/release-builder.toml \
  --output /path/to/release \
  [--platform macos --platform linux] [--format native|pyz|pypi]
```

## Output formats

| Format | Needs Python on target? | How |
| --- | --- | --- |
| `native` (default) | No | PyInstaller-frozen executable + OS installer |
| `pyz` | Yes | `zipapp` archive, built per platform |
| `pypi` | — | sdist + wheel per `local_dependencies` entry, then the project |

The `.pyz` format is what self-updating tools (like
[Lake Terminal](Lake-Terminal)) download: `lake-terminal-1.2.3-macos-arm64.pyz`.

## Manifest reference

```toml
[project]
name = "my-app"                 # artifact name prefix
version_file = "pkg/product.py" # or literal `version = "1.2.3"`
entrypoint = "app.py"           # or `entry_module`
identifier = "dev.example.app"  # installer identity
console = true                  # keep console window (default false)
pyinstaller_args = []           # extra PyInstaller flags
pip_args = []                   # extra pip install flags
local_dependencies = ["../arrow"]  # sibling projects installed first

[pyz]
name = "my-app"                 # optional; defaults to project.name
bootstrap = "pyz_main.py"       # shipped as the archive's __main__.py

[macos]                         # optional per-platform tables
python = "/usr/bin/python3"
signing_identity = null

[linux]
docker_platform = "linux/arm64"
image = "python:3.13-slim-bookworm"
maintainer = "You <you@example.com>"
description = "My app"

[windows]                       # only required when building windows
host = "192.0.2.9"              # SSH build worker
port = 2222
user = "builder"
identity_file = "~/.ssh/id_ed25519"
```

### `local_dependencies`

Sibling project directories (each with its own `pyproject.toml`) installed
into the build environment **before** the main project — e.g. Lake Terminal
declares `["../arrow"]` because it imports `arrow_uri`. Missing directories
and duplicate names fail the build early with `BuildError`. In source
archives for remote workers they ship under `.local-deps/<name>/`.

The same ordering matters at publish time: `pip install lake-terminal`
resolves `arrow-local-uri` from PyPI, so the sibling must be published too.
`--format pypi` builds the siblings first, then the project — all from
staged copies — and the whole directory uploads in one command:

```bash
python -m tools.release_builder --project tools/lake_terminal \
  --manifest tools/lake_terminal/release-builder.toml \
  --format pypi --output build/pypi
python -m tools.release_publish.upload build/pypi
```

## Model

`ManifestLoader.load` validates the manifest into a `BuildConfiguration`;
`ReleaseBuilder` dispatches each selected platform to its builder
sequentially and finally writes `artifacts.json`. Every external command
flows through the **`Runner` protocol** (`CommandRunner` in production) —
builds are deterministic, captured, and fail fast via `BuildError` on any
non-zero exit.

## The `.pyz` pipeline

Compiled dependencies make an archive platform-specific, so one is built
per target machine rather than cross-compiled:

1. Copy the project to a throwaway directory — `pip` never leaves
   `build/` or `*.egg-info` in your source tree.
2. `pip install --target <stage> --only-binary :all:` the copy plus
   `pip_args`.
3. Drop console scripts and `__pycache__`, ship `bootstrap` as
   `__main__.py`, write the archive with `zipapp` (compressed,
   `/usr/bin/env python3` shebang).
4. **Smoke test:** run `--version` with an isolated home; fail with
   `BuildError` unless the output contains the project version.

Platform tags: `macos-arm64`, `macos-x86_64`, `linux-x86_64`,
`linux-aarch64`, `windows-amd64`, `windows-arm64`.

## vs. PyInstaller / Nuitka directly

Those tools freeze an interpreter *into* one binary. `release_builder` goes
further — real OS installers (PKG/DEB/EXE), `.pyz` archives, digests, a
manifest, deterministic command capture, and a clean source tree — while
still using PyInstaller under the hood for the native path.

## Real-environment tests

Unit tests fake the `Runner`. `tests/tools/release_builder/
test_real_pipelines.py` runs the actual pipelines where the host can:

- **Linux** — real Docker build (`docker info` must answer): installs the
  project *and* its sibling dependency from staged copies in the container
  and asserts the `.deb` is a real `ar` archive.
- **macOS** — real `venv` → `pip install` → PyInstaller → `pkgbuild` on a
  macOS host; asserts a real `xar` `.pkg`.
- **Windows** — real SSH/QEMU guest, enabled by
  `RELEASE_BUILDER_WINDOWS_HOST` (plus `…_PORT`/`…_USER`/`…_KEY`/`…_PYTHON`/
  `…_ROOT`).

Next: [Release Publish](Release-Publish) · [Releasing](Releasing)
