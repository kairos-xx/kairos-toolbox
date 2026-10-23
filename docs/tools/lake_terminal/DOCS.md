# Lake Terminal — Extended Documentation and API Reference

This document describes the current `lake_terminal` implementation. It covers architecture, runtime semantics, configuration, CLI integration points and the public Python API exposed by the modules under `lake_terminal.lake_terminal`.

> **Canonical design rule:** SQLite is the only persistent control-plane state. Lake Terminal has no JSON session registry or state mirror. A narrow one-time importer migrates pre-Lake `.shared_tmux/shared.db` state into `.lake_terminal/lake.db`.

## Contents

1. [Conceptual model](#conceptual-model)
2. [Runtime and backend selection](#runtime-and-backend-selection)
3. [State and data model](#state-and-data-model)
4. [Agent attribution](#agent-attribution)
5. [Command lifecycle](#command-lifecycle)
6. [Daemon and IPC](#daemon-and-ipc)
7. [Monitoring surfaces](#monitoring-surfaces)
8. [Remote hosts](#remote-hosts)
9. [Privacy, history and notifications](#privacy-history-and-notifications)
10. [Installation](#installation)
11. [CLI reference](#cli-reference)
12. [Configuration and state files](#configuration-and-state-files)
13. [Python API reference](#python-api-reference)
14. [Integration guidance](#integration-guidance)
15. [Failure and recovery semantics](#failure-and-recovery-semantics)

---

## Conceptual model

Lake Terminal models execution around a **session**, not around a terminal window. A session has a stable internal UUID, a short public ID, host identity, process identity, backend, agent/editor attribution, workspace, current command and lifecycle state. Whether the session can be reattached is an explicit capability rather than an assumption.

The control plane has four major layers:

1. **Execution backends** — tmux, Windows ConPTY or process-only observation.
2. **Canonical state** — `LakeDatabase` and `SessionRepository` persist normalized state in SQLite.
3. **Attribution/lifecycle** — provider hooks, shell lifecycle hooks and process inspection attach semantic identity to executions.
4. **Surfaces** — CLI, TUI, web dashboard, daemon subscribers and editor integrations consume the same model.

### Persistent versus observable

A process can be observable without being reconnectable. Lake Terminal records this distinction in `SessionEntry.reconnectable` and in the runtime capability model. This prevents a direct IDE child process from being presented as a persistent terminal simply because Lake Terminal can see its PID.

### Execution-host ownership

The host that executes a command owns its backend and database. A macOS desktop connected to a Linux SSH host does not imply the remote command is macOS-backed. Remote status aggregation treats each execution host independently.

---

## Runtime and backend selection

`RuntimePlatform.detect()` normalizes the operating system and architecture and selects a backend based on actual capabilities.

| Host condition | Backend | Persistent | Reconnectable |
| --- | --- | ---: | ---: |
| macOS/Linux with `tmux` | `tmux` | yes | yes |
| Windows with ConPTY available | `conpty` | yes | yes |
| No persistent PTY capability | `process` | no | no |

Architectures are normalized where possible (`amd64`, `x64`, `x86_64` → `x86_64`; `aarch64`, `arm64` → `arm64`).

`RuntimePlatform.surface()` returns an explicit `LAKE_AGENT_SURFACE` when configured, otherwise reports `vscode` when an editor environment is detected, falling back to `cli`. `RuntimePlatform.editor()` uses VS Code environment markers and `LAKE_EDITOR` to identify the hosting editor.

`Capabilities` exposes:

- `persistent_sessions`
- `read_only_capture`
- `process_tree`
- `clipboard`
- `clickable_copy`
- `notifications`
- `editor_agent_bridge`
- `interactive_reconnect`

---

## State and data model

### SQLite database

The canonical database is:

```text
~/.lake_terminal/lake.db
```

`LakeDatabase` creates the state directory, opens the database, applies schema migrations and exposes transactional operations for sessions, commands, events, observed processes, hosts, agent context and web-server registration.

The implementation records normalized entities rather than serializing a monolithic registry. The important logical groups are:

- **sessions** — current execution/session metadata;
- **commands** — durable command lifecycle records;
- **events** — normalized control-plane events;
- **processes** — observed workload/process information;
- **hosts** — configured remote execution hosts;
- **agent context** — provider/session/workspace association;
- **web server** — validated running dashboard registration.

### `SessionEntry`

`SessionEntry` is the canonical immutable session record:

```python
SessionEntry(
    id: str,
    command: str,
    session: str,
    created_at: str = "",
    initiator: str = "",
    pid: int = 0,
    parent_pid: int = 0,
    hostname: str = "",
    cwd: str = "",
    shell_location: str = "",
    term: str = "",
    terminal_program: str = "",
    tty: str = "",
    invocation: str = "",
    initial_command: str = "",
    agent: str = "",
    agent_session: str = "",
    backend: str = "tmux",
    operating_system: str = "",
    architecture: str = "",
    surface: str = "cli",
    editor: str = "",
    workspace: str = "",
    attribution: str = "",
    reconnectable: bool = True,
    current_command: str = "",
    last_activity_at: str = "",
    uuid: str = "",
    host_id: str = "",
    process_started_token: str = "",
    current_command_id: str = "",
    agent_state: str = "IDLE",
)
```

The six-character `id` is the public session handle. `uuid` is the stable internal identity. Provider session IDs remain separate in `agent_session`.

### Stable identities

`ProcessIdentity.capture(pid=...)` captures process identity data used to reduce PID-reuse ambiguity. `IdentityStore` generates stable IDs, machine fingerprints and host IDs.

---

## Agent attribution

Lake Terminal supports two complementary attribution mechanisms.

### Native provider hooks

The current provider adapters are:

- `ClaudeHooks`
- `CodexHooks`
- `CursorHooks`
- `DevinHooks`

Each adapter owns provider-specific installation/configuration and writes provider state into the generic control plane. Provider-specific logic is intentionally kept outside the core session model.

`ProviderState.record(home=..., agent=..., agent_session=..., hook_event=...)` persists the provider/session/hook association.

### Configurable detection

`AgentConfiguration` manages `~/.lake_terminal/agents.json`. Each `AgentType` can match:

- exact process names;
- substrings in process names;
- word tokens in command lines;
- phrases in command lines (`command_matches`);
- environment-variable evidence.

`AgentDetector.is_agent_shell()` evaluates the current shell ancestry. `ancestor_commands()` returns inspected ancestor commands, and `matches()` tests a command against an `AgentType` signature. Setting `LAKE_DISABLE_AGENT` suppresses detection entirely, which is a useful opt-out for the automatic agent-routing shell block.

Signatures must identify the agent process, not the hosting IDE. The Devin signature matches only the `devin acp` and `devin sandbox` command phrases so terminals created by the user inside the Devin application — whose ancestry contains `Devin`/`Devin Helper` binaries — are not attributed as agent shells. Cursor likewise matches `cursor-agent` rather than the `Cursor` application binary.

Built-in signatures include Codex and Claude Code. Native provider hooks remain stronger evidence than heuristic signatures.

---

## Command lifecycle

`CommandLifecycle` provides authoritative start/finish recording:

```python
start(*, session_id: str, command: str, cwd: str) -> str
finish(*, session_id: str, command_id: str, exit_code: int) -> None
```

The returned command ID ties start and finish records together. The database records timestamps, exit code and duration.

`LifecycleInstaller` generates and installs shell integration blocks for supported shells, including PowerShell. These hooks are normally installed by the component installer rather than called manually.

When authoritative lifecycle hooks are unavailable, process-tree inspection provides fallback workload observation.

---

## Daemon and IPC

The daemon is the local event/control bridge used by headless subscribers and monitoring surfaces.

### `DaemonControl`

```python
DaemonControl.start(*, home: Path) -> bool
DaemonControl.ping(*, home: Path) -> bool
DaemonControl.stop(*, home: Path) -> bool
DaemonControl.spawn() -> subprocess.Popen[bytes]
```

### `LakeDaemon`

`LakeDaemon.run()` starts the daemon loop. `sample_once()` samples current state, `on_publish(payload)` receives publication payloads and `handle(request)` handles normalized daemon operations.

### IPC primitives

`IpcEndpoint.for_home(home=...)` resolves the per-user endpoint.

`EventServer.request(home=..., operation=..., values=None)` is the convenient request/response API. `EventServer.serve()` runs the server, `handle(connection=...)` processes one connection and `broadcast(payload=...)` publishes to subscribers.

`EventPublisher.publish(payload=...)` emits an event. `EventSubscriber.receive(timeout=1.0)` waits for one payload and `close()` releases the subscription.

`IpcConnection` is the low-level framed connection wrapper with `send`, `recv`, `poll` and `close`.

---

## Monitoring surfaces

### CLI status

`lake --status` is the human-readable aggregate status surface. `lake --status --json --local-only` is the preferred editor/automation API because it avoids recursive remote aggregation.

### TUI

`WatchTui` renders `DashboardRow` values. `once()` returns/renders a single snapshot; `run()` drives the interactive dashboard. `render(rows=..., selected=0)` and `key(timeout=...)` expose the presentation primitives.

### Web dashboard

`WebDashboard` serves the live dashboard from bundled assets. It reuses/validates an existing registered server when possible.

```python
WebDashboard(
    *,
    service: LakeTerminal,
    home: Path | None = None,
)
```

Public methods:

- `port(host, requested)` — select/validate the listening port;
- `session_payload(entry)` — serialize a session for the dashboard;
- `health_check(url)` — validate a registered server;
- `application()` — build the ASGI application;
- `run(host, port, browser=True)` — run/reuse the dashboard;
- `running_url()` — return the validated running URL when available;
- `payload()` — return the current dashboard payload;
- `execute(session_id, request)` — asynchronously run a command and capture its output;
- `send(session_id, request)` — asynchronously inject literal text and whitelisted key presses;
- `action(session_id, action)` — asynchronously execute a supported session action.

`WebStartup` manages per-user dashboard startup. `state()` returns `StartupState`; `enable(command=None)` and `disable()` mutate startup configuration.

---

## Remote hosts

`RemoteHost` represents a configured remote execution host. `HostRepository` persists host definitions in the canonical database:

```python
read() -> tuple[RemoteHost, ...]
upsert(*, host: RemoteHost) -> None
remove(*, name: str) -> None
```

`RemoteAggregator.statuses()` queries configured hosts and returns aggregated status data. CLI operations are:

```bash
lake --hosts
lake --host-add NAME SSH_TARGET [LAKE_COMMAND]
lake --host-remove NAME
```

Remote invocations should use local-only status on the execution host to prevent recursive aggregation.

---

## Privacy, history and notifications

### Privacy policy

`PrivacyPolicy.load(home=...)` reads `~/.lake_terminal/privacy.json` when present. Defaults are:

```json
{
  "history_days": 30,
  "event_days": 90,
  "persist_output": false,
  "output_limit_bytes": 262144,
  "redact_patterns": []
}
```

Invalid/missing values fall back safely to defaults. `output_limit_bytes` has a minimum accepted value of 4096.

### Redaction

`Redactor.redact(value=..., extra_patterns=())` removes common sensitive material before persistence. Built-in patterns cover common API-key/token/password assignments, authorization headers and common `sk_`, `ghp_` and `github_pat_` token forms. User regexes can extend this set.

### History

`History.lines()` returns durable command-history lines from the canonical database. The CLI exposes this through `lake --history`.

### Notifications

`NotificationRule.matches(event=...)` evaluates one rule. `NotificationRules.read()` loads configured rules. `Notifier.for_event(event=..., rules=...)` derives notifications and `Notifier.send(title=..., message=...)` uses the platform notification surface.

---

## Installation

The top-level installer entry point is:

```bash
python lake_terminal/install.py
```

`lake_terminal.installer.main()` implements the same installer API programmatically.

### Installer operations

```text
install     resolve and apply selected components
plan        print the resolved plan without filesystem mutation
status      inspect components, PATH, interpreter, manifest and state
uninstall   remove Lake Terminal-owned artifacts; --purge also removes state
```

Important install options include `--alias`, `--bin-dir`, `--lib-dir`, `--mode live|copy`, `--preset minimal|default|full`, `--with`, `--without`, `--yes`, `--force`, `--dry-run`, `--quiet` and `--home`.

### Deployment modes

- `live` — launcher targets the checkout;
- `copy` — package is copied into the configured library directory.

### Components

The installer can manage the launcher, tmux, zsh/bash/PowerShell lifecycle integration, agent routing, Claude/Codex/Cursor/Devin hooks, web startup and macOS copy-URL handling.

`Layout` describes installation paths. Its properties include `launcher`, `state_dir`, `manifest_path`, `import_root` and `copy_app`.

The manifest is stored at:

```text
~/.lake_terminal/install.json
```

It records owned installation components so uninstall can be conservative.

---

## CLI reference

Run `lake --help` for the complete built-in manual. The principal public commands are summarized below.

### Session creation and control

```text
lake
lake COMMAND [ARG ...]
lake ID
lake ID --exec CMD...
lake ID --send TEXT...
lake ID --send-key KEY
lake ID --kill
lake --list [AGENT ...]
lake --cleanup ...
```

`lake` with no command creates and attaches to a managed persistent session when the backend supports it. Supplying a command creates the session, runs the command and keeps the managed shell alive. A bare known session ID attaches to it.

### Agent routing

```text
lake --agent [CMD ...]
lake --ensure [CMD ...]
lake --is-agent-shell
lake --session-name [NAME [ACTION ...]]
```

`--agent` and `--ensure` reuse/create the current provider-associated session. `--is-agent-shell` is suitable for shell routing logic and exits successfully when the current shell is attributed to a configured agent.

`lake --session-name` is a GET/enter pair on the recorded agent session name. Bare, inside a managed session, it prints the current agent session name — `LAKE_AGENT_SESSION` when present (the env-injected inner-agent protocol Cursor uses), then the session's recorded `agent_session`, then provider environment markers such as `DEVIN_SESSION_ID` — so a Devin-attributed shell prints its `DEVIN_SESSION_ID`. Outside a managed session, `lake --session-name NAME [ACTION ...]` resolves the newest live session recorded under `agent_session == NAME` and behaves exactly like `lake ID [ACTION ...]` (attach, `--exec`, `--send`, `--send-key`, `--kill`). Bare, outside, it exits `1` like `--id`.

### Observation

```text
lake --status [--json] [--local-only]
lake --watch [FILTER ...] [--once]
lake --view ID
lake --history [FILTER ...]
lake --events [FILTER ...]
lake --subscribe [--once]
```

### Web

```text
lake --web [--host HOST] [--port PORT] [--no-browser]
lake --web info
lake --web --startup
lake --web --no-startup
lake --web --startup state
```

### Daemon

```text
lake --daemon-start
lake --daemon-stop
lake --daemon-status
```

### MCP server

```text
lake --mcp
```

`lake --mcp` runs a resident MCP (Model Context Protocol) server over Content-Length framed JSON-RPC on stdio (protocol `2025-06-18`). The process keeps `LakeTerminal` in-process so each tool call avoids startup cost. Exposed tools:

| Tool | Purpose |
|---|---|
| `lake_exec` | Run a shell command in a session; captured output + exit status. |
| `lake_send` | Inject `text` and/or one whitelisted `key` (`C-c`, `Enter`, `Escape`, `Up`, ...); `enter` defaults to true when `text` is given. |
| `lake_output` | Recent pane output of a session. |
| `lake_sessions` | List live sessions. |
| `lake_new` | Create a detached session (optional `command`, `cwd`). |
| `lake_kill` | Destroy a session and its backend. |
| `lake_events` | Normalized activity events (`limit`, `filter`). |
| `lake_commands` | Recorded command history (`limit`, `session_id`). |

Every tool returns human-readable `content` plus `structuredContent` matching its declared `outputSchema`. `session_id` defaults only when exactly one live session exists; `lake_new` sessions are agent-attributed.

### Remote hosts

```text
lake --hosts
lake --host-add NAME SSH_TARGET [LAKE_COMMAND]
lake --host-remove NAME
```

### Maintenance

```text
lake --doctor [--full]
lake --purge
```

### Internal integration endpoints

These are intended for installed hooks/providers rather than manual use:

```text
--cursor-hook
--devin-hook
--codex-hook
--claude-hook
--lifecycle-start
--lifecycle-finish
--agent-state
```

### Return codes

- `0` — success;
- `1` — runtime operation unavailable or failed;
- `2` — invalid public CLI usage or unknown requested session.

---

## Configuration and state files

All paths below are relative to the selected user home unless a caller explicitly supplies another home.

| Path | Purpose |
| --- | --- |
| `~/.lake_terminal/lake.db` | Canonical SQLite state. |
| `~/.lake_terminal/install.json` | Installer-owned component manifest. |
| `~/.lake_terminal/agents.json` | Configurable agent signatures. |
| `~/.lake_terminal/privacy.json` | Retention/output/redaction policy. |
| Provider-specific config paths | Managed by `ClaudeHooks`, `CodexHooks`, `CursorHooks` and `DevinHooks`. |

Environment variables used by the runtime include:

- `LAKE_SESSION_ID` — current managed-session recursion/identity marker;
- `LAKE_DISABLE_AGENT` — suppresses agent-shell detection when set;
- `LAKE_AGENT_SURFACE` — explicit execution surface override;
- `LAKE_EDITOR` — explicit editor identity;
- provider-specific environment markers used by agent detection;
- normal VS Code environment markers such as `VSCODE_PID`/`VSCODE_CWD` for editor detection.

---

# Python API reference

The following reference lists the public classes/functions currently defined by the package. Methods beginning with `_` are implementation details and intentionally omitted.

## `agent`

### `AgentType`

Immutable configured agent signature with `identifier`, `name`, `enabled`, `processes`, `process_contains`, `command_contains`, `command_matches` and `environment`.

### `AgentConfiguration`

- `load(*, home: Path) -> tuple[AgentType, ...]` — load `agents.json`, creating defaults when absent.
- `default_agents() -> tuple[AgentType, ...]` — built-in signatures.
- `write(*, path: Path, agents: tuple[AgentType, ...]) -> None` — atomically write human-editable configuration.

### `AgentDetector`

- `is_agent_shell() -> bool` — inspect current ancestry/environment.
- `ancestor_commands() -> tuple[str, ...]` — collect ancestor commands.
- `matches(*, command: str, agent: AgentType) -> bool` — test one command against one signature.

## `buffer`

### `OutputRingBuffer`

Bounded transient output buffer.

- `append(*, text: str) -> None`
- `text() -> str`

## `claude`, `codex`, `cursor`, `devin`

Provider hook classes expose provider-specific installation and event entry points.

### `ClaudeHooks`

- `install(*, home: Path, launcher: Path) -> None`
- `uninstall(*, home: Path) -> None`
- `run_hook(*, home: Path) -> int`
- `session_path(*, home: Path) -> Path`
- `config_path(*, home: Path) -> Path`

### `CodexHooks`

- `install(*, home: Path, launcher: Path) -> None`
- `uninstall(*, home: Path) -> None`
- `run_hook(*, home: Path) -> int`
- `session_path(*, home: Path) -> Path`
- `config_path(*, home: Path) -> Path`

### `CursorHooks`

- `install(*, home: Path, launcher: Path) -> None`
- `uninstall(*, home: Path) -> None`
- `run_hook(*, home: Path) -> int`
- `config_path(*, home: Path) -> Path`

### `DevinHooks`

- `install(*, home: Path, launcher: Path) -> None`
- `uninstall(*, home: Path) -> None`
- `run_hook(*, home: Path) -> int`
- `config_path(*, home: Path) -> Path`

## `cli`

### `main`

```python
main(*, arguments: tuple[str, ...] | None = None) -> int
```

Programmatic CLI entry point. Pass an explicit tuple to avoid reading process arguments.

### `Cli`

`Cli.run(arguments=None)` performs full dispatch. Its public helper methods expose the same functional areas used by the command line: session listing/view, attach/exec/kill, history/events/subscriptions, hosts, lifecycle endpoints, agent state, daemon operations, web/startup and global status.

### `status_value`

```python
status_value(*, value: JsonValue) -> str
```

Normalize a JSON-compatible status value for human-readable output.

## `conpty`

### `ConPtyCapabilities`

Describes whether the Windows pseudoconsole backend is available and why.

### `ConPtyInfo`

Describes a managed ConPTY session.

### `WindowsConPty`

Low-level Windows pseudoconsole wrapper.

- `spawn(*, command: tuple[str, ...], cwd: str, columns: int = 120, rows: int = 40) -> WindowsConPty`
- `capabilities() -> ConPtyCapabilities`
- `kernel32()` — return the required Windows kernel API binding.
- `read(*, size: int = 4096) -> bytes`
- `write(*, data: bytes) -> None`
- `resize(*, columns: int, rows: int) -> None`
- `alive() -> bool`
- `terminate() -> None`

### `ConPtyManager`

In-process registry of ConPTY sessions:

- `create(session_uuid, command, cwd)`
- `output(session_uuid)`
- `write(session_uuid, text)`
- `kill(session_uuid)`
- `info(session_uuid)`
- `drain(session_uuid)`

### `ConPtyClient`

Daemon/client-facing ConPTY API:

- `create(session_uuid, cwd, command)`
- `output(session_uuid)`
- `write(session_uuid, text)`
- `kill(session_uuid)`
- `alive(session_uuid)`
- `attach(session_uuid)`

`last_error()` and `win_dll_factory()` expose low-level Windows error/DLL helpers.

## `copy_url`

- `command_from_url(*, url: str) -> str` — derive the command carried by a Lake Terminal URL.
- `main(*, arguments: tuple[str, ...] | None = None) -> int` — copy-URL command entry point.

## `core`

### `ExecResult`

Immutable command result containing `output` and `exit_status`.

### `SessionRepository`

```python
SessionRepository(*, home: Path)
```

- `database -> LakeDatabase`
- `read() -> tuple[SessionEntry, ...]`
- `add(*, entry: SessionEntry) -> None`
- `remove(*, session_id: str) -> None`
- `replace(*, entries: tuple[SessionEntry, ...]) -> None`
- `update(*, transform: Callable[[tuple[SessionEntry, ...]], tuple[SessionEntry, ...]]) -> tuple[SessionEntry, ...]`

### `LakeTerminal`

```python
LakeTerminal(*, repository: SessionRepository)
```

Backend-neutral session service.

- `exists(*, session: str) -> bool` — tmux-session liveness helper.
- `new_id(*, entries: tuple[SessionEntry, ...]) -> str` — allocate a public short ID.
- `live_session_names() -> frozenset[str]` — current tmux sessions.
- `pane_pid(*, session: str) -> int` — root pane PID.
- `pane_is_shell(*, session: str) -> bool` — whether the pane is at a shell.
- `parse_exec(*, captured: str, start: str, end: str) -> ExecResult` — parse delimited execution capture.
- `create(*, initial_command: tuple[str, ...] = (), initiator: str = "user") -> int`
- `spawn(*, initial_command: tuple[str, ...] = (), initiator: str = "user", cwd: str = "") -> SessionEntry` — create a detached session without attaching the caller.
- `ensure_agent(*, initial_command: tuple[str, ...] = ()) -> int`
- `attach(*, session_id: str) -> int`
- `kill(*, session_id: str) -> None`
- `send(*, session_id: str, text: str = "", keys: tuple[str, ...] = ()) -> None` — inject literal text and whitelisted key presses into a tmux pane.
- `execute(*, session_id: str, command: str) -> ExecResult`
- `cleanup(*, session_id: str) -> None`
- `list_entries() -> tuple[SessionEntry, ...]`
- `reconcile() -> tuple[SessionEntry, ...]`
- `status(*, session_id: str) -> SessionEntry | None`
- `create_conpty(*, initial_command: tuple[str, ...], initiator: str) -> SessionEntry`
- `entry_alive(*, entry: SessionEntry) -> bool`

`default_service()` builds the default service for the current home.

## `daemon`

### `LakeDaemon`

- `run() -> int`
- `sample_once() -> None`
- `on_publish(payload: str) -> None`
- `handle(request: dict[str, str]) -> dict[str, str]`

## `daemon_control`

### `DaemonControl`

- `start(*, home: Path) -> bool`
- `ping(*, home: Path) -> bool`
- `stop(*, home: Path) -> bool`
- `spawn()` — spawn the daemon process.

## `events`

### `EventTypes`

Namespace of normalized event-type identifiers.

### `EventDetails`

Structured event details.

### `LakeEvent`

- `create(*, event_type: str, host_id: str, details: EventDetails | None = None) -> LakeEvent`
- `json() -> str` — JSON serialization for transport/persistence.

## `history`

### `History`

- `lines() -> tuple[str, ...]`

## `hosts`

### `RemoteHost`

Remote host definition.

### `HostRepository`

- `read() -> tuple[RemoteHost, ...]`
- `upsert(*, host: RemoteHost) -> None`
- `remove(*, name: str) -> None`

### `RemoteAggregator`

- `statuses()` — aggregate configured remote status.

## `identity`

### `ProcessIdentity`

- `capture(*, pid: int) -> ProcessIdentity`

### `IdentityStore`

- `new_id() -> str`
- `machine_fingerprint() -> str`
- `host_id() -> str`

## `installer`

### `Layout`

Installation layout with properties `launcher`, `state_dir`, `manifest_path`, `import_root` and `copy_app`.

### `Component`

Describes one installable component.

### `Reporter`

Installer output abstraction with `paint`, `line`, `step`, `pending`, `note`, `warn` and `done`.

### `Installer`

- `components(*, layout: Layout) -> tuple[Component, ...]`
- `layout(*, alias, mode, bin_dir, lib_dir) -> Layout`
- `install(*, layout, selected, dry_run, force) -> int`
- `uninstall(*, purge: bool, dry_run: bool) -> int`
- `status(*, layout: Layout) -> int`
- `check_conflict(*, layout: Layout, force: bool) -> None`
- `resolve_python() -> Path`

### Installer helper API

Public helper functions include:

- launcher: `write_launcher`, `owned_launcher`, `install_launcher`, `remove_launcher`;
- tmux: `install_tmux`, `tmux_installed`;
- shells: `install_zsh`, `install_bash`, `install_powershell`, matching `*_installed` and `remove_*` functions;
- routing: `routing_block`, `install_block`, `remove_block`, `routing_files`, `install_routing`, `routing_installed`, `remove_routing`;
- providers: `install_claude`, `install_codex`, `install_cursor`, `install_devin`, corresponding installed/remove functions and `hook_installed`;
- copy URL: `install_copyurl`, `copyurl_installed`, `remove_copyurl`;
- selection/layout: `components`, `windows_root`, `default_bin_dir`, `default_lib_dir`, `validated_alias`, `resolve_selection`, `csv`;
- manifest: `read_manifest`, `write_manifest`, `manifest_layout`;
- wizard: `read_key`, `wizard_frame`, `prompt_alias`, `wizard_toggle`, `wizard_edit`, `wizard`;
- entry point: `main(arguments=None, stream=None) -> int`.

## `ipc`

### `IpcConnection`

- `send(obj)`
- `recv()`
- `poll(timeout=0.0)`
- `close()`

### `IpcEndpoint`

- `for_home(*, home: Path) -> IpcEndpoint`

### `EventPublisher`

- `publish(*, payload: str) -> None`

### `EventSubscriber`

- `receive(*, timeout: float = 1.0) -> str | None`
- `close() -> None`

### `EventServer`

- `request(*, home: Path, operation: str, values: dict[str, str] | None = None)`
- `serve()`
- `handle(*, connection: IpcConnection)`
- `broadcast(*, payload: str)`

## `lifecycle`

### `CommandLifecycle`

- `start(*, session_id: str, command: str, cwd: str) -> str`
- `finish(*, session_id: str, command_id: str, exit_code: int) -> None`

### `LifecycleInstaller`

- `block(*, command: str = "lake") -> str`
- `powershell_block(*, command: str = "lake") -> str`
- `remove_file(*, path: Path) -> None`
- `install_powershell(*, path: Path, command: str = "lake") -> None`
- `install_file(*, path: Path, command: str = "lake") -> None`

## `models`

### `SessionEntry`

Canonical immutable session model. See [State and data model](#state-and-data-model) for all fields.

## `monitoring`

### `SessionSnapshot`

Normalized monitor snapshot.

### `Monitor`

- `snapshot(*, entry: SessionEntry) -> SessionSnapshot`
- `state(*, live: bool, command: str, explicit: str) -> str`
- `process_matches(*, entry: SessionEntry) -> bool`

## `notifications`

### `NotificationRule`

- `matches(*, event: LakeEvent) -> bool`

### `NotificationRules`

- `read() -> tuple[NotificationRule, ...]`

### `Notifier`

- `for_event(*, event: LakeEvent, rules: tuple[NotificationRule, ...])`
- `send(*, title: str, message: str) -> None`

## `platform`

### `Capabilities`

Immutable runtime capability record.

### `RuntimePlatform`

- `detect() -> RuntimePlatform`
- `surface() -> str`
- `editor() -> str`

## `privacy`

### `PrivacyPolicy`

- `load(*, home: Path) -> PrivacyPolicy`

Fields: `history_days`, `event_days`, `persist_output`, `output_limit_bytes`, `redact_patterns`.

### `Redactor`

- `redact(*, value: str, extra_patterns: tuple[str, ...] = ()) -> str`

## `process`

```python
run(
    *,
    args: tuple[str, ...],
    check: bool,
    capture_output: bool = False,
    text: bool = False,
)
```

Typed subprocess wrapper used throughout the package.

## `process_tree`

### `ProcessNode`

Normalized process-tree node.

### `ProcessInspector`

- `descendants(*, root_pid: int) -> tuple[ProcessNode, ...]`
- `workload(*, root_pid: int) -> tuple[ProcessNode, ...]`
- `windows_nodes() -> tuple[ProcessNode, ...]`
- `unix_nodes() -> tuple[ProcessNode, ...]`
- `integer(*, value: ProcessValue) -> int`
- `text(*, value: ProcessValue) -> str`

## `provider_state`

### `ProviderState`

- `record(*, home: Path, agent: str, agent_session: str, hook_event: str) -> None`

## `recorder`

### `EventRecorder`

- `record(*, event: LakeEvent) -> None`

## `resources`

### Data types

`NetworkAddress`, `NetworkConnection`, `IoCounters`, `MemoryInfo`, `ProcessResource` and `ResourceSnapshot` model process/network/resource observations. `ProcessProtocol` describes the process API required by the collector.

### `ResourceSnapshot`

- `process_count() -> int`

### `ResourceCollector`

- `collect(*, entry: SessionEntry) -> ResourceSnapshot`
- `process_resource(*, process: ProcessProtocol) -> ProcessResource`
- `connections(*, sockets: list[NetworkConnection])`
- `empty(*, partial: bool = False) -> ResourceSnapshot`
- `listening_ports(*, sockets: list[NetworkConnection])`
- `root_pid(*, entry: SessionEntry) -> int`

## `startup`

### `StartupState`

Current web-startup state.

### `WebStartup`

- `quote(*, value: str) -> str`
- `state() -> StartupState`
- `enable(*, command: tuple[str, ...] | None = None) -> StartupState`
- `disable() -> StartupState`

## `storage`

### `CommandStartedValues`

Typed keyword shape accepted by `LakeDatabase.command_started`.

### `LakeDatabase`

```python
LakeDatabase(*, home: Path)
```

Schema/integrity:

- `upgrade_sqlite_schema(*, database: Connection, version: int) -> None`
- `table_exists(*, database: Connection, table: str) -> bool`
- `schema_version() -> int`
- `integrity()` — inspect database integrity/recovery state.

Agent/session state:

- `set_agent_context(*, provider: str, session_id: str, workspace: str) -> None`
- `agent_context(*, workspace: str)`
- `sessions() -> tuple[SessionEntry, ...]`
- `replace_sessions(*, entries: tuple[SessionEntry, ...]) -> None`
- `update_sessions(*, transform=...) -> tuple[SessionEntry, ...]`

Events/commands/processes:

- `append_event(*, event: LakeEvent) -> None`
- `event_json(*, filters: tuple[str, ...] = ()) -> tuple[str, ...]`
- `command_started(**values: Unpack[CommandStartedValues]) -> None`
- `observe_processes(*, session_uuid: str, processes: tuple[tuple[int, str, str], ...]) -> None`
- `process_count(*, session_uuid: str) -> int`
- `command_started_at(*, command_id: str) -> str`
- `command_finished(*, command_id: str, finished_at: str, exit_code: int, duration_seconds: float) -> None`
- `command_json()`

Hosts:

- `host_rows()`
- `host_details()`
- `upsert_host(*, host_id: str, name: str, target: str, command: str, enabled: bool) -> None`
- `remove_host(*, name: str) -> None`

Retention/diagnostics:

- `purge(*, policy: PrivacyPolicy)`
- `row_counts()`

Web registration:

- `set_web_server(*, pid: int, started_token: str, host: str, port: int, url: str) -> None`
- `web_server()`
- `clear_web_server() -> None`

## `tui`

### `DashboardRow`

One rendered dashboard row.

### `WatchTui`

- `render(*, rows: tuple[DashboardRow, ...], selected: int = 0) -> str`
- `key(*, timeout: float)`
- `once()`
- `run()`

## `web`

See [Monitoring surfaces](#monitoring-surfaces).

### `WebDashboard`

- `port(*, host: str, requested: int) -> int`
- `session_payload(*, entry: SessionEntry)`
- `health_check(*, url: str) -> bool`
- `application()`
- `run(*, host: str, port: int, browser: bool = True) -> int`
- `running_url() -> str | None`
- `payload()`
- `action(session_id: str, action: str)` — async session action.

---

## Integration guidance

### Creating and controlling sessions from Python

```python
from pathlib import Path

from lake_terminal.core import SessionRepository, LakeTerminal

repository = SessionRepository(home=Path.home())
service = LakeTerminal(repository=repository)

entry = service.create(
    initial_command=("python", "-m", "pytest"),
    initiator="automation",
)

result = service.execute(
    session_id=entry.id,
    command="git status --short",
)

print(result.output)
print(result.exit_status)
```

Callers should use the public short session ID for user-facing control and retain the stable UUID only when working with internal normalized state.

### Reading canonical state

```python
from pathlib import Path

from lake_terminal.storage import LakeDatabase

store = LakeDatabase(home=Path.home())
for entry in store.sessions():
    print(entry.id, entry.agent, entry.current_command)
```

Read through `LakeDatabase`/`SessionRepository`; do not mutate `lake.db` with ad-hoc SQL from integrations.

### Editor integrations

Prefer:

```bash
lake --status --json --local-only
```

for snapshots and the daemon subscription API for changes. This keeps editor code backend-neutral and prevents it from depending on the SQLite schema directly.

### Provider integrations

Provider adapters should translate provider-specific lifecycle data into `ProviderState`/normalized session events. They should not introduce provider-specific fields into the core session registry unless those fields are generally meaningful across providers.

### Headless automation

Use `--status --json`, `--exec`, `--subscribe`, lifecycle endpoints and the Python service/database APIs. Avoid scraping TUI/web output.

---

## Failure and recovery semantics

### Unknown or unavailable session

Public CLI misuse or an unknown requested session returns exit code `2`. A valid request whose runtime operation cannot be completed returns `1`.

### Backend disappearance

`LakeTerminal.reconcile()` compares persisted sessions with live backend/process state and updates the canonical view. `entry_alive(entry=...)` performs backend-aware liveness evaluation.

### PID reuse

Stable process-start identity is stored separately from a PID. Monitoring logic can therefore distinguish a reused PID from the original process.

### Database recovery

Database setup performs migrations transactionally. The implementation tracks recovery state through `LakeDatabase.recovered_from`; diagnostics expose database integrity rather than silently treating corruption as valid state.

### Daemon unavailable

Daemon operations are independently diagnosable with `lake --daemon-status` and `lake --doctor --full`. Core persisted state remains SQLite-backed rather than being owned exclusively by the daemon process.

### No persistent backend

Lake Terminal falls back to the `process` backend and marks the execution non-reconnectable. Integrations should honor `SessionEntry.reconnectable` instead of displaying attach controls unconditionally.

---

## Recommended extension points

For new functionality, prefer these boundaries:

- **new coding-agent provider** → provider hook adapter + `ProviderState`;
- **new monitoring UI** → `LakeDatabase`/status JSON + daemon events;
- **new persistent terminal backend** → backend-neutral `LakeTerminal` semantics and explicit runtime capabilities;
- **new remote transport** → execution-host abstraction rather than pretending the remote process is local;
- **new retention/redaction behavior** → `PrivacyPolicy`/`Redactor` before persistence;
- **new lifecycle source** → normalized `CommandLifecycle`/event model.

Avoid parallel registries, UI-owned session truth, provider-specific core state, or marking an execution reconnectable without a backend that can actually reconnect it.

---

For a concise project introduction, see [README.md](../../../tools/lake_terminal/README.md). For the authoritative CLI manual installed with the application, run:

```bash
lake --help
```

## Product identity and migration API

### `Product`

`lake_terminal.product.Product` is the single authority for externally visible product
identity. It defines `name`, `command`, `module`, current/legacy state directory names,
current/legacy database names, and the URL scheme. Runtime code should consume these
values rather than introducing new product-name literals.

### `state_directory(*, home: Path) -> Path`

Returns the private Lake Terminal state directory. On first access it atomically renames a
legacy `~/.shared_tmux` directory when `.lake_terminal` does not yet exist, migrates
`shared.db` to `lake.db`, creates the current directory when needed, and applies user-only
permissions on Unix-like systems.

### `secure_private_file(*, path: Path) -> None`

Applies mode `0600` to private state files on Unix-like systems. The IPC authentication key
and SQLite database use this helper.

## Extension contracts

### `AgentProvider`

`lake_terminal.agent.AgentProvider` defines the installation boundary for coding-agent
integrations. Built-in providers are exposed through `ProviderRegistry.builtins()` under
stable identifiers `claude`, `codex`, `cursor`, and `devin`. Provider-specific parsing and
configuration remain isolated in their own modules.

### `SessionBackend`

`lake_terminal.core.SessionBackend` defines the minimal backend lifecycle boundary:
`alive`, `capture`, and `kill`. The current core continues to use the mature tmux and ConPTY
implementations while this protocol is the compatibility boundary for additional backend
implementations. Runtime capability selection remains owned by `RuntimePlatform`.

## Web security contract

`WebDashboard.run()` accepts only `127.0.0.1`, `localhost`, or `::1`. Non-loopback binding
raises `ValueError`. This is deliberate because session actions and process/terminal data
are privileged local information. Remote browser access must be provided through an
authenticated reverse proxy or SSH tunnel terminating at the loopback listener.

# Integration qualification API

## Attribution evidence

`lake_terminal.attribution.AttributionStrength` ranks evidence in this order:
execution token, provider hook, explicit environment, editor metadata, process
tree, heuristic, unknown. `AttributionResolver.resolve()` always selects the
strongest evidence. Equally strong conflicting providers resolve to
`unknown`; Lake deliberately prefers missing attribution to false attribution.

`AttributionEvidence` records provider, provider session, source, detail,
surface, editor, and workspace. `AttributionDecision` records the selected
evidence, all ordered evidence, and whether the decision was conflicted.

## IntegrationHarness

`IntegrationHarness.synthetic(home=..., provider=..., provider_session=...)`
runs an unattended deterministic probe for one provider contract.

`IntegrationHarness.synthetic_all(home=...)` runs human, Claude, Codex,
Cursor, and Devin synthetic scenarios.

`IntegrationHarness.real(home=..., provider=..., timeout_seconds=...)`
generates a cryptographically random probe token, prints the exact command to
run through the real provider, waits for evidence, and verifies the observed
provider identity.

`IntegrationHarness.probe_command(token=..., output=...)` exposes the exact
probe command for external harnesses.

## Probe workload

`python -m lake_terminal.integration_probe` creates child processes, emits
stable stdout markers, optionally opens a loopback listener, records process
IDs and Lake provider metadata, waits for a controlled lifetime, and exits
cleanly. The same workload is used by macOS real-agent qualification and Linux
synthetic/container qualification.

## CLI

```text
lake test integration synthetic all
lake test integration concurrent
lake test integration synthetic PROVIDER
lake test integration real PROVIDER
lake explain SESSION_ID
```

`lake explain` reports provider, provider session, attribution source,
confidence, surface, editor, workspace, PID/parent PID, backend, and
reconnectability for the selected execution.

## Qualification policy

The v1 qualification target is:

- macOS: native runtime plus real Codex, Claude, Cursor, and Devin provider
  integration, including desktop/editor surfaces where installed;
- Linux: full headless runtime qualification in Docker plus synthetic provider
  contracts; Docker Sandboxes may additionally run real agent kits;
- Windows: implementation/unit/contract qualification until a native Windows
  runner is available.

Graphical editors are intentionally not installed inside ordinary Linux
containers. Linux tests Lake's runtime and provider contracts; macOS tests the
real graphical/provider integration surface.
