# `plugins/`

Technical documentation for the VS Code-compatible extension projects.

<!-- exactus:inventory:start -->
## Directory contents

### Files

No direct files besides this README.

### Subdirectories

- [`close_deleted_editors/`](close_deleted_editors/README.md) — The extension is intentionally split into a deterministic `core.js` and a thin VS Code adapter in `extension.js`. This keeps filesystem/process policy testable without an Extension Host while the adapter owns VS Code lifecycle APIs.
- [`devin_remote_control/`](devin_remote_control/README.md) — The release is deliberately dependency-light: the extension host and remote web application are plain Node.js/CommonJS plus browser JavaScript, HTML, and CSS. No runtime `npm install` is required.
- [`open_selected_editor_panel/`](open_selected_editor_panel/README.md) — Open an Explorer selection into the editor group you chose, recursively expanding folders and keeping files pinned as real tabs.
- [`simple_web_server/`](simple_web_server/README.md) — The extension is intentionally split into a deterministic `core.js` and a thin VS Code adapter in `extension.js`. This keeps filesystem/process policy testable without an Extension Host while the adapter owns VS Code lifecycle APIs.

<!-- exactus:inventory:end -->
