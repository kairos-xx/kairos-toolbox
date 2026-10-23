# Copyright (c) 2026 Project contributors.
"""Exercise the public CLI surface for coverage evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tools.lake_terminal.lake_terminal.cli import Cli
from tools.lake_terminal.lake_terminal.core import (
    LakeTerminal,
    SessionRepository,
)
from tools.lake_terminal.lake_terminal.models import SessionEntry

from .helpers import check, expect

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_cli_public_validation(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise CLI validation through the public dispatcher.

    Args:
        tmp_path: Temporary home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    service: LakeTerminal = LakeTerminal(
        repository=SessionRepository(home=tmp_path),
    )
    cli: Cli = Cli(service=service)
    expect(
        actual=cli.run(arguments=("--unknown-coverage-option",)),
        expected=2,
    )
    expect(
        actual=cli.run(arguments=("--web", "--port", "invalid")),
        expected=2,
    )
    expect(
        actual=cli.run(arguments=("--web", "--port", "65536")),
        expected=2,
    )


def test_cli_public_observation_commands(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise non-interactive public observation and validation commands.

    Args:
        tmp_path: Temporary home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    service: LakeTerminal = LakeTerminal(
        repository=SessionRepository(home=tmp_path),
    )
    cli: Cli = Cli(service=service)
    invocations: tuple[tuple[tuple[str, ...], tuple[int, ...]], ...] = (
        (("--help",), (0,)),
        (("--is-agent-shell",), (0, 1)),
        (("--cleanup",), (2,)),
        (("--web", "info"), (0, 1)),
        (("--web", "--startup", "state"), (0,)),
        (("--watch", "--once"), (0,)),
        (("--view",), (2,)),
        (("--doctor",), (0,)),
        (("--doctor", "--full"), (0,)),
        (("--history",), (0,)),
        (("--events",), (0,)),
        (("--purge",), (0,)),
        (("--hosts",), (0,)),
        (("--host-add",), (2,)),
        (("--host-remove",), (2,)),
        (("--lifecycle-start",), (2,)),
        (("--lifecycle-finish",), (2,)),
        (("--agent-state",), (2,)),
        (("--list",), (0,)),
        (("--list", "devin"), (0,)),
        (("--agent",), (0, 1, 127)),
        (("--ensure",), (0, 1, 127)),
        (("--status",), (0,)),
        (("--id",), (1,)),
        (("--exec",), (2, 127)),
    )
    for arguments, expected_values in invocations:
        result: int = cli.run(arguments=arguments)
        check(
            condition=result in expected_values,
            reason=f"unexpected result for {arguments}: {result}",
        )


def test_cli_send_dispatch(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise --send and --send-key dispatch against a fake service.

    Args:
        tmp_path: Temporary home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    service: LakeTerminal = LakeTerminal(
        repository=SessionRepository(home=tmp_path),
    )
    entry: SessionEntry = SessionEntry(
        id="abc123",
        command="shell",
        session="shared-abc123",
    )
    sent: list[tuple[str, str, tuple[str, ...]]] = []

    def _entries() -> tuple[SessionEntry, ...]:
        """Return the single fake live entry.

        Returns:
            Fake live session entries.

        """
        return (entry,)

    def _send(
        *,
        session_id: str,
        text: str,
        keys: tuple[str, ...],
    ) -> None:
        """Record send calls or raise deterministic failures.

        Args:
            session_id: Session identifier.
            text: Literal text.
            keys: Key names.

        Raises:
            KeyError: When the session identifier is dead00.
            ValueError: When a key name is rm.

        """
        if session_id == "dead00":
            raise KeyError(session_id)
        if "rm" in keys:
            msg: str = "Unsupported key name."
            raise ValueError(msg)
        sent.append((session_id, text, keys))

    monkeypatch.setattr(
        target=service,
        name="list_entries",
        value=_entries,
    )
    monkeypatch.setattr(target=service, name="send", value=_send)
    cli: Cli = Cli(service=service)
    expect(
        actual=cli.run(arguments=("abc123", "--send", "hello", "world")),
        expected=0,
    )
    expect(
        actual=cli.run(arguments=("abc123", "--send-key", "C-c")),
        expected=0,
    )
    expect(actual=cli.run(arguments=("--send", "y")), expected=0)
    expect(actual=cli.run(arguments=("--send-key", "Up")), expected=0)
    expect(
        actual=sent,
        expected=[
            ("abc123", "hello world", ("Enter",)),
            ("abc123", "", ("C-c",)),
            ("abc123", "y", ("Enter",)),
            ("abc123", "", ("Up",)),
        ],
    )
    expect(actual=cli.run(arguments=("--send",)), expected=2)
    expect(
        actual=cli.run(arguments=("abc123", "--send-key", "a", "b")),
        expected=2,
    )
    expect(actual=cli.run(arguments=("dead00", "--send", "y")), expected=2)
    expect(
        actual=cli.run(arguments=("abc123", "--send-key", "rm")),
        expected=2,
    )


def test_cli_integration_and_explain_commands(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise integration qualification and explain dispatch paths.

    Args:
        tmp_path: Temporary home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    service: LakeTerminal = LakeTerminal(
        repository=SessionRepository(home=tmp_path),
    )
    cli: Cli = Cli(service=service)
    invocations: tuple[tuple[tuple[str, ...], tuple[int, ...]], ...] = (
        (("test", "integration"), (2,)),
        (("--test-integration",), (2,)),
        (("test", "integration", "bogus"), (2,)),
        (("--test-integration", "real", "bogus"), (2,)),
        (("--test-integration", "real", "codex", "extra"), (2,)),
        (("test", "integration", "synthetic", "bogus"), (2,)),
        (("explain",), (2,)),
        (("--explain", "missing"), (2,)),
        (("test", "integration", "synthetic", "codex"), (0,)),
        (("--test-integration", "concurrent"), (0,)),
    )
    for arguments, expected_values in invocations:
        result: int = cli.run(arguments=arguments)
        check(
            condition=result in expected_values,
            reason=f"unexpected result for {arguments}: {result}",
        )
