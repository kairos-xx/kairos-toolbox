# District Terminal

> **A cross-platform execution control plane for humans and coding agents.**  
> Persistent terminals, process attribution, durable command history, normalized events, and one observable state model for CLI tools, IDE agents, automation, and remote execution hosts.

<p align="center">
  <strong>macOS · Linux · Windows · VS Code-compatible editors · Claude Code · Codex · Cursor · Devin Local Agent</strong>
</p>

---

## Why District Terminal exists

Software development is no longer driven by one person typing into one terminal. A modern workspace can have a developer, an IDE-hosted coding agent, a CLI agent, background automation, remote SSH hosts, and several shells all changing the same project at once.

The hard problem is no longer merely **starting a terminal**. It is knowing:

- which execution belongs to which agent or human;
- which commands are still running;
- whether a terminal is actually reconnectable;
- what happened before an agent disappeared or a window closed;
- how to inspect several execution surfaces without maintaining separate state;
- how an editor integration can observe terminals without scraping UI state;
- and how to retain useful history without casually persisting secrets.

**District Terminal turns those executions into one local control plane.** It uses a canonical session model, SQLite-backed state, normalized lifecycle events, process-tree inspection, provider hooks, and platform-specific terminal backends. CLI, TUI, web, daemon, and editor integrations all consume that same state instead of inventing their own registries.

```text
Human terminal ───────┐
Claude / Codex ───────┤
Cursor / Devin ───────┤        ┌───────────────┐
IDE processes ────────┼───────▶│ District Terminal control │
Remote hosts ─────────┘        │     plane      │
                               └───────┬───────┘
                                       │
                ┌──────────────────────┼──────────────────────┐
                ▼                      ▼                      ▼
          persistent PTY          SQLite state          event stream
          tmux / ConPTY      sessions/commands/...     daemon / TUI / web
```

## What you get

| Capability | What District Terminal provides |
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
python district/install.py
```

The installer supports an interactive component wizard as well as non-interactive `install`, `plan`, `status`, and `uninstall` operations. A normal installed command is `district`.

```bash
# Create and attach to a managed persistent shell.
district

# Create a managed shell, run a command, then keep the shell alive.
district pytest -q

# See everything District Terminal currently knows about.
district --status

# Interactive multi-session monitoring.
district --watch

# Browser dashboard backed by the same control-plane state.
district --web

# Optionally publish the dashboard through a charter backend.
district --web --expose ngrok --charter-short --charter-alias district

# Or pair a phone on the same Wi-Fi/LAN by scanning a QR code.
district --web --qr

