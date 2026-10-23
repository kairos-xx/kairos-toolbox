# `tools/`

This revision implements the non-legacy Shared control-plane architecture:

<!-- exactus:inventory:start -->
## Directory contents

### Files

No direct files besides this README.

### Subdirectories

- [`arrow/`](arrow/README.md) — Arrow is an independent local URI trust boundary for Python applications. It provides signed and expiring envelopes, authentication strength, provenance, capabilities, validity, replay protection, revocation, delegation, tracing, redaction, rate limiting, idempotency, OAuth primitives, and native OS privilege elevation.
- [`lake_terminal/`](lake_terminal/README.md) — > **A cross-platform execution control plane for humans and coding agents.** > Persistent terminals, process attribution, durable command history, normalized events, and one observable state model for CLI tools, IDE agents, automation, and remote execution hosts.

<!-- exactus:inventory:end -->

- `arrow/` — reusable local URI trust, validity, tracing, authentication, and native-elevation framework.
