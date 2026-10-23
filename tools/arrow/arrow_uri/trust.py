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
        UNTRUSTED: No provenance established.
        ASSERTED: Caller-asserted value.
        VERIFIED_BY_PROCESS: Verified by the local process.
        VERIFIED_BY_OS: Verified by the operating system.
        VERIFIED_BY_KERNEL: Verified by the kernel.
        CRYPTOGRAPHICALLY_VERIFIED: Verified by a signature.

    """

    UNTRUSTED = 0
    ASSERTED = 10
    VERIFIED_BY_PROCESS = 20
    VERIFIED_BY_OS = 30
    VERIFIED_BY_KERNEL = 40
    CRYPTOGRAPHICALLY_VERIFIED = 50


class AuthenticationStrength(IntEnum):
    """Strength of the established principal.

    Attributes:
        ANONYMOUS: No authenticated identity.
        PROCESS: Process-level identity.
        LOCAL_SESSION: Signed local session identity.
        OAUTH: OAuth-authenticated identity.
        OAUTH_MFA: OAuth identity with a second factor.
        OS_USER: Operating-system user identity.
        OS_ADMIN: Operating-system administrator identity.

    """

    ANONYMOUS = 0
    PROCESS = 10
    LOCAL_SESSION = 20
    OAUTH = 30
    OAUTH_MFA = 40
    OS_USER = 50
    OS_ADMIN = 60


class EvidenceKind(StrEnum):
    """Origin of authentication/provenance evidence.

    Attributes:
        ASSERTION: Caller-asserted evidence.
        PROCESS: Process-derived evidence.
        KERNEL: Kernel-derived evidence.
        OAUTH: OAuth-derived evidence.
        SESSION: Session-derived evidence.
        NATIVE_OS: Native OS consent evidence.
        SIGNATURE: Cryptographic signature evidence.

    """

    ASSERTION = "assertion"
    PROCESS = "process"
    KERNEL = "kernel"
    OAUTH = "oauth"
    SESSION = "session"
    NATIVE_OS = "native_os"
    SIGNATURE = "signature"


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
    strength: AuthenticationStrength = AuthenticationStrength.ANONYMOUS
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
