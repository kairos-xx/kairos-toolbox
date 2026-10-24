# District Terminal Security

## Trust boundary

District Terminal is a local execution control plane. Session metadata, command history,
process information, IPC authentication material, and optional terminal output are
private user data.

## Web dashboard

The built-in dashboard is intentionally **loopback-only**. `district --web --host` accepts
`127.0.0.1`, `localhost`, or `::1`; non-loopback binding is rejected. If remote browser
access is required, place an authenticated TLS reverse proxy or SSH charter in front of
the loopback listener. Do not expose the dashboard directly.

## Local state

On Unix-like systems `~/.district` is forced to mode `0700`; private files such as
the IPC key and SQLite database are forced to `0600`. Windows relies on the user's
profile ACL and user-scoped named-pipe environment.

## IPC

Local daemon IPC uses a randomly generated 256-bit authentication key. The key is
stored only in the District Terminal state directory.

## Reporting

Do not publish secrets, terminal captures, command histories, or state databases in a
public issue. Provide a minimal reproduction with sensitive values redacted.
