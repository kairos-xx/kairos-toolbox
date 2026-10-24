# Copyright (c) 2026 Project contributors.
"""Bowman signed URI tests."""

from __future__ import annotations

from datetime import timedelta

import pytest
from bowman.errors import AuthorizationError
from bowman.security import NonceStore
from bowman.uri import SignedURIManager, SignedURIRequest

from .helpers import expect


def _manager() -> SignedURIManager:
    """Build a signed URI manager for tests.

    Returns:
        The configured signed URI manager.

    """
    return SignedURIManager(
        scheme="test-bowman",
        issuer="tests",
        audience="machine",
        key=b"a" * 32,
        nonces=NonceStore(),
    )


def test_signed_uri_round_trip() -> None:
    """A created URI validates back into its request."""
    manager: SignedURIManager = _manager()
    uri: str = manager.create(
        operation="session.read",
        values={"id": "abc", "tail": 20},
        subject="agent",
    )
    request: SignedURIRequest = manager.validate(uri=uri)
    expect(actual=request.operation, expected="session.read")
    expect(actual=request.values, expected={"id": "abc", "tail": 20})
    expect(actual=request.subject, expected="agent")


def test_signed_uri_replay_is_rejected() -> None:
    """A consumed URI cannot be replayed."""
    manager: SignedURIManager = _manager()
    uri: str = manager.create(operation="read")
    _ = manager.validate(uri=uri)
    with pytest.raises(expected_exception=AuthorizationError):
        manager.validate(uri=uri)


def test_signed_uri_expiry_is_rejected() -> None:
    """Expired URIs fail validation."""
    manager: SignedURIManager = _manager()
    uri: str = manager.create(
        operation="read",
        ttl=timedelta(seconds=-60),
    )
    with pytest.raises(expected_exception=AuthorizationError):
        manager.validate(uri=uri)


def test_signed_uri_wrong_target_and_version() -> None:
    """Wrong scheme, authority, and version paths are rejected."""
    manager: SignedURIManager = _manager()
    with pytest.raises(expected_exception=AuthorizationError):
        manager.validate(uri="other://request/v1/token")
    with pytest.raises(expected_exception=AuthorizationError):
        manager.validate(uri="test-bowman://other/v1/token")
    with pytest.raises(expected_exception=AuthorizationError):
        manager.validate(uri="test-bowman://request/v2/token")
    with pytest.raises(expected_exception=AuthorizationError):
        manager.validate(uri="test-bowman://request/v1/token")


def test_signed_uri_tamper_is_rejected() -> None:
    """A changed payload invalidates the signature."""
    manager: SignedURIManager = _manager()
    uri: str = manager.create(operation="read")
    marker: str = "/v1/"
    prefix: str
    token: str
    prefix, token = uri.split(sep=marker, maxsplit=1)
    body: str
    signature: str
    body, signature = token.split(sep=".", maxsplit=1)
    replacement: str = "A" if body[0] != "A" else "B"
    tampered: str = prefix + marker + replacement + body[1:] + "." + signature
    with pytest.raises(expected_exception=AuthorizationError):
        manager.validate(uri=tampered)


def test_signed_uri_bad_encoding_and_payload() -> None:
    """Invalid base64 and non-object payloads are rejected."""
    manager: SignedURIManager = _manager()
    with pytest.raises(expected_exception=AuthorizationError):
        manager.validate(uri="test-bowman://request/v1/%%%.sig")
    with pytest.raises(expected_exception=AuthorizationError):
        manager.validate(uri="test-bowman://request/v1/WzFd.c2ln")


def test_signed_uri_cross_manager_rejected() -> None:
    """URIs signed for another issuer or audience are rejected."""
    manager: SignedURIManager = _manager()
    other: SignedURIManager = SignedURIManager(
        scheme="test-bowman",
        issuer="other",
        audience="machine",
        key=b"a" * 32,
        nonces=NonceStore(),
    )
    uri: str = other.create(operation="read")
    with pytest.raises(expected_exception=AuthorizationError):
        manager.validate(uri=uri)


def test_signed_uri_consume_false_allows_revalidation() -> None:
    """Validation without consumption may be repeated."""
    manager: SignedURIManager = _manager()
    uri: str = manager.create(operation="read")
    _ = manager.validate(uri=uri, consume=False)
    request: SignedURIRequest = manager.validate(uri=uri, consume=False)
    expect(actual=request.operation, expected="read")
