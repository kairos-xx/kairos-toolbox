"""Validity windows and policy-driven evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class ValidityCode(StrEnum):
    """Machine-readable validity outcome.

    Attributes:
        valid: Token is valid for immediate use.
        not_yet_valid: Token is not yet active.
        expired: Token validity elapsed.
        wrong_audience: Token targets a different audience.
        wrong_machine: Token targets a different machine.
        wrong_session: Token targets a different session.
        wrong_issuer: Token comes from an unexpected issuer.
        clock_skew: Issue timestamp is implausible.

    """

    valid = "valid"
    not_yet_valid = "not_yet_valid"
    expired = "expired"
    wrong_audience = "wrong_audience"
    wrong_machine = "wrong_machine"
    wrong_session = "wrong_session"
    wrong_issuer = "wrong_issuer"
    clock_skew = "clock_skew"


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
                code=ValidityCode.wrong_issuer,
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
        return self._result(code=ValidityCode.valid, reason="Token is valid.")

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
            valid=code is ValidityCode.valid,
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
                code=ValidityCode.clock_skew,
                reason="Token issue timestamp is in the future.",
            )
        if window.not_before is not None and now < window.not_before:
            return self._result(
                code=ValidityCode.not_yet_valid,
                reason="Token is not yet valid.",
            )
        if window.expires_at is not None and now >= window.expires_at:
            return self._result(
                code=ValidityCode.expired,
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
                code=ValidityCode.wrong_audience,
                reason="Token audience does not match this context.",
            )
        if window.machine_id is not None and window.machine_id != machine_id:
            return self._result(
                code=ValidityCode.wrong_machine,
                reason="Token is bound to a different machine.",
            )
        if window.session_id is not None and window.session_id != session_id:
            return self._result(
                code=ValidityCode.wrong_session,
                reason="Token is bound to a different session.",
            )
        return None