# Deep diagnostics.
district --doctor --full
```

## Public dashboard exposure

`district --web` binds to a loopback host only. To share the dashboard, `--expose` routes the port through a `tools.charter` backend (`ngrok`, `pinggy`, `tunnl` or `runlocal`); `direct` (the default) keeps it local. The charter opens after the server binds and closes when the dashboard stops; the public URL is printed and opened in the browser.

Exposure is configurable at three layers — the Python API (`district.exposure.Exposure`), the CLI, and the `[tool.district.charter]` table of the nearest `pyproject.toml`:

```toml
[tool.district.charter]
backend = "ngrok"         # direct | ngrok | pinggy | tunnl | runlocal
ngrok_authtoken = "…"     # or set NGROK_AUTHTOKEN / [tool.charter]
shorten = true            # shorten the public URL
alias = "district"            # requested short-link alias
```

CLI flags override the project file: `--expose BACKEND`, `--charter-authtoken TOKEN`, `--charter-alias NAME`, `--charter-short` / `--no-charter-short`. Backend mechanics (executables, timeouts, the shortener endpoint) stay under `[tool.charter]`. Charter backends are spawned on demand, so exposure requires a source checkout providing `tools.charter`; installed builds report a clear error.

## Phone console and device pairing

`district --web --qr` turns the dashboard into a remote console your phone can reach. The server additionally binds the host's LAN address (never a wildcard address — loopback and the LAN interface only) and prints a QR code beside its link:

```bash
district --web --qr                      # LAN-only phone console
district --web --qr --expose ngrok       # pair on LAN, continue on the charter
```

Every device must be **paired** before the console opens for it. The QR code carries a one-time pairing token signed by a key in `~/.district/web-auth.key`; the token is redeemable only by a device on the same LAN, and is valid for five minutes. Scanning it exchanges the token for a twelve-hour session cookie — the LAN handshake. When a charter is configured, the LAN device is then redirected to the public URL through a one-minute *handoff* token, so the console ends up reachable away from home too. Re-running `district --web --qr` while the dashboard is up prints a fresh pairing link without restarting anything; deleting `web-auth.key` revokes every paired device at once.

## Designed for the agentic development loop

A coding agent should not need a special, invisible terminal universe. District Terminal makes agent execution inspectable through the same primitives used for human execution.

Provider hooks record the strongest attribution available: provider, provider session, workspace and lifecycle state. When a native provider hook is unavailable, District Terminal can progressively fall back to editor/environment/process evidence. The resulting attribution is stored with the session rather than inferred independently by every UI.

This matters when several agents work concurrently. An editor can ask for machine-readable local status:

```bash
district --status --json --local-only
```

Automation can consume normalized events:

```bash
district --subscribe
```

An agent can idempotently obtain/reuse its managed session:

```bash
district --agent
# or
district --ensure
```

The result is a useful separation of concerns: **agents execute work; District Terminal owns execution identity, lifecycle and observability.**

## Platform model

District Terminal detects the execution host rather than assuming the desktop operating system is the execution environment.

### macOS and Linux

When `tmux` is available, District Terminal uses it for persistent, reconnectable terminals and read-only capture. Process-tree inspection augments terminal state with workload information.

### Windows

When the Windows pseudoconsole API is available, District Terminal uses ConPTY for persistent PowerShell/session execution. The ConPTY layer supports creation, input, output, resize, liveness and termination.

### Process-only environments

If no persistent PTY backend is available—or an IDE runs a process directly—District Terminal still records and observes the execution, but marks it as non-reconnectable. This is intentional: observability does not imply terminal persistence.

## One database, one truth

Canonical state lives in:

```text
~/.district/district.db
```

The normalized SQLite model stores sessions, commands, events, observed processes, remote hosts, web-server registration and agent context. Updates are transactional. Stable session/host/process identities protect the model from PID reuse and short-lived process ambiguity.

JSON is used only where it is appropriate—for user configuration or explicit JSON transport/output—not as a parallel state database.

## Everyday commands

```bash
district                         # create/attach
district <id>                    # attach to a reconnectable session
district <id> --exec <cmd...>    # execute in an existing session
district <id> --send <text...>   # type text into a session and press Enter
district <id> --send-key <key>   # press one key (Enter, C-c, Escape, Up, ...)
district <id> --kill             # terminate/remove
district --kill-all              # terminate/remove every local session
district --list                  # list local sessions
district --status [--json]       # local + configured remote status
district --watch [--once]        # TUI dashboard
district --view <id>             # read-only session view
district --history               # durable command history
district --events                # normalized events
district --web                   # live web dashboard
district --web --qr              # pair a phone on the same LAN (QR pairing link)
district --hosts                 # configured remote hosts
district --doctor --full         # diagnostics
district --purge                 # apply retention policy immediately
district --mcp                   # resident MCP stdio server for agent clients
```

`district --help` is intentionally a complete operational manual, including agent-routing, daemon, remote-host and internal integration endpoints.

## MCP server

`district --mcp` starts a resident MCP (Model Context Protocol) stdio server. The server keeps `District` in-process for the client's lifetime, so every call avoids interpreter and service startup cost; requests execute directly against the live session backends.

```bash
district --mcp
```

Exposed tools:

| Tool | Purpose |
|---|---|
| `district_exec` | Run a shell command in a session; returns captured output and exit status. |
| `district_send` | Type text and/or press keys (`C-c`, `Enter`, `Escape`, `Up`, `Tab`, ...) to answer prompts, drive REPLs or interrupt processes. Enter defaults to true when `text` is given, false for key-only input. |
| `district_output` | Read the recent pane output of a session. |
| `district_sessions` | List live sessions. |
| `district_new` | Create a detached session; optional `command` and `cwd`. |
| `district_kill` | Destroy a session and its backend. |
| `district_events` | Recent normalized activity events; optional `limit` and `filter`. |
| `district_commands` | Recorded command history; optional `limit` and `session_id`. |

Tools that target a session accept an optional `session_id`; it defaults only when exactly one live session exists. Every tool returns human-readable `content` plus machine-readable `structuredContent` matching its declared `outputSchema` (protocol `2025-06-18`). Sessions created through `district_new` are attributed as agent-initiated.

The installer registers the server for you (`python district/install.py install --with mcp`): it edits Claude Desktop's `claude_desktop_config.json` (only when Claude Desktop is installed) and uses `claude mcp add-json --scope user` for Claude Code, falling back to `~/.claude.json`. `district --doctor` reports whether it is registered. `district --mcp --read-only` (or `DISTRICT_MCP_READ_ONLY=1`) exposes only the inspection tools (`district_sessions`, `district_output`, `district_events`, `district_commands`).

To register the server manually with an MCP client such as Claude Desktop, add it to the client's server configuration:

```json
{
  "mcpServers": {
    "district": {
      "command": "district",
      "args": ["--mcp"]
    }
  }
}
```

## Agent identity: one variable, every provider

District resolves who is running a shell from the provider's own environment, so no
hook is needed for attribution. `DISTRICT_AGENT` / `DISTRICT_AGENT_SESSION` win when set;
otherwise the first native session variable present is used:

| Provider | Session variable(s), strongest first | Surface |
| --- | --- | --- |
| Claude Code | `CLAUDE_CODE_SESSION_ID`, `CLAUDE_SESSION_ID` | `CLAUDE_CODE_ENTRYPOINT`: `cli`, `claude-vscode` → `vscode`, `claude-desktop` → `desktop`, `sdk-*` → `sdk` |
| Codex | `CODEX_SESSION_ID`, `CODEX_THREAD_ID` | |
| Cursor | `CURSOR_SESSION_ID` | |
| Devin | `DEVIN_SESSION_ID` | |

Claude Code and Devin run shell commands in their own non-interactive
processes, so their native hooks also record each Bash/`exec` tool call into a
non-reconnectable *hook session* (state, command, exit status) and `district
--doctor` shows the identity resolved for the current process. VS Code forks
(Devin, Cursor, Windsurf, VSCodium) are told apart from the IPC hook path.

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
│ tmux / PTY  │◀────────▶│      District      │◀────────▶│ process tree│
│   ConPTY    │          │ backend-neutral core │          │ inspection  │
└─────────────┘          └──────────┬───────────┘          └─────────────┘
                                   │
                         ┌─────────▼──────────┐
                         │   DistrictDatabase   │
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

District Terminal redacts common credential forms before persistence, including token/password assignments, authorization headers and common secret-token formats. Additional patterns can be configured in:

```text
~/.district/privacy.json
```

Default policy values are 30 days of command history, 90 days of events, no persisted terminal output, and a 256 KiB output limit when persistence is enabled. `district --purge` applies the active retention policy immediately.

## Remote and editor workflows

Remote machines are modeled as execution hosts. Add them with:

```bash
district --host-add build-box user@example-host district
district --hosts
district --status
```

`--status` may aggregate configured hosts over SSH. `--local-only` disables aggregation and is the correct mode for editor integrations and remote recursion prevention.

For VS Code-compatible integrations, the intended machine interface is the SQLite-backed CLI plus daemon events—not direct database mutation and not a JSON registry.

## Installation model

The component installer can install only what a machine needs: launcher, tmux, shell lifecycle hooks, agent routing, provider hooks, web startup and the native `district://` URI handler.

