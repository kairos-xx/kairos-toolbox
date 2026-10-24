"""Trust, authentication strength, and caller provenance."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

from .errors import AuthenticationError

if TYPE_CHECKING:
    from collections.abc import Mapping


class TrustLevel(IntEnum):
    """Strength of provenance for an asserted value.

    Attributes:
        untrusted: No provenance established.
        asserted: Caller-asserted value.
        verified_by_process: Verified by the local process.
        verified_by_os: Verified by the operating system.
        verified_by_kernel: Verified by the kernel.
        cryptographically_verified: Verified by a signature.

    """

    untrusted = 0
    asserted = 10
    verified_by_process = 20
    verified_by_os = 30
    verified_by_kernel = 40
    cryptographically_verified = 50


class AuthenticationStrength(IntEnum):
    """Strength of the established principal.

    Attributes:
        anonymous: No authenticated identity.
        process: Process-level identity.
        local_session: Signed local session identity.
        oauth: OAuth-authenticated identity.
        oauth_mfa: OAuth identity with a second factor.
        os_user: Operating-system user identity.
        os_admin: Operating-system administrator identity.

    """

    anonymous = 0
    process = 10
    local_session = 20
    oauth = 30
    oauth_mfa = 40
    os_user = 50
    os_admin = 60


class EvidenceKind(StrEnum):
    """Origin of authentication/provenance evidence.

    Attributes:
        assertion: Caller-asserted evidence.
        process: Process-derived evidence.
        kernel: Kernel-derived evidence.
        oauth: OAuth-derived evidence.
        session: Session-derived evidence.
        native_os: Native OS consent evidence.
        signature: Cryptographic signature evidence.

    """

    assertion = "assertion"
    process = "process"
    kernel = "kernel"
    oauth = "oauth"
    session = "session"
    native_os = "native_os"
    signature = "signature"


@dataclass(frozen=True, slots=True, kw_only=True)
class Evidence:
    """One attributable trust assertion.

    Attributes:
        kind: Evidence origin kind.
        name: Evidence name.
        value: Asserted value.
        trust: Provenance strength.
        issuer: Optional evidence issuer.

    """

    kind: EvidenceKind
    name: str
    value: str
    trust: TrustLevel
    issuer: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Principal:
    """Consolidated authenticated principal.

    Attributes:
        subject: Authenticated subject name.
        strength: Authentication strength.
        scopes: Granted scopes.
        capabilities: Granted capabilities.
        evidence: Collected provenance evidence.
        attributes: Additional principal attributes.

    """

    subject: str | None = None
    strength: AuthenticationStrength = AuthenticationStrength.anonymous
    scopes: frozenset[str] = frozenset()
    capabilities: frozenset[str] = frozenset()
    evidence: tuple[Evidence, ...] = ()
    attributes: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType(mapping={}),
    )

    def require_strength(
        self,
        *,
        minimum: AuthenticationStrength,
    ) -> None:
        """Require a minimum authentication strength.

        Args:
            minimum: Minimum acceptable strength.

        Raises:
            AuthenticationError: When the principal is too weak.

        """
        if self.strength < minimum:
            msg: str = f"Authentication strength {minimum.name} is required."
            raise AuthenticationError(
                msg,
            )
