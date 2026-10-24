"""Generic revocation registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(slots=True, kw_only=True)
class RevocationRegistry:
    """Track revoked identifiers and their reasons."""

    _items: dict[str, tuple[datetime, str]] = field(default_factory=dict)

    def revoke(self, *, identifier: str, reason: str) -> None:
        """Revoke an identifier.

        Args:
            identifier: Identifier to revoke.
            reason: Human-readable revocation reason.

        """
        self._items[identifier] = (datetime.now(tz=UTC), reason)

    def is_revoked(self, *, identifier: str) -> bool:
        """Return whether an identifier is revoked.

        Args:
            identifier: Identifier to check.

        Returns:
            Whether the identifier was revoked.

        """
        return identifier in self._items

    def reason(self, *, identifier: str) -> str | None:
        """Return revocation reason.

        Args:
            identifier: Revoked identifier.

        Returns:
            The stored reason, or `None` when not revoked.

        """
        item: tuple[datetime, str] | None = self._items.get(identifier)
        return None if item is None else item[1]
