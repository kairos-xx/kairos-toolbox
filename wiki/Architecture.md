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
              │                 DISTRICT TERMINAL                  │
              │  canonical sessions · commands · events ·      │
              │  processes · hosts → all in one SQLite DB      │
              └───────┬───────────────────────┬────────────────┘
                      │                       │
        tmux / ConPTY │                       │ daemon IPC · TUI · web
        persistent PTY│                       │ CLI · editor bridge
                      ▼                       ▼
              ┌──────────────┐        ┌─────────────────────────┐
              │    BOWMAN     │        │   devin_remote_control  │
              │ signed local │        │   gateway → QR → phone  │
              │ URI trust    │        └─────────────────────────┘
              └──────────────┘

        ┌─────────────── SHIPPING ────────────────┐
        │  stationer → installers / .pyz   │
        │         → plans → GH releases  │
        └─────────────────────────────────────────┘
```

## Dependency direction

- **`bowman` is standalone.** It is a reusable package under
  `tools/bowman/` with no District Terminal dependency; anything that exposes
  local URI endpoints can embed it.
- **District Terminal consumes Bowman** through `district/bowman_api.py`.
  `bowman` is a declared dependency of `district`, installed into
  every build as a `local_dependencies` sibling project (see
  [Stationer](Stationer-Builder)).
- **stationer's builder** knows nothing about District Terminal — it builds
  any Python project described by a manifest.
- **stationer's control plane** knows nothing about building — it plans and
  executes releases described by `release.toml`. The two halves meet only
  through CI: a pushed tag triggers the workflow that runs `stationer`, then
  `stationer publish` attaches the artifacts (see [Releasing](Releasing)).
- **Plugins are leaf nodes.** `devin_remote_control` is self-contained
  (no runtime npm dependencies); the small plugins depend only on the
  editor API.

## The canonical design rules

1. **SQLite is the only persistent control-plane state.** No JSON session
   registry, no mirror files, no second source of truth. JSON is used only
   for configuration and explicit transport output such as `--status --json`.
   See [Data Model & Privacy](District-Terminal-Data-Model).

2. **Honest capability reporting.** When a platform cannot provide a
   persistent PTY (e.g. a direct IDE extension-host process), District Terminal
   reports *process-only observability* rather than claiming a session is
   reconnectable when it is not.

3. **Local URIs are a security boundary.** `district://` invocations
   pass through [Bowman](Bowman)'s provenance/authentication/authorization
   pipeline before any application code runs.

4. **Releases are planned, then executed.** `stationer` compiles
   intent into immutable `Action` objects; nothing mutates the world during
   planning. See [Stationer Publish](Stationer-Publish).

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
| `tools/district/` | District Terminal package, installer, `.pyz` entrypoint, integration assets |
| `tools/bowman/bowman/` | The Bowman trust framework package |
| `tools/stationer/` | Builder (`builder`, `pyz_builder`, `publisher`, remote workers, Windows guest bootstrap) + release control plane (engine, planner, providers, CLI, upload) |
| `plugins/*/src/` | Extension source per plugin |
| `tests/` | Test suites mirroring the source tree |
| `docs/` | Long-form technical documentation mirroring the tree |

Next: [District Terminal](District-Terminal) · [Getting Started](Getting-Started)
