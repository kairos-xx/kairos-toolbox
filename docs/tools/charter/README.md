# `charter/`

`tools.charter` exposes a local TCP port through a managed public endpoint — `ngrok`, `pinggy`, `tunnl`, `runlocal` or `direct` — with one layered settings system (`[tool.charter]`), a keep-alive/TTL lifecycle model and anonymous URL shortening. District Terminal and the stationer's remote workers consume it.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`DOCS.md`](DOCS.md) — `tools.charter` exposes a local TCP port through a managed public endpoint. It is a deliberately small, dependency-free package: five backends (`direct`, `ngrok`, `pinggy`, `tunnl`, `runlocal`), one layered settings system, one process lifecycle model, and one anonymous URL shortener — usable identically from the `charter` CLI and from Python.

### Subdirectories

No subdirectories.

<!-- exactus:inventory:end -->
