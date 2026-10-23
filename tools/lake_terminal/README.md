# Lake Terminal

> **A cross-platform execution control plane for humans and coding agents.**  
> Persistent terminals, process attribution, durable command history, normalized events, and one observable state model for CLI tools, IDE agents, automation, and remote execution hosts.

<p align="center">
  <strong>macOS · Linux · Windows · VS Code-compatible editors · Claude Code · Codex · Cursor · Devin Local Agent</strong>
</p>

---

## Why Lake Terminal exists

Software development is no longer driven by one person typing into one terminal. A modern workspace can have a developer, an IDE-hosted coding agent, a CLI agent, background automation, remote SSH hosts, and several shells all changing the same project at once.

The hard problem is no longer merely **starting a terminal**. It is knowing:

- which execution belongs to which agent or human;
- which commands are still running;
- whether a terminal is actually reconnectable;
- what happened before an agent disappeared or a window closed;
- how to inspect several execution surfaces without maintaining separate state;
- how an editor integration can observe terminals without scraping UI state;
- and how to retain useful history without casually persisting secrets.

**Lake Terminal turns those executions into one local control plane.** It uses a canonical session model, SQLite-backed state, normalized lifecycle events, process-tree inspection, provider hooks, and platform-specific terminal backends. CLI, TUI, web, daemon, and editor integrations all consume that same state instead of inventing their own registries.

```text
Human terminal ───────┐
Claude / Codex ───────┤
Cursor / Devin ───────┤        ┌───────────────┐
IDE processes ────────┼───────▶│ Lake Terminal control │
Remote hosts ─────────┘        │     plane      │
                               └───────┬───────┘
                                       │
                ┌──────────────────────┼──────────────────────┐
                ▼                      ▼                      ▼
          persistent PTY          SQLite state          event stream
          tmux / ConPTY      sessions/commands/...     daemon / TUI / web
```

## What you get

| Capability | What Lake Terminal provides |
| --- | --- |
| **Persistent execution** | tmux-backed sessions on macOS/Linux and ConPTY-backed sessions on Windows when the platform supports them. |
| **Honest fallback** | Direct IDE/extension-host processes remain observable in process-only mode instead of being falsely labelled reconnectable. |
| **Agent attribution** | Native hooks for Claude Code, Codex, Cursor and Devin Local Agent, plus configurable process/environment signatures. |
| **One state model** | SQLite is the only persistent session/control-plane database. There is no JSON session registry or state mirror. |
| **Command lifecycle** | Durable command start/finish/exit information with process-tree observation as a fallback. |
| **Observability** | CLI status, interactive TUI, read-only views, event subscriptions, history and a live web dashboard. |
| **Editor integration** | A machine-readable local API and daemon event stream suitable for VS Code-compatible extensions. |
| **Remote hosts** | Named SSH execution hosts and aggregated status without recursively confusing local and remote state. |
| **Privacy controls** | Redaction before persistence, configurable retention, bounded transient output buffers and explicit purge. |
| **Recovery** | Transactional schema upgrades, backend reconciliation, diagnostics and SQLite integrity handling. |

## Quick start

From this checkout:

```bash
python lake_terminal/install.py
```

The installer supports an interactive component wizard as well as non-interactive `install`, `plan`, `status`, and `uninstall` operations. A normal installed command is `lake`.

```bash
# Create and attach to a managed persistent shell.
shared

# Create a managed shell, run a command, then keep the shell alive.
lake pytest -q

# See everything Lake Terminal currently knows about.
lake --status

# Interactive multi-session monitoring.
lake --watch

# Browser dashboard backed by the same control-plane state.
lake --web

# Deep diagnostics.
lake --doctor --full
```

## Designed for the agentic development loop

A coding agent should not need a special, invisible terminal universe. Lake Terminal makes agent execution inspectable through the same primitives used for human execution.

Provider hooks record the strongest attribution available: provider, provider session, workspace and lifecycle state. When a native provider hook is unavailable, Lake Terminal can progressively fall back to editor/environment/process evidence. The resulting attribution is stored with the session rather than inferred independently by every UI.

This matters when several agents work concurrently. An editor can ask for machine-readable local status:

```bash
lake --status --json --local-only
```

Automation can consume normalized events:

```bash
lake --subscribe
```

An agent can idempotently obtain/reuse its managed session:

```bash
lake --agent
# or
lake --ensure
```

The result is a useful separation of concerns: **agents execute work; Lake Terminal owns execution identity, lifecycle and observability.**

## Platform model

Lake Terminal detects the execution host rather than assuming the desktop operating system is the execution environment.

### macOS and Linux

When `tmux` is available, Lake Terminal uses it for persistent, reconnectable terminals and read-only capture. Process-tree inspection augments terminal state with workload information.

### Windows

When the Windows pseudoconsole API is available, Lake Terminal uses ConPTY for persistent PowerShell/session execution. The ConPTY layer supports creation, input, output, resize, liveness and termination.

### Process-only environments

