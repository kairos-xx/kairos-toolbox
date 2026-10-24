# Bowman

Bowman is an independent local URI trust boundary for Python applications. It
provides signed and expiring envelopes, authentication strength, provenance,
capabilities, validity, replay protection, revocation, delegation, tracing,
redaction, rate limiting, idempotency, OAuth primitives, and native OS
privilege elevation.

Bowman contains no District Terminal storage or session-management logic.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`__init__.py`](__init__.py) — Bowman public interface.
- [`audit.py`](audit.py) — Tamper-evident hash-chained audit records.
- [`context.py`](context.py) — Immutable trusted request context.
- [`delegation.py`](delegation.py) — Capability delegation for local agents and applications.
- [`elevation.py`](elevation.py) — Explicit OS-native privilege elevation.
- [`elevation_uri.py`](elevation_uri.py) — Signed one-use elevation URIs backed by native OS authentication.
- [`envelope.py`](envelope.py) — Signed request/response protocol envelopes.
- [`errors.py`](errors.py) — Bowman exceptions.
- [`helper.py`](helper.py) — One-time signed grants for a minimal privileged helper.
- [`identity.py`](identity.py) — Caller provenance and executable authorization.
- [`json_types.py`](json_types.py) — Strict JSON-compatible value types used across Bowman.
- [`keys.py`](keys.py) — Local signing key ring and rotation.
- [`models.py`](models.py) — Core immutable Bowman models.
- [`oauth.py`](oauth.py) — OAuth 2.x and OpenID Connect local-client primitives.
- [`protection.py`](protection.py) — Rate limiting, idempotency, and redaction helpers.
- [`pyproject.toml`](pyproject.toml) — Provide pyproject toml content.
- [`revocation.py`](revocation.py) — Generic revocation registry.
- [`secrets.py`](secrets.py) — Secret storage and refresh-token vault.
- [`security.py`](security.py) — Security policies, OAuth bearer validation, and signed local sessions.
- [`tmux.py`](tmux.py) — Typed tmux status-line configuration, format DSL, and CLI renderer.
- [`trace.py`](trace.py) — Structured trace spans.
- [`trust.py`](trust.py) — Trust, authentication strength, and caller provenance.
- [`uri.py`](uri.py) — Signed, expiring, optionally one-use local URI envelopes.
- [`validity.py`](validity.py) — Validity windows and policy-driven evaluation.

### Subdirectories

No subdirectories.

<!-- exactus:inventory:end -->
