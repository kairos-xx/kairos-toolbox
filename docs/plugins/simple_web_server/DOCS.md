# Simple Workspace Web Server — Technical Documentation

> Launch a localhost static server for the active workspace with start,
> stop and browser commands.

## Architecture

The extension is intentionally split into a deterministic `core.js` and a
thin VS Code adapter in `extension.js`. This keeps filesystem/process policy
testable without an Extension Host while the adapter owns VS Code lifecycle
APIs.

## Source layout

- `src/package.json` — extension manifest and command contributions.
- `src/core.js` — `ServerPolicy`: pure command/URL/port logic
  (`normalizePort`, `url`, `command`, `root`).
- `src/extension.js` — `WebServer` service owning the child process,
  `activate`, `deactivate`.
- `install.py` / `__main__.py` — editor discovery and install lifecycle.
- `tests/` — Node test-runner coverage of `core.js`.

## Activation

Activation event: `onStartupFinished`. Activation registers commands and
subscriptions only; no server is spawned until explicitly started.

## Commands

- `simpleWebServer.start` — Web Server: Start
- `simpleWebServer.stop` — Web Server: Stop
- `simpleWebServer.open` — Web Server: Open in Browser

## Configuration

| Setting | Default | Description |
| --- | --- | --- |
| `simpleWebServer.port` | `8000` | Listen port; validated to `1..65535` by `ServerPolicy.normalizePort`, which throws `RangeError` on invalid values. |
| `simpleWebServer.python` | `python3` | Interpreter used to launch `http.server`. |

## Server model

The server binds explicitly to `127.0.0.1`, never all network interfaces. It
serves the first workspace folder (or the extension host working directory
when no workspace exists) using Python's standard `http.server` —
`python -m http.server <port> --bind 127.0.0.1 --directory <root>` — spawned
with `stdio: "ignore"`. The browsable URL is
`http://127.0.0.1:<port>/`.

`start` is idempotent while a server process is owned (a second call returns
early); `stop` terminates it; `open` launches the URL through
`env.openExternal`. A start confirmation notification shows the bound URL.

## Compatibility

VS Code API `^1.90.0` and compatible VS Code-derived editors. The installer
discovers Visual Studio Code (`code`), VS Code Insiders (`code-insiders`),
VSCodium (`codium`/`vscodium`), Cursor (`cursor`), Windsurf (`windsurf`),
and Devin (`devin`) through their CLI commands, then installs the extension
as an extension directory under each editor's extensions folder.

## Installation

```bash
python install.py                 # interactive editor selection
python install.py --list          # list detected editors
python install.py --editor code --editor cursor
python install.py --uninstall     # remove the extension
```

## Lifecycle and failure behavior

Activation registers commands and subscriptions only. Errors are surfaced
through VS Code notifications rather than swallowed. Deactivation releases
owned resources.

## Testing

Run `node --test --experimental-test-coverage tests/*.test.js`. Core
production modules are required to remain at or above 90% line coverage.
