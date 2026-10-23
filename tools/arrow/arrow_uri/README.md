# `arrow_uri/`

Arrow public interface.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`__init__.py`](__init__.py) — Arrow public interface.
- [`audit.py`](audit.py) — Tamper-evident hash-chained audit records.
- [`context.py`](context.py) — Immutable trusted request context.
- [`delegation.py`](delegation.py) — Capability delegation for local agents and applications.
- [`elevation.py`](elevation.py) — Explicit OS-native privilege elevation.
- [`elevation_uri.py`](elevation_uri.py) — Signed one-use elevation URIs backed by native OS authentication.
- [`envelope.py`](envelope.py) — Signed request/response protocol envelopes.
- [`errors.py`](errors.py) — Arrow exceptions.
- [`helper.py`](helper.py) — One-time signed grants for a minimal privileged helper.
- [`identity.py`](identity.py) — Caller provenance and executable authorization.
- [`json_types.py`](json_types.py) — Strict JSON-compatible value types used across Arrow.
- [`keys.py`](keys.py) — Local signing key ring and rotation.
- [`models.py`](models.py) — Core immutable Arrow models.
- [`oauth.py`](oauth.py) — OAuth 2.x and OpenID Connect local-client primitives.
- [`protection.py`](protection.py) — Rate limiting, idempotency, and redaction helpers.
- [`revocation.py`](revocation.py) — Generic revocation registry.
- [`secrets.py`](secrets.py) — Secret storage and refresh-token vault.
- [`security.py`](security.py) — Security policies, OAuth bearer validation, and signed local sessions.
- [`trace.py`](trace.py) — Structured trace spans.
- [`trust.py`](trust.py) — Trust, authentication strength, and caller provenance.
- [`uri.py`](uri.py) — Signed, expiring, optionally one-use local URI envelopes.
- [`validity.py`](validity.py) — Validity windows and policy-driven evaluation.

### Subdirectories

No subdirectories.

<!-- exactus:inventory:end -->
