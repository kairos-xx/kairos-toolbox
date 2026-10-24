"""Capability delegation for local agents and applications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import TYPE_CHECKING

from .errors import AuthorizationError

if TYPE_CHECKING:
    from .revocation import RevocationRegistry


@dataclass(frozen=True, slots=True, kw_only=True)
class Delegation:
    """Temporary subset of a principal's capabilities.

    Attributes:
        delegation_id: Unique delegation identifier.
        issuer: Issuing principal.
        subject: Delegated subject.
        capabilities: Delegated capability names.
        issued_at: Issue timestamp.
        expires_at: Expiry timestamp.
        depth: Chain depth of this delegation.
        max_depth: Maximum permitted chain depth.

    """

    delegation_id: str
    issuer: str
    subject: str
    capabilities: frozenset[str]
    issued_at: datetime
    expires_at: datetime
    depth: int
    max_depth: int

    @property
    def valid(self) -> bool:
        """Return whether the delegation remains temporally valid.

        Returns:
            Whether the expiry timestamp is still in the future.

        """
        return datetime.now(tz=UTC) < self.expires_at


@dataclass(frozen=True, slots=True, kw_only=True)
class DelegationRequest:
    """Fields describing one requested delegation.

    Attributes:
        issuer: Issuing principal.
        subject: Delegated subject.
        issuer_capabilities: Capabilities the issuer holds.
        capabilities: Capabilities to delegate.
        ttl: Delegation lifetime.
        depth: Chain depth of this delegation.
        max_depth: Maximum permitted chain depth.

    """

    issuer: str
    subject: str
    issuer_capabilities: frozenset[str]
    capabilities: frozenset[str]
    ttl: timedelta
    depth: int = 0
    max_depth: int = 1


@dataclass(slots=True, kw_only=True)
class DelegationManager:
    """Issue constrained delegation chains.

    Attributes:
        revocations: Revocation registry consulted during validation.

    """

    revocations: RevocationRegistry

    @staticmethod
    def issue(*, request: DelegationRequest) -> Delegation:
        """Issue a subset-only delegation.

        Args:
            request: Requested delegation fields.

        Returns:
            The issued delegation.

        Raises:
            AuthorizationError: When the request exceeds the issuer's
                capabilities or the permitted chain depth.

        """
        if not request.capabilities <= request.issuer_capabilities:
            msg: str = (
                "Cannot delegate capabilities the issuer does not possess."
            )
            raise AuthorizationError(
                msg,
            )
        if request.depth > request.max_depth:
            msg = "Delegation depth exceeded."
            raise AuthorizationError(msg)
        now: datetime = datetime.now(tz=UTC)
        return Delegation(
            delegation_id=token_urlsafe(nbytes=18),
            issuer=request.issuer,
            subject=request.subject,
            capabilities=request.capabilities,
            issued_at=now,
            expires_at=now + request.ttl,
            depth=request.depth,
            max_depth=request.max_depth,
        )

    def validate(self, *, delegation: Delegation) -> None:
        """Validate expiry and revocation.

        Args:
            delegation: Delegation to validate.

        Raises:
            AuthorizationError: When the delegation is expired or revoked.

        """
        if not delegation.valid:
            msg: str = "Delegation expired."
            raise AuthorizationError(msg)
        if self.revocations.is_revoked(
            identifier=delegation.delegation_id,
        ):
            msg = "Delegation revoked."
            raise AuthorizationError(msg)
