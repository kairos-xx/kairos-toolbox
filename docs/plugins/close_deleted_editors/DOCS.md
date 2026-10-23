# Close Deleted Editors — Technical Documentation

> Keep your tab strip clean by closing editors whose files no longer exist
> on disk.

## Architecture

The extension is intentionally split into a deterministic `core.js` and a
thin VS Code adapter in `extension.js`. This keeps filesystem/process policy
testable without an Extension Host while the adapter owns VS Code lifecycle
APIs.

## Source layout

- `src/package.json` — extension manifest and command contribution.
- `src/core.js` — `DeletedEditorPolicy`: pure tab classification helpers
  (`uriPath`, `existing`, `deleted`) with an injectable existence probe.
- `src/extension.js` — `DeletedEditors` service, `activate`, `deactivate`.
- `install.py` / `__main__.py` — editor discovery and install lifecycle.
- `tests/` — Node test-runner coverage of `core.js`.

## Activation and triggers

- Activation event: `onStartupFinished`.
- The command is registered on activation and additionally invoked whenever
  VS Code reports workspace file deletions through
  `workspace.onDidDeleteFiles`, so tabs disappear promptly after external
  deletes.

## Commands

- `closeDeletedEditors.close` — Close Deleted Editors

## Runtime behavior

`DeletedEditors.close` scans every tab group, extracts each tab's
`tab.input.uri.fsPath` via `DeletedEditorPolicy.uriPath`, filters the paths
through `DeletedEditorPolicy.deleted` (default probe `fs.existsSync`), and
closes matching tabs with `tabGroups.close(deleted, true)` — the force flag
discards them even when dirty. It returns the number of closed tabs.

## Deletion semantics

Only tabs backed by a filesystem URI are candidates. Untitled, diff, custom
and non-file tabs are ignored because `uriPath` yields `null` for them. The
command is also invoked after VS Code reports workspace file deletions.

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
