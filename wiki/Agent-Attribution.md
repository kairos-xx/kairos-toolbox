# Agent Attribution

Attribution answers the question every multi-agent workspace asks:
**which execution belongs to which agent or human?**

## How it works

Lake Terminal records attribution *with the session*, rather than having
every UI infer it independently. The attribution ladder, strongest first:

1. **Native provider hooks** — the provider tells Lake Terminal directly
   who it is and which conversation/session it serves.
2. **Editor/environment evidence** — variables the IDE or provider exports.
3. **Process signatures** — configurable command-line/environment matchers
   as the weakest fallback.

## Supported providers

| Provider | Hook | What is recorded |
| --- | --- | --- |
| Claude Code | `--claude-hook` | Provider, provider session ID, workspace, lifecycle state |
| Codex | `--codex-hook` | Same model |
| Cursor | `--cursor-hook` | Same, plus embedded-agent hooks: `sessionStart`, shell execution, response/thought, stop, session end |
| Devin Local Agent | `--devin-hook` | Same — Devin-specific code is confined to this adapter |

Provider hooks are installed as optional
[installer components](Lake-Terminal-Install). When no native hook exists,
progressively weaker evidence is used and *recorded as such* — the database
knows the difference between a hook-attributed session and a signature
guess.

## Agent entry points

```text
lake --agent [CMD ...]        Reuse/create this provider's session
lake --ensure [CMD ...]       Idempotent equivalent
lake --is-agent-shell         Probe for scripts/routing
lake --session-name NAME      Jump to the session behind an agent name
```

Inside a managed session:

```text
lake --id                     Six-hex public session ID
lake --session-name           Agent session name (e.g. DEVIN_SESSION_ID,
                              CLAUDE_CODE_SESSION_ID)
lake --status                 Current session metadata
```

## Why this matters

- **Concurrent agents** — several agents can share a workspace; the control
  plane distinguishes their sessions, commands and events.
- **Machine consumers** — editors call `lake --status --json --local-only`
  and get attribution straight from SQLite instead of scraping terminal UI.
- **Automation** — `lake --subscribe` emits normalized events carrying the
  attribution, so a monitor always knows *whose* command finished.
- **Honest degradation** — a process-only execution is labelled
  process-only; it is never dressed up as a reconnectable session.

See also: [Data Model & Privacy](Lake-Terminal-Data-Model) ·
[CLI reference](Lake-Terminal-CLI)
