# Architecture

This page explains how the components fit together and the rules that hold
the design together.

## The big picture

```text
            ┌──────────────────── AGENTS & HUMANS ────────────────────┐
            │  Claude Code   Codex   Cursor   Devin Local Agent   you │
            └──────────┬───────────────┬───────────────┬──────────────┘
                       │               │               │
                       ▼               ▼               ▼
              ┌────────────────────────────────────────────────┐
              │                 LAKE TERMINAL                  │
              │  canonical sessions · commands · events ·      │
              │  processes · hosts → all in one SQLite DB      │
              └───────┬───────────────────────┬────────────────┘
                      │                       │
        tmux / ConPTY │                       │ daemon IPC · TUI · web
        persistent PTY│                       │ CLI · editor bridge
                      ▼                       ▼
              ┌──────────────┐        ┌─────────────────────────┐
              │    ARROW     │        │   devin_remote_control  │
              │ signed local │        │   gateway → QR → phone  │
              │ URI trust    │        └─────────────────────────┘
              └──────────────┘

        ┌─────────────── SHIPPING ────────────────┐
        │  release_builder → installers / .pyz    │
        │  release_publish → plans → GH releases  │
        └─────────────────────────────────────────┘
```

## Dependency direction

- **`arrow_uri` is standalone.** It is a reusable package under
  `tools/arrow/` with no Lake Terminal dependency; anything that exposes
  local URI endpoints can embed it.
- **Lake Terminal consumes Arrow** through `lake_terminal/arrow_api.py`.
  `arrow_uri` is a declared dependency of `lake-terminal`, installed into
  every build as a `local_dependencies` sibling project (see
  [Release Builder](Release-Builder)).
- **release_builder** knows nothing about Lake Terminal — it builds any
  Python project described by a manifest.
- **release_publish** knows nothing about building — it plans and executes
  releases described by `release.toml`. The two tools meet only through CI:
  a pushed tag triggers the workflow that runs `release_builder`, then
  `release_publish publish` attaches the artifacts (see [Releasing](Releasing)).
- **Plugins are leaf nodes.** `devin_remote_control` is self-contained
  (no runtime npm dependencies); the small plugins depend only on the
  editor API.

## The canonical design rules

1. **SQLite is the only persistent control-plane state.** No JSON session
   registry, no mirror files, no second source of truth. JSON is used only
   for configuration and explicit transport output such as `--status --json`.
   See [Data Model & Privacy](Lake-Terminal-Data-Model).

2. **Honest capability reporting.** When a platform cannot provide a
   persistent PTY (e.g. a direct IDE extension-host process), Lake Terminal
   reports *process-only observability* rather than claiming a session is
   reconnectable when it is not.

3. **Local URIs are a security boundary.** `lake-terminal://` invocations
   pass through [Arrow](Arrow)'s provenance/authentication/authorization
   pipeline before any application code runs.

4. **Releases are planned, then executed.** `release_publish` compiles
   intent into immutable `Action` objects; nothing mutates the world during
   planning. See [Release Publish](Release-Publish).

5. **Determinism everywhere.** Builders capture every external command
   through a `Runner` protocol; the `.pyz` pipeline copies the project
   before `pip` touches it (the source tree stays clean); the plugin
   packager emits byte-identical archives on every run.

6. **Zero suppressions.** The code is clean under `ruff --select ALL`,
   strict `basedpyright`, `pyrefly`, and the full exactus gate without a
   single `# noqa` or type-ignore comment. See
   [Engineering Standards](Engineering-Standards).

## Where things live

| Path | Contents |
| --- | --- |
| `tools/lake_terminal/` | Lake Terminal package, installer, `.pyz` entrypoint, integration assets |
| `tools/arrow/arrow_uri/` | The Arrow trust framework package |
| `tools/release_builder/` | Builder module + `pyz_builder` + Windows guest bootstrap |
| `tools/release_publish/` | Engine, planner, providers, CLI, upload |
| `plugins/*/src/` | Extension source per plugin |
| `tests/` | Test suites mirroring the source tree |
| `docs/` | Long-form technical documentation mirroring the tree |

Next: [Lake Terminal](Lake-Terminal) · [Getting Started](Getting-Started)
