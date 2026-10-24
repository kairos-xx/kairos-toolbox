"""Immutable trusted request context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import CallerInfo, SecurityContext
    from .trace import Trace
    from .trust import Principal
    from .validity import ValidityResult


@dataclass(frozen=True, slots=True, kw_only=True)
class Deadline:
    """Absolute request deadline.

    Attributes:
        at: Instant at which the deadline elapses.

    """

    at: datetime

    @property
    def expired(self) -> bool:
        """Return whether the deadline has elapsed.

        Returns:
            Whether the current time is at or past the deadline.

        """
        return datetime.now(tz=UTC) >= self.at


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestIdentity:
    """Stable identity for one invocation.

    Attributes:
        request_id: Unique request identifier.
        operation: Requested operation name.
        uri: Source request URI.

    """

    request_id: str
    operation: str
    uri: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestContext:
    """Trusted context delivered to application code.

    Attributes:
        request: Stable request identity.
        caller: Transport-established caller provenance.
        principal: Consolidated authenticated principal.
        security: Authenticated identity and authorization state.
        validity: Validity evaluation result.
        trace: Trace collector for the request.
        deadline: Optional absolute request deadline.

    """

    request: RequestIdentity
    caller: CallerInfo
    principal: Principal
    security: SecurityContext
    validity: ValidityResult
    trace: Trace
    deadline: Deadline | None = None

    @property
    def executable(self) -> bool:
        """Return whether validity and deadline permit execution.

        Returns:
            Whether the request may execute right now.

        """
        return self.validity.valid and (
            self.deadline is None or not self.deadline.expired
        )
