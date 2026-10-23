# Copyright (c) 2026 Project contributors.
"""Tests for editor, surface and agent detection from native variables."""

from __future__ import annotations

from dataclasses import replace
from json import dumps
from subprocess import CompletedProcess
from typing import TYPE_CHECKING

import pytest

from tools.lake_terminal.lake_terminal import agent as agent_module
from tools.lake_terminal.lake_terminal.agent import (
    AgentConfiguration,
    AgentDetector,
)
from tools.lake_terminal.lake_terminal.platform import RuntimePlatform

from .helpers import check, expect

if TYPE_CHECKING:
    from pathlib import Path

    from tools.lake_terminal.lake_terminal.agent import AgentType


@pytest.mark.parametrize(
    ("hook", "editor"),
    [
        ("/Users/u/Library/Application Support/Devin/1.12-main.sock", "devin"),
        (
            "/Users/u/Library/Application Support/Cursor/1.0-main.sock",
            "cursor",
        ),
        ("/Users/u/Library/Application Support/Windsurf/1.0.sock", "windsurf"),
        ("/Users/u/Library/Application Support/VSCodium/1.0.sock", "vscodium"),
        ("/Users/u/Library/Application Support/Code/1.0-main.sock", "vscode"),
        ("/run/user/1000/vscode-abc-1.0-ipc.sock", "vscode"),
    ],
)
def test_editor_fork_is_identified_from_ipc_hook(
    monkeypatch: pytest.MonkeyPatch,
    hook: str,
    editor: str,
) -> None:
    """Tell VS Code-compatible forks apart.

    Args:
        monkeypatch: Monkeypatch.
        hook: VSCODE_IPC_HOOK value.
        editor: Expected editor identity.

    """
    monkeypatch.setenv(name="VSCODE_PID", value="1")
    monkeypatch.setenv(name="VSCODE_IPC_HOOK", value=hook)
    expect(actual=RuntimePlatform.editor(), expected=editor)


def test_editor_defaults_to_vscode_and_honors_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the generic default and the explicit LAKE_EDITOR override.

    Args:
        monkeypatch: Monkeypatch.

    """
    monkeypatch.setenv(name="VSCODE_PID", value="1")
    expect(actual=RuntimePlatform.editor(), expected="vscode")
    monkeypatch.setenv(name="LAKE_EDITOR", value="zed")
    expect(actual=RuntimePlatform.editor(), expected="zed")


def test_surface_follows_claude_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report desktop for Claude Code Desktop even without an editor.

    Args:
        monkeypatch: Monkeypatch.

    """
    expect(actual=RuntimePlatform.surface(), expected="cli")
    monkeypatch.setenv(name="CLAUDECODE", value="1")
    monkeypatch.setenv(name="CLAUDE_CODE_ENTRYPOINT", value="claude-desktop")
    expect(actual=RuntimePlatform.surface(), expected="desktop")
    monkeypatch.setenv(name="LAKE_AGENT_SURFACE", value="remote")
    expect(actual=RuntimePlatform.surface(), expected="remote")


@pytest.mark.parametrize(
    ("variable", "agent"),
    [
        ("CLAUDE_CODE_SESSION_ID", "claude-code"),
        ("CLAUDE_CODE_ENTRYPOINT", "claude-code"),
        ("DEVIN_SESSION_ID", "devin"),
        ("CURSOR_SESSION_ID", "cursor"),
        ("CODEX_THREAD_ID", "codex"),
    ],
)
def test_detector_uses_provider_native_variables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    agent: str,
) -> None:
    """Detect agents from provider-native environment variables.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.
        variable: Provider-native variable.
        agent: Expected provider.

    """
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    _ = AgentConfiguration.load(home=tmp_path)
    monkeypatch.setattr(
        target=AgentDetector,
        name="ancestor_commands",
        value=lambda: (),
    )
    monkeypatch.setenv(name=variable, value="v1")
    expect(actual=AgentDetector.detect(), expected=(agent, "environment"))


def test_detector_respects_disabled_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not detect a provider the user disabled in agents.json.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    agents: tuple[AgentType, ...] = tuple(
        replace(item, enabled=False)
        if item.identifier == "claude-code"
        else item
        for item in AgentConfiguration.default_agents()
    )
    AgentConfiguration.write(
        path=tmp_path / ".lake_terminal" / "agents.json",
        agents=agents,
    )
    monkeypatch.setattr(
        target=AgentDetector,
        name="ancestor_commands",
        value=lambda: (),
    )
    monkeypatch.setenv(name="CLAUDE_CODE_SESSION_ID", value="x")
    check(condition=AgentDetector.detect() is None, reason="disabled")


def test_desktop_chat_app_is_not_an_agent_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Separate Claude Desktop chat from its embedded Claude Code binary.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    chat: tuple[str, ...] = (
        "/Applications/Claude.app/Contents/MacOS/Claude",
        "/Applications/Claude.app/Contents/Frameworks/Claude Helper.app/"
        "Contents/MacOS/Claude Helper --type=utility",
    )
    monkeypatch.setattr(
        target=AgentDetector,
        name="ancestor_commands",
        value=lambda: chat,
    )
    check(condition=AgentDetector.detect() is None, reason="chat app")
    embedded: tuple[str, ...] = (
        "/Users/u/Library/Application Support/Claude/claude-code/2.1.308/"
        "a93d/claude.app/Contents/MacOS/claude --output-format stream-json",
    )
    monkeypatch.setattr(
        target=AgentDetector,
        name="ancestor_commands",
        value=lambda: embedded,
    )
    expect(
        actual=AgentDetector.detect(),
        expected=("claude-code", "process_tree"),
    )


def test_windows_ancestry_is_walked_from_cim_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve parent command lines on Windows through PowerShell JSON.

    Args:
        monkeypatch: Monkeypatch.

    """
    table: str = dumps(
        obj=[
            {"ProcessId": 30, "ParentProcessId": 20, "CommandLine": "pwsh"},
            {
                "ProcessId": 20,
                "ParentProcessId": 10,
                "CommandLine": "claude.exe -p",
            },
            {
                "ProcessId": 10,
                "ParentProcessId": 0,
                "CommandLine": "explorer.exe",
            },
        ],
    )

    def _fake_run(**_: object) -> CompletedProcess[str]:
        """Return the canned process table.

        Args:
            **_: Ignored run arguments.

        Returns:
            Completed PowerShell process.

        """
        return CompletedProcess(args=(), returncode=0, stdout=table, stderr="")

    monkeypatch.setattr(target=agent_module, name="os_name", value="nt")
    monkeypatch.setattr(target=agent_module, name="getppid", value=lambda: 30)
    monkeypatch.setattr(target=agent_module, name="run", value=_fake_run)
    expect(
        actual=AgentDetector.ancestor_commands(),
        expected=("pwsh", "claude.exe -p", "explorer.exe"),
    )
