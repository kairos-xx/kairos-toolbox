# Lake Terminal & Friends — Wiki

Welcome to the documentation wiki for this repository: a toolkit for making
agentic development **observable, attributable, and shippable**.

A modern workspace is no longer one human typing into one terminal. It is a
developer, IDE-hosted agents, CLI agents, background automation, and remote
hosts all mutating the same tree. The components here exist to answer the
questions that environment creates: *who ran what, where is it running, is it
still alive, and how do we ship it?*

## Components

| Component | Kind | Purpose |
| --- | --- | --- |
| [Lake Terminal](Lake-Terminal) | Tool | Cross-platform execution control plane: persistent terminals, agent attribution, durable command history, normalized events — one SQLite state model. |
| [Arrow](Arrow) | Library | Local URI trust framework: signed, expiring, replay-proof `scheme://` envelopes with provenance, delegation, audit, and native OS elevation. |
| [Release Builder](Release-Builder) | Tool | One TOML manifest → macOS `.pkg`, Debian `.deb`, Windows `.exe`, or self-contained `.pyz`. |
| [Release Publish](Release-Publish) | Tool | Releases compiled into immutable, inspectable, resumable plans. Monorepo-native. |
| [Devin Remote Control](Devin-Remote-Control) | Plugin | Phone-first remote IDE and Devin operations console as a VS Code-compatible extension. |
| [Editor Plugins](Editor-Plugins) | Plugins | Three focused quality-of-life extensions. |

## Wiki map

### Using the tools

- [Getting Started](Getting-Started) — install Lake Terminal, install the plugins, run the CLI.
- [Lake Terminal CLI](Lake-Terminal-CLI) — every `lake` subcommand and flag.
- [Installing Lake Terminal](Lake-Terminal-Install) — wizard, components, deployment modes, the URI handler.
- [Releasing](Releasing) — the end-to-end flow: version bump → tag → CI build → GitHub release.

### Understanding the design

- [Architecture](Architecture) — how the pieces fit and the rules they obey.
- [Data Model & Privacy](Lake-Terminal-Data-Model) — the SQLite-only rule, tables, redaction, recovery.
- [Agent Attribution](Agent-Attribution) — how Claude, Codex, Cursor and Devin executions get identified.
- [Arrow Trust Model](Arrow) — the seven-step boundary-crossing pipeline.

### Building and shipping

- [Release Builder Manifest](Release-Builder) — `[project]`, `[pyz]`, per-platform tables, `local_dependencies`.
- [Release Publish Plans](Release-Publish) — plan/execute/journal/resume, `publish --update`, `repo-sync`.

### Contributing

- [Engineering Standards](Engineering-Standards) — the enforced rules: strict typing, kwarg-only calls, no suppressions.
- [Testing](Testing) — suite layout, the `expect`/`check`/`present` DSL, coverage gates.
- [Glossary](Glossary) · [FAQ](FAQ)

## Quick start

```bash
# Install Lake Terminal (interactive wizard)
python tools/lake_terminal/install.py

# Then:
lake                 # managed persistent shell
lake --status        # what the control plane knows
lake --watch         # live multi-session TUI
lake --web           # browser dashboard
```

See [Getting Started](Getting-Started) for the full walkthrough.
