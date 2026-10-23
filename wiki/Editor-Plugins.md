# Editor Plugins

Small, focused extensions for VS Code-compatible editors (VS Code, Cursor,
Windsurf, VSCodium). Each ships a cross-platform `install.py` that discovers
compatible editors and supports interactive install and `--uninstall`.

```bash
python plugins/<name>/install.py           # interactive install
python plugins/<name>/install.py --list    # detected editor targets
python plugins/<name>/install.py --uninstall
```

## The plugins

| Plugin | What it does |
| --- | --- |
| [`close_deleted_editors`](../plugins/close_deleted_editors/) | Closes editors whose files no longer exist on disk — keeps the tab strip honest after branches switch, generated files vanish, or agents delete things. |
| [`open_selected_editor_panel`](../plugins/open_selected_editor_panel/) | Opens an Explorer selection into the editor group you chose, recursively expanding folders and keeping files pinned as real tabs. |
| [`simple_web_server`](../plugins/simple_web_server/) | Launches a localhost static server for the active workspace — start, stop, and open-in-browser commands. |
| [`devin_remote_control`](../plugins/devin_remote_control/) | Phone-first remote IDE & Devin console — [full page](Devin-Remote-Control). |

## close_deleted_editors

Watches for files deleted beneath open tabs (checkout, `git clean`, agent
refactors) and closes the dead editors — so the tab strip only shows files
that exist. Policy helpers normalize ports/roots and decide what qualifies
for closing; docs: `docs/plugins/close_deleted_editors/DOCS.md`.

## open_selected_editor_panel

Explores what you selected in the Explorer — expanding folders recursively —
and opens the files into the chosen editor group, pinned as real tabs.
Useful for "open this whole feature folder beside the current file."
Docs: `docs/plugins/open_selected_editor_panel/DOCS.md`.

## simple_web_server

One-command `python -m http.server` management for the workspace: normalized
ports (1–65535), workspace root resolution, loopback URLs, and start/stop/
browser commands. No config files, no deps.
Docs: `docs/plugins/simple_web_server/DOCS.md`.

## Common traits

- Plain Node/CommonJS + the editor API — no heavy dependency trees.
- Tests under `tests/plugins/<name>/` run with `node --test`.
- Every plugin carries a long-form `DOCS.md` under `docs/plugins/`.
