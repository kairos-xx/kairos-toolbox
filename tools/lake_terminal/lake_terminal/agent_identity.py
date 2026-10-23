# Copyright (c) 2026 Project contributors.
"""Resolve the coding-agent identity exported to a process."""

from __future__ import annotations

from dataclasses import dataclass
from os import environ
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True, kw_only=True)
class _Provider:
    """Describe the environment variables one coding agent exports.

    Attributes:
        agent: Canonical provider identifier.
        sessions: Native session variables, strongest first.
        markers: Variables whose presence alone identifies the provider.
        entrypoint: Variable naming the invocation surface, when any.

    """

    agent: str
    sessions: tuple[str, ...]
    markers: tuple[str, ...] = ()
    entrypoint: str = ""


class Constants:
    """Group provider variables and normalization tables.

    Attributes:
        providers: Known providers in tie-break order.
        aliases: Alternative provider names mapped to canonical ones.
        surfaces: Claude Code entrypoints mapped to Lake surfaces.
        ai_agent: Cross-vendor variable naming the running agent.
        agent_variable: Explicit Lake provider override.
        session_variable: Explicit Lake provider-session override.
        surface_variable: Explicit Lake surface override.

    """

    providers: Final[tuple[_Provider, ...]] = (
        _Provider(
            agent="claude-code",
            sessions=("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"),
            markers=("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"),
            entrypoint="CLAUDE_CODE_ENTRYPOINT",
        ),
        _Provider(
            agent="codex",
            sessions=("CODEX_SESSION_ID", "CODEX_THREAD_ID"),
            markers=("CODEX_SANDBOX",),
        ),
        _Provider(
            agent="cursor",
            sessions=("CURSOR_SESSION_ID",),
            markers=("CURSOR_AGENT",),
        ),
        _Provider(agent="devin", sessions=("DEVIN_SESSION_ID",)),
    )
    aliases: Final[dict[str, str]] = {"claude": "claude-code"}
    surfaces: Final[dict[str, str]] = {
        "cli": "cli",
        "claude-vscode": "vscode",
        "claude-desktop": "desktop",
    }
    ai_agent: Final[str] = "AI_AGENT"
    agent_variable: Final[str] = "LAKE_AGENT"
    session_variable: Final[str] = "LAKE_AGENT_SESSION"
    surface_variable: Final[str] = "LAKE_AGENT_SURFACE"


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentIdentity:
    """Describe the agent, provider session and surface of this process.

    Attributes:
        agent: Canonical provider identifier.
        session: Provider conversation/session identifier, or empty.
        surface: Invocation surface such as cli, vscode, or desktop.
        source: Environment variable that supplied the strongest evidence.

    """

    agent: str
    session: str = ""
    surface: str = ""
    source: str = ""

    @classmethod
    def from_environment(
        cls,
        *,
        environment: Mapping[str, str] | None = None,
    ) -> AgentIdentity | None:
        """Resolve the agent identity exported to this process.

        Explicit ``LAKE_AGENT``/``LAKE_AGENT_SESSION`` win. Otherwise the
        provider-native session variable (``CLAUDE_CODE_SESSION_ID``,
        ``DEVIN_SESSION_ID``, ``CURSOR_SESSION_ID``, ``CODEX_SESSION_ID``,
        ``CODEX_THREAD_ID``, ``CLAUDE_SESSION_ID``) supplies the session, so
        no hook is required for an agent shell to be attributable.

        Args:
            environment: Variables to inspect; defaults to ``os.environ``.

        Returns:
            The resolved identity, or None when no agent evidence exists.

        """
        env: Mapping[str, str] = (
            environ if environment is None else environment
        )
        explicit: str = cls.canonical(
            name=env.get(Constants.agent_variable, ""),
        )
        if explicit:
            return cls._explicit(env=env, agent=explicit)
        sessioned: AgentIdentity | None = None
        marked: AgentIdentity | None = None
        for provider in Constants.providers:
            variable, value = cls._first(env=env, names=provider.sessions)
            if value and sessioned is None:
                sessioned = cls._build(
                    env=env,
                    provider=provider,
                    session=value,
                    source=variable,
                )
            elif marked is None and (
                marker := cls._marker(env=env, provider=provider)
            ):
                marked = cls._build(
                    env=env,
                    provider=provider,
                    session="",
                    source=marker,
                )
        return sessioned or marked

    @staticmethod
    def canonical(*, name: str) -> str:
        """Return the canonical provider identifier for a provider name.

        Args:
            name: Provider name or alias.

        Returns:
            The canonical lowercase identifier.

        """
        lowered: str = name.strip().lower()
        return Constants.aliases.get(lowered, lowered)

    def lake_variables(self) -> dict[str, str]:
        """Return the Lake variables a hook should export to child shells.

        Returns:
            Non-empty ``LAKE_AGENT*`` variables.

        """
        values: dict[str, str] = {
            Constants.agent_variable: self.agent,
            Constants.session_variable: self.session,
            Constants.surface_variable: self.surface,
        }
        return {name: value for name, value in values.items() if value}

    @classmethod
    def _explicit(
        cls,
        *,
        env: Mapping[str, str],
        agent: str,
    ) -> AgentIdentity:
        """Build an identity from an explicit ``LAKE_AGENT`` declaration.

        Args:
            env: Variables to inspect.
            agent: Canonical provider named by ``LAKE_AGENT``.

        Returns:
            The explicit identity, borrowing that provider's native session.

        """
        provider: _Provider | None = next(
            (item for item in Constants.providers if item.agent == agent),
            None,
        )
        session: str = env.get(Constants.session_variable, "").strip()
        source: str = Constants.session_variable if session else ""
        if not session and provider is not None:
            source, session = cls._first(env=env, names=provider.sessions)
        surface: str = cls._surface(env=env, provider=provider)
        return AgentIdentity(
            agent=agent,
            session=session,
            surface=surface,
            source=source or Constants.agent_variable,
        )

    @classmethod
    def _build(
        cls,
        *,
        env: Mapping[str, str],
        provider: _Provider,
        session: str,
        source: str,
    ) -> AgentIdentity:
        """Assemble an identity for an implicitly detected provider.

        Args:
            env: Variables to inspect.
            provider: Detected provider.
            session: Provider session, or empty.
            source: Variable that supplied the evidence.

        Returns:
            The assembled identity.

        """
        return AgentIdentity(
            agent=provider.agent,
            session=session,
            surface=cls._surface(env=env, provider=provider),
            source=source,
        )

    @staticmethod
    def _first(
        *,
        env: Mapping[str, str],
        names: tuple[str, ...],
    ) -> tuple[str, str]:
        """Return the first non-empty variable among ``names``.

        Args:
            env: Variables to inspect.
            names: Candidate variable names, strongest first.

        Returns:
            The variable name and value, or two empty strings.

        """
        for name in names:
            value: str = env.get(name, "").strip()
            if value:
                return name, value
        return "", ""

    @staticmethod
    def _marker(*, env: Mapping[str, str], provider: _Provider) -> str:
        """Return the presence marker that identifies a provider.

        Args:
            env: Variables to inspect.
            provider: Provider to test.

        Returns:
            The marker variable name, or empty when absent.

        """
        for name in provider.markers:
            if env.get(name, "").strip():
                return name
        ai_agent: str = env.get(Constants.ai_agent, "").strip().lower()
        if ai_agent.startswith(provider.agent):
            return Constants.ai_agent
        return ""

    @staticmethod
    def _surface(
        *,
        env: Mapping[str, str],
        provider: _Provider | None,
    ) -> str:
        """Return the explicit or provider-derived invocation surface.

        Args:
            env: Variables to inspect.
            provider: Provider whose entrypoint variable applies, if any.

        Returns:
            Normalized surface, or empty when unknown.

        """
        explicit: str = env.get(Constants.surface_variable, "").strip().lower()
        if explicit:
            return explicit
        if provider is None or not provider.entrypoint:
            return ""
        entrypoint: str = env.get(provider.entrypoint, "").strip().lower()
        if entrypoint.startswith("sdk"):
            return "sdk"
        return Constants.surfaces.get(entrypoint, "")
