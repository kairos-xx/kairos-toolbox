# Copyright (c) 2026 Project contributors.
"""Agent-provider contracts and built-in provider registry."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from .claude import ClaudeHooks
from .codex import CodexHooks
from .cursor import CursorHooks
from .devin import DevinHooks

if TYPE_CHECKING:
    from .agent import AgentProvider


@final
class ProviderRegistry:
    """Expose the built-in coding-agent provider implementations."""

    @staticmethod
    def builtins() -> dict[str, type[AgentProvider]]:
        """Return built-in providers by stable provider identifier.

        Returns:
            Built-in provider classes.

        """
        return {
            "claude": ClaudeHooks,
            "codex": CodexHooks,
            "cursor": CursorHooks,
            "devin": DevinHooks,
        }
