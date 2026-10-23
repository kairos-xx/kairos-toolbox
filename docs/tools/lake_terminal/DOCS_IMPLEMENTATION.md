# Implementation state

This revision implements the non-legacy Lake Terminal control-plane architecture:

- SQLite-only normalized state (`sessions`, `commands`, `events`, `processes`, `hosts`).
- Stable host/session/process identities and PID-reuse protection.
- Transactional command lifecycle and normalized event model.
- Authenticated local IPC daemon and event subscriptions.
- Bounded live-output buffers.
- tmux persistence/read-only capture on Unix-capable hosts.
- Windows ConPTY persistent-session backend behind runtime capability detection.
- Process-only observability for direct IDE execution without a PTY.
- Cross-platform runtime/capability detection.
- Provider attribution through native provider hooks and configurable signatures.
- VS Code-compatible editor bridge using the SQLite-backed control-plane API.
- Interactive/read-only `--watch`/`--view`, history/events, status and full diagnostics.
- Remote execution-host definitions and SSH status aggregation.
- Privacy redaction before persistence plus retention/purge.
- Notification rules and agent-state events.

There is intentionally no JSON session/state compatibility path. JSON is used only for configuration where appropriate and for explicit transport/output such as `--status --json`.

- Cursor embedded-agent native hooks (`sessionStart`, shell execution, response/thought, stop and session end) with stable conversation attribution.
- VS Code-compatible extension UI is provider-neutral; Devin-specific code is restricted to the Devin Local Agent hook adapter.
- Component-based installer (`install.py`, `lake_terminal/installer.py`) with an interactive TUI wizard, a persisted manifest at `~/.lake_terminal/install.json` and owned-artefact uninstall.

## Installing

`python lake_terminal/install.py` opens the wizard when interactive;
subcommands drive it non-interactively:

- `install` — resolve and apply a component selection. Options: `--alias`,
  `--bin-dir`, `--lib-dir`, `--mode live|copy`, `--preset minimal|default|full`,
  `--with`, `--without`, `--yes`, `--force`, `--dry-run`, `--quiet`, `--home`.
- `plan` — print the resolved plan without touching the filesystem.
- `status` — component, PATH, interpreter, manifest and state-directory table.
- `uninstall` — remove owned artefacts only; `--purge` also removes state.

The wizard (`↑`/`↓` or `j`/`k` to move, space to toggle, `a` for alias, `m`
for live/copy, enter to install, `q` to cancel) runs only on a TTY and is
skipped by `--yes` and `--dry-run`. Components cover the launcher, tmux,
zsh/bash/PowerShell lifecycle hooks, agent routing, Claude/Codex/Cursor/Devin
provider hooks, web autostart and the macOS copy-URL handler. `live` mode
points the launcher at the checkout; `copy` mode copies the package into
`--lib-dir`.
