"""Rate limiting, idempotency, and redaction helpers."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(slots=True, kw_only=True)
class RateLimiter:
    """Simple in-memory sliding-window rate limiter.

    Attributes:
        limit: Maximum allowed events per window.
        window: Sliding window duration.

    """

    limit: int
    window: timedelta
    _events: dict[str, deque[datetime]] = field(
        default_factory=lambda: defaultdict(deque),
    )

    def allow(self, *, key: str) -> bool:
        """Return whether one more event is allowed for key.

        Args:
            key: Caller or operation key.

        Returns:
            Whether the event is allowed.

        """
        now: datetime = datetime.now(tz=UTC)
        events: deque[datetime] = self._events[key]
        while events and now - events[0] >= self.window:
            events.popleft()
        if len(events) >= self.limit:
            return False
        events.append(now)
        return True


@dataclass(slots=True, kw_only=True)
class IdempotencyStore:
    """Remember first-seen results for duplicate submissions."""

    _values: dict[str, str] = field(default_factory=dict)

    def get(self, *, key: str) -> str | None:
        """Return a stored result for an idempotency key.

        Args:
            key: Idempotency key.

        Returns:
            The stored result, or `None` when absent.

        """
        return self._values.get(key)

    def put(self, *, key: str, value: str) -> None:
        """Store the first result for an idempotency key.

        Args:
            key: Idempotency key.
            value: Result value to remember.

        """
        _ = self._values.setdefault(key, value)


@dataclass(frozen=True, slots=True, kw_only=True)
class Redactor:
    """Redact sensitive fields before logging.

    Attributes:
        sensitive_names: Lowercase substrings identifying sensitive keys.
        replacement: Redaction replacement text.

    """

    sensitive_names: frozenset[str] = frozenset(
        {
            "authorization",
            "access_token",
            "refresh_token",
            "id_token",
            "token",
            "secret",
            "password",
            "key",
            "signature",
        },
    )
    replacement: str = "[REDACTED]"

    def mapping(
        self,
        *,
        values: Mapping[str, str],
    ) -> dict[str, str]:
        """Return a redacted copy of a string mapping.

        Args:
            values: Values to redact.

        Returns:
            Mapping with sensitive values replaced.

        """
        return {
            name: (
                self.replacement
                if any(
                    needle in name.lower() for needle in self.sensitive_names
                )
                else value
            )
            for name, value in values.items()
        }
