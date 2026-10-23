# Copyright (c) 2026 Project contributors.
"""Tests for hook-backed sessions recorded by Claude Code and Devin hooks."""

from __future__ import annotations

from io import StringIO
from json import dumps, loads
from typing import TYPE_CHECKING

import pytest

from tools.lake_terminal.lake_terminal import claude as claude_module
from tools.lake_terminal.lake_terminal import devin as devin_module
from tools.lake_terminal.lake_terminal.claude import ClaudeHooks
from tools.lake_terminal.lake_terminal.core import (
    LakeTerminal,
    SessionRepository,
)
from tools.lake_terminal.lake_terminal.devin import DevinHooks
from tools.lake_terminal.lake_terminal.models import SessionEntry
from tools.lake_terminal.lake_terminal.storage import LakeDatabase

from .helpers import check, expect

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path
    from types import ModuleType
type Payload = Mapping[str, object]


def _claude(
    *,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: Payload,
) -> None:
    """Deliver one payload to the Claude hook entry point.

    Args:
        home: Lake home directory.
        monkeypatch: Monkeypatch.
        payload: Hook payload.

    """
    _deliver(module=claude_module, monkeypatch=monkeypatch, payload=payload)
    expect(actual=ClaudeHooks.run_hook(home=home), expected=0)


def _deliver(
    *,
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    payload: Payload,
) -> None:
    """Route a JSON payload through a module's stdin binding.

    Args:
        module: Hook module.
        monkeypatch: Monkeypatch.
        payload: Hook payload.

    """
    monkeypatch.setattr(
        target=module,
        name="stdin",
        value=StringIO(initial_value=dumps(obj=payload)),
    )


def _commands(*, home: Path) -> list[dict[str, object]]:
    """Return recorded commands as dictionaries.

    Args:
        home: Lake home directory.

    Returns:
        Command history rows.

    """
    return [loads(s=row) for row in LakeDatabase(home=home).command_json()]


def _bash(
    *,
    event: str,
    tool_use_id: str,
    command: str = "",
    session: str = "cc-1",
) -> Payload:
    """Build a Claude Bash tool payload.

    Args:
        event: Hook event name.
        tool_use_id: Tool-use identifier.
        command: Shell command.
        session: Claude session identifier.

    Returns:
        Hook payload.

    """
    return {
        "hook_event_name": event,
        "session_id": session,
        "cwd": "/work/project",
        "tool_name": "Bash",
        "tool_use_id": tool_use_id,
        "tool_input": {"command": command},
    }


def test_claude_bash_commands_are_recorded_in_a_hook_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Make Claude's Bash tool visible without a tmux session.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    monkeypatch.setenv(name="CLAUDECODE", value="1")
    monkeypatch.setenv(name="CLAUDE_CODE_ENTRYPOINT", value="claude-desktop")
    for payload in (
        {"hook_event_name": "SessionStart", "session_id": "cc-1"},
        _bash(event="PreToolUse", tool_use_id="t1", command="pytest -q"),
        _bash(event="PostToolUse", tool_use_id="t1"),
    ):
        _claude(home=tmp_path, monkeypatch=monkeypatch, payload=payload)
    sessions: tuple[SessionEntry, ...] = SessionRepository(
        home=tmp_path,
    ).read()
    expect(actual=len(sessions), expected=1)
    entry: SessionEntry = sessions[0]
    expect(actual=entry.agent, expected="claude-code")
    expect(actual=entry.agent_session, expected="cc-1")
    expect(actual=entry.backend, expected="hook")
    expect(actual=entry.surface, expected="desktop")
    check(condition=not entry.reconnectable, reason="hook not reconnectable")
    expect(actual=entry.agent_state, expected="THINKING")
    rows: list[dict[str, object]] = _commands(home=tmp_path)
    expect(actual=len(rows), expected=1)
    expect(actual=rows[0]["command"], expected="pytest -q")
    expect(actual=rows[0]["cwd"], expected="/work/project")
    expect(actual=rows[0]["exit_code"], expected=0)
    expect(actual=rows[0]["agent_session"], expected="cc-1")


def test_claude_failed_tool_records_nonzero_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Record PostToolUseFailure as a failed command.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    for payload in (
        _bash(event="PreToolUse", tool_use_id="t1", command="false"),
        _bash(event="PostToolUseFailure", tool_use_id="t1"),
    ):
        _claude(home=tmp_path, monkeypatch=monkeypatch, payload=payload)
    expect(actual=_commands(home=tmp_path)[0]["exit_code"], expected=1)


