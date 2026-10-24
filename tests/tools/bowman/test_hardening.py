# Copyright (c) 2026 Project contributors.
"""Production-hardening feature tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, final

import pytest
from bowman import (
    AuditEvent,
    AuditRecord,
    AuthorizationRequest,
    ExecutablePolicy,
    HashChainAuditLog,
    HelperGrant,
    HelperGrantSigner,
    MemorySecretStore,
    NonceStore,
    OAuthClient,
    PKCEPair,
    RefreshTokenVault,
    TokenSet,
)
from bowman.errors import AuthorizationError, ElevationError
from bowman.models import CallerInfo

from .helpers import check, expect

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from bowman.json_types import JSONValue


@final
class Constants:
    """Group module-level constants.

    Attributes:
        second_sequence: Grouped module constant.

    """

    second_sequence: Final[int] = 2


class FakeOAuthTransport:
    """Deterministic OAuth transport."""

    @staticmethod
    def post_form(
        *,
        url: str,
        form: Mapping[str, str],
    ) -> Mapping[str, JSONValue]:
        """Return a fixed token-endpoint response.

        Args:
            url: Endpoint URL.
            form: Submitted form fields.

        Returns:
            The fixed token payload.

        """
        expect(actual=url, expected="https://issuer/token")
        check(
            condition="grant_type" in form,
            reason="form must carry a grant_type",
        )
        return {
            "access_token": "access",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "openid profile",
            "refresh_token": "refresh-2",
            "id_token": "identity",
        }


def test_pkce_and_oauth_exchange_refresh() -> None:
    """PKCE pairs are created and tokens exchange/refresh."""
    pair: PKCEPair = PKCEPair.create()
    expect(actual=pair.method, expected="S256")
    check(condition=bool(pair.verifier), reason="verifier must exist")
    check(condition=bool(pair.challenge), reason="challenge must exist")
    endpoint: str = "https://issuer/token"
    client: OAuthClient = OAuthClient(
        client_id="client",
        token_endpoint=endpoint,
        transport=FakeOAuthTransport(),
    )
    request: AuthorizationRequest = client.authorization_request(
        authorization_endpoint="https://issuer/authorize",
        client_id="client",
        redirect_uri="demo://oauth/callback",
        scopes=frozenset({"openid", "profile"}),
    )
    check(
        condition="code_challenge=" in request.url(),
        reason="authorization URL must carry the PKCE challenge",
    )
    token: TokenSet = client.exchange_code(
        code="code",
        redirect_uri="demo://oauth/callback",
        verifier=request.pkce.verifier,
    )
    expect(actual=token.access_token, expected="access")
    expect(actual=token.refresh_token, expected="refresh-2")
    rotated: str = "refresh"
    expect(
        actual=client.refresh(refresh_token=rotated).access_token,
        expected="access",
    )


def test_refresh_vault_rotation() -> None:
    """Refresh tokens rotate, read back, and revoke locally."""
    store: MemorySecretStore = MemorySecretStore()
    vault: RefreshTokenVault = RefreshTokenVault(store=store, service="uriapi")
    expect(actual=vault.get(subject="user"), expected=None)
    first: str = "one"
    second: str = "two"
    vault.rotate(subject="user", token=first)
    expect(actual=vault.get(subject="user"), expected="one")
    vault.rotate(subject="user", token=second)
    expect(actual=vault.get(subject="user"), expected="two")
    vault.revoke_local(subject="user")
    expect(actual=vault.get(subject="user"), expected=None)


def test_helper_grant_is_operation_bound_and_one_time() -> None:
    """Helper grants bind an operation and cannot be replayed."""
    signer: HelperGrantSigner = HelperGrantSigner(
        key=b"k" * 32,
        issuer="uriapi",
        nonces=NonceStore(),
    )
    token: str = signer.issue(operation="install", subject="user")
    grant: HelperGrant = signer.consume(token=token, operation="install")
    expect(actual=grant.operation, expected="install")
    with pytest.raises(expected_exception=ElevationError):
        signer.consume(token=token, operation="install")
    other: str = signer.issue(operation="remove", subject="user")
    with pytest.raises(expected_exception=ElevationError):
        signer.consume(token=other, operation="install")


def test_hash_chain_links_records(tmp_path: Path) -> None:
    """Each appended record chains to the previous record hash.

    Args:
        tmp_path: Temporary test directory.

    """
    audit: HashChainAuditLog = HashChainAuditLog(path=tmp_path / "audit.jsonl")
    first: AuditRecord = audit.append(
        event=AuditEvent(
            timestamp=datetime.now(tz=UTC).isoformat(),
            request_id="r1",
            trace_id="t1",
            principal="user",
            action="read",
            outcome="ok",
        ),
    )
    second: AuditRecord = audit.append(
        event=AuditEvent(
            timestamp=datetime.now(tz=UTC).isoformat(),
            request_id="r2",
            trace_id="t2",
            principal="user",
            action="write",
            outcome="ok",
        ),
    )
    expect(actual=second.sequence, expected=Constants.second_sequence)
    expect(actual=second.previous_hash, expected=first.record_hash)


def test_executable_policy_path_and_hash(tmp_path: Path) -> None:
    """Caller executables authorize by path or digest.

    Args:
        tmp_path: Temporary test directory.

    """
    executable: Path = tmp_path / "tool"
    executable.write_bytes(data=b"known")
    digest: str = ExecutablePolicy.digest(path=executable)
    policy: ExecutablePolicy = ExecutablePolicy(
        allowed_paths=frozenset({executable}),
        allowed_sha256=frozenset({digest}),
    )
    policy.authorize(caller=CallerInfo(executable=str(object=executable)))
    with pytest.raises(expected_exception=AuthorizationError):
        policy.authorize(caller=CallerInfo())
