"""Structured trace spans."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from secrets import token_hex
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


class SpanStatus(StrEnum):
    """Trace span status.

    Attributes:
        ok: Span completed successfully.
        error: Span completed with an error.

    """

    ok = "ok"
    error = "error"


@dataclass(frozen=True, slots=True, kw_only=True)
class Span:
    """Completed trace span.

    Attributes:
        trace_id: Owning trace identifier.
        span_id: Unique span identifier.
        parent_span_id: Optional parent span identifier.
        name: Span name.
        started_at: Span start timestamp.
        ended_at: Span end timestamp.
        status: Span status.
        attributes: Span attributes.

    """

    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    started_at: datetime
    ended_at: datetime
    status: SpanStatus
    attributes: Mapping[str, str]

    @property
    def duration_seconds(self) -> float:
        """Return span duration.

        Returns:
            Duration between start and end in seconds.

        """
        return (self.ended_at - self.started_at).total_seconds()


@dataclass(slots=True, kw_only=True)
class Trace:
    """In-memory trace collector.

    Attributes:
        trace_id: Owning trace identifier.
        spans: Finished spans collected so far.

    """

    trace_id: str = field(default_factory=lambda: token_hex(nbytes=16))
    spans: list[Span] = field(default_factory=list)

    def finish(
        self,
        *,
        name: str,
        started_at: datetime,
        parent_span_id: str | None = None,
        status: SpanStatus = SpanStatus.ok,
        attributes: Mapping[str, str] | None = None,
    ) -> Span:
        """Finish and collect a span.

        Args:
            name: Span name.
            started_at: Span start timestamp.
            parent_span_id: Optional parent span identifier.
            status: Span status.
            attributes: Optional span attributes.

        Returns:
            The completed span.

        """
        span: Span = Span(
            trace_id=self.trace_id,
            span_id=token_hex(nbytes=8),
            parent_span_id=parent_span_id,
            name=name,
            started_at=started_at,
            ended_at=datetime.now(tz=UTC),
            status=status,
            attributes=attributes or {},
        )
        self.spans.append(span)
        return span
