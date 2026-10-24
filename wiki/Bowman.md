# Bowman — Local URI Trust Framework

Bowman (`tools/bowman/bowman/`) is a standalone Python package that turns a
local URI invocation — e.g. `district://request/v1/...` — into a
**security boundary crossing** instead of a bare function call. District
Terminal consumes it through `district/bowman_api.py`; Bowman has no
District Terminal dependency and can be embedded by any Python application that
exposes local URI endpoints.

## Why it exists

Registering a `scheme://` handler is trivial. Making it safe is not: *any*
process can fire a URI at your handler. Bowman provides the missing layers —
who called, with what provenance, under which policy, within what validity
window, without replay, and with elevation only through the OS's own
consent surface.

## The boundary pipeline

Before application code runs, Bowman can:

1. **Establish provenance** — who called, from which executable, at which
   trust level (`CallerInfo`, `Evidence`, `TrustLevel`).
2. **Authenticate** — bind the call to a principal with declared strength
   (`Principal`, `AuthenticationStrength`).
3. **Authorize** — compare required scopes, capabilities, authentication and
   elevation against policy (`AuthorizationPolicy`, `SecurityContext`).
4. **Constrain in time** — issue/expiry windows, issuer, audience, machine
   and session binding (`ValidityWindow`, `ValidityPolicy`).
5. **Prevent replay** — consume nonces and idempotency keys (`NonceStore`,
   `IdempotencyStore`).
6. **Observe** — trace identifiers, finished spans, hash-chained audit
   records (`Trace`, `Span`, `HashChainAuditLog`).
7. **Elevate explicitly** — narrowly allowlisted commands through the OS's
   native consent (`PrivilegeBroker`, `ElevationURIManager`) — UAC, `sudo`,
   or macOS authorization, never silent privilege.

## Feature surface

- **Signed, expiring envelopes** with configurable signing keys.
- **Authentication strength & trust levels** — not all callers are equal.
- **Capabilities and scopes** — least-privilege authorization.
- **Replay & idempotency** — nonce consumption, idempotency stores.
- **Revocation & delegation** — revocable grants, delegated envelopes.
- **Tracing & audit** — hash-chained audit log for later verification.
- **Rate limiting & redaction** — abuse containment and log safety.
- **OAuth 2.x / OIDC primitives** — for flows that need them.
- **Native OS privilege elevation** — per-OS consent surfaces.

## Model style

All models are frozen dataclasses or enums. Managers are deterministic:
they receive key material, issuer/audience strings, and stores at
construction — no ambient global state.

## Contrast with web-token libraries

| | JWT / OAuth libraries | Bowman |
| --- | --- | --- |
| Trust domain | Web tokens between networked parties | Local IPC between processes on one machine |
| Caller identity | Claims (`sub`, `iss`) | Executable provenance, trust level, evidence |
| Elevation | Out of scope | Native OS consent, narrowly allowlisted |
| Audit | Out of scope | Hash-chained local audit log |
| Revocation/delegation | Token introspection / none | First-class stores |

## In this repository

District Terminal registers the `district://` scheme at install time
(see [Installing](District-Terminal-Install)); invocations route through
`district --handle-uri` → `url_handler` → Bowman's `request/v1` validation →
`DistrictBowman` execution. See [Architecture](Architecture).

📖 Long-form API reference: `docs/tools/bowman/DOCS.md`
