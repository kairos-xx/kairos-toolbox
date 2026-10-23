# Copyright (c) 2026 Project contributors.
"""Tests for provider-neutral agent identity resolution."""

from __future__ import annotations

import pytest

from tools.lake_terminal.lake_terminal.agent_identity import AgentIdentity

from .helpers import check, expect


@pytest.mark.parametrize(
    ("variable", "agent"),
    [
        ("CLAUDE_CODE_SESSION_ID", "claude-code"),
        ("CLAUDE_SESSION_ID", "claude-code"),
        ("CODEX_SESSION_ID", "codex"),
        ("CODEX_THREAD_ID", "codex"),
        ("CURSOR_SESSION_ID", "cursor"),
        ("DEVIN_SESSION_ID", "devin"),
    ],
)
def test_native_session_variable_becomes_lake_session(
    variable: str,
    agent: str,
) -> None:
    """Map every provider-native session variable to Lake's identity.

    Args:
        variable: Provider-native session variable.
        agent: Expected canonical provider.

    """
    identity: AgentIdentity | None = AgentIdentity.from_environment(
        environment={variable: "native-123"},
    )
    check(condition=identity is not None, reason="identity resolved")
    if identity is None:
        return
    expect(actual=identity.agent, expected=agent)
    expect(actual=identity.session, expected="native-123")
    expect(actual=identity.source, expected=variable)


def test_claude_code_session_id_outranks_hook_variable() -> None:
    """Prefer Claude Code's native id over the hook-exported one."""
    identity: AgentIdentity | None = AgentIdentity.from_environment(
        environment={
            "CLAUDE_SESSION_ID": "from-hook",
            "CLAUDE_CODE_SESSION_ID": "native",
        },
    )
    expect(actual=identity and identity.session, expected="native")


def test_explicit_lake_identity_wins_over_native_variables() -> None:
    """Honor LAKE_AGENT and LAKE_AGENT_SESSION before provider variables."""
    identity: AgentIdentity | None = AgentIdentity.from_environment(
        environment={
            "LAKE_AGENT": "codex",
            "LAKE_AGENT_SESSION": "explicit",
            "CLAUDE_CODE_SESSION_ID": "native",
        },
    )
    expect(actual=identity and identity.agent, expected="codex")
    expect(actual=identity and identity.session, expected="explicit")
    expect(actual=identity and identity.source, expected="LAKE_AGENT_SESSION")


def test_explicit_agent_borrows_that_providers_native_session() -> None:
    """Fill a missing LAKE_AGENT_SESSION from the named provider only."""
    identity: AgentIdentity | None = AgentIdentity.from_environment(
        environment={
            "LAKE_AGENT": "devin",
            "CLAUDE_CODE_SESSION_ID": "other",
            "DEVIN_SESSION_ID": "mine",
        },
    )
    expect(actual=identity and identity.agent, expected="devin")
    expect(actual=identity and identity.session, expected="mine")


def test_claude_alias_is_canonicalized() -> None:
    """Accept the short provider name used by the provider registry."""
    expect(
        actual=AgentIdentity.canonical(name="Claude"),
        expected="claude-code",
    )
    identity: AgentIdentity | None = AgentIdentity.from_environment(
        environment={"LAKE_AGENT": "claude", "LAKE_AGENT_SESSION": "s"},
    )
    expect(actual=identity and identity.agent, expected="claude-code")


def test_marker_without_session_identifies_provider_only() -> None:
    """Report the provider when only a presence marker is exported."""
    identity: AgentIdentity | None = AgentIdentity.from_environment(
        environment={"CLAUDECODE": "1"},
    )
    expect(actual=identity and identity.agent, expected="claude-code")
    expect(actual=identity and identity.session, expected="")


def test_ai_agent_standard_variable_is_a_marker() -> None:
    """Recognize the cross-vendor AI_AGENT convention."""
    identity: AgentIdentity | None = AgentIdentity.from_environment(
        environment={"AI_AGENT": "claude-code_2-1-302_agent"},
    )
    expect(actual=identity and identity.agent, expected="claude-code")


def test_session_bearing_provider_beats_marker_only_provider() -> None:
    """Prefer concrete session evidence over a bare marker."""
    identity: AgentIdentity | None = AgentIdentity.from_environment(
        environment={"CLAUDECODE": "1", "DEVIN_SESSION_ID": "d-1"},
    )
    expect(actual=identity and identity.agent, expected="devin")


def test_no_agent_evidence_resolves_to_none() -> None:
    """Return nothing when no provider variables exist."""
    check(
        condition=AgentIdentity.from_environment(environment={"HOME": "/h"})
        is None,
        reason="no identity",
    )
    check(
        condition=AgentIdentity.from_environment(
            environment={"LAKE_AGENT_SESSION": "orphan"},
        )
        is None,
        reason="session without provider is not an identity",
    )


@pytest.mark.parametrize(
    ("entrypoint", "surface"),
    [
        ("cli", "cli"),
        ("claude-vscode", "vscode"),
        ("claude-desktop", "desktop"),
        ("sdk-ts", "sdk"),
        ("sdk-py", "sdk"),
    ],
)
def test_claude_entrypoint_selects_surface(
    entrypoint: str,
    surface: str,
) -> None:
    """Derive the invocation surface from CLAUDE_CODE_ENTRYPOINT.

    Args:
        entrypoint: Claude Code entrypoint value.
        surface: Expected normalized surface.

    """
    identity: AgentIdentity | None = AgentIdentity.from_environment(
        environment={
            "CLAUDECODE": "1",
            "CLAUDE_CODE_ENTRYPOINT": entrypoint,
        },
    )
    expect(actual=identity and identity.surface, expected=surface)


def test_explicit_surface_overrides_entrypoint() -> None:
    """Let LAKE_AGENT_SURFACE override provider-derived surfaces."""
    identity: AgentIdentity | None = AgentIdentity.from_environment(
        environment={
            "CLAUDECODE": "1",
            "CLAUDE_CODE_ENTRYPOINT": "cli",
            "LAKE_AGENT_SURFACE": "Remote",
        },
    )
    expect(actual=identity and identity.surface, expected="remote")


def test_exports_lake_variables_for_child_shells() -> None:
    """Render the identity as the variables a hook should export."""
    identity: AgentIdentity | None = AgentIdentity.from_environment(
        environment={
            "CLAUDE_CODE_SESSION_ID": "abc",
            "CLAUDE_CODE_ENTRYPOINT": "claude-desktop",
        },
    )
    check(condition=identity is not None, reason="identity resolved")
    if identity is None:
        return
    expect(
        actual=identity.lake_variables(),
        expected={
            "LAKE_AGENT": "claude-code",
            "LAKE_AGENT_SESSION": "abc",
            "LAKE_AGENT_SURFACE": "desktop",
        },
    )
