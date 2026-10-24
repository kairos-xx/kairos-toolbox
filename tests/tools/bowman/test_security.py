# Copyright (c) 2026 Project contributors.
"""Security lifecycle tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import sleep

import pytest
from bowman import (
    AuthorizationPolicy,
    ElevationGrant,
    PrivilegeBroker,
    SecurityContext,
    SignedSessionManager,
)
from bowman.errors import (
    AuthenticationError,
    AuthorizationError,
    ElevationError,
    SessionError,
)
from bowman.security import (
    NonceStore,
    OAuthToken,
    SessionClaims,
    StaticOAuthValidator,
)

from .helpers import check, expect


def test_signed_session_round_trip_and_tamper() -> None:
    """Sessions validate, map to security context, and reject tampering."""
    manager: SignedSessionManager = SignedSessionManager(
        key=b"x" * 32,
        issuer="tests",
    )
    token: str = manager.issue(
        subject="joao",
        scopes=frozenset({"project:read"}),
        capabilities=frozenset({"analysis:run"}),
    )
    claims: SessionClaims = manager.validate(token=token)
    expect(actual=claims.subject, expected="joao")
    check(
        condition="project:read" in claims.scopes,
        reason="issued scopes must survive validation",
    )
    context: SecurityContext = manager.security_context(claims=claims)
    check(
        condition=context.valid,
        reason="fresh session claims must build a valid context",
    )
    expect(
        actual=context.authentication_method,
        expected="signed_session",
    )
    with pytest.raises(expected_exception=SessionError):
        manager.validate(token=token + "x")


def test_one_time_session_replay_is_rejected() -> None:
    """One-time sessions cannot be consumed twice."""
    manager: SignedSessionManager = SignedSessionManager(
        key=b"y" * 32,
        issuer="tests",
    )
    token: str = manager.issue(subject="joao")
    _ = manager.validate(token=token, consume_once=True)
    with pytest.raises(expected_exception=SessionError):
        manager.validate(token=token, consume_once=True)


def test_expired_session_is_rejected() -> None:
    """Expired sessions fail validation."""
    manager: SignedSessionManager = SignedSessionManager(
        key=b"z" * 32,
        issuer="tests",
    )
    token: str = manager.issue(
        subject="joao",
        ttl=timedelta(microseconds=1),
    )
    sleep(0.002)
    with pytest.raises(expected_exception=SessionError):
        manager.validate(token=token)


def test_session_wrong_issuer_is_rejected() -> None:
    """Sessions signed by another issuer are rejected."""
    manager: SignedSessionManager = SignedSessionManager(
        key=b"w" * 32,
        issuer="tests",
    )
    other: SignedSessionManager = SignedSessionManager(
        key=b"w" * 32,
        issuer="other",
    )
    token: str = other.issue(subject="joao")
    with pytest.raises(expected_exception=SessionError):
        manager.validate(token=token)


def test_static_oauth_validator_scopes_and_expiry() -> None:
    """Static validation checks token presence, expiry, and scopes."""
    future: datetime = datetime.now(tz=UTC) + timedelta(minutes=5)
    valid: OAuthToken = OAuthToken(
        subject="user",
        issuer="issuer",
        audience="audience",
        scopes=frozenset({"openid", "profile"}),
        expires_at=future,
    )
    expired: OAuthToken = OAuthToken(
        subject="user",
        issuer="issuer",
        audience="audience",
        scopes=frozenset({"openid"}),
        expires_at=datetime.now(tz=UTC) - timedelta(minutes=5),
    )
    validator: StaticOAuthValidator = StaticOAuthValidator(
        tokens={"good": valid, "stale": expired},
    )
    granted: str = "good"
    stale: str = "stale"
    missing: str = "absent"
    result: OAuthToken = validator.validate(
        token=granted,
        required_scopes=frozenset({"openid"}),
    )
    expect(actual=result.subject, expected="user")
    with pytest.raises(expected_exception=AuthorizationError):
        validator.validate(
            token=granted,
            required_scopes=frozenset({"admin"}),
        )
    with pytest.raises(expected_exception=AuthenticationError):
        validator.validate(token=stale, required_scopes=frozenset())
    with pytest.raises(expected_exception=AuthenticationError):
        validator.validate(token=missing, required_scopes=frozenset())


def test_authorization_requires_all_dimensions() -> None:
    """Policies require identity, capabilities, scopes, and elevation."""
    policy: AuthorizationPolicy = AuthorizationPolicy(
        capabilities=frozenset({"admin:write"}),
        scopes=frozenset({"profile"}),
        require_authentication=True,
        require_elevation=True,
    )
    with pytest.raises(expected_exception=AuthenticationError):
        policy.authorize(security=SecurityContext())
    with pytest.raises(expected_exception=AuthorizationError):
        policy.authorize(
            security=SecurityContext(
                authenticated=True,
                principal="joao",
                scopes=frozenset({"profile"}),
                capabilities=frozenset({"admin:write"}),
                expires_at=datetime.now(tz=UTC) + timedelta(minutes=1),
            ),
        )
    policy.authorize(
        security=SecurityContext(
            authenticated=True,
            principal="joao",
            scopes=frozenset({"profile"}),
            capabilities=frozenset({"admin:write"}),
            expires_at=datetime.now(tz=UTC) + timedelta(minutes=1),
            elevated=True,
        ),
    )


def test_nonce_replay_and_retention() -> None:
    """Nonces cannot be consumed twice inside the retention window."""
    nonces: NonceStore = NonceStore()
    nonces.consume(nonce="once")
    with pytest.raises(expected_exception=SessionError):
        nonces.consume(nonce="once")


def test_expired_elevation_grant_is_rejected() -> None:
    """Expired grants cannot request OS elevation."""
    grant: ElevationGrant = PrivilegeBroker.grant(
        capabilities=frozenset({"system:write"}),
        reason="test",
        ttl=timedelta(microseconds=1),
    )
    sleep(0.002)
    with pytest.raises(expected_exception=ElevationError):
        PrivilegeBroker.execute(
            executable=Path("/bin/true"),
            arguments=(),
            grant=grant,
            required_capability="system:write",
        )
