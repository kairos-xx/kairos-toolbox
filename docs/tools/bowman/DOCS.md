# Bowman — Extended Documentation and API Reference

Bowman is the reusable URI security/trust layer under `tools/bowman`, packaged as
the `bowman` package. District Terminal consumes Bowman through its own
`bowman_api.py` adapter; Bowman itself has no District Terminal dependency and can be
embedded by any Python application that exposes local URI endpoints.

## Contents

- [Conceptual model](#conceptual-model)
- [Token and URI formats](#token-and-uri-formats)
- [Trust and authentication model](#trust-and-authentication-model)
- [Signed local URIs](#signed-local-uris)
- [Validity windows](#validity-windows)
- [Envelopes and signing keys](#envelopes-and-signing-keys)
- [Sessions and authorization](#sessions-and-authorization)
- [OAuth 2.x and OIDC](#oauth-2x-and-oidc)
- [Privilege elevation](#privilege-elevation)
- [Caller identity and request context](#caller-identity-and-request-context)
- [Delegation and revocation](#delegation-and-revocation)
- [Protection helpers](#protection-helpers)
- [Tracing and audit](#tracing-and-audit)
- [Secret storage](#secret-storage)
- [Request/response models](#requestresponse-models)
- [Errors](#errors)
- [Testing](#testing)

## Conceptual model

Bowman treats a local URI invocation (`district://request/v1/...`) as a
security boundary crossing. Before application code runs, Bowman can:

1. **Establish provenance** — who called, from which executable, at which
   trust level (`CallerInfo`, `Evidence`, `TrustLevel`).
2. **Authenticate** — bind the call to a principal with a declared
   authentication strength (`Principal`, `AuthenticationStrength`).
3. **Authorize** — compare required scopes, capabilities, authentication and
   elevation against policy (`AuthorizationPolicy`, `SecurityContext`).
4. **Constrain in time** — enforce issue/expiry windows, issuer, audience,
   machine and session binding (`ValidityWindow`, `ValidityPolicy`).
5. **Prevent replay** — consume nonces and idempotency keys
   (`NonceStore`, `IdempotencyStore`).
6. **Observe** — attach trace identifiers, finish spans, and append
   hash-chained audit records (`Trace`, `Span`, `HashChainAuditLog`).
7. **Elevate explicitly** — run narrowly allowlisted commands through the
   operating system's native consent surface (`PrivilegeBroker`,
   `ElevationURIManager`).

All models are frozen dataclasses or enums. Managers are deterministic: they
take their key material, issuer/audience strings, and stores at construction
time, and perform no ambient I/O beyond explicit backends (audit file, secret
store, OAuth transport).

## Token and URI formats

Signed tokens use a compact two-part form:

```
<base64url(JSON payload)>.<base64url(HMAC-SHA256 signature)>
```

`base64url` padding is stripped on encode and restored on decode. Payloads are
canonical JSON documents with issuer/audience/time claims.

- A signed request URI is
  `{scheme}://request/v1/{payload}.{signature}`
  produced by `SignedURIManager`.
- An elevation URI is `{scheme}://elevate/v1/{token}` produced by
  `ElevationURIManager`, where the token is URL-quoted inside the path.

HMAC-SHA-256 is the signature primitive throughout. Key material is held by the
caller or by `KeyRing`; Bowman never reads keys from the environment unless
`KeyRing.load` is asked to.

## Trust and authentication model

`bowman.trust` grades how much weight an asserted identity carries.

- `TrustLevel` (`IntEnum`) — strength of provenance for one value:
  `UNTRUSTED` (0), `ASSERTED` (10), `VERIFIED_BY_PROCESS` (20),
  `VERIFIED_BY_OS` (30), `VERIFIED_BY_KERNEL` (40),
  `CRYPTOGRAPHICALLY_VERIFIED` (50).
- `AuthenticationStrength` (`IntEnum`) — strength of the established
  principal: `ANONYMOUS` (0), `PROCESS` (10), `LOCAL_SESSION` (20),
  `OAUTH` (30), `OAUTH_MFA` (40), `OS_USER` (50), `OS_ADMIN` (60).
- `EvidenceKind` (`StrEnum`) — origin of an assertion: `ASSERTION`,
  `PROCESS`, `KERNEL`, `OAUTH`, `SESSION`, `NATIVE_OS`, `SIGNATURE`.
- `Evidence` — one attributable assertion: `kind`, `name`, `value`, `trust`,
  `issuer`.
- `Principal` — consolidated authenticated identity: `subject`, `strength`,
  `scopes`, `capabilities`, `evidence`, `attributes`.
  `require_strength(minimum)` raises when the principal's strength is below
  the requested `AuthenticationStrength`.

## Signed local URIs

`bowman.uri` issues and validates signed, expiring, optionally single-use
request URIs.

```python
manager = SignedURIManager(
    scheme="district",
    issuer="district",
    audience="district",
    key=secret_bytes,
    nonces=NonceStore(),
)
uri = manager.create(
    operation="token",
    values={"scope": ["read"]},
    subject="user-42",
    ttl=timedelta(minutes=2),
)
request = manager.validate(uri=uri, consume=True)
```

- `SignedURIManager(*, scheme, issuer, audience, key, nonces,
  default_ttl=timedelta(seconds=120))` — create and validate signed local
  URI requests.
- `SignedURIRequest` — a validated request: `request_id`, `operation`,
  `values`, `subject`, `issued_at`, `expires_at`, `trace_id`.
- `URIValue`/`URIValues` — typed query value containers.
- `create(*, operation, values=None, subject="local", trace_id=None,
  ttl=None)` returns the full `scheme://request/v1/...` URI.
- `validate(*, uri, consume=True)` verifies scheme, signature and validity,
  consumes the nonce when `consume` is set (single-use), and returns the
  decoded `SignedURIRequest`.

## Validity windows

`bowman.validity` evaluates whether a token is usable *now, here, by this
audience*.

- `ValidityWindow` — time and binding claims: `issued_at`, `not_before`,
  `expires_at`, `issuer`, `audience`, `machine_id`, `session_id`.
- `ValidityPolicy(*, issuer, audience,
  clock_skew=timedelta(seconds=30))` — expected issuer/audience plus
  tolerated clock drift; `evaluate(window, machine_id, session_id, now)`
  returns a `ValidityResult`.
- `ValidityResult` — `valid` flag plus machine-readable `code` and
  human-readable `reason`.
- `ValidityCode` (`StrEnum`) — `VALID`, `NOT_YET_VALID`, `EXPIRED`,
  `WRONG_AUDIENCE`, `WRONG_MACHINE`, `WRONG_SESSION`, `WRONG_ISSUER`,
  `CLOCK_SKEW`.

## Envelopes and signing keys

`bowman.envelope` and `bowman.keys` provide canonical envelope signing
and key rotation.

- `SignedEnvelope` — `version`, `key_id`, `payload`, `signature`;
  `verify(keys)` checks the signature against a `KeyRing`.
- `EnvelopeSigner` — `sign(keys, values, version)` produces a
  `SignedEnvelope` over canonical JSON; `digest(values)` exposes the exact
  canonical digest that is signed.
- `KeyRing(*, keys=None)` — rotating HMAC key ring with one active key:
  - `KeyRing.load(path, environment)` — load or create a persisted ring
    (honouring the configured key environment variable and default path);
  - `rotate(key_id)` — mark the active key retiring and activate `key_id`;
  - `get(key_id)` — look up a `SigningKey`;
  - `revoke(key_id)` — permanently disable a key;
  - `sign(payload)` — return `(key_id, signature)` using the active key;
  - `verify(key_id, payload, signature)` — constant-time verification.
- `SigningKey` — `key_id`, `material`, `status`.
- `KeyStatus` (`StrEnum`) — `ACTIVE`, `RETIRING`, `REVOKED`. Retiring keys
  still verify but no longer sign.

## Sessions and authorization

`bowman.security` supplies bearer validation, signed local sessions,
authorization policy and replay protection.

- `OAuthToken` — validated bearer result: `subject`, `issuer`, `audience`,
  `scopes`, `expires_at`, `claims`; `valid()` checks expiry.
- `OAuthTokenValidator` — protocol for pluggable validators;
  `StaticOAuthValidator` is a deterministic in-memory validator for local
  brokers and tests. `validate(token, required_scopes)` returns an
  `OAuthToken`.
- `AuthorizationPolicy(*, capabilities, scopes, require_authentication,
  require_elevation)` — route requirements; `authorize(security)` checks a
  `SecurityContext` and raises `AuthorizationError` on failure.
- `NonceStore(*, retention=timedelta(minutes=10))` — bounded-time replay
  protection; `consume(nonce)` returns `True` for a fresh nonce and `False`
  for a replay.
- `SessionClaims` — `session_id`, `subject`, `issued_at`, `expires_at`,
  `scopes`, `capabilities`; `valid()` checks expiry.
- `SignedSessionManager(*, key, issuer, default_ttl=30min, nonces)` —
  `issue(subject, scopes, capabilities, ttl)` returns a signed session
  token; `validate(token, consume_once)` returns `SessionClaims`;
  `security_context(claims)` maps claims to a `SecurityContext`.

## OAuth 2.x and OIDC

`bowman.oauth` implements local-client OAuth primitives: Authorization
Code + PKCE and Device Authorization, behind a provider-neutral transport
protocol so the client performs no direct I/O.

- `PKCEPair` — RFC 7636 `verifier`/`challenge`/`method`;
  `PKCEPair.create()` generates a new pair.
- `AuthorizationRequest` — `authorization_endpoint`, `client_id`,
  `redirect_uri`, `scopes`, `state`, `pkce`, `audience`; `url()` renders the
  authorization URL.
- `DeviceAuthorization` — device/user codes, verification URIs, expiry and
  poll interval.
- `TokenSet` — `access_token`, `token_type`, `expires_in`, `scope`,
  `refresh_token`, `id_token`.
- `OAuthTransport` — protocol with a single `post_form(url, form)` method;
  supply urllib-, requests-, or test-backed implementations.
- `OAuthClient(*, client_id, token_endpoint, transport)` —
  `authorization_request(...)` builds an `AuthorizationRequest`,
  `exchange_code(code, redirect_uri, verifier)` trades a code for a
  `TokenSet`, `refresh(refresh_token)` renews a `TokenSet`.

## Privilege elevation

Bowman keeps elevation explicit: an operation must be allowlisted, scoped to a
narrow capability, and consented through the operating system's own UI.
Generic `sudo`/`runas` shells are never constructed.

### `bowman.elevation`

- `ElevationGrant` — short-lived authorization: `grant_id`, `issued_at`,
  `expires_at`, `capabilities`, `reason`; `valid()` checks expiry.
- `PrivilegeBroker` — invoke a narrowly defined command through official OS
  consent UI:
  - `macos(executable, arguments)` — `osascript`'s
    `do shell script ... with administrator privileges` consent dialog;
  - `windows(executable, arguments)` — PowerShell
    `Start-Process -Verb RunAs` (UAC);
  - `linux(executable, arguments)` — `pkexec`/polkit;
  - `grant(capabilities, reason, ttl)` — build a short-lived
    `ElevationGrant` (default TTL 300 s);
  - `execute(executable, arguments, grant, required_capability)` — verify
    the grant then dispatch to the platform method.
  - Quoting helpers `powershell_literal`, `shell_command`, `apple_string`,
    `windows_quote` keep argument escaping deterministic.

### `bowman.elevation_uri`

Signed one-use elevation URIs backed by native OS authentication —
`{scheme}://elevate/v1/{token}`.

- `PrivilegedOperation` — `name`, `executable`, `argument_names`,
  `capability`.
- `PrivilegedRegistry` — explicit allowlist;
  `register(name, executable_path, argument_names, capability)` declares an
  operation, `get(name)` looks it up.
- `ElevationEnvelope` — validated elevation request: `grant_id`, `issuer`,
  `audience`, `subject`, `operation`, `arguments`, `reason`, `issued_at`,
  `not_before`, `expires_at`, `capability`.
- `ElevationURIManager(*, key, issuer, audience, scheme, registry, nonces,
  default_ttl)` —
  - `create(operation, arguments, subject, reason, ttl)` → URI;
  - `validate(uri)` → `ElevationEnvelope` (signature, registry membership,
    validity window, nonce);
  - `execute(uri)` → exit code after native elevation;
  - `open(uri)` and `timestamp(raw, name)` classmethod helpers for URI
    dispatch and timestamp parsing.

### `bowman.helper`

Minimal one-time grants for a dedicated privileged helper executable.

- `HelperGrant` — `grant_id`, `operation`, `subject`, `expires_at`.
- `HelperGrantSigner(*, key, issuer, nonces, ttl)` —
  `issue(operation, subject)` returns a signed token;
  `consume(token, operation)` validates and burns it once.

## Caller identity and request context

`bowman.identity` and `bowman.context` carry verified provenance into
application code.

- `PeerIdentityProvider` — protocol; `caller()` returns the transport's
  verified `CallerInfo` (socket peer credentials, named pipe identity, etc.).
- `ExecutablePolicy(*, allowed_paths, allowed_sha256)` — authorize callers
  by absolute executable path or SHA-256 digest; `digest(path)` computes the
  digest, `authorize(caller)` enforces the policy.
- `Deadline` — absolute request deadline with `expired()`.
- `RequestIdentity` — `request_id`, `operation`, `uri`.
- `RequestContext` — the trusted context delivered to handlers: `request`,
  `caller`, `principal`, `security`, `validity`, `trace`, `deadline`;
  `executable()` returns the caller's executable path.

## Delegation and revocation

`bowman.delegation` and `bowman.revocation` model capability
attenuation.

- `Delegation` — a temporary subset of a principal's capabilities:
  `delegation_id`, `issuer`, `subject`, `capabilities`, `issued_at`,
  `expires_at`, `depth`, `max_depth`; `valid()` checks expiry.
- `DelegationRequest` — `issuer`, `subject`, `issuer_capabilities`,
  `capabilities`, `ttl`, `depth`, `max_depth`.
- `DelegationManager(*, revocations)` — `issue(request)` enforces
  capability subsetting and depth limits; `validate(delegation)` checks
  expiry, depth, and revocation status.
- `RevocationRegistry` — `revoke(identifier, reason)`,
  `is_revoked(identifier)`, `reason(identifier)` for sessions, delegations,
  grants and key identifiers.

## Protection helpers

`bowman.protection` guards local endpoints.

- `RateLimiter(*, limit, window)` — in-memory sliding-window limiter;
  `allow(key)` admits at most `limit` events per `window` per key.
- `IdempotencyStore` — `get(key)` / `put(key, value)` remember first-seen
  results so duplicate submissions replay the original outcome.
- `Redactor(*, sensitive_names, replacement="[REDACTED]")` — `mapping`
  scrubs sensitive fields before logging; defaults cover `token`,
  `access_token`, `id_token`, `refresh_token`, `authorization`, `password`,
  `secret`, `key`, `signature`.

## Tracing and audit

`bowman.trace` and `bowman.audit` provide observability.

- `Trace(*, trace_id)` — in-memory span collector;
  `finish(name, started_at, parent_span_id, status, attributes)` records a
  completed `Span`.
- `Span` — `trace_id`, `span_id`, `parent_span_id`, `name`, `started_at`,
  `ended_at`, `status`, `attributes`; `duration_seconds()` reports elapsed
  time.
- `SpanStatus` (`StrEnum`) — `OK`, `ERROR`.
- `TraceContext` — `request_id`, `trace_id`, `span_id`, `parent_span_id`,
  `started_at`.
- `AuditEvent` — `timestamp`, `request_id`, `trace_id`, `principal`,
  `action`, `outcome`.
- `AuditRecord` — `AuditEvent` fields plus `sequence`, `previous_hash`,
  `record_hash`.
- `HashChainAuditLog(*, path)` — append-only JSONL log; `append(event)`
  chains each record's SHA-256 into the next, making history tamper-evident.

## Secret storage

`bowman.secrets` keeps refresh tokens out of process memory where
possible.

- `SecretStore` — protocol: `store(subject, token)`, `get(subject)`,
  `delete(subject)`.
- `MemorySecretStore` — in-memory implementation for tests.
- `RefreshTokenVault(*, store, service)` — `rotate(subject, token)` stores a
  rotated refresh token, `get(subject)` retrieves it, `revoke_local(subject)`
  deletes it locally.

## Request/response models

`bowman.models` defines the normalized request pipeline types.

- `URIRequest` — `uri`, `scheme`, `authority`, `path`, `query`, `fragment`,
  `operation`, `caller`, `trace`, `security`.
- `URIResponse` — `status`, `data`, `error`, `request_id`, `trace_id`.
- `URIStatus` (`StrEnum`) — `OK`, `ACCEPTED`, `BAD_REQUEST`, `UNAUTHORIZED`,
  `FORBIDDEN`, `NOT_FOUND`, `CONFLICT`, `ERROR`.
- `CallerInfo` — `transport`, `pid`, `parent_pid`, `executable`,
  `process_name`, `user`, `session_id`, `source_application`, `metadata`.
- `SecurityContext` — `authenticated`, `principal`,
  `authentication_method`, `capabilities`, `scopes`, `claims`, `expires_at`,
  `elevated`; `valid()` checks expiry.

## Errors

`bowman.errors` — `BowmanError` is the framework base class;
`RouteNotFoundError`, `RequestValidationError`, `AuthenticationError`,
`AuthorizationError`, `SessionError`, and `ElevationError` distinguish the
failure stage so handlers can map them to `URIStatus` values.

## Testing

Bowman is exercised by `tests/tools/bowman/` (URI envelopes, elevation URIs,
sessions, keys, OAuth, delegation, protection, audit). Tests run under the
repository's normal `pytest` invocation; no external services, sockets, or
privileged operations are required because transports, stores, and native
elevation calls are injected boundaries.
