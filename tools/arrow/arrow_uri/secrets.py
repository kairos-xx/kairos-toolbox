"""Secret storage and refresh-token vault."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class SecretStore(Protocol):
    """Backend storing refresh tokens out of process memory."""

    def store(self, *, subject: str, token: str) -> None:
        """Store one token for a subject.

        Args:
            subject: Token subject.
            token: Refresh token material.

        """
        ...

    def get(self, *, subject: str) -> str | None:
        """Return a stored token for a subject.

        Args:
            subject: Token subject.

        Returns:
            The stored token, or `None` when absent.

        """
        ...

    def delete(self, *, subject: str) -> None:
        """Delete a stored token for a subject.

        Args:
            subject: Token subject.

        """
        ...


@dataclass(slots=True, kw_only=True)
class MemorySecretStore:
    """In-memory secret store for tests."""

    _values: dict[str, str] = field(default_factory=dict)

    def store(self, *, subject: str, token: str) -> None:
        """Store one token for a subject.

        Args:
            subject: Token subject.
            token: Refresh token material.

        """
        self._values[subject] = token

    def get(self, *, subject: str) -> str | None:
        """Return a stored token for a subject.

        Args:
            subject: Token subject.

        Returns:
            The stored token, or `None` when absent.

        """
        return self._values.get(subject)

    def delete(self, *, subject: str) -> None:
        """Delete a stored token for a subject.

        Args:
            subject: Token subject.

        """
        _ = self._values.pop(subject, None)


@dataclass(slots=True, kw_only=True)
class RefreshTokenVault:
    """Rotate and revoke OAuth refresh tokens.

    Attributes:
        store: Secret storage backend.
        service: Service namespace used for storage keys.

    """

    store: SecretStore
    service: str

    def rotate(self, *, subject: str, token: str) -> None:
        """Store a newly issued refresh token.

        Args:
            subject: Token subject.
            token: Refresh token material.

        """
        self.store.store(subject=self._key(subject=subject), token=token)

    def get(self, *, subject: str) -> str | None:
        """Return the current token for a subject.

        Args:
            subject: Token subject.

        Returns:
            The stored token, or `None` when absent.

        """
        return self.store.get(subject=self._key(subject=subject))

    def revoke_local(self, *, subject: str) -> None:
        """Delete the locally stored refresh token.

        Args:
            subject: Token subject.

        """
        self.store.delete(subject=self._key(subject=subject))

    def _key(self, *, subject: str) -> str:
        """Build a service-scoped storage key.

        Args:
            subject: Token subject.

        Returns:
            The scoped storage key.

        """
        return f"{self.service}:{subject}:refresh"
