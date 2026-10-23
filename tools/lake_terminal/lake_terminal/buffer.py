# Copyright (c) 2026 Project contributors.
"""Bounded in-memory terminal output buffers."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass(slots=True, kw_only=True)
class OutputRingBuffer:
    """Keep only the newest output up to a byte budget.

    Attributes:
        limit_bytes: Limit bytes.

    """

    limit_bytes: int = 262_144
    _chunks: deque[str] = field(default_factory=deque)
    _size: int = 0

    def append(self, *, text: str) -> None:
        """Append output while evicting the oldest chunks.

        Args:
            text: Text.

        """
        encoded_size: int = len(text.encode())
        self._chunks.append(text)
        self._size += encoded_size
        while self._chunks and self._size > self.limit_bytes:
            removed: str = self._chunks.popleft()
            self._size -= len(removed.encode())

    def text(self) -> str:
        """Return currently retained output.

        Returns:
            The resulting value.

        """
        return "".join(self._chunks)