```bash
python district/install.py plan --preset default
python district/install.py install --preset default --yes
python district/install.py status
python district/install.py uninstall
```

Two deployment modes are supported:

- **live** — the launcher points at the checkout; useful while developing District Terminal;
- **copy** — the package is copied into the configured library directory.

The install manifest is stored at `~/.district/install.json` so uninstall removes District Terminal-owned artifacts rather than indiscriminately deleting user configuration.

## Python API

District Terminal is also usable as a Python library. The main programmatic entry points are `District`, `SessionRepository`, `DistrictDatabase`, `CommandLifecycle`, `RuntimePlatform`, `ProcessInspector`, `AgentConfiguration`, `WebDashboard`, `EventServer`, and the provider hook adapters.

```python
from pathlib import Path

from district.core import SessionRepository, District
from district.storage import DistrictDatabase

home = Path.home()
database = DistrictDatabase(home=home)
repository = SessionRepository(home=home)
service = District(repository=repository)

entry = service.create(initial_command=("python", "-m", "pytest"))
print(entry.id)
print(database.schema_version())
```

For the complete API, data model, CLI semantics, configuration files, daemon protocol, platform backends and every public class/function, see **[DOCS.md](../../docs/tools/district/DOCS.md)**.

## Project principles

District Terminal is intentionally built around a few constraints:

