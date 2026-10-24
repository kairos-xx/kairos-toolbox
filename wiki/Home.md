# District Terminal & Friends — Wiki

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
| [District Terminal](District-Terminal) | Tool | Cross-platform execution control plane: persistent terminals, agent attribution, durable command history, normalized events — one SQLite state model. |
| [Bowman](Bowman) | Library | Local URI trust framework: signed, expiring, replay-proof `scheme://` envelopes with provenance, delegation, audit, and native OS elevation. |
| [Stationer](Stationer-Builder) | Tool | One TOML manifest → macOS `.pkg`, Debian `.deb`, Windows `.exe`, or self-contained `.pyz`. |
| [Stationer Publish](Stationer-Publish) | Tool | Releases compiled into immutable, inspectable, resumable plans. Monorepo-native. |
| [Devin Remote Control](Devin-Remote-Control) | Plugin | Phone-first remote IDE and Devin operations console as a VS Code-compatible extension. |
| [Editor Plugins](Editor-Plugins) | Plugins | Three focused quality-of-life extensions. |

## Wiki map

### Using the tools

- [Getting Started](Getting-Started) — install District Terminal, install the plugins, run the CLI.
- [District Terminal CLI](District-Terminal-CLI) — every `district` subcommand and flag.
- [Installing District Terminal](District-Terminal-Install) — wizard, components, deployment modes, the URI handler.
- [Releasing](Releasing) — the end-to-end flow: version bump → tag → CI build → GitHub release.

### Understanding the design

- [Architecture](Architecture) — how the pieces fit and the rules they obey.
- [Data Model & Privacy](District-Terminal-Data-Model) — the SQLite-only rule, tables, redaction, recovery.
- [Agent Attribution](Agent-Attribution) — how Claude, Codex, Cursor and Devin executions get identified.
- [Bowman Trust Model](Bowman) — the seven-step boundary-crossing pipeline.

### Building and shipping

- [Stationer Manifest](Stationer-Builder) — `[project]`, `[pyz]`, per-platform tables, `local_dependencies`.
- [Release Publish Plans](Stationer-Publish) — plan/execute/journal/resume, `publish --update`, `repo-sync`.

### Contributing

- [Engineering Standards](Engineering-Standards) — the enforced rules: strict typing, kwarg-only calls, no suppressions.
- [Testing](Testing) — suite layout, the `expect`/`check`/`present` DSL, coverage gates.
- [Glossary](Glossary) · [FAQ](FAQ)

## Quick start

```bash
# Install District Terminal (interactive wizard)
python tools/district/install.py

# Then:
district                 # managed persistent shell
district --status        # what the control plane knows
district --watch         # live multi-session TUI
district --web           # browser dashboard
```

See [Getting Started](Getting-Started) for the full walkthrough.
