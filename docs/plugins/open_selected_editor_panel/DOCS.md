# Open in Selected Editor Panel — Technical Documentation

> Open an Explorer selection into the editor group you chose, recursively
> expanding folders and keeping files pinned as real tabs.

## Purpose

Open an Explorer selection into the editor group you chose, recursively
expanding folders and keeping files pinned as real tabs.

## Source layout

- `src/package.json`: extension manifest and VS Code contributions.
- `src/extension.js`: activation and command adapter.
- `src/file-tree.js`: deterministic recursive Explorer traversal.
- `tests/`: automated behavior tests.
- `install.py`: editor discovery and installation lifecycle.

## Commands and menus

- `openSelectedEditorPanel.open` — *Open in the Selected Editor Panel*;
  contributed to `explorer/context` when `explorerResourceIsFolder ==
  false` (file selections).
- `openSelectedEditorPanel.openAll` — *Open All Files in the Selected
  Editor Panel*; contributed to `explorer/context` when
  `explorerResourceIsFolder == true` (folder selections).

Both appear in the Explorer context menu's `navigation@20` group, and the
command palette.

## Runtime behavior

The active editor group is captured before traversal; it is the destination
`ViewColumn` (falling back to `ViewColumn.Active` when none is resolved).
Explorer resources are normalized from the context resource plus VS Code's
multi-selection. Directories are recursively enumerated through
`workspace.fs.readDirectory`; files are deduplicated by URI and sorted
deterministically. Each document is opened with preview disabled, so tabs
stay pinned rather than being reused as a preview. Cancellation stops
further opens without undoing documents already opened.

## Failure handling

A failure to open an individual document produces a warning and traversal
continues. Enumeration errors that are not a normal non-directory condition
propagate rather than being silently hidden.

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

## Testing

Run `node --test --experimental-test-coverage tests/*.test.js`. The core
traversal module must maintain at least 90% line coverage.
