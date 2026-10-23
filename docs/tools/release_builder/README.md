# `release_builder/`

`tools.release_builder` builds release artifacts for a Python project on the three target environments instead of cross-compiling them from one OS.

Two output formats:

- `--format native` (default): frozen PyInstaller installers (macOS `.pkg`, Linux `.deb`, Windows ARM64 setup `.exe`) that need no Python on the target.
- `--format pyz`: one self-contained `<name>-<version>-<platform>.pyz` for the current machine, described by an optional `[pyz]` manifest table (`name`, `bootstrap`). It needs Python on the target and is the format self-updating tools download.

The manifest may take the project version from a file (`project.version_file`, so there is no second copy to go stale), and the `[windows]` worker is only required when building Windows.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`DOCS.md`](DOCS.md) — `tools.release_builder` builds release artifacts for a Python project on the three target environments instead of cross-compiling them from one OS: macOS is built natively, Linux inside a Docker container, and Windows ARM64 inside an SSH-accessible QEMU guest. Each platform produces a bare frozen executable plus a native installer, SHA-256 digests for both, and a machine-readable `artifacts.json` manifest.

### Subdirectories

No subdirectories.

<!-- exactus:inventory:end -->