1. **One state store.** SQLite is canonical; there is no JSON state path.
2. **No fake persistence.** A process is reconnectable only when its backend actually supports it.
3. **Provider-neutral core.** Provider-specific behavior stays in hook adapters.
4. **Execution-host truth.** Capabilities belong to the machine/container/remote host running the command.
5. **One event model.** CLI, TUI, web and editor surfaces consume normalized lifecycle state.
6. **Privacy before persistence.** Redaction and retention are control-plane concerns, not UI afterthoughts.
7. **Headless first.** Status, execution, subscriptions and lifecycle endpoints are automation-friendly.

## Development

The repository includes Python tests for the control plane and Node tests for the web assets. The project is designed to run across macOS, Linux and Windows, with backend-specific tests isolated behind capability/fake layers.

```bash
python -m pytest tests/tools/district
node --test "tests/tools/district/js/*.test.mjs"
```

The test suite is hermetic: `tests/conftest.py` strips `CLAUDE*`, `CODEX_*`,
`CURSOR_*`, `DEVIN_*`, `DISTRICT_*` and `VSCODE_*` variables, so it gives the same
result when run from inside Claude Code, Devin or Cursor.

## Documentation

- **[DOCS.md](../../docs/tools/district/DOCS.md)** — complete architecture, capabilities, configuration and Python API reference.
- `district --help` — complete command-line operational manual.

---

**District Terminal makes concurrent agent execution observable, attributable and controllable without forcing every agent, editor and terminal to invent its own state.**

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`LICENSE`](LICENSE) — Provide LICENSE project content.
- [`PLATFORMS.md`](PLATFORMS.md) — District Terminal is OS- and CPU-architecture independent at the control-plane layer. Backend capabilities are discovered at runtime.
- [`SECURITY.md`](SECURITY.md) — District Terminal is a local execution control plane. Session metadata, command history, process information, IPC authentication material, and optional terminal output are private user data.
- [`__init__.py`](__init__.py) — Cross-platform coding-agent execution observability and control plane.
- [`__main__.py`](__main__.py) — Execute the District Terminal control plane as a module or directory.
- [`agent.py`](agent.py) — Detect interactive shells launched under configured local coding agents.
- [`agent_identity.py`](agent_identity.py) — Resolve the coding-agent identity exported to a process.
- [`attribution.py`](attribution.py) — Resolve agent attribution from explicit evidence without guessing.
- [`bowman_api.py`](bowman_api.py) — Bowman trust boundary for District Terminal URI control.
- [`buffer.py`](buffer.py) — Bounded in-memory terminal output buffers.
- [`claude.py`](claude.py) — Integrate Claude Code lifecycle hooks with district attribution.
- [`cli.py`](cli.py) — Command-line interface for District agent execution observability.
- [`codex.py`](codex.py) — Integrate Codex lifecycle hooks with district attribution.
- [`conpty.py`](conpty.py) — Native Windows ConPTY sessions owned by the District Terminal daemon.
- [`core.py`](core.py) — District Terminal session lifecycle and canonical SQLite session state.
- [`cursor.py`](cursor.py) — Integrate Cursor embedded-agent hooks with District observability.
- [`daemon.py`](daemon.py) — Long-lived local control-plane daemon for subscriptions and buffers.
- [`daemon_control.py`](daemon_control.py) — Control-plane lifecycle for the detached local District Terminal daemon.
- [`devin.py`](devin.py) — Integrate Devin hook events with district agent attribution.
- [`district_commands.py`](district_commands.py) — Run district commands for the web console and return display widgets.
- [`district_entry.py`](district_entry.py) — Entry script frozen by ``tools.stationer`` into native installers.
- [`district_reports.py`](district_reports.py) — Turn district data into display widgets; no input or output happens here.
- [`events.py`](events.py) — Normalized observability event definitions.
- [`exposure.py`](exposure.py) — Optional public exposure of the web dashboard through a managed charter.
- [`history.py`](history.py) — Queryable command activity history backed exclusively by SQLite.
- [`hook_session.py`](hook_session.py) — Hook-backed sessions for agents whose shells District cannot own.
- [`host_info.py`](host_info.py) — Collect facts about the machine a district runs on, for the overview.
- [`hosts.py`](hosts.py) — Configure and query local/remote District Terminal execution hosts.
- [`identity.py`](identity.py) — Stable host, session, and process identity helpers.
- [`install.py`](install.py) — Install, inspect, and remove the District control-plane integration.
- [`installer.py`](installer.py) — Install, inspect, and remove the District control-plane integration.
- [`integration.py`](integration.py) — Integration qualification harness for real and synthetic coding agents.
- [`integration_probe.py`](integration_probe.py) — Deterministic workload used to qualify District Terminal integrations.
- [`ipc.py`](ipc.py) — Authenticated local IPC for event publication and subscription.
- [`json_config.py`](json_config.py) — Read and write third-party JSON configuration without destroying it.
- [`keys.py`](keys.py) — Validate key names a client may press inside a session pane.
- [`lifecycle.py`](lifecycle.py) — Authoritative shell command lifecycle instrumentation.
- [`mcp_registration.py`](mcp_registration.py) — Register the District Terminal MCP server with Claude clients.
- [`mcp_server.py`](mcp_server.py) — Minimal-latency MCP stdio server for District Terminal.
- [`models.py`](models.py) — Canonical District Terminal data models.
- [`monitoring.py`](monitoring.py) — Non-invasive session monitoring and process inspection.
- [`notifications.py`](notifications.py) — Rule-driven native notifications for significant agent events.
- [`pairing.py`](pairing.py) — Device pairing for the remote web console.
- [`platform.py`](platform.py) — Cross-platform runtime and capability detection.
- [`privacy.py`](privacy.py) — Redact sensitive values and load retention/privacy policy.
- [`process.py`](process.py) — Run child processes without shell interpretation.
- [`process_tree.py`](process_tree.py) — Process-tree inspection and command correlation.
- [`product.py`](product.py) — Central District Terminal product identity and state directory.
- [`provider_state.py`](provider_state.py) — Correlate provider lifecycle hooks with canonical District session state.
- [`providers.py`](providers.py) — Agent-provider contracts and built-in provider registry.
- [`pyproject.toml`](pyproject.toml) — Provide pyproject toml content.
- [`pyz_main.py`](pyz_main.py) — Entry point of the self-contained ``district`` archive.
- [`qr.py`](qr.py) — Dependency-free QR code generation for terminal output.
- [`recorder.py`](recorder.py) — Persist and publish normalized observability events.
- [`release.py`](release.py) — Front end for ``district --make-release`` (source checkouts).
- [`resources.py`](resources.py) — Portable per-session process and resource telemetry.
- [`selftest.py`](selftest.py) — Structured ``district --test`` self-check emitting a JSON report.
- [`settings.py`](settings.py) — Report every effective District Terminal setting as one JSON document.
- [`startup.py`](startup.py) — Cross-platform per-user web-dashboard startup management.
- [`storage.py`](storage.py) — Transactional SQLite state, command, process, and event storage.
- [`tui.py`](tui.py) — Dependency-free terminal dashboard for live agent activity.
- [`update.py`](update.py) — Check GitHub Releases for newer District Terminal builds and self-update.
- [`uri_registry.py`](uri_registry.py) — Issue, track and redeem signed URIs that each run one district command.
- [`url_handler.py`](url_handler.py) — Handle District Terminal custom URIs through Bowman.
- [`web.py`](web.py) — Demand-driven FastAPI dashboard for District session observability.
- [`web_api.py`](web_api.py) — HTTP handlers for host facts, district commands and URIs.
- [`widgets.py`](widgets.py) — Typed result widgets that the web console renders instead of raw JSON.

