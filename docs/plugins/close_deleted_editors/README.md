# `close_deleted_editors/`

The extension is intentionally split into a deterministic `core.js` and a thin VS Code adapter in `extension.js`. This keeps filesystem/process policy testable without an Extension Host while the adapter owns VS Code lifecycle APIs.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`DOCS.md`](DOCS.md) — > Keep your tab strip clean by closing editors whose files no longer exist > on disk.

### Subdirectories

No subdirectories.

<!-- exactus:inventory:end -->
