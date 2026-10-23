# Open in Selected Editor Panel — Technical Documentation

## Purpose

Open an Explorer selection into the editor group you chose, recursively expanding folders and keeping files pinned as real tabs.

## Source layout

- `src/package.json`: extension manifest and VS Code contributions.
- `src/extension.js`: activation and command adapter.
- `src/file-tree.js`: deterministic recursive Explorer traversal.
- `tests/`: automated behavior tests.
- `install.py`: editor discovery and installation lifecycle.

## Runtime behavior

The active editor group is captured before traversal. Explorer resources are normalized from the context resource plus VS Code's multi-selection. Directories are recursively enumerated through `workspace.fs.readDirectory`; files are deduplicated by URI and sorted deterministically. Each document is opened with preview disabled. Cancellation stops further opens without undoing documents already opened.

## Failure handling

A failure to open an individual document produces a warning and traversal continues. Enumeration errors that are not a normal non-directory condition propagate rather than being silently hidden.

## Testing

Run `node --test --experimental-test-coverage tests/*.test.js`. The core traversal module must maintain at least 90% line coverage.
