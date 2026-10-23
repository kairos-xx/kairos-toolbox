# Lake Terminal CLI Reference

The installed command is `lake` (a configurable alias — see
[Installing](Lake-Terminal-Install)). All monitoring surfaces and editor
integrations consume the same SQLite-backed state; the CLI is just one
client of the control plane.

## Core commands

```text
lake                          Create and attach to a persistent session.
lake COMMAND [ARG ...]        Create, run COMMAND, keep the shell alive.
lake ID                       Attach to reconnectable session ID.
lake ID --exec CMD...         Execute CMD in an existing managed session.
lake ID --send TEXT...        Type TEXT into the session, press Enter.
lake ID --send-key KEY        Press one key (Enter, C-c, Escape, Up, ...).
lake ID --kill                Terminate/remove the session.
lake --list [AGENT ...]       List local sessions, optionally by provider.
lake --status [--json]        Aggregate local and configured remote status.
lake --status --json --local-only
                              Machine API used by editor integrations.
lake --watch [FILTER ...]     Interactive/read-only multi-session TUI.
lake --watch --once           Render one dashboard snapshot and exit.
lake --view ID                Read-only view of one session.
lake --history [FILTER ...]   Durable command history from SQLite.
lake --events [FILTER ...]    Normalized lifecycle events from SQLite.
```

## Web dashboard

```text
lake --web [--host HOST] [--port PORT] [--no-browser]
                              Start/reuse the live WebSocket dashboard.
lake --web info               Show the validated running server URL.
lake --web --startup          Enable per-user startup.
lake --web --no-startup       Disable per-user startup.
lake --web --startup state    Show startup state.
```

## Agent routing

```text
lake --agent [CMD ...]        Reuse/create the current provider session.
lake --ensure [CMD ...]       Equivalent idempotent agent-session entry.
lake --is-agent-shell         Exit 0 when the shell is agent-owned.
lake --session-name NAME ...  Enter/control the session recorded under
                              agent session NAME (same actions as lake ID).
```

See [Agent Attribution](Agent-Attribution) for how routing is detected.

## Control plane

```text
lake --daemon-start           Start the authenticated event/PTY daemon.
lake --daemon-stop            Stop it.
lake --daemon-status          Print running/stopped.
lake --subscribe [--once]     Stream normalized events as JSON lines.
lake --doctor [--full]        Validate platform, SQLite, daemon, hooks.
lake --purge                  Apply privacy/retention policy now.
```

## Remote execution hosts

```text
lake --hosts                              List configured hosts.
lake --host-add NAME SSH_TARGET [LAKE_COMMAND]
lake --host-remove NAME
```

The execution host owns the backend and database — remote SSH, WSL and
container workflows are modelled as *execution hosts*, not assumed to be
the desktop OS. `--status` aggregates configured hosts over SSH;
`--local-only` prevents recursive aggregation.

## Introspection (inside a managed session)

```text
lake --id                     Current short session ID.
lake --session-name           Agent session name
                              (DEVIN_SESSION_ID, CLAUDE_CODE_SESSION_ID, ...).
lake --status                 Current session metadata.
```

## Updates

```text
lake --version                Installed version.
lake --check-updates          Check GitHub Releases now (--update-check).
lake --update [VERSION]       Install latest/specific release.
                              Live checkouts are never rewritten.
lake --settings               Every effective setting as JSON.
lake --make-release [patch|minor|major|X.Y.Z] [--dry-run] [--no-verify]
                    [--watch] Maintainer release driver (see Releasing).
```

Update policy lives in `~/.lake_terminal/update.json`
(`check: always | hourly | daily | weekly | never`, `repository`,
`tag_prefix`, `channel`, `timeout_seconds`).
`LAKE_DISABLE_UPDATE_CHECK=1` disables checks; `LAKE_UPDATE_REPO=owner/repo`
overrides the repository; `GH_TOKEN`/`GITHUB_TOKEN` authenticate against a
private one.

## Internal hooks

`--cursor-hook`, `--devin-hook`, `--codex-hook`, `--claude-hook`,
`--lifecycle-start`, `--lifecycle-finish`, `--agent-state`, `--handle-uri`
and `--mcp` are integration endpoints registered by installers and provider
integrations — not meant for manual invocation. `--handle-uri` is what the
OS-registered `lake-terminal://` handler calls (see
[Arrow](Arrow)).

## Environment

| Variable | Effect |
| --- | --- |
| `LAKE_SESSION_ID` | Marks the current managed session (recursion guard). |
| `LAKE_MCP_READ_ONLY` / `--mcp --read-only` | Hides execution tools from MCP clients. |
| `LAKE_DISABLE_AGENT` | Opts the current shell out of agent routing. |
| `LAKE_DISABLE_UPDATE_CHECK` | Turns update checks off. |
| `LAKE_UPDATE_REPO` | Overrides the release repository. |

## Exit and process semantics

`lake` does not redefine shell `exit`. Exiting a child application returns
to the managed shell; exiting the top-level managed shell detaches all
attached clients and respawns a fresh one. The session stays alive until
`lake ID --kill` or the tmux session is destroyed. A recursion guard
prevents `lake` inside `lake` from nesting persistent sessions.

## Return codes

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | Unavailable/failed runtime operation |
| `2` | Invalid CLI usage or unknown session ID |

## Identifiers

Public session IDs are six lowercase hex characters. Internal stable UUIDs
identify sessions across process/PID changes; provider session IDs and
editor/workspace metadata are stored separately when available.
