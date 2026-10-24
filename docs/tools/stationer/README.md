# `stationer/`

`tools.stationer` merges the release builder and the release control plane:

- [`BUILDER.md`](BUILDER.md) — `tools.stationer` builds release artifacts for a Python project on the three target environments instead of cross-compiling them from one OS: macOS is built natively, Linux inside a Docker container, and Windows ARM64 inside an SSH-accessible QEMU guest. Each platform produces a bare frozen executable plus a native installer, SHA-256 digests for both, and a machine-readable `artifacts.json` manifest. A `--format pyz` mode and remote pull workers cover the remaining outputs.
- [`PUBLISH.md`](PUBLISH.md) — the provider-agnostic release control plane for a single repository or a monorepo containing independently versioned projects. It deliberately separates **planning** from **execution**: a release is first compiled into an immutable plan; the same plan can be inspected, rendered, run as a dry-run, executed, journaled, and resumed.

A bare `python -m tools.stationer` runs the pipeline described by
`[tool.stationer.builder]`; the publish subcommands (`init`, `status`, `show`,
`plan`, `release`, `publish`, `repo-sync`) select the control plane, whose
bare-invocation defaults live in `[tool.stationer.publish]`.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`BUILDER.md`](BUILDER.md) — `tools.stationer` builds release artifacts for a Python project on the three target environments instead of cross-compiling them from one OS: macOS is built natively, Linux inside a Docker container, and Windows ARM64 inside an SSH-accessible QEMU guest. Each platform produces a bare frozen executable plus a native installer, SHA-256 digests for both, and a machine-readable `artifacts.json` manifest.
- [`PUBLISH.md`](PUBLISH.md) — `tools.stationer` is a provider-agnostic release control plane for a single repository or a monorepo containing independently versioned projects. It deliberately separates **planning** from **execution**: a release is first compiled into an immutable plan; the same plan can be inspected, rendered, run as a dry-run, executed, journaled, and resumed.

### Subdirectories

No subdirectories.

<!-- exactus:inventory:end -->
