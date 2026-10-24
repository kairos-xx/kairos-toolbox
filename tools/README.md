# `tools/`

Developer tools: the District Terminal control plane, the Bowman URI trust framework, and the stationer release tool.

<!-- exactus:inventory:start -->
## Directory contents

### Files

No direct files besides this README.

### Subdirectories

- [`bowman/`](bowman/README.md) — Bowman is an independent local URI trust boundary for Python applications. It provides signed and expiring envelopes, authentication strength, provenance, capabilities, validity, replay protection, revocation, delegation, tracing, redaction, rate limiting, idempotency, OAuth primitives, and native OS privilege elevation.
- [`conjure/`](conjure/README.md) — Public API for host- and guest-agnostic execution backends.
- [`district/`](district/README.md) — > **A cross-platform execution control plane for humans and coding agents.** > Persistent terminals, process attribution, durable command history, normalized events, and one observable state model for CLI tools, IDE agents, automation, and remote execution hosts.
- [`charter/`](charter/README.md) — Public API and CLI for exposing a local TCP port through a managed charter: `ngrok`, `pinggy`, `tunnl`, `runlocal` or `direct` (no charter). The stationer's remote pull workers reuse this package when `--expose` selects a backend.
- [`stationer/`](stationer/README.md) — Cross-platform release builder and publishing control plane.

<!-- exactus:inventory:end -->

- `bowman/` — reusable local URI trust, validity, tracing, authentication, and native-elevation framework.
- `district/` — cross-platform execution control plane for humans and coding agents.
- `stationer/` — builds `.pyz` archives and native installers for a Python project, and plans and executes versioned monorepo releases.
- `conjure/` — host- and guest-agnostic execution backends.
- `charter/` — managed public tunnels (ngrok, pinggy, tunnl, runlocal) for a local port.
