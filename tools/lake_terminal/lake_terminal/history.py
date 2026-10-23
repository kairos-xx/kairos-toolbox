# Copyright (c) 2026 Project contributors.
"""Queryable command activity history backed exclusively by SQLite."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .storage import LakeDatabase

if TYPE_CHECKING:
    from pathlib import Path


class History:
    """Expose canonical SQLite command history."""

    def __init__(self, *, home: Path) -> None:
        """Initialize history access.

        Args:
            home: Home.

        """
        self._database: LakeDatabase = LakeDatabase(home=home)

    def lines(self) -> tuple[str, ...]:
        """Return canonical command rows as compact JSON lines.

        Returns:
            The resulting value.

        """
        return self._database.command_json()
