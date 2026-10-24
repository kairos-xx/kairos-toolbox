# Copyright (c) 2026 Project contributors.
"""Bowman boundary-branch coverage tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast

import pytest
from bowman.errors import AuthorizationError, BowmanError
from bowman.identity import ExecutablePolicy, PeerIdentityProvider
from bowman.keys import Constants, KeyRing, KeyStatus, SigningKey
from bowman.models import CallerInfo

from .helpers import check, expect

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
KEY_TEXT_LENGTH: Final[int] = 64


def test_expect_and_check_raise_assertion_errors() -> None:
    """Helper assertions raise AssertionError with context."""
    with pytest.raises(expected_exception=AssertionError):
        expect(actual=1, expected=2)
    with pytest.raises(expected_exception=AssertionError):
        check(condition=False, reason="must fail")


def test_peer_identity_provider_protocol_body() -> None:
    """The protocol default returns no caller."""

    class FakePeer(PeerIdentityProvider):
        """Minimal protocol implementation for coverage."""

        def caller(self) -> CallerInfo:
            """Return the protocol default.

            Returns:
                Caller provenance.

            """
            default: Callable[[PeerIdentityProvider], CallerInfo] = cast(
                typ="Callable[[PeerIdentityProvider], CallerInfo]",
                val=PeerIdentityProvider.caller,
            )
            return default(self)

    check(
        condition=cast(typ="CallerInfo | None", val=FakePeer().caller())
        is None,
        reason="protocol stubs must return None",
    )


def test_executable_policy_accepts_missing_when_unrestricted() -> None:
    """An unrestricted policy accepts callers without executables."""
    policy: ExecutablePolicy = ExecutablePolicy()
    policy.authorize(caller=CallerInfo())


def test_executable_policy_requires_executable(tmp_path: Path) -> None:
    """A restricted policy rejects missing executables.

    Args:
        tmp_path: Temporary test directory.

    """
    executable: Path = tmp_path / "tool"
    executable.write_bytes(data=b"data")
    policy: ExecutablePolicy = ExecutablePolicy(
        allowed_paths=frozenset({executable}),
    )
    with pytest.raises(expected_exception=AuthorizationError):
        policy.authorize(caller=CallerInfo())


def test_executable_policy_authorizes_by_digest(tmp_path: Path) -> None:
    """Digest allowlists authorize unknown paths.

    Args:
        tmp_path: Temporary test directory.

    """
    executable: Path = tmp_path / "tool"
    executable.write_bytes(data=b"data")
    other: Path = tmp_path / "other"
    other.write_bytes(data=b"other")
    policy: ExecutablePolicy = ExecutablePolicy(
        allowed_paths=frozenset({other}),
        allowed_sha256=frozenset(
            {ExecutablePolicy.digest(path=executable)},
        ),
    )
    policy.authorize(caller=CallerInfo(executable=str(object=executable)))
    forbidden: Path = tmp_path / "forbidden"
    forbidden.write_bytes(data=b"forbidden")
    with pytest.raises(expected_exception=AuthorizationError):
        policy.authorize(
            caller=CallerInfo(executable=str(object=forbidden)),
        )


def test_keyring_load_from_environment() -> None:
    """Environment key material seeds an active primary key."""
    ring: KeyRing = KeyRing.load(
        environment={Constants.key_environment: "material"},
    )
    active: SigningKey | None = ring.get()
    check(
        condition=active is not None,
        reason="loaded rings must have an active key",
    )
    if active is not None:
        expect(actual=active.material, expected=b"material")


def test_keyring_load_generates_and_reloads(tmp_path: Path) -> None:
    """Key files are generated once and reloaded afterwards.

    Args:
        tmp_path: Temporary test directory.

    """
    key_path: Path = tmp_path / "keyring.hex"
    first: KeyRing = KeyRing.load(path=key_path)
    check(
        condition=key_path.is_file(),
        reason="missing key files must be generated",
    )
    expect(
        actual=len(key_path.read_text().strip()),
        expected=KEY_TEXT_LENGTH,
    )
    second: KeyRing = KeyRing.load(path=key_path)
    first_key: SigningKey | None = first.get()
    second_key: SigningKey | None = second.get()
    check(
        condition=first_key is not None and second_key is not None,
        reason="reloaded rings must have an active key",
    )
    if first_key is not None and second_key is not None:
        expect(
            actual=second_key.material,
            expected=first_key.material,
        )


def test_keyring_sign_verify_and_revoke_edges() -> None:
    """Empty rings, missing keys, and revoked keys fail safely."""
    ring: KeyRing = KeyRing()
    with pytest.raises(expected_exception=BowmanError):
        ring.sign(payload=b"data")
    rotated: SigningKey = ring.rotate(key_id="primary")
    expect(actual=rotated.key_id, expected="primary")
    key_id: str
    signature: str
    key_id, signature = ring.sign(payload=b"data")
    expect(actual=key_id, expected="primary")
    check(
        condition=ring.verify(
            key_id="primary",
            payload=b"data",
            signature=signature,
        ),
        reason="fresh signatures must verify",
    )
    check(
        condition=not ring.verify(
            key_id="missing",
            payload=b"data",
            signature=signature,
        ),
        reason="unknown keys must not verify",
    )
    ring.revoke(key_id="missing")
    ring.revoke(key_id="primary")
    revoked: SigningKey | None = ring.get(key_id="primary")
    check(
        condition=revoked is not None,
        reason="revoked keys remain in the ring",
    )
    if revoked is not None:
        expect(actual=revoked.status, expected=KeyStatus.REVOKED)
    check(
        condition=not ring.verify(
            key_id="primary",
            payload=b"data",
            signature=signature,
        ),
        reason="revoked keys must not verify",
    )
