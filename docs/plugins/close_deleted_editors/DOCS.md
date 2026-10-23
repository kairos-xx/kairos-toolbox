# Close Deleted Editors — Technical Documentation

## Architecture

The extension is intentionally split into a deterministic `core.js` and a thin VS Code adapter in `extension.js`. This keeps filesystem/process policy testable without an Extension Host while the adapter owns VS Code lifecycle APIs.

## Commands

- `closeDeletedEditors.close` — Close Deleted Editors

## Compatibility

VS Code API `^1.90.0` and compatible VS Code-derived editors. The root `install.py` discovers supported editor CLIs and installs the project as an extension directory.

## Testing

Run `node --test --experimental-test-coverage tests/*.test.js`. Core production modules are required to remain at or above 90% line coverage.

## Lifecycle and failure behavior

Activation registers commands and subscriptions only. Errors are surfaced through VS Code notifications rather than swallowed. Deactivation releases owned resources.

## Deletion semantics

Only tabs backed by a filesystem URI are candidates. Untitled, diff, custom and non-file tabs are ignored. The command is also invoked after VS Code reports workspace file deletions.
