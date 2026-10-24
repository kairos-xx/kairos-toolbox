# `tools/`

Technical documentation for the companion tools.

<!-- exactus:inventory:start -->
## Directory contents

### Files

No direct files besides this README.

### Subdirectories

- [`bowman/`](bowman/README.md) — Bowman is the reusable URI security/trust layer under `tools/bowman`. District Terminal consumes Bowman through its own `bowman_api.py` adapter.
- [`charter/`](charter/README.md) — `tools.charter` exposes a local TCP port through a managed public endpoint — `ngrok`, `pinggy`, `tunnl`, `runlocal` or `direct` — with one layered settings system (`\[tool.charter\]`), a keep-alive/TTL lifecycle model and anonymous URL shortening. District Terminal and the stationer's remote workers consume it.
- [`conjure/`](conjure/README.md) — `conjure` is a host/guest-agnostic job execution layer for local hosts, QEMU virtual machines, and Docker containers.
- [`district/`](district/README.md) — Complete architecture, configuration and Python API reference for the District Terminal control plane.
- [`stationer/`](stationer/README.md) — `tools.stationer` merges the release builder and the release control plane:

<!-- exactus:inventory:end -->
