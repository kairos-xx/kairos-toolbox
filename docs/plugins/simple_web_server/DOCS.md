# Simple Workspace Web Server — Technical Documentation

## Architecture

The extension is intentionally split into a deterministic `core.js` and a thin VS Code adapter in `extension.js`. This keeps filesystem/process policy testable without an Extension Host while the adapter owns VS Code lifecycle APIs.

## Commands

- `simpleWebServer.start` — Web Server: Start
- `simpleWebServer.stop` — Web Server: Stop
- `simpleWebServer.open` — Web Server: Open in Browser

## Compatibility

VS Code API `^1.90.0` and compatible VS Code-derived editors. The root `install.py` discovers supported editor CLIs and installs the project as an extension directory.

## Testing

Run `node --test --experimental-test-coverage tests/*.test.js`. Core production modules are required to remain at or above 90% line coverage.

## Lifecycle and failure behavior

Activation registers commands and subscriptions only. Errors are surfaced through VS Code notifications rather than swallowed. Deactivation releases owned resources.

## Server model

The server binds explicitly to `127.0.0.1`, never all network interfaces. It serves the first workspace folder (or the extension host working directory when no workspace exists) using Python’s standard `http.server`.
