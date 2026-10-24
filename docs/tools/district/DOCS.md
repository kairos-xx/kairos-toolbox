# District Terminal — Extended Documentation and API Reference

This document describes the current `district` implementation. It covers architecture, runtime semantics, configuration, CLI integration points and the public Python API exposed by the modules under `district`.

> **Canonical design rule:** SQLite is the only persistent control-plane state. District Terminal has no JSON session registry or state mirror.

## Contents

1. [Capabilities](#capabilities)
2. [Conceptual model](#conceptual-model)
3. [Runtime and backend selection](#runtime-and-backend-selection)
4. [State and data model](#state-and-data-model)
5. [Agent attribution](#agent-attribution)
6. [Command lifecycle](#command-lifecycle)
7. [Daemon and IPC](#daemon-and-ipc)
8. [Monitoring surfaces](#monitoring-surfaces)
9. [Remote hosts](#remote-hosts)
10. [Privacy, history and notifications](#privacy-history-and-notifications)
11. [Installation](#installation)
12. [CLI reference](#cli-reference)
13. [Configuration and state files](#configuration-and-state-files)
14. [Updates](#updates)
15. [Settings report](#settings-report)
16. [Releasing](#releasing)
17. [Distribution](#distribution)
18. [Python API reference](#python-api-reference)
19. [Integration guidance](#integration-guidance)
20. [Failure and recovery semantics](#failure-and-recovery-semantics)

---

## Capabilities

- SQLite-only normalized state (`sessions`, `commands`, `events`, `processes`, `hosts`).
- Stable host/session/process identities and PID-reuse protection.
- Transactional command lifecycle and normalized event model.
- Authenticated local IPC daemon and event subscriptions.
- Bounded live-output buffers.
- tmux persistence/read-only capture on Unix-capable hosts.
- Windows ConPTY persistent-session backend behind runtime capability detection.
- Process-only observability for direct IDE execution without a PTY.
- Cross-platform runtime/capability detection.
- Provider attribution through native provider hooks and configurable signatures, including Cursor embedded-agent hooks (`sessionStart`, shell execution, response/thought, stop and session end) with stable conversation attribution.
- VS Code-compatible editor bridge using the SQLite-backed control-plane API; the extension UI is provider-neutral and Devin-specific code is restricted to the Devin Local Agent hook adapter.
- Interactive/read-only `--watch`/`--view`, history/events, status and full diagnostics.
- Remote execution-host definitions and SSH status aggregation.
- Privacy redaction before persistence plus retention/purge.
- Notification rules and agent-state events.
- Component-based installer with an interactive wizard, a persisted manifest and owned-artefact uninstall.

JSON is used only for configuration where appropriate and for explicit transport/output such as `--status --json`.

---

## Conceptual model

District Terminal models execution around a **session**, not around a terminal window. A session has a stable internal UUID, a short public ID, host identity, process identity, backend, agent/editor attribution, workspace, current command and lifecycle state. Whether the session can be reattached is an explicit capability rather than an assumption.

The control plane has four major layers:

1. **Execution backends** — tmux, Windows ConPTY or process-only observation.
2. **Canonical state** — `DistrictDatabase` and `SessionRepository` persist normalized state in SQLite.
3. **Attribution/lifecycle** — provider hooks, shell lifecycle hooks and process inspection attach semantic identity to executions.
4. **Surfaces** — CLI, TUI, web dashboard, daemon subscribers and editor integrations consume the same model.

### Persistent versus observable

A process can be observable without being reconnectable. District Terminal records this distinction in `SessionEntry.reconnectable` and in the runtime capability model. This prevents a direct IDE child process from being presented as a persistent terminal simply because District Terminal can see its PID.

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

`RuntimePlatform.surface()` returns an explicit `DISTRICT_AGENT_SURFACE` when configured, otherwise reports `vscode` when an editor environment is detected, falling back to `cli`. `RuntimePlatform.editor()` uses VS Code environment markers and `DISTRICT_EDITOR` to identify the hosting editor.

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
~/.district/district.db
```

`DistrictDatabase` creates the state directory, opens the database, applies schema migrations and exposes transactional operations for sessions, commands, events, observed processes, hosts, agent context and web-server registration.

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

District Terminal supports two complementary attribution mechanisms.

### Native provider hooks

The current provider adapters are:

- `ClaudeHooks`
- `CodexHooks`
- `CursorHooks`
- `DevinHooks`

Each adapter owns provider-specific installation/configuration and writes provider state into the generic control plane. Provider-specific logic is intentionally kept outside the core session model.

`ProviderState.record(home=..., agent=..., agent_session=..., hook_event=...)` persists the provider/session/hook association.

### Configurable detection

`AgentConfiguration` manages `~/.district/agents.json`. Each `AgentType` can match:

- exact process names;
- substrings in process names;
- word tokens in command lines;
- phrases in command lines (`command_matches`);
- environment-variable evidence.

`AgentDetector.is_agent_shell()` evaluates the current shell ancestry. `ancestor_commands()` returns inspected ancestor commands, and `matches()` tests a command against an `AgentType` signature. Setting `DISTRICT_DISABLE_AGENT` suppresses detection entirely, which is a useful opt-out for the automatic agent-routing shell block.

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

### `DistrictDaemon`

`DistrictDaemon.run()` starts the daemon loop. `sample_once()` samples current state, `on_publish(payload)` receives publication payloads and `handle(request)` handles normalized daemon operations.

### IPC primitives

`IpcEndpoint.for_home(home=...)` resolves the per-user endpoint.

`EventServer.request(home=..., operation=..., values=None)` is the convenient request/response API. `EventServer.serve()` runs the server, `handle(connection=...)` processes one connection and `broadcast(payload=...)` publishes to subscribers.

`EventPublisher.publish(payload=...)` emits an event. `EventSubscriber.receive(timeout=1.0)` waits for one payload and `close()` releases the subscription.

`IpcConnection` is the low-level framed connection wrapper with `send`, `recv`, `poll` and `close`.

---

## Monitoring surfaces

### CLI status

`district --status` is the human-readable aggregate status surface. `district --status --json --local-only` is the preferred editor/automation API because it avoids recursive remote aggregation.

### TUI

`WatchTui` renders `DashboardRow` values. `once()` returns/renders a single snapshot; `run()` drives the interactive dashboard. `render(rows=..., selected=0)` and `key(timeout=...)` expose the presentation primitives.

### Web dashboard

`WebDashboard` serves the live dashboard from bundled assets. It reuses/validates an existing registered server when possible.

```python
WebDashboard(
    *,
    service: District,
    home: Path | None = None,
)
```

Public methods:

- `port(host, requested)` — select/validate the listening port;
- `session_payload(entry)` — serialize a session for the dashboard;
- `health_check(url)` — validate a registered server;
- `application()` — build the ASGI application;
- `run(host, port, browser=True, exposure=None, lan=False)` — run/reuse the dashboard;
- `pair_link(base, kind)` — compose a one-time `/pair` URL for a device;
- `running_url()` — return the validated running URL when available;
- `payload()` — return the current dashboard payload;
- `execute(session_id, request)` — asynchronously run a command and capture its output;
- `send(session_id, request)` — asynchronously inject literal text and whitelisted key presses;
- `action(session_id, action)` — asynchronously execute a supported session action.

`WebStartup` manages per-user dashboard startup. `state()` returns `StartupState`; `enable(command=None)` and `disable()` mutate startup configuration.

#### Pairing and `district --web --qr`

`run()` enables device pairing: every route except static assets, `/health` and `/pair` requires a paired browser's `district_session` cookie. The browser opened on the host is paired through a one-time `local` link. With `lan=True` (the `--qr` flag) the server additionally binds the host's primary private-LAN address — loopback plus that address only, never a wildcard — and `PairingBanner` prints a QR code whose `lan` link pairs a phone on the same network. A `lan` token is redeemable only from an address on one of the host's LANs; once a LAN device proves proximity and a charter is configured, `/pair` redirects it to the public origin with a one-minute `handoff` token so the console opens there already signed in. Without a private LAN the run fails with `PairingUnavailableError`, and re-running `district --web --qr` against a loopback-only dashboard reports the conflict instead of guessing.

#### Full-screen console

The dashboard's **Attach** action opens `/console?session=<id>`, a separate full-screen window with one tab per open shell, a `+` picker for others, and a link-status indicator. It is meant for a phone held in either orientation:

- for a mirrored pane the font size follows the window — **Fit** sizes the text so the widest line is visible, **Read** keeps a comfortable size and pans sideways when a line is wider — and is recomputed on rotation and when the on-screen keyboard resizes the viewport (a transcript always wraps at the reading size instead);
- output is followed only while the view is within a few pixels of the bottom, so scrolling back is never yanked away by a refresh; a **Latest** button returns to the end;
- a key dock sends `Esc`, `Tab`, `^C`, `^D`, `Enter`, the arrows and `Backspace` (arrows and `Backspace` repeat while held), and a text line sends literal text through `POST /api/sessions/{id}/send`.

Sessions backed by a terminal multiplexer mirror the live pane and accept input. Sessions driven by a provider **hook** (for example an agent running inside an editor) have no pane to mirror, so the console shows a **read-only transcript** of the commands the hook observed, each with its exit status and duration, from `GET /api/sessions/{id}/transcript`. The response carries `persistOutput` so the console can say why output is absent instead of showing an empty block.

Command output is stored only when the privacy policy opts in (see [Privacy policy](#privacy-policy)); without it the transcript lists commands but not what they printed. The console shows the last 4,000 characters of each command's output.

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
district --hosts
district --host-add NAME SSH_TARGET [DISTRICT_COMMAND]
district --host-remove NAME
```

Remote invocations should use local-only status on the execution host to prevent recursive aggregation.

---

## Privacy, history and notifications

### Privacy policy

`PrivacyPolicy.load(home=...)` reads `~/.district/privacy.json` when present. Defaults are:

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

`persist_output` is off by default because command output routinely contains material that was never meant to be kept. When it is `true`, output is saved when a command finishes: it is passed through the [redactor](#redaction) first, then trimmed to its **last** `output_limit_bytes` (the end of a long output is usually the part that matters). The file is read on every command finish, so changing it takes effect without restarting anything and applies only to commands that finish afterwards. Create it owner-only:

```sh
umask 077 && printf '{"persist_output": true}\n' > ~/.district/privacy.json
```

### Redaction

`Redactor.redact(value=..., extra_patterns=())` removes common sensitive material before persistence. Built-in patterns cover common API-key/token/password assignments, authorization headers and common `sk_`, `ghp_` and `github_pat_` token forms. User regexes can extend this set.

### History

`History.lines()` returns durable command-history lines from the canonical database. The CLI exposes this through `district --history`.

### Notifications

`NotificationRule.matches(event=...)` evaluates one rule. `NotificationRules.read()` loads configured rules. `Notifier.for_event(event=..., rules=...)` derives notifications and `Notifier.send(title=..., message=...)` uses the platform notification surface.

---

## Installation

The top-level installer entry point is:

```bash
python district/install.py
```

`district.installer.main()` implements the same installer API programmatically.

### Installer operations

```text
install     resolve and apply selected components
plan        print the resolved plan without filesystem mutation
status      inspect components, PATH, interpreter, manifest and state
uninstall   remove District Terminal-owned artifacts; --purge also removes state
```

The interactive wizard (`↑`/`↓` or `j`/`k` to move, space to toggle, `a` for alias, `m` for live/copy, enter to install, `q` to cancel) runs only on a TTY and is skipped by `--yes` and `--dry-run`. Components cover the launcher, tmux, zsh/bash/PowerShell lifecycle hooks, agent routing, Claude/Codex/Cursor/Devin provider hooks, web autostart and the URI handler.

Important install options include `--alias`, `--bin-dir`, `--lib-dir`, `--mode live|copy|pyz`, `--preset minimal|default|full`, `--with`, `--without`, `--yes`, `--force`, `--dry-run`, `--quiet` and `--home`.

### Deployment modes

- `live` — launcher targets the checkout;
- `copy` — package is copied into the configured library directory;
- `pyz` — the running self-contained `.pyz` archive is copied to `<lib-dir>/district.pyz` and the launcher runs it directly (`exec <python> <lib-dir>/district.pyz "$@"`). Running the installer from inside an archive (`python district-….pyz run install`) selects this mode automatically; `live`/`copy` are rejected there because they need a source checkout, and `--mode pyz` is rejected outside an archive. The launcher still pins a Python ≥ 3.13 interpreter.

The manifest additionally records `version` and `payload` (the archive file name) so updates know what is installed. Older manifests without them still load.

### Components

The installer can manage the launcher, tmux, zsh/bash/PowerShell lifecycle integration, agent routing, Claude/Codex/Cursor/Devin hooks, web startup and the native `district://` URI handler.

`Layout` describes installation paths. Its properties include `launcher`, `state_dir`, `manifest_path`, `import_root` and `uri_app`.

The manifest is stored at:

```text
~/.district/install.json
```

It records owned installation components so uninstall can be conservative.

---

## CLI reference

Run `district --help` for the complete built-in manual. The principal public commands are summarized below.

### Session creation and control

```text
district
district COMMAND [ARG ...]
district ID
district ID --exec CMD...
district ID --send TEXT...
district ID --send-key KEY
district ID --kill
district --kill-all
district --list [AGENT ...]
district --cleanup ...
```

`district` with no command creates and attaches to a managed persistent session when the backend supports it. Supplying a command creates the session, runs the command and keeps the managed shell alive. A bare known session ID attaches to it.

### Agent routing

```text
district --agent [CMD ...]
district --ensure [CMD ...]
district --is-agent-shell
district --session-name [NAME [ACTION ...]]
```

`--agent` and `--ensure` reuse/create the current provider-associated session. `--is-agent-shell` is suitable for shell routing logic and exits successfully when the current shell is attributed to a configured agent.

`district --session-name` is a GET/enter pair on the recorded agent session name. Bare, inside a managed session, it prints the current agent session name — `DISTRICT_AGENT_SESSION` when present (the env-injected inner-agent protocol Cursor uses), then the session's recorded `agent_session`, then provider environment markers such as `DEVIN_SESSION_ID` — so a Devin-attributed shell prints its `DEVIN_SESSION_ID`. Outside a managed session, `district --session-name NAME [ACTION ...]` resolves the newest live session recorded under `agent_session == NAME` and behaves exactly like `district ID [ACTION ...]` (attach, `--exec`, `--send`, `--send-key`, `--kill`). Bare, outside, it exits `1` like `--id`.

### Observation

```text
district --status [--json] [--local-only]
district --watch [FILTER ...] [--once]
district --view ID
district --history [FILTER ...]
district --events [FILTER ...]
district --subscribe [--once]
```

### Web

```text
district --web [--host HOST] [--port PORT] [--no-browser]
        [--expose BACKEND] [--charter-short] [--charter-alias NAME]
        [--charter-authtoken TOKEN]
district --web --qr [--expose BACKEND]
district --web info
district --web --startup
district --web --no-startup
district --web --startup state
```

`--expose` publishes the dashboard through a `tools.charter` backend (`ngrok`, `pinggy`, `tunnl`, `runlocal`), with defaults from `[tool.district.charter]`. `--qr` binds the LAN address as well as loopback and prints a QR pairing banner: scanning it with a phone on the same Wi-Fi/LAN redeems a one-time `lan` token, sets the session cookie, and — when a charter is configured — redirects through a `handoff` token to the public URL. See [Pairing](#pairing-and-district---web---qr) above.

### Daemon

```text
district --daemon-start
district --daemon-stop
district --daemon-status
```

### MCP server

```text
district --mcp
```

`district --mcp` runs a resident MCP (Model Context Protocol) server over Content-Length framed JSON-RPC on stdio (protocol `2025-06-18`). The process keeps `District` in-process so each tool call avoids startup cost. Exposed tools:

| Tool | Purpose |
|---|---|
| `district_exec` | Run a shell command in a session; captured output + exit status. |
| `district_send` | Inject `text` and/or one whitelisted `key` (`C-c`, `Enter`, `Escape`, `Up`, ...); `enter` defaults to true when `text` is given. |
| `district_output` | Recent pane output of a session. |
| `district_sessions` | List live sessions. |
| `district_new` | Create a detached session (optional `command`, `cwd`). |
| `district_kill` | Destroy a session and its backend. |
| `district_events` | Normalized activity events (`limit`, `filter`). |
| `district_commands` | Recorded command history (`limit`, `session_id`). |

Every tool returns human-readable `content` plus `structuredContent` matching its declared `outputSchema`. `session_id` defaults only when exactly one live session exists; `district_new` sessions are agent-attributed.

### Remote hosts

```text
district --hosts
district --host-add NAME SSH_TARGET [DISTRICT_COMMAND]
district --host-remove NAME
```

### Maintenance

```text
district --doctor [--full]
district --purge
```

### Version, updates and settings

```text
district --version
district --check-updates            (alias: --update-check)
district --update [VERSION]
district --settings
district --make-release [patch|minor|major|X.Y.Z] [--dry-run] [--no-verify] [--watch]
```

See [Updates](#updates), [Settings report](#settings-report) and [Releasing](#releasing).

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
| `~/.district/district.db` | Canonical SQLite state. |
| `~/.district/install.json` | Installer-owned component manifest. |
| `~/.district/agents.json` | Configurable agent signatures. |
| `~/.district/privacy.json` | Retention/output/redaction policy. |
| `~/.district/update.json` | Hand-edited update-check policy ([Updates](#updates)). |
| `~/.district/update-state.json` | Machine-written last-check/notice state (mode `0600`). |
| `~/.district/web-auth.key` | Pairing-token signing key (mode `0600`); deleting it revokes every paired device. |
| `~/.district/pyz-cache/` | Per-build unpacked copies of a `.pyz` archive. |
| Provider-specific config paths | Managed by `ClaudeHooks`, `CodexHooks`, `CursorHooks` and `DevinHooks`. |

Environment variables used by the runtime include:

- `DISTRICT_SESSION_ID` — current managed-session recursion/identity marker;
- `DISTRICT_DISABLE_AGENT` — suppresses agent-shell detection when set;
- `DISTRICT_AGENT_SURFACE` — explicit execution surface override;
- `DISTRICT_EDITOR` — explicit editor identity;
- `DISTRICT_DISABLE_UPDATE_CHECK` — turns automatic and on-demand update checks off when set;
- `DISTRICT_UPDATE_REPO` — `owner/repo` that overrides the release repository;
- `GH_TOKEN` / `GITHUB_TOKEN` — authenticate GitHub requests (needed only for a private release repository);
- provider-specific environment markers used by agent detection;
- normal VS Code environment markers such as `VSCODE_PID`/`VSCODE_CWD` for editor detection.

---

## Updates

District Terminal checks GitHub Releases for newer builds, tells interactive users, and can replace itself.

### Commands

| Command | Behaviour |
| --- | --- |
| `district --version` | Prints `district X.Y.Z`. |
| `district --check-updates` (`--update-check`) | Forces a check now, ignoring the throttle. Prints `up to date (X.Y.Z)` or `update available: X.Y.Z — run "district --update"` (exit `0`). A network failure or a repository with no matching release prints `could not check for updates …` (exit `1`). When checks are disabled it says so (exit `0`). |
| `district --update [VERSION]` | Installs the latest release, or exactly `VERSION` (which may be older). |

### Automatic notice

Interactive invocations — bare `district`, `district ID`, `district COMMAND…`, `--status`, `--list`, `--watch`, `--view` and `--doctor` — spawn a detached background refresh when the cached answer is stale and print a one-line notice on stderr when a newer release is cached:

```text
update available: district 1.0.0 → 1.2.0 (run "district --update")
```

The notice appears at most once per day per version. It never delays the command: the lookup runs in a detached process and the *next* invocation sees the result. A notice or refresh is skipped when stderr is not a terminal, when `DISTRICT_SESSION_ID` is set (inside a managed session), or when checks are disabled. Machine paths never check or notify: `--exec`, `--send`, `--session-name`, `--status --json`, `--settings`, `--subscribe`, provider hooks, `--mcp`, `--cleanup`, `test integration` and `explain`.

### Policy: `~/.district/update.json`

```json
{
  "check": "daily",
  "repository": "kairos-xx/kairos-toolbox",
  "tag_prefix": "district/v",
  "channel": "stable",
  "timeout_seconds": 2
}
```

| Key | Values | Default |
| --- | --- | --- |
| `check` | `always`, `hourly`, `daily`, `weekly`, `never` | `daily` |
| `repository` | `owner/repo` publishing releases | the monorepo above |
| `tag_prefix` | tag prefix identifying this project's releases | `district/v` |
| `channel` | `stable` (skips prereleases) or `prerelease` | `stable` |
| `timeout_seconds` | positive integer network timeout | `2` |

Invalid or missing values fall back to defaults; a corrupt file never raises. `DISTRICT_DISABLE_UPDATE_CHECK=1` disables checks and `DISTRICT_UPDATE_REPO=owner/repo` overrides `repository`; both win over the file. The latest release is the **highest semantic version** among non-draft releases whose tag starts with `tag_prefix`, not merely the newest one, so other projects in the monorepo are ignored.

Set `GH_TOKEN` or `GITHUB_TOKEN` to authenticate API requests. Anonymous checks receive `404` from a private repository, and release assets are downloaded through their public URLs, so user-facing updates need a public repository.

### How `district --update` behaves per install

| Install | Result |
| --- | --- |
| `live` | Refuses with exit `2`: `Live install points at <source>; update with "git pull" or reinstall with "run install --mode copy".` A development checkout is never rewritten. |
| source checkout, no install | Refuses with exit `2` and the same `git pull` advice. |
| `copy` | Downloads the release archive and converts the install to `pyz` (launcher and manifest rewritten). The old copied package directory is left in place. |
| `pyz` | Replaces `<lib-dir>/district.pyz`. |
| bare archive, no manifest | The running `.pyz` replaces itself. |
| native installer (PyInstaller build) | Refuses with exit `2`: install the newer package from the release page. |

The download goes to `<lib-dir>/.district-download/`. The archive must be a zip and its SHA-256 must match the release's `SHA256SUMS.txt`; a missing checksum asset, missing platform build or mismatched digest aborts with exit `1` and leaves the installed archive untouched. The swap is an atomic `os.replace`. If the OS refuses it (a locked file on Windows), District writes `~/.district/.district-update-helper.py` — a stdlib-only script that retries the swap, stamps the new `version` into the manifest and removes itself — starts it detached, and exits `0` with "completing the update … in the background".

Assets are named `district-<version>-<platform>.pyz` with platform `macos-arm64`, `macos-x86_64`, `linux-x86_64`, `linux-aarch64` or `windows-amd64`.

---

## Settings report

`district --settings` prints one pure JSON document (nothing else is written to stdout) describing every effective setting:

```json
{
  "version": "1.0.0",
  "product": {"name": "District Terminal", "command": "district"},
  "install": {
    "mode": "pyz", "alias": "district", "launcher": "…", "bin_dir": "…",
    "lib_dir": "…", "payload": "district.pyz", "source": "…",
    "python": "…", "version": "1.0.0", "components": ["launcher"],
    "installed_at": "…"
  },
  "updates": {
    "enabled": true, "check": "daily", "channel": "stable",
    "repository": "owner/repo", "repository_from_env": false,
    "tag_prefix": "district/v", "timeout_seconds": 2,
    "last_check": 1790000000.0, "latest": "1.2.0", "release_url": "…",
    "update_available": true,
    "config_file": "…/update.json", "state_file": "…/update-state.json"
  },
  "privacy": {"history_days": 30, "event_days": 90, "persist_output": false,
              "output_limit_bytes": 262144, "redact_patterns": [],
              "config_file": "…/privacy.json"},
  "paths": {"state_directory": "…", "database": "…/district.db"},
  "environment": {"DISTRICT_DISABLE_UPDATE_CHECK": false, "DISTRICT_UPDATE_REPO": false,
                  "GH_TOKEN": false, "GITHUB_TOKEN": false,
                  "DISTRICT_DISABLE_AGENT": false}
}
```

`install` is `null` for an uninstalled source run. `updates.enabled` is false when `check` is `never` or `DISTRICT_DISABLE_UPDATE_CHECK` is set. The `environment` block reports only whether each variable is set — token values are never emitted.

---

## Releasing

`district --make-release [patch|minor|major|X.Y.Z]` (default `patch`) is a maintainer command and works only from a source checkout (a git work tree containing `tools/district/pyproject.toml` and the repository's `release.toml`); from an archive, copy or native installer it prints `--make-release is only available from a source checkout.` and exits `2`.

It is a thin front end over [`tools.stationer`](../stationer/PUBLISH.md). District Terminal keeps no release logic of its own: version arithmetic, the clean-tree and tag guards, the version-file rewrite, the commit, the tag and the push all belong to `stationer`, driven by `release.toml` at the repository root:

```toml
[release]
tag_template = "{project}/v{version}"     # -> district/v1.2.3
publish_pypi = false
create_github_release = false              # CI attaches the archives
pre_hooks = [["{python}", "-m", "pytest", "../../tests/tools/district", "-q"]]

[projects.district]
path = "tools/district"
version_files = ["tools/district/product.py"]
```

The version is read from `Product.version`, so there is no second copy to keep in sync; `pyproject.toml` reads it dynamically too. The tag template, repository and tag prefix must match what the updater looks for (`UpdateConstants`); a test pins that.

Flags: `--dry-run` prints the plan and changes nothing; `--no-verify` skips the configured hooks (the test run); `--watch` follows the CI run with `gh run watch` after a successful push. The command always passes `--no-publish`, so nothing is uploaded from your machine.

Sequence (all inside `stationer`):

1. Resolve the new version from the version file; an explicit `X.Y.Z` must exceed the latest `district/v*` tag and the tag must not already exist.
2. Preflight: no uncommitted changes (`git diff --quiet HEAD`).
3. Hooks: the test suite. Lint runs in CI on the tag.
4. Rewrite only the `Product.version` literal.
5. Stage and commit **only the version files** (pathspec commit, so untracked or unrelated files cannot enter the release commit), `git tag -a district/vX.Y.Z`, then push the commit and the tag.

Pushing the tag starts `.github/workflows/district-release.yml`. It asks `stationer show` for the project directory and version (and refuses a tag that disagrees), lints and tests, builds one archive per platform (`macos-14`, `macos-15-intel`, `ubuntu-latest`, `ubuntu-24.04-arm`, `windows-latest`) with `python -m tools.stationer … --format pyz` (Bowman, which District imports, is a declared dependency and a `local_dependencies` entry of the manifest), writes `SHA256SUMS.txt` with `python -m tools.stationer.artifacts dist --name SHA256SUMS.txt`, and creates the GitHub release with `python -m tools.stationer … publish` (generated notes starting from the previous `district/v*` tag). District Terminal contains no build or publish code of its own: only the manifest, `release.toml`, the archive bootstrap `pyz_main.py` and the workflow that wires the two tools together. A `workflow_dispatch` run builds the artifacts without publishing, which is the recommended rehearsal before the first real tag.

Manual equivalent: `python -m tools.stationer --config release.toml release --project district --bump patch --no-publish` (add `--dry-run` or run `plan` first).

Multi-platform archives cannot be built on one machine by design, which is why releasing pushes a tag and lets CI build.

---

## Distribution

Each release carries a self-contained archive per platform. Compiled dependencies (`psutil`) cannot be imported from inside a zip, so the archive unpacks itself once per build into `~/.district/pyz-cache/` and imports from there; replaced builds are pruned. It still needs a Python ≥ 3.13 interpreter on `PATH`.

```bash
curl -LO https://github.com/kairos-xx/kairos-toolbox/releases/download/district/v1.0.0/district-1.0.0-macos-arm64.pyz
curl -LO https://github.com/kairos-xx/kairos-toolbox/releases/download/district/v1.0.0/SHA256SUMS.txt
shasum -a 256 -c --ignore-missing SHA256SUMS.txt
chmod +x district-1.0.0-macos-arm64.pyz
./district-1.0.0-macos-arm64.pyz run install
```

`run install` forwards to the installer, which detects the archive and installs in `pyz` mode. Afterwards `district --update` keeps it current.

### Native installers (no Python required)

The `.pyz` archives need a Python ≥ 3.13 interpreter. For machines without one, [`tools.stationer`](../stationer/README.md) freezes District Terminal with PyInstaller into a macOS `.pkg`, a Linux `.deb` and a Windows ARM64 setup `.exe`, each built on its own target environment:

```bash
python -m tools.stationer \
    --project tools/district \
    --manifest tools/district/templates/stationer.toml \
    --output dist/native --platform macos
```

[`stationer.toml`](../../../tools/district/templates/stationer.toml) reads the version from `product.py` (`version_file`), freezes [`district_entry.py`](../../../tools/district/district_entry.py) (a frozen script cannot use `__main__.py`'s relative imports) and passes `--collect-submodules district --collect-data district` because `cli.py` imports `update`, `settings` and `release` with `importlib`, which PyInstaller cannot trace. The `[windows]` worker is only required with `--platform windows`. Results and checksums land in `dist/native/artifacts.json`; attach them to the release with `gh release upload district/vX.Y.Z dist/native/<platform>/*`. A frozen build cannot replace itself, so `district --update` explains that the newer package must be installed instead.

To build the `.pyz` locally for the current platform:

```bash
python -m tools.stationer --project tools/district \
    --manifest tools/district/templates/stationer.toml \
    --output dist --format pyz
```

`stationer` installs a throwaway copy of the project and its wheels with `pip install --target … --only-binary :all:`, ships `pyz_main.py` (the `[pyz] bootstrap` in the manifest) as the archive's `__main__.py`, writes `district-<version>-<platform>.pyz` and smoke-tests `--version` with an isolated home directory. Checksums for the release are written once, in CI, by `stationer`.

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

## `agent_identity`

Resolves the coding-agent identity exported to a process.

### `AgentIdentity`

Describes the agent, provider session and surface of this process (`agent`, `session`, `surface`, `source`).

- `from_environment(*, environment) -> AgentIdentity | None` — resolve provider, session and surface from environment variables.
- `canonical(*, name: str) -> str` — normalize provider names and aliases.
- `district_variables() -> dict[str, str]` — the `DISTRICT_` variables a hook exports to child shells.

## `bowman_api`

Bowman trust boundary for District Terminal URI control.

### `DistrictBowmanOperation`

Maps one public Bowman operation to existing District Terminal IPC (`name`, `ipc_operation`, `destructive`).

### `DistrictBowmanResult`

Result and trusted context for one Bowman invocation (`response`, `context`).

### `DistrictBowman`

Exposes existing District Terminal control through signed Bowman URIs.

- `create_uri(*, operation, values, subject, ttl) -> str` — issue a signed, expiring operation URI.
- `execute_uri(*, uri) -> DistrictBowmanResult` — validate, authorize and dispatch a URI to IPC.

## `attribution`

Resolves agent attribution from explicit evidence without guessing.

### `AttributionStrength`

Ranks attribution evidence from weakest to strongest.

### `AttributionEvidence`

One provider-attribution observation (`provider`, `provider_session`, `source`, `detail`, `surface`, `editor`, `workspace`); `authoritative()` reports whether the observation can decide attribution alone.

### `AttributionDecision`

The selected attribution and all supporting evidence (`selected`, `evidence`, `conflicted`).

### `AttributionResolver`

- `resolve(*, evidence) -> AttributionDecision` — deterministic evidence precedence.

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

## `core`

### `ExecResult`

Immutable command result containing `output` and `exit_status`.

### `SessionRepository`

```python
SessionRepository(*, home: Path)
```

- `database -> DistrictDatabase`
- `read() -> tuple[SessionEntry, ...]`
- `add(*, entry: SessionEntry) -> None`
- `remove(*, session_id: str) -> None`
- `replace(*, entries: tuple[SessionEntry, ...]) -> None`
- `update(*, transform: Callable[[tuple[SessionEntry, ...]], tuple[SessionEntry, ...]]) -> tuple[SessionEntry, ...]`

### `District`

```python
District(*, repository: SessionRepository)
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

### `DistrictDaemon`

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

### `DistrictEvent`

- `create(*, event_type: str, host_id: str, details: EventDetails | None = None) -> DistrictEvent`
- `json() -> str` — JSON serialization for transport/persistence.

## `history`

### `History`

- `lines() -> tuple[str, ...]`

## `hook_session`

Hook-backed sessions for agents whose shells District cannot own. Provider hooks translate native tool activity into ordinary observable sessions.

### `HookSessions`

- `ensure(*, home, agent, agent_session, workspace, create) -> SessionEntry | None` — find or create the session for a provider-native tool run.
- `command_started(*, home, entry, tool_use_id, command, cwd) -> None` — record a tool command start.
- `command_finished(*, home, entry, tool_use_id, failed, response) -> None` — record completion and response.

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

Installation layout with properties `launcher`, `state_dir`, `manifest_path`, `import_root` and `uri_app`.

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
- URI handler: `install_uri_handler`, `uri_handler_installed`, `remove_uri_handler`;
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

## `json_config`

Reads and writes third-party JSON configuration without destroying it.

### `JsonConfig`

- `load(*, path: Path) -> JsonObject` — return the stored object, or `{}` when absent; refuses non-object/invalid JSON rather than overwriting it.
- `save(*, path: Path, data: JsonObject) -> None` — atomically replace the file, keeping a one-time `.district-bak` backup.

## `lifecycle`

### `CommandLifecycle`

- `start(*, session_id: str, command: str, cwd: str) -> str`
- `finish(*, session_id: str, command_id: str, exit_code: int) -> None`

### `LifecycleInstaller`

- `block(*, command: str = "district") -> str`
- `powershell_block(*, command: str = "district") -> str`
- `remove_file(*, path: Path) -> None`
- `install_powershell(*, path: Path, command: str = "district") -> None`
- `install_file(*, path: Path, command: str = "district") -> None`

## `mcp_registration`

Registers the District Terminal MCP server with Claude clients.

### `McpRegistration`

- `install(*, home, launcher, read_only) -> None` — add the `district --mcp` server entry.
- `uninstall(*, home) -> None` — remove the registration.
- `installed(*, home) -> bool` — report whether the server is registered.
- `claude_desktop_path(*, home) -> Path` / `claude_code_path(*, home) -> Path` — the client configuration files.

## `mcp_server`

Minimal-latency MCP stdio server for District Terminal.

### `DistrictMcpServer`

Serves MCP tool calls against a resident District Terminal service (`protocol_version`, `server_name`, `send_keys`, `mutating_tools`, `tools`); `serve()` runs the stdio loop.

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

- `matches(*, event: DistrictEvent) -> bool`

### `NotificationRules`

- `read() -> tuple[NotificationRule, ...]`

### `Notifier`

- `for_event(*, event: DistrictEvent, rules: tuple[NotificationRule, ...])`
- `send(*, title: str, message: str) -> None`

## `pairing`

Device pairing for the remote web console. Tokens are HMAC-signed with a key in the state directory (`web-auth.key`, mode `0600`), so the process that prints a QR code and the server that redeems it share no channel beyond that file. Deleting the key file revokes every credential at once.

### `PairingKind`

Token kinds: `local` (browser on the host), `lan` (QR code, redeemable only on the host's network), `handoff` (carries a proven-nearby device to the public origin), `session` (the signed-in browser cookie).

### `PeerKind`

Peer classification: `loopback`, `lan`, `other`.

### `PairingUnavailableError`

Raised when LAN pairing is requested but the host has no private network.

### `LanAddress`

One private IPv4 address of the host. Fields: `interface`, `address`, `network`.

### `PairingKey`

- `load(*, home: Path) -> bytes` — return the signing key, creating it on first use.

### `PairingAuth`

- `for_home(*, home: Path) -> PairingAuth`
- `mint(*, kind: PairingKind, ttl: float | None = None) -> str`
- `redeem(*, token: str, kinds: frozenset[PairingKind]) -> PairingKind | None`
- `valid_session(*, cookie: str | None) -> bool`

Pairing and handoff tokens are single use and expire in minutes; a session lasts `PairingConstants.session_seconds` (twelve hours).

### `LanNetwork`

- `primary() -> LanAddress | None` — pick the address a phone on the same network would reach.
- `classify(*, peer: str | None) -> PeerKind` — place a connection's source address.
- `addresses() -> tuple[LanAddress, ...]` — list the host's private IPv4 addresses on active interfaces.

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

## `qr`

Dependency-free QR code generation for terminal output; used by `district --web --qr` to print the pairing link.

### `ErrorCorrection`

QR error-correction levels: `low`, `medium`, `quartile`, `high`.

### `QrCode`

```python
QrCode(
    *,
    data: bytes,
    error_correction: ErrorCorrection = ErrorCorrection.medium,
    version: int | None = None,
    mask: int | None = None,
)
```

Byte-mode QR matrix (versions 1–10). Properties: `version`, `size`, `modules`.

- `render_terminal(border=2, compact=True, invert=False, ansi=True) -> str`

### `QrRenderer`

- `render(*, text: str, color: bool) -> tuple[str, ...]` — draw two module rows per terminal line.
- `width(*, text: str) -> int`
- `modules(*, text: str) -> tuple[tuple[bool, ...], ...]`

### `PairingBanner`

- `render(*, url: str, headline: Sequence[str], columns: int, color: bool) -> str` — compose the QR code, instructions and link, side by side or stacked on a narrow terminal.

## `recorder`

### `EventRecorder`

- `record(*, event: DistrictEvent) -> None`

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

## `release`

Front end behind `district --make-release`; the release logic lives in `tools.stationer`.

- `repo_root(*, anchor)` — git work tree containing the project and `release.toml`, else `None`.
- `release_command(*, root, arg, dry_run, verify)` — the `stationer` argument vector; raises `ValueError` for anything but `patch|minor|major|X.Y.Z`.
- `run_cli(*, arguments, anchor, stream)` — argument parsing, the source-checkout gate, running the command and optionally `gh run watch`.
- `execute(*, args, root, capture)` — the process seam tests replace.

## `settings`

### `settings_document`

`settings_document(*, home)` returns the JSON-serialisable report printed by `district --settings`.

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

Typed keyword shape accepted by `DistrictDatabase.command_started`.

### `DistrictDatabase`

```python
DistrictDatabase(*, home: Path)
```

Schema/integrity:

- `schema_version() -> int`
- `integrity()` — inspect database integrity/recovery state.

Agent/session state:

- `set_agent_context(*, provider: str, session_id: str, workspace: str) -> None`
- `agent_context(*, workspace: str)`
- `sessions() -> tuple[SessionEntry, ...]`
- `replace_sessions(*, entries: tuple[SessionEntry, ...]) -> None`
- `update_sessions(*, transform=...) -> tuple[SessionEntry, ...]`

Events/commands/processes:

- `append_event(*, event: DistrictEvent) -> None`
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

## `update`

- `UpdatePolicy.load(*, home)` / `UpdateState.load(*, home)` — policy (`update.json` plus environment) and cached state (`update-state.json`).
- `parse_version`, `is_newer` — tolerant numeric version comparison.
- `latest_release(*, policy)`, `release_for_version(*, policy, version)`, `select_asset(*, release)`, `platform_tag()`.
- `fetch_json(*, url, policy)`, `fetch_bytes(*, url, policy)` — HTTPS-only network seams.
- `check(*, home)`, `maybe_refresh(*, home)`, `notify(*, home, stream)`, `check_updates(*, home, stream)`.
- `run_update(*, home, requested, stream)` — verified download and atomic archive replacement.
- `main(*, arguments, home)` — internal `--update-refresh` entry point.

## `url_handler`

Handles `district://` custom URIs through Bowman.

- `main(*, arguments: tuple[str, ...] | None = None) -> int` — URI-handler entry point: Bowman `request/v1` URIs are validated and dispatched through `DistrictBowman`; responses are written to stdout.

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

from district.core import SessionRepository, District

repository = SessionRepository(home=Path.home())
service = District(repository=repository)

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

from district.storage import DistrictDatabase

store = DistrictDatabase(home=Path.home())
for entry in store.sessions():
    print(entry.id, entry.agent, entry.current_command)
```

Read through `DistrictDatabase`/`SessionRepository`; do not mutate `district.db` with ad-hoc SQL from integrations.

### Editor integrations

Prefer:

```bash
district --status --json --local-only
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

`District.reconcile()` compares persisted sessions with live backend/process state and updates the canonical view. `entry_alive(entry=...)` performs backend-aware liveness evaluation.

### PID reuse

Stable process-start identity is stored separately from a PID. Monitoring logic can therefore distinguish a reused PID from the original process.

### Database recovery

Database setup creates the schema transactionally. A file that is corrupt or has an older, unsupported schema is set aside (renamed with a `.corrupt-<timestamp>` suffix) and replaced with a fresh database; a newer schema is left untouched. The implementation tracks this through `DistrictDatabase.recovered_from`, and diagnostics expose database integrity rather than silently treating corruption as valid state.

### Daemon unavailable

Daemon operations are independently diagnosable with `district --daemon-status` and `district --doctor --full`. Core persisted state remains SQLite-backed rather than being owned exclusively by the daemon process.

### No persistent backend

District Terminal falls back to the `process` backend and marks the execution non-reconnectable. Integrations should honor `SessionEntry.reconnectable` instead of displaying attach controls unconditionally.

---

## Recommended extension points

For new functionality, prefer these boundaries:

- **new coding-agent provider** → provider hook adapter + `ProviderState`;
- **new monitoring UI** → `DistrictDatabase`/status JSON + daemon events;
- **new persistent terminal backend** → backend-neutral `District` semantics and explicit runtime capabilities;
- **new remote transport** → execution-host abstraction rather than pretending the remote process is local;
- **new retention/redaction behavior** → `PrivacyPolicy`/`Redactor` before persistence;
- **new lifecycle source** → normalized `CommandLifecycle`/event model.

Avoid parallel registries, UI-owned session truth, provider-specific core state, or marking an execution reconnectable without a backend that can actually reconnect it.

---

For a concise project introduction, see [README.md](../../../tools/district/README.md). For the authoritative CLI manual installed with the application, run:

```bash
district --help
```

## Product identity API

### `Product`

`district.product.Product` is the single authority for externally visible product
identity. It defines `name`, `command`, `module`, the state directory and database names, and the URL scheme. Runtime code should consume these
values rather than introducing new product-name literals.

### `state_directory(*, home: Path) -> Path`

Returns the private District Terminal state directory, creating it when needed and applying
user-only permissions on Unix-like systems.

### `secure_private_file(*, path: Path) -> None`

Applies mode `0600` to private state files on Unix-like systems. The IPC authentication key
and SQLite database use this helper.

## Extension contracts

### `AgentProvider`

`district.agent.AgentProvider` defines the installation boundary for coding-agent
integrations. Built-in providers are exposed through `ProviderRegistry.builtins()` under
stable identifiers `claude`, `codex`, `cursor`, and `devin`. Provider-specific parsing and
configuration remain isolated in their own modules.

### `SessionBackend`

`district.core.SessionBackend` defines the minimal backend lifecycle boundary:
`alive`, `capture`, and `kill`. The current core continues to use the mature tmux and ConPTY
implementations while this protocol is the compatibility boundary for additional backend
implementations. Runtime capability selection remains owned by `RuntimePlatform`.

## Web security contract

`WebDashboard.run()` accepts only `127.0.0.1`, `localhost`, or `::1`. Non-loopback binding
raises `ValueError`. This is deliberate because session actions and process/terminal data
are privileged local information. Remote browser access must be provided through an
authenticated reverse proxy or SSH charter terminating at the loopback listener.

# Integration qualification API

## Attribution evidence

`district.attribution.AttributionStrength` ranks evidence in this order:
execution token, provider hook, explicit environment, editor metadata, process
tree, heuristic, unknown. `AttributionResolver.resolve()` always selects the
strongest evidence. Equally strong conflicting providers resolve to
`unknown`; District deliberately prefers missing attribution to false attribution.

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

`python -m district.integration_probe` creates child processes, emits
stable stdout markers, optionally opens a loopback listener, records process
IDs and District provider metadata, waits for a controlled lifetime, and exits
cleanly. The same workload is used by macOS real-agent qualification and Linux
synthetic/container qualification.

## CLI

```text
district test integration synthetic all
district test integration concurrent
district test integration synthetic PROVIDER
district test integration real PROVIDER
district explain SESSION_ID
```

`district explain` reports provider, provider session, attribution source,
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
containers. Linux tests District's runtime and provider contracts; macOS tests the
real graphical/provider integration surface.
