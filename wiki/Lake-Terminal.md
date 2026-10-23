# Lake Terminal

> **A cross-platform execution control plane for humans and coding agents.**

Persistent terminals, process attribution, durable command history,
normalized events, and one observable state model for CLI tools, IDE agents,
automation, and remote execution hosts.

**Platforms:** macOS · Linux · Windows — plus VS Code-compatible editors and
native hooks for Claude Code, Codex, Cursor, and Devin Local Agent.

## The problem

The hard part of modern development is no longer *starting* a terminal — it
is knowing:

- which execution belongs to which agent or human;
- which commands are still running;
- whether a terminal is actually reconnectable;
- what happened before an agent disappeared or a window closed;
- how to inspect several execution surfaces without separate state;
- how an editor can observe terminals without scraping UI;
- how to keep useful history without casually persisting secrets.

Lake Terminal turns executions into **one local control plane**: a canonical
session model, SQLite-backed state, normalized lifecycle events,
process-tree inspection, provider hooks, and platform-specific terminal
backends. CLI, TUI, web, daemon, and editor integrations all consume the
*same* state instead of inventing their own registries.

## Capabilities

- **SQLite-only normalized state** — `sessions`, `commands`, `events`,
  `processes`, `hosts`. No JSON registry or mirror.
- **Stable identities** — host/session/process IDs with PID-reuse protection.
- **Transactional command lifecycle** and a normalized event model.
- **Authenticated local IPC daemon** with event subscriptions.
- **Bounded live-output buffers** — useful tails without unbounded capture.
- **tmux persistence** and read-only capture on Unix-capable hosts.
- **Windows ConPTY backend** behind runtime capability detection.
- **Process-only observability** for direct IDE execution without a PTY —
  honest degraded mode, not false "reconnectable" claims.
- **Provider attribution** — native hooks for Claude Code, Codex, Cursor,
  and Devin Local Agent (including Cursor embedded-agent hooks:
  `sessionStart`, shell execution, response/thought, stop, session end),
  plus configurable process/environment signatures. See
  [Agent Attribution](Agent-Attribution).
- **Editor bridge** — provider-neutral SQLite-backed API; Devin-specific
  code lives only in the Devin Local Agent hook adapter.
- **Monitoring surfaces** — `lake --watch` TUI, read-only `--view`,
  `--status`/`--json`, history, events, and full diagnostics.
- **Remote hosts** — named SSH execution hosts with aggregated status.
- **Privacy** — redaction *before* persistence, retention/purge rules.
- **Installer** — interactive component wizard, persisted manifest,
  owned-artifact uninstall. See [Installing](Lake-Terminal-Install).

## Backends

| Backend | Where | Provides |
| --- | --- | --- |
| tmux | macOS / Linux | Persistent, reconnectable sessions + capture |
| ConPTY | Windows (capable) | Persistent sessions |
| process-only | anywhere | Observation for non-PTY executions |

The backend is selected at runtime by capability detection — not by
platform-name guessing.

## Interfaces

| Surface | Command / entry point |
| --- | --- |
| Interactive shell | `lake` |
| Status | `lake --status [--json]` |
| Watch TUI | `lake --watch` |
| Web dashboard | `lake --web` |
| Events | `lake --subscribe`, `lake --events` |
| Diagnostics | `lake --doctor [--full]` |
| Settings report | `lake --settings` |
| URI handler | `lake --handle-uri <uri>` (OS-registered) |

Full reference: [Lake Terminal CLI](Lake-Terminal-CLI).

## Why not a multiplexer?

Lake Terminal is **not** a tmux replacement — it *uses* tmux/ConPTY as
backends and adds the layer multiplexers lack: queryable state, attribution,
history, events, privacy, and machine-readable APIs. See the comparison
table in the [README](../README.md) or [FAQ](FAQ).

Next: [CLI reference](Lake-Terminal-CLI) ·
[Data Model & Privacy](Lake-Terminal-Data-Model) ·
[Installing](Lake-Terminal-Install)