def test_claude_parallel_tool_calls_do_not_collide(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Correlate overlapping Bash calls by tool_use_id.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    for payload in (
        _bash(event="PreToolUse", tool_use_id="a", command="sleep 2"),
        _bash(event="PreToolUse", tool_use_id="b", command="echo hi"),
        _bash(event="PostToolUse", tool_use_id="b"),
        _bash(event="PostToolUseFailure", tool_use_id="a"),
    ):
        _claude(home=tmp_path, monkeypatch=monkeypatch, payload=payload)
    rows: dict[object, dict[str, object]] = {
        row["command"]: row for row in _commands(home=tmp_path)
    }
    expect(actual=rows["echo hi"]["exit_code"], expected=0)
    expect(actual=rows["sleep 2"]["exit_code"], expected=1)
    expect(
        actual=len(SessionRepository(home=tmp_path).read()),
        expected=1,
    )


def test_claude_commands_are_redacted_before_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never persist credential assignments from tool commands.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    _claude(
        home=tmp_path,
        monkeypatch=monkeypatch,
        payload=_bash(
            event="PreToolUse",
            tool_use_id="t1",
            command="deploy --token=hunter2secret",
        ),
    )
    stored: tuple[str, ...] = LakeDatabase(home=tmp_path).command_json()
    expect(actual=len(stored), expected=1)
    check(
        condition="hunter2secret" not in stored[0],
        reason="secret redacted",
    )


def test_claude_session_end_marks_dead_without_creating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End an existing session but never create one from SessionEnd.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    end: Payload = {"hook_event_name": "SessionEnd", "session_id": "cc-1"}
    _claude(home=tmp_path, monkeypatch=monkeypatch, payload=end)
    expect(actual=SessionRepository(home=tmp_path).read(), expected=())
    start: Payload = {"hook_event_name": "SessionStart", "session_id": "cc-1"}
    _claude(home=tmp_path, monkeypatch=monkeypatch, payload=start)
    _claude(home=tmp_path, monkeypatch=monkeypatch, payload=end)
    expect(
        actual=SessionRepository(home=tmp_path).read()[0].agent_state,
        expected="DEAD",
    )


def test_hook_sessions_survive_reconcile(tmp_path: Path) -> None:
    """Keep live hook sessions when the control plane reconciles state.

    Args:
        tmp_path: Tmp path.

    """
    repository: SessionRepository = SessionRepository(home=tmp_path)
    repository.add(
        entry=SessionEntry(
            id="hk1234",
            command="",
            session="",
            agent="claude-code",
            agent_session="cc-1",
            backend="hook",
            reconnectable=False,
        ),
    )
    repository.add(
        entry=SessionEntry(
            id="hk5678",
            command="",
            session="",
            agent="claude-code",
            agent_session="cc-2",
            backend="hook",
            reconnectable=False,
            agent_state="DEAD",
        ),
    )
    live: tuple[SessionEntry, ...] = LakeTerminal(
        repository=repository,
    ).reconcile()
    expect(actual=[entry.id for entry in live], expected=["hk1234"])


def test_session_start_exports_lake_identity_for_child_shells(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Export LAKE_AGENT_SURFACE next to the session variables.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    env_file: Path = tmp_path / "env.sh"
    monkeypatch.setenv(name="CLAUDE_ENV_FILE", value=str(object=env_file))
    monkeypatch.setenv(name="CLAUDE_CODE_ENTRYPOINT", value="claude-desktop")
    _claude(
        home=tmp_path,
        monkeypatch=monkeypatch,
        payload={"hook_event_name": "SessionStart", "session_id": "cc-1"},
    )
    exported: str = env_file.read_text(encoding="utf-8")
    for line in (
        "export LAKE_AGENT=claude-code",
        "export LAKE_AGENT_SESSION=cc-1",
        "export LAKE_AGENT_SURFACE=desktop",
        "export CLAUDE_SESSION_ID=cc-1",
    ):
        check(condition=line in exported, reason=line)


def test_claude_install_preserves_unreadable_settings(
    tmp_path: Path,
) -> None:
    """Refuse to overwrite settings.json that is not valid JSON.

    Args:
        tmp_path: Tmp path.

    """
    settings: Path = ClaudeHooks.config_path(home=tmp_path)
    settings.parent.mkdir(parents=True)
    _ = settings.write_text(data="{not json", encoding="utf-8")
    with pytest.raises(expected_exception=ValueError, match="valid JSON"):
        _ = ClaudeHooks.install(home=tmp_path, launcher=tmp_path / "lake")
    expect(actual=settings.read_text(encoding="utf-8"), expected="{not json")


def test_claude_install_backs_up_existing_settings_once(
    tmp_path: Path,
) -> None:
    """Keep a one-time backup of the user's original settings.

    Args:
        tmp_path: Tmp path.

    """
    settings: Path = ClaudeHooks.config_path(home=tmp_path)
    settings.parent.mkdir(parents=True)
    original: str = dumps(obj={"model": "opus"})
    _ = settings.write_text(data=original, encoding="utf-8")
    _ = ClaudeHooks.install(home=tmp_path, launcher=tmp_path / "lake")
    _ = ClaudeHooks.install(home=tmp_path, launcher=tmp_path / "lake")
    backup: Path = settings.with_name(name="settings.json.lake-bak")
    expect(actual=backup.read_text(encoding="utf-8"), expected=original)
    expect(
        actual=loads(s=settings.read_text(encoding="utf-8"))["model"],
        expected="opus",
    )


def test_devin_hook_drives_state_and_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Give Devin the same state machine and command history as Claude.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    monkeypatch.setenv(name="VSCODE_PID", value="1")
    for payload in (
        {"hook_event_name": "SessionStart", "session_id": "dv-1"},
        {
            "hook_event_name": "PreToolUse",
            "session_id": "dv-1",
            "tool_use_id": "x1",
            "tool_input": {"command": "make test"},
        },
    ):
        _deliver(module=devin_module, monkeypatch=monkeypatch, payload=payload)
        expect(actual=DevinHooks.run_hook(home=tmp_path), expected=0)
    entry: SessionEntry = SessionRepository(home=tmp_path).read()[0]
    expect(actual=entry.agent, expected="devin")
    expect(actual=entry.agent_session, expected="dv-1")
    expect(actual=entry.agent_state, expected="EXECUTING")
    expect(actual=entry.editor, expected="vscode")
    expect(actual=_commands(home=tmp_path)[0]["command"], expected="make test")
