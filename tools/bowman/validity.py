"""Validity windows and policy-driven evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class ValidityCode(StrEnum):
    """Machine-readable validity outcome.

    Attributes:
        VALID: Token is valid for immediate use.
        NOT_YET_VALID: Token is not yet active.
        EXPIRED: Token validity elapsed.
        WRONG_AUDIENCE: Token targets a different audience.
        WRONG_MACHINE: Token targets a different machine.
        WRONG_SESSION: Token targets a different session.
        WRONG_ISSUER: Token comes from an unexpected issuer.
        CLOCK_SKEW: Issue timestamp is implausible.

    """

    VALID = "valid"
    NOT_YET_VALID = "not_yet_valid"
    EXPIRED = "expired"
    WRONG_AUDIENCE = "wrong_audience"
    WRONG_MACHINE = "wrong_machine"
    WRONG_SESSION = "wrong_session"
    WRONG_ISSUER = "wrong_issuer"
    CLOCK_SKEW = "clock_skew"


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidityResult:
    """Validity evaluation result.

    Attributes:
        valid: Whether the token is valid.
        code: Machine-readable outcome code.
        reason: Human-readable explanation.

    """

    valid: bool
    code: ValidityCode
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidityWindow:
    """Time and binding window attached to a token.

    Attributes:
        issued_at: Token issue timestamp.
        not_before: Optional earliest-use timestamp.
        expires_at: Optional expiry timestamp.
        issuer: Optional expected issuer.
        audience: Optional expected audience.
        machine_id: Optional machine binding.
        session_id: Optional session binding.

    """

    issued_at: datetime
    not_before: datetime | None = None
    expires_at: datetime | None = None
    issuer: str | None = None
    audience: str | None = None
    machine_id: str | None = None
    session_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidityPolicy:
    """Expected audience, issuer, and skew policy.

    Attributes:
        issuer: Expected issuer.
        audience: Expected audience.
        clock_skew: Tolerated clock skew.

    """

    issuer: str
    audience: str
    clock_skew: timedelta = timedelta(seconds=30)

    def evaluate(
        self,
        *,
        window: ValidityWindow,
        machine_id: str | None = None,
        session_id: str | None = None,
        now: datetime | None = None,
    ) -> ValidityResult:
        """Evaluate a validity window against this policy.

        Args:
            window: Token time and binding window.
            machine_id: Optional local machine identifier.
            session_id: Optional local session identifier.
            now: Optional evaluation timestamp.

        Returns:
            The first failing check, or a valid result.

        """
        checked: datetime = now or datetime.now(tz=UTC)
        if window.issuer is not None and window.issuer != self.issuer:
            return self._result(
                code=ValidityCode.WRONG_ISSUER,
                reason="Token issuer is not the expected issuer.",
            )
        temporal: ValidityResult | None = self._temporal(
            window=window,
            now=checked,
        )
        if temporal is not None:
            return temporal
        binding: ValidityResult | None = self._binding(
            window=window,
            machine_id=machine_id,
            session_id=session_id,
        )
        if binding is not None:
            return binding
        return self._result(code=ValidityCode.VALID, reason="Token is valid.")

    @staticmethod
    def _result(*, code: ValidityCode, reason: str) -> ValidityResult:
        """Build a validity result.

        Args:
            code: Machine-readable outcome code.
            reason: Human-readable explanation.

        Returns:
            A validity result for the code.

        """
        return ValidityResult(
            valid=code is ValidityCode.VALID,
            code=code,
            reason=reason,
        )

    def _temporal(
        self,
        *,
        window: ValidityWindow,
        now: datetime,
    ) -> ValidityResult | None:
        """Check issue, activation, and expiry timestamps.

        Args:
            window: Token time window.
            now: Evaluation timestamp.

        Returns:
            The failing result, or `None` when timestamps are valid.

        """
        if window.issued_at > now + self.clock_skew:
            return self._result(
                code=ValidityCode.CLOCK_SKEW,
                reason="Token issue timestamp is in the future.",
            )
        if window.not_before is not None and now < window.not_before:
            return self._result(
                code=ValidityCode.NOT_YET_VALID,
                reason="Token is not yet valid.",
            )
        if window.expires_at is not None and now >= window.expires_at:
            return self._result(
                code=ValidityCode.EXPIRED,
                reason="Token has expired.",
            )
        return None

    def _binding(
        self,
        *,
        window: ValidityWindow,
        machine_id: str | None,
        session_id: str | None,
    ) -> ValidityResult | None:
        """Check audience, machine, and session bindings.

        Args:
            window: Token binding window.
            machine_id: Optional local machine identifier.
            session_id: Optional local session identifier.

        Returns:
            The failing result, or `None` when bindings match.

        """
        if window.audience is not None and window.audience != self.audience:
            return self._result(
                code=ValidityCode.WRONG_AUDIENCE,
                reason="Token audience does not match this context.",
            )
        if window.machine_id is not None and window.machine_id != machine_id:
            return self._result(
                code=ValidityCode.WRONG_MACHINE,
                reason="Token is bound to a different machine.",
            )
        if window.session_id is not None and window.session_id != session_id:
            return self._result(
                code=ValidityCode.WRONG_SESSION,
                reason="Token is bound to a different session.",
            )
        return None