If no persistent PTY backend is available—or an IDE runs a process directly—Lake Terminal still records and observes the execution, but marks it as non-reconnectable. This is intentional: observability does not imply terminal persistence.

## One database, one truth

Canonical state lives in:

```text
~/.lake_terminal/lake.db
```

The normalized SQLite model stores sessions, commands, events, observed processes, remote hosts, web-server registration and agent context. Updates are transactional. Stable session/host/process identities protect the model from PID reuse and short-lived process ambiguity.

JSON is used only where it is appropriate—for user configuration or explicit JSON transport/output—not as a parallel state database.

## Everyday commands

```bash
lake                         # create/attach
lake <id>                    # attach to a reconnectable session
lake <id> --exec <cmd...>    # execute in an existing session
lake <id> --send <text...>   # type text into a session and press Enter
lake <id> --send-key <key>   # press one key (Enter, C-c, Escape, Up, ...)
lake <id> --kill             # terminate/remove
lake --list                  # list local sessions
lake --status [--json]       # local + configured remote status
lake --watch [--once]        # TUI dashboard
lake --view <id>             # read-only session view
lake --history               # durable command history
lake --events                # normalized events
lake --web                   # live web dashboard
lake --hosts                 # configured remote hosts
lake --doctor --full         # diagnostics
lake --purge                 # apply retention policy immediately
lake --mcp                   # resident MCP stdio server for agent clients
```

`lake --help` is intentionally a complete operational manual, including agent-routing, daemon, remote-host and internal integration endpoints.

## MCP server

`lake --mcp` starts a resident MCP (Model Context Protocol) stdio server. The server keeps `LakeTerminal` in-process for the client's lifetime, so every call avoids interpreter and service startup cost; requests execute directly against the live session backends.

```bash
lake --mcp
```

Exposed tools:

| Tool | Purpose |
|---|---|
| `lake_exec` | Run a shell command in a session; returns captured output and exit status. |
| `lake_send` | Type text and/or press keys (`C-c`, `Enter`, `Escape`, `Up`, `Tab`, ...) to answer prompts, drive REPLs or interrupt processes. Enter defaults to true when `text` is given, false for key-only input. |
| `lake_output` | Read the recent pane output of a session. |
| `lake_sessions` | List live sessions. |
| `lake_new` | Create a detached session; optional `command` and `cwd`. |
| `lake_kill` | Destroy a session and its backend. |
| `lake_events` | Recent normalized activity events; optional `limit` and `filter`. |
| `lake_commands` | Recorded command history; optional `limit` and `session_id`. |

Tools that target a session accept an optional `session_id`; it defaults only when exactly one live session exists. Every tool returns human-readable `content` plus machine-readable `structuredContent` matching its declared `outputSchema` (protocol `2025-06-18`). Sessions created through `lake_new` are attributed as agent-initiated.

To register the server with an MCP client such as Claude Desktop, add it to the client's server configuration:

```json
{
  "mcpServers": {
    "lake-terminal": {
      "command": "lake",
      "args": ["--mcp"]
    }
  }
}
```

## Control-plane architecture

```text
                    ┌─────────────────────────────┐
                    │        provider hooks       │
                    │ Claude · Codex · Cursor ·   │
                    │ Devin · configurable agents │
                    └──────────────┬──────────────┘
                                   │ attribution
                                   ▼
┌─────────────┐          ┌──────────────────────┐          ┌─────────────┐
│ tmux / PTY  │◀────────▶│      LakeTerminal      │◀────────▶│ process tree│
│   ConPTY    │          │ backend-neutral core │          │ inspection  │
└─────────────┘          └──────────┬───────────┘          └─────────────┘
                                   │
                         ┌─────────▼──────────┐
                         │   LakeDatabase   │
                         │ SQLite-only state  │
                         └─────────┬──────────┘
                                   │
              ┌────────────────────┼─────────────────────┐
              ▼                    ▼                     ▼
        daemon / IPC          CLI / TUI             web dashboard
        event stream          history/status        WebSocket actions
```

The core deliberately keeps presentation surfaces downstream of the canonical model. The web dashboard, TUI and editor bridge do not maintain independent session registries.

## Privacy and retention

Lake Terminal redacts common credential forms before persistence, including token/password assignments, authorization headers and common secret-token formats. Additional patterns can be configured in:

```text
~/.lake_terminal/privacy.json
```

Default policy values are 30 days of command history, 90 days of events, no persisted terminal output, and a 256 KiB output limit when persistence is enabled. `lake --purge` applies the active retention policy immediately.

## Remote and editor workflows

Remote machines are modeled as execution hosts. Add them with:

```bash
lake --host-add build-box user@example-host lake
lake --hosts
lake --status
```

`--status` may aggregate configured hosts over SSH. `--local-only` disables aggregation and is the correct mode for editor integrations and remote recursion prevention.

For VS Code-compatible integrations, the intended machine interface is the SQLite-backed CLI plus daemon events—not direct database mutation and not a JSON registry.

## Installation model

The component installer can install only what a machine needs: launcher, tmux, shell lifecycle hooks, agent routing, provider hooks, web startup and the macOS copy-URL integration.

