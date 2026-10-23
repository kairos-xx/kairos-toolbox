# Simple Workspace Web Server

> Launch a localhost static server for the active workspace with start, stop and browser commands.

## Why this exists

This extension removes a small but persistent IDE workflow cost and is designed to work in VS Code and compatible editors. It is dependency-light, local-first, and ships with a cross-platform installer.

## Features

- VS Code-compatible extension implementation under `src/`.
- Automated tests under `tests/` with a 90% minimum coverage target for owned core logic.
- Full technical documentation in [`DOCS.md`](../../docs/plugins/simple_web_server/DOCS.md).
- Cross-platform `install.py` that discovers compatible editors and supports interactive installation and `--uninstall`.
- No marketplace account is required for local installation.

## Install

```bash
python3 install.py
```

The installer discovers compatible editors and asks where to install. To remove it:

```bash
python3 install.py --uninstall
```

Use `python3 install.py --list` to inspect detected targets and `--editor <id>` for non-interactive selection.

## Development

The extension manifest is `src/package.json`. Run the JavaScript tests from the project root with the command documented in `docs/plugins/simple_web_server/DOCS.md`.

## Compatibility

The installer recognizes Visual Studio Code and common VS Code-family editors on macOS, Linux, and Windows when their CLI or extension directory is discoverable.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`__init__.py`](__init__.py) — Runnable wrapper exposing the extension installer entry point.
- [`__main__.py`](__main__.py) — Execute the extension installer as a module or directory.
- [`install.py`](install.py) — Install or uninstall the extension in VS Code-compatible editors.

### Subdirectories

- [`src/`](src/README.md) — Provide core js content.
- [`vsix/`](vsix/README.md) — Provide simple web server 1.0.0 vsix content.

<!-- exactus:inventory:end -->
