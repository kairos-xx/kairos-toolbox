# District Terminal CLI Reference

The installed command is `district` (a configurable alias — see
[Installing](District-Terminal-Install)). All monitoring surfaces and editor
integrations consume the same SQLite-backed state; the CLI is just one
client of the control plane.

## Core commands

```text
district                          Create and attach to a persistent session.
district COMMAND [ARG ...]        Create, run COMMAND, keep the shell alive.
district ID                       Attach to reconnectable session ID.
district ID --exec CMD...         Execute CMD in an existing managed session.
district ID --send TEXT...        Type TEXT into the session, press Enter.
district ID --send-key KEY        Press one key (Enter, C-c, Escape, Up, ...).
district ID --kill                Terminate/remove the session.
district --kill-all               Terminate/remove every local session.
district --list [AGENT ...]       List local sessions, optionally by provider.
district --status [--json]        Aggregate local and configured remote status.
district --status --json --local-only
                              Machine API used by editor integrations.
district --watch [FILTER ...]     Interactive/read-only multi-session TUI.
district --watch --once           Render one dashboard snapshot and exit.
district --view ID                Read-only view of one session.
district --history [FILTER ...]   Durable command history from SQLite.
district --events [FILTER ...]    Normalized lifecycle events from SQLite.
```

## Web dashboard

```text
district --web [--host HOST] [--port PORT] [--no-browser]
        [--expose BACKEND] [--charter-short]
        [--charter-alias NAME] [--charter-authtoken TOKEN]
                              Start/reuse the live WebSocket dashboard.
                              `--expose` (ngrok|pinggy|tunnl|runlocal)
                              publishes it through tools.charter; defaults
                              come from [tool.district.charter].
district --web --qr [--expose BACKEND]
                              Print a QR code (with its link beside it) that
                              pairs a phone on the same Wi-Fi/LAN and opens
                              the console there, already signed in. With a
                              charter configured the phone continues on the
                              public URL. Every device must be paired.
district --web info               Show the validated running server URL.
district --web --startup          Enable per-user startup.
district --web --no-startup       Disable per-user startup.
district --web --startup state    Show startup state.
```

`--qr` makes the server bind the host's LAN address in addition to loopback
(never a wildcard) and prints a QR code carrying a one-time, five-minute
pairing token signed by `~/.district/web-auth.key`. Only a device on
the same LAN can redeem it; redeeming sets a twelve-hour session cookie.
When a charter is configured the paired phone is redirected to the public URL
through a short-lived handoff token, so the console keeps working away from
the LAN. Re-running `district --web --qr` against a running LAN dashboard prints
a fresh pairing link; deleting `web-auth.key` revokes every paired device.

## Agent routing

```text
district --agent [CMD ...]        Reuse/create the current provider session.
district --ensure [CMD ...]       Equivalent idempotent agent-session entry.
district --is-agent-shell         Exit 0 when the shell is agent-owned.
district --session-name NAME ...  Enter/control the session recorded under
                              agent session NAME (same actions as district ID).
```

See [Agent Attribution](Agent-Attribution) for how routing is detected.

## Control plane

```text
district --daemon-start           Start the authenticated event/PTY daemon.
district --daemon-stop            Stop it.
district --daemon-status          Print running/stopped.
district --subscribe [--once]     Stream normalized events as JSON lines.
district --doctor [--full]        Validate platform, SQLite, daemon, hooks.
district --purge                  Apply privacy/retention policy now.
```

## Remote execution hosts

```text
district --hosts                              List configured hosts.
district --host-add NAME SSH_TARGET [DISTRICT_COMMAND]
district --host-remove NAME
```

The execution host owns the backend and database — remote SSH, WSL and
container workflows are modelled as *execution hosts*, not assumed to be
the desktop OS. `--status` aggregates configured hosts over SSH;
`--local-only` prevents recursive aggregation.

## Introspection (inside a managed session)

```text
district --id                     Current short session ID.
district --session-name           Agent session name
                              (DEVIN_SESSION_ID, CLAUDE_CODE_SESSION_ID, ...).
district --status                 Current session metadata.
```

## Updates

```text
district --version                Installed version.
district --check-updates          Check GitHub Releases now (--update-check).
district --update [VERSION]       Install latest/specific release.
                              Live checkouts are never rewritten.
district --settings               Every effective setting as JSON.
district --make-release [patch|minor|major|X.Y.Z] [--dry-run] [--no-verify]
                    [--watch] Maintainer release driver (see Releasing).
```

Update policy lives in `~/.district/update.json`
(`check: always | hourly | daily | weekly | never`, `repository`,
`tag_prefix`, `channel`, `timeout_seconds`).
`DISTRICT_DISABLE_UPDATE_CHECK=1` disables checks; `DISTRICT_UPDATE_REPO=owner/repo`
overrides the repository; `GH_TOKEN`/`GITHUB_TOKEN` authenticate against a
private one.

## Internal hooks

`--cursor-hook`, `--devin-hook`, `--codex-hook`, `--claude-hook`,
`--lifecycle-start`, `--lifecycle-finish`, `--agent-state`, `--handle-uri`
and `--mcp` are integration endpoints registered by installers and provider
integrations — not meant for manual invocation. `--handle-uri` is what the
OS-registered `district://` handler calls (see
[Bowman](Bowman)).

## Environment

| Variable | Effect |
| --- | --- |
| `DISTRICT_SESSION_ID` | Marks the current managed session (recursion guard). |
| `DISTRICT_MCP_READ_ONLY` / `--mcp --read-only` | Hides execution tools from MCP clients. |
| `DISTRICT_DISABLE_AGENT` | Opts the current shell out of agent routing. |
| `DISTRICT_DISABLE_UPDATE_CHECK` | Turns update checks off. |
| `DISTRICT_UPDATE_REPO` | Overrides the release repository. |

## Exit and process semantics

`district` does not redefine shell `exit`. Exiting a child application returns
to the managed shell; exiting the top-level managed shell detaches all
attached clients and respawns a fresh one. The session stays alive until
`district ID --kill` or the tmux session is destroyed. A recursion guard
prevents `district` inside `district` from nesting persistent sessions.

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
