# `simple_web_server/`

The extension is intentionally split into a deterministic `core.js` and a thin VS Code adapter in `extension.js`. This keeps filesystem/process policy testable without an Extension Host while the adapter owns VS Code lifecycle APIs.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`DOCS.md`](DOCS.md) — > Launch a localhost static server for the active workspace with start, > stop and browser commands.

### Subdirectories

No subdirectories.

<!-- exactus:inventory:end -->
