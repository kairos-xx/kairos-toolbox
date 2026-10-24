# Copyright (c) 2026 Project contributors.
"""Trust-envelope framework tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from bowman import (
    AuthenticationStrength,
    Deadline,
    Delegation,
    DelegationManager,
    DelegationRequest,
    EnvelopeSigner,
    Evidence,
    EvidenceKind,
    IdempotencyStore,
    KeyRing,
    KeyStatus,
    Principal,
    RateLimiter,
    Redactor,
    RequestContext,
    RequestIdentity,
    RevocationRegistry,
    SecurityContext,
    SignedEnvelope,
    SigningKey,
    Span,
    Trace,
    TrustLevel,
    ValidityCode,
    ValidityPolicy,
    ValidityResult,
    ValidityWindow,
)
from bowman.errors import AuthenticationError, AuthorizationError
from bowman.models import CallerInfo
from bowman.trace import SpanStatus

from .helpers import check, expect


def test_key_rotation_signing_and_revocation() -> None:
    """Rotation retires the old key and revocation ends verification."""
    keys: KeyRing = KeyRing()
    rotated: SigningKey = keys.rotate(key_id="first")
    expect(actual=rotated.key_id, expected="first")
    envelope: SignedEnvelope = EnvelopeSigner.sign(
        keys=keys,
        values={"operation": "read"},
    )
    expect(actual=envelope.key_id, expected="first")
    check(
        condition=envelope.verify(keys=keys),
        reason="freshly signed envelopes must verify",
    )
    second: SigningKey = keys.rotate(key_id="second")
    expect(actual=second.status, expected=KeyStatus.ACTIVE)
    first_key: SigningKey | None = keys.get(key_id="first")
    check(
        condition=first_key is not None,
        reason="retired keys must remain for verification",
    )
    if first_key is not None:
        expect(actual=first_key.status, expected=KeyStatus.RETIRING)
    check(
        condition=envelope.verify(keys=keys),
        reason="retiring keys must still verify",
    )
    keys.revoke(key_id="first")
    check(
        condition=not envelope.verify(keys=keys),
        reason="revoked keys must not verify",
    )


def test_delegation_cannot_escalate_and_can_be_revoked() -> None:
    """Delegations stay within issuer capabilities and revocable."""
    revocations: RevocationRegistry = RevocationRegistry()
    manager: DelegationManager = DelegationManager(
        revocations=revocations,
    )
    delegation: Delegation = manager.issue(
        request=DelegationRequest(
            issuer="parent",
            subject="child",
            issuer_capabilities=frozenset({"read", "write"}),
            capabilities=frozenset({"read"}),
            ttl=timedelta(minutes=1),
        ),
    )
    manager.validate(delegation=delegation)
    with pytest.raises(expected_exception=AuthorizationError):
        manager.issue(
            request=DelegationRequest(
                issuer="parent",
                subject="child",
                issuer_capabilities=frozenset({"read"}),
                capabilities=frozenset({"admin"}),
                ttl=timedelta(minutes=1),
            ),
        )
    revocations.revoke(
        identifier=delegation.delegation_id,
        reason="test",
    )
    expect(
        actual=revocations.reason(
            identifier=delegation.delegation_id,
        ),
        expected="test",
    )
    with pytest.raises(expected_exception=AuthorizationError):
        manager.validate(delegation=delegation)


def test_validity_policy_all_major_time_and_binding_states() -> None:
    """Windows report issuer, expiry, machine, and skew states."""
    now: datetime = datetime.now(tz=UTC)
    policy: ValidityPolicy = ValidityPolicy(
        issuer="issuer",
        audience="shared",
        clock_skew=timedelta(seconds=0),
    )
    valid: ValidityWindow = ValidityWindow(
        issued_at=now,
        not_before=now - timedelta(seconds=1),
        expires_at=now + timedelta(minutes=1),
        issuer="issuer",
        audience="shared",
        machine_id="machine",
        session_id="session",
    )
    expect(
        actual=policy.evaluate(
            window=valid,
            machine_id="machine",
            session_id="session",
            now=now,
        ).code,
        expected=ValidityCode.VALID,
    )
    wrong_issuer: ValidityWindow = ValidityWindow(
        issued_at=now,
        not_before=now,
        expires_at=now + timedelta(minutes=1),
        issuer="other",
        audience="shared",
    )
    expect(
        actual=policy.evaluate(
            window=wrong_issuer,
            now=now,
        ).code,
        expected=ValidityCode.WRONG_ISSUER,
    )
    expired: ValidityWindow = ValidityWindow(
        issued_at=now - timedelta(minutes=2),
        not_before=now - timedelta(minutes=2),
        expires_at=now - timedelta(seconds=1),
        issuer="issuer",
        audience="shared",
    )
    expect(
        actual=policy.evaluate(
            window=expired,
            now=now,
        ).code,
        expected=ValidityCode.EXPIRED,
    )
    future_issued: ValidityWindow = ValidityWindow(
        issued_at=now + timedelta(minutes=1),
        issuer="issuer",
        audience="shared",
    )
    expect(
        actual=policy.evaluate(
            window=future_issued,
            now=now,
        ).code,
        expected=ValidityCode.CLOCK_SKEW,
    )
    not_yet: ValidityWindow = ValidityWindow(
        issued_at=now,
        not_before=now + timedelta(minutes=1),
        issuer="issuer",
        audience="shared",
    )
    expect(
        actual=policy.evaluate(
            window=not_yet,
            now=now,
        ).code,
        expected=ValidityCode.NOT_YET_VALID,
    )
    wrong_audience: ValidityWindow = ValidityWindow(
        issued_at=now,
        expires_at=now + timedelta(minutes=1),
        issuer="issuer",
        audience="other",
    )
    expect(
        actual=policy.evaluate(
            window=wrong_audience,
            now=now,
        ).code,
        expected=ValidityCode.WRONG_AUDIENCE,
    )
    expect(
        actual=policy.evaluate(
            window=valid,
            machine_id="other",
            session_id="session",
            now=now,
        ).code,
        expected=ValidityCode.WRONG_MACHINE,
    )
    expect(
        actual=policy.evaluate(
            window=valid,
            machine_id="machine",
            session_id="other",
            now=now,
        ).code,
        expected=ValidityCode.WRONG_SESSION,
    )


def test_principal_strength_and_evidence() -> None:
    """Principals enforce a minimum authentication strength."""
    principal: Principal = Principal(
        subject="user",
        strength=AuthenticationStrength.OAUTH,
        evidence=(
            Evidence(
                kind=EvidenceKind.OAUTH,
                name="subject",
                value="user",
                trust=TrustLevel.CRYPTOGRAPHICALLY_VERIFIED,
            ),
        ),
    )
    principal.require_strength(minimum=AuthenticationStrength.PROCESS)
    with pytest.raises(expected_exception=AuthenticationError):
        principal.require_strength(
            minimum=AuthenticationStrength.OS_ADMIN,
        )


def test_trace_context_deadline_and_request_context() -> None:
    """Traces finish spans and contexts gate execution."""
    trace: Trace = Trace(trace_id="a" * 32)
    started: datetime = datetime.now(tz=UTC) - timedelta(milliseconds=5)
    span: Span = trace.finish(
        name="authorization",
        started_at=started,
        status=SpanStatus.OK,
        attributes={"policy": "default"},
    )
    check(
        condition=span.duration_seconds > 0,
        reason="finished spans must have positive duration",
    )
    expect(actual=len(trace.spans), expected=1)
    validity: ValidityResult = ValidityResult(
        valid=True,
        code=ValidityCode.VALID,
        reason="ok",
    )
    context: RequestContext = RequestContext(
        request=RequestIdentity(
            request_id="request",
            operation="read",
            uri="shared://thing",
        ),
        caller=CallerInfo(),
        principal=Principal(),
        security=SecurityContext(),
        validity=validity,
        trace=trace,
        deadline=Deadline(
            at=datetime.now(tz=UTC) + timedelta(minutes=1),
        ),
    )
    check(
        condition=context.executable,
        reason="valid contexts within deadline must be executable",
    )
    expired_context: RequestContext = RequestContext(
        request=RequestIdentity(
            request_id="request",
            operation="read",
            uri="shared://thing",
        ),
        caller=CallerInfo(),
        principal=Principal(),
        security=SecurityContext(),
        validity=validity,
        trace=trace,
        deadline=Deadline(
            at=datetime.now(tz=UTC) - timedelta(minutes=1),
        ),
    )
    check(
        condition=not expired_context.executable,
        reason="contexts past their deadline must not be executable",
    )


def test_rate_limit_idempotency_redaction_and_digest() -> None:
    """Rate limits, idempotency, redaction, and digests behave."""
    limiter: RateLimiter = RateLimiter(
        limit=2,
        window=timedelta(minutes=1),
    )
    check(
        condition=limiter.allow(key="caller"),
        reason="first event must be allowed",
    )
    check(
        condition=limiter.allow(key="caller"),
        reason="second event must be allowed",
    )
    check(
        condition=not limiter.allow(key="caller"),
        reason="third event must be rejected",
    )
    store: IdempotencyStore = IdempotencyStore()
    expect(actual=store.get(key="x"), expected=None)
    store.put(key="x", value="first")
    store.put(key="x", value="second")
    expect(actual=store.get(key="x"), expected="first")
    redactor: Redactor = Redactor()
    result: dict[str, str] = redactor.mapping(
        values={
            "authorization": "Bearer secret",
            "ordinary": "visible",
        },
    )
    expect(actual=result["authorization"], expected="[REDACTED]")
    expect(actual=result["ordinary"], expected="visible")
    first: str = EnvelopeSigner.digest(values={"b": 2, "a": 1})
    second: str = EnvelopeSigner.digest(values={"a": 1, "b": 2})
    expect(actual=first, expected=second)
