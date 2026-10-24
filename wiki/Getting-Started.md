# Getting Started

**Requirements:** Python ≥ 3.13. Plugins target VS Code-compatible editors
(VS Code, Cursor, Windsurf, VSCodium) on macOS, Linux, and Windows.

## Install District Terminal

```bash
python tools/district/install.py
```

On a TTY this opens the **interactive wizard** (`↑`/`↓` or `j`/`k` to move,
space to toggle components, `a` for alias, `m` for live/copy mode, enter to
install, `q` to cancel). Non-interactive operation:

```bash
python tools/district/install.py install --yes
python tools/district/install.py plan      # preview
python tools/district/install.py status    # what is installed
python tools/district/install.py uninstall # owned artifacts only
```

Useful flags: `--preset minimal|default|full`, `--with` / `--without`
component selection, `--alias`, `--mode live|copy|pyz`, `--bin-dir`,
`--lib-dir`, `--dry-run`, `--force`, `--home`. See
[Installing District Terminal](District-Terminal-Install).

## First commands

```bash
district                    # attach to a managed persistent shell
district pytest -q          # run in a managed shell that survives you
district --status           # everything the control plane knows
district --watch            # interactive multi-session dashboard
district --web              # live browser dashboard
district --doctor --full    # validate platform, SQLite, daemon, hooks
```

Full reference: [District Terminal CLI](District-Terminal-CLI).

## Install the plugins

Each plugin ships a cross-platform installer that discovers compatible
editors:

```bash
python plugins/devin_remote_control/install.py          # interactive
python plugins/devin_remote_control/install.py --list   # detected targets
python plugins/devin_remote_control/install.py --uninstall
```

The same pattern applies to `close_deleted_editors`,
`open_selected_editor_panel`, and `simple_web_server`. See
[Editor Plugins](Editor-Plugins) and
[Devin Remote Control](Devin-Remote-Control).

## Repository development

```bash
# Python suite
python -m pytest -q

# Plugin JS tests (example)
node --test tests/plugins/devin_remote_control/js/tools.test.js

# Structural gate
exactus --pipeline unified
```

The full quality bar is documented under
[Engineering Standards](Engineering-Standards) and [Testing](Testing).

## Where to go next

- Understand the pieces → [Architecture](Architecture)
- Ship a release → [Releasing](Releasing)
- Build native installers → [Stationer](Stationer-Builder)
- Publish a monorepo → [Stationer Publish](Stationer-Publish)
