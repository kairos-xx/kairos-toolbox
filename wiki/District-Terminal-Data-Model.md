# Data Model & Privacy

## The canonical rule

> **SQLite is the only persistent control-plane state.**

`~/.district/district.db` holds everything the control plane knows. There
is no JSON session registry, no mirror files, no second source of truth.
JSON appears only for configuration and explicit transport output such as
`--status --json`.

## Normalized schema

| Table | Contents |
| --- | --- |
| `sessions` | Canonical session records: stable UUID, short public ID, backend, provider attribution, workspace/editor metadata |
| `commands` | Transactional command lifecycle: start, finish, exit information |
| `events` | Normalized lifecycle events — the same stream the daemon publishes |
| `processes` | Observed process-tree state (fallback when hooks are unavailable) |
| `hosts` | Remote execution-host definitions |

Stable host/session/process identities prevent PID-reuse ambiguity:
an internal UUID survives process and PID changes; the six-hex-char public
ID is a display handle.

## Lifecycle authority

Command lifecycle hooks provide **authoritative** start/finish/exit records.
Process-tree inspection is the documented fallback — never the other way
around. Every monitoring surface (`--watch`, the daemon stream, the editor
bridge) reads the same normalized events rather than reconstructing state.

## Privacy

- **Redaction before persistence** — sensitive command material is scrubbed
  before it ever reaches the database.
- **Retention policy** — applies to persisted command/event records;
  `district --purge` applies it immediately.
- **Bounded buffers** — live output capture is transient and bounded; the
  dashboard does not silently accumulate history.
- **No secrets by default** — hooks record provider/session/workspace
  attribution, not payloads.

## Recovery semantics

- Schema creation runs **transactionally**.
- A corrupt database, or one with an **older unsupported schema**, is set
  aside — renamed with a `.corrupt-<timestamp>` suffix — and replaced with
  a fresh database. `DistrictDatabase.recovered_from` records what happened.
- A **newer** schema is left untouched (never downgraded in place).
- `district --doctor --full` reports database integrity rather than treating
  corruption as valid state.

## State files at a glance

| Path | Contents |
| --- | --- |
| `~/.district/district.db` | The control-plane database (only persistent state) |
| `~/.district/update.json` | Update-check policy |
| `~/.district/` manifest | What the installer owns (drives clean uninstall) |

State directory permissions are user-only on Unix-like systems; the
directory is created on first access.

Next: [Agent Attribution](Agent-Attribution) ·
[CLI reference](District-Terminal-CLI)
