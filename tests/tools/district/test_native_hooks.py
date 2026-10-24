# Copyright (c) 2026 Project contributors.
"""Tests for hook-backed sessions recorded by Claude Code and Devin hooks."""

from __future__ import annotations

from io import StringIO
from json import dumps, loads
from typing import TYPE_CHECKING

import pytest
from district import claude as claude_module
from district import devin as devin_module
from district.claude import ClaudeHooks
from district.core import (
    District,
    SessionRepository,
)
from district.devin import DevinHooks
from district.hook_session import (
    Constants as HookConstants,
)
from district.hook_session import HookSessions
from district.models import SessionEntry
from district.product import state_directory
from district.storage import DistrictDatabase

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
        home: District home directory.
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
        home: District home directory.

    Returns:
        Command history rows.

    """
    return [loads(s=row) for row in DistrictDatabase(home=home).command_json()]


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
    stored: tuple[str, ...] = DistrictDatabase(home=tmp_path).command_json()
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
    live: tuple[SessionEntry, ...] = District(
        repository=repository,
    ).reconcile()
    expect(actual=[entry.id for entry in live], expected=["hk1234"])


def test_session_start_exports_district_identity_for_child_shells(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Export DISTRICT_AGENT_SURFACE next to the session variables.

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
        "export DISTRICT_AGENT=claude-code",
        "export DISTRICT_AGENT_SESSION=cc-1",
        "export DISTRICT_AGENT_SURFACE=desktop",
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
        _ = ClaudeHooks.install(home=tmp_path, launcher=tmp_path / "district")
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
    _ = ClaudeHooks.install(home=tmp_path, launcher=tmp_path / "district")
    _ = ClaudeHooks.install(home=tmp_path, launcher=tmp_path / "district")
    backup: Path = settings.with_name(name="settings.json.district-bak")
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


def _finished(
    *,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    response: Payload,
) -> dict[str, object]:
    """Run one Bash command through the hooks and return its transcript row.

    Args:
        home: District home directory.
        monkeypatch: Monkeypatch.
        response: The tool response the provider reports.

    Returns:
        The stored command with its output.

    """
    _claude(
        home=home,
        monkeypatch=monkeypatch,
        payload=_bash(event="PreToolUse", tool_use_id="t1", command="make"),
    )
    _claude(
        home=home,
        monkeypatch=monkeypatch,
        payload={
            **_bash(event="PostToolUse", tool_use_id="t1"),
            "tool_response": response,
        },
    )
    session: str = SessionRepository(home=home).read()[0].id
    rows: tuple[str, ...] = DistrictDatabase(home=home).command_transcript(
        session_id=session,
        limit=5,
    )
    expect(actual=len(rows), expected=1)
    return loads(s=rows[0])


def _allow_output(*, home: Path, text: str) -> None:
    """Write a privacy policy file.

    Args:
        home: District home directory.
        text: JSON document.

    """
    (state_directory(home=home) / "privacy.json").write_text(
        data=text,
        encoding="utf-8",
    )


def test_command_output_is_not_stored_unless_the_policy_allows_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The privacy default holds: nothing the command printed is persisted.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    row: dict[str, object] = _finished(
        home=tmp_path,
        monkeypatch=monkeypatch,
        response={"stdout": "built ok\n", "stderr": ""},
    )
    expect(actual=row["output"], expected="")
    expect(actual=row["exit_code"], expected=0)


def test_command_output_is_stored_redacted_and_limited_when_allowed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With persistence on, output is kept with secrets removed and cut.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    limit: int = 4096
    _ = DistrictDatabase(home=tmp_path)
    _allow_output(
        home=tmp_path,
        text=dumps(
            obj={
                "persist_output": True,
                "output_limit_bytes": limit,
                "redact_patterns": ["internal-[0-9]+"],
            },
        ),
    )
    row: dict[str, object] = _finished(
        home=tmp_path,
        monkeypatch=monkeypatch,
        response={
            "stdout": "password=hunter2 ok internal-42\n"
            + "x" * 9000
            + "TAIL",
            "stderr": "warning: slow",
        },
    )
    output: str = str(object=row["output"])
    check(condition="hunter2" not in output, reason="credential redacted")
    check(condition="internal-42" not in output, reason="custom pattern")
    check(condition=output.endswith("warning: slow"), reason="stderr follows")
    check(condition="TAIL" in output, reason="the end of the output is kept")
    expect(actual=len(output) <= limit, expected=True)


def test_output_of_reads_both_streams_and_keeps_the_end() -> None:
    """Only text fields count, joined, and the tail survives the limit."""
    expect(actual=HookSessions.output_of(response=None), expected="")
    expect(actual=HookSessions.output_of(response={}), expected="")
    expect(
        actual=HookSessions.output_of(
            response={"stdout": "a", "stderr": "b", "interrupted": True},
        ),
        expected="a\nb",
    )
    expect(
        actual=HookSessions.output_of(response={"stdout": 7, "stderr": ""}),
        expected="",
    )
    long: str = "y" * (HookConstants.output_limit + 10) + "Z"
    kept: str = HookSessions.output_of(response={"stdout": long})
    expect(actual=len(kept), expected=HookConstants.output_limit)
    check(condition=kept.endswith("Z"), reason="tail kept")