### Subdirectories

- [`integration/`](integration/README.md) — This harness validates the part of District Terminal most likely to fail in real agentic development: **execution attribution under concurrent providers and surfaces**.
- [`templates/`](templates/README.md) — Provide stationer toml content.
- [`web/`](web/README.md) — Browser assets for the local FastAPI/WebSocket District Terminal control-plane dashboard.

<!-- exactus:inventory:end -->

## Integration qualification

District includes a first-class qualification harness for the ambiguity that
matters most in multi-agent development: provider/session attribution under
concurrent execution.

```bash
district test integration synthetic all
district test integration concurrent
district test integration real codex
district test integration real claude
district test integration real cursor
district test integration real devin
district explain SESSION_ID
```

Synthetic qualification is unattended and suitable for CI/Docker. Real mode
prints a unique probe command for the selected agent and waits for the
provider metadata propagated by District's actual hook/environment integration.
It therefore verifies observed identity rather than trusting which provider
the operator said they were testing.

See `integration/README.md` for the concurrency matrix, Docker workflow, and
Docker Sandboxes workflow.


## Bowman URI control

District Terminal uses the independent `tools/bowman` framework as its URI trust
boundary. `DistrictBowman` creates signed, expiring, one-use `district://`
control URIs and dispatches validated operations into the existing authenticated
IPC daemon. The installer registers the URI scheme with Launch Services on
macOS, XDG on Linux, and the per-user URL Protocol registry on Windows.