```bash
python lake_terminal/install.py plan --preset default
python lake_terminal/install.py install --preset default --yes
python lake_terminal/install.py status
python lake_terminal/install.py uninstall
```

Two deployment modes are supported:

- **live** — the launcher points at the checkout; useful while developing Lake Terminal;
- **copy** — the package is copied into the configured library directory.

The install manifest is stored at `~/.lake_terminal/install.json` so uninstall removes Lake Terminal-owned artifacts rather than indiscriminately deleting user configuration.

## Python API

Lake Terminal is also usable as a Python library. The main programmatic entry points are `LakeTerminal`, `SessionRepository`, `LakeDatabase`, `CommandLifecycle`, `RuntimePlatform`, `ProcessInspector`, `AgentConfiguration`, `WebDashboard`, `EventServer`, and the provider hook adapters.

```python
from pathlib import Path

from lake_terminal.core import SessionRepository, LakeTerminal
from lake_terminal.storage import LakeDatabase

home = Path.home()
database = LakeDatabase(home=home)
repository = SessionRepository(home=home)
service = LakeTerminal(repository=repository)

entry = service.create(initial_command=("python", "-m", "pytest"))
print(entry.id)
print(database.schema_version())
```

For the complete API, data model, CLI semantics, configuration files, daemon protocol, platform backends and every public class/function, see **[DOCS.md](../../docs/tools/lake_terminal/DOCS.md)**.

## Project principles

Lake Terminal is intentionally built around a few constraints:

1. **No legacy JSON state path.** SQLite is canonical.
2. **No fake persistence.** A process is reconnectable only when its backend actually supports it.
3. **Provider-neutral core.** Provider-specific behavior stays in hook adapters.
4. **Execution-host truth.** Capabilities belong to the machine/container/remote host running the command.
5. **One event model.** CLI, TUI, web and editor surfaces consume normalized lifecycle state.
6. **Privacy before persistence.** Redaction and retention are control-plane concerns, not UI afterthoughts.
7. **Headless first.** Status, execution, subscriptions and lifecycle endpoints are automation-friendly.

## Development

The repository includes Python tests for the control plane and Node tests for the web assets. The project is designed to run across macOS, Linux and Windows, with backend-specific tests isolated behind capability/fake layers.

```bash
python -m pytest lake_terminal/tests
node --test lake_terminal/lake_terminal/web_assets/app.test.mjs
```

## Documentation

- **[DOCS.md](../../docs/tools/lake_terminal/DOCS.md)** — complete architecture, configuration and Python API reference.
- **[DOCS_IMPLEMENTATION.md](../../docs/tools/lake_terminal/DOCS_IMPLEMENTATION.md)** — concise implementation-state notes.
- `lake --help` — complete command-line operational manual.

---

**Lake Terminal makes concurrent agent execution observable, attributable and controllable without forcing every agent, editor and terminal to invent its own state.**

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`PLATFORMS.md`](PLATFORMS.md) — Lake Terminal is OS- and CPU-architecture independent at the control-plane layer. Backend capabilities are discovered at runtime.
- [`SECURITY.md`](SECURITY.md) — Lake Terminal is a local execution control plane. Session metadata, command history, process information, IPC authentication material, and optional terminal output are private user data.
- [`__init__.py`](__init__.py) — Runnable wrapper exposing the Lake Terminal control-plane entry points.
- [`__main__.py`](__main__.py) — Execute the Lake Terminal control plane as a module or directory.
- [`install.py`](install.py) — Install, inspect, and remove the Lake Terminal control-plane integration.
- [`pyproject.toml`](pyproject.toml) — Provide pyproject toml content.

### Subdirectories

- [`integration/`](integration/README.md) — This harness validates the part of Lake Terminal most likely to fail in real agentic development: **execution attribution under concurrent providers and surfaces**.
- [`lake_terminal/`](lake_terminal/README.md) — Cross-platform coding-agent execution observability and control plane.

<!-- exactus:inventory:end -->

## Integration qualification

Lake includes a first-class qualification harness for the ambiguity that
matters most in multi-agent development: provider/session attribution under
concurrent execution.

```bash
lake test integration synthetic all
lake test integration concurrent
lake test integration real codex
lake test integration real claude
lake test integration real cursor
lake test integration real devin
lake explain SESSION_ID
```

Synthetic qualification is unattended and suitable for CI/Docker. Real mode
prints a unique probe command for the selected agent and waits for the
provider metadata propagated by Lake's actual hook/environment integration.
It therefore verifies observed identity rather than trusting which provider
the operator said they were testing.

See `integration/README.md` for the concurrency matrix, Docker workflow, and
Docker Sandboxes workflow.


## Arrow URI control

Lake Terminal uses the independent `tools/arrow` framework as its URI trust
boundary. `LakeArrow` creates signed, expiring, one-use `lake-terminal://`
control URIs and dispatches validated operations into the existing authenticated
IPC daemon. The installer registers the URI scheme with Launch Services on
macOS, XDG on Linux, and the per-user URL Protocol registry on Windows.
