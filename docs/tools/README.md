# `tools/`

Technical documentation for the companion tools.

<!-- exactus:inventory:start -->
## Directory contents

### Files

No direct files besides this README.

### Subdirectories

- [`arrow/`](arrow/README.md) — Arrow is the reusable URI security/trust layer under `tools/arrow`. Lake Terminal consumes Arrow through its own `arrow_api.py` adapter.
- [`lake_terminal/`](lake_terminal/README.md) — Complete architecture, configuration and Python API reference for the Lake Terminal control plane.
- [`release_builder/`](release_builder/README.md) — `tools.release_builder` builds release artifacts for a Python project on the three target environments instead of cross-compiling them from one OS.
- [`release_publish/`](release_publish/README.md) — `release-publish` is a provider-agnostic release control plane for a single repository or a monorepo containing independently versioned projects.

<!-- exactus:inventory:end -->
