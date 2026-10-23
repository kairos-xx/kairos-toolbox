# Copyright (c) 2026 Project contributors.
"""Tests for provider hooks, lifecycle, startup, hosts, and resources."""

from __future__ import annotations

from io import StringIO
from json import dumps, loads
from os import getpid
from pathlib import Path
from subprocess import CompletedProcess
from typing import TYPE_CHECKING, Final, TypedDict, final

from tools.lake_terminal.lake_terminal import claude as claude_module
from tools.lake_terminal.lake_terminal import codex as codex_module
from tools.lake_terminal.lake_terminal import cursor as cursor_module
from tools.lake_terminal.lake_terminal import devin as devin_module
from tools.lake_terminal.lake_terminal import hosts as hosts_module
from tools.lake_terminal.lake_terminal.claude import ClaudeHooks
from tools.lake_terminal.lake_terminal.codex import CodexHooks
from tools.lake_terminal.lake_terminal.cursor import CursorHooks
from tools.lake_terminal.lake_terminal.daemon_control import DaemonControl
from tools.lake_terminal.lake_terminal.devin import DevinHooks
from tools.lake_terminal.lake_terminal.hosts import (
    HostRepository,
    RemoteAggregator,
    RemoteHost,
)
from tools.lake_terminal.lake_terminal.lifecycle import LifecycleInstaller
from tools.lake_terminal.lake_terminal.models import SessionEntry
from tools.lake_terminal.lake_terminal.provider_state import ProviderState
from tools.lake_terminal.lake_terminal.resources import (
    NetworkAddress,
    NetworkConnection,
    ResourceCollector,
    ResourceSnapshot,
)
from tools.lake_terminal.lake_terminal.startup import (
    StartupState,
    WebStartup,
)
from tools.lake_terminal.lake_terminal.storage import LakeDatabase

from .helpers import check, expect

if TYPE_CHECKING:
    from types import ModuleType

    import pytest


class _CommandHook(TypedDict):
    """Represent one nested command hook.

    Attributes:
        type: Hook type identifier.
        command: Command executed by the hook.

    """

    type: str
    command: str


class _HookGroup(TypedDict, total=False):
    """Represent one hook group in a provider configuration.

    Attributes:
        matcher: Matcher selecting commands for the group.
        hooks: Command hooks belonging to the group.

    """

    matcher: str
    hooks: list[_CommandHook]


class _DevinConfig(TypedDict):
    """Represent the Devin configuration shape exercised by these tests.

    Attributes:
        hooks: Hook groups keyed by hook phase.
        other: Unrelated configuration value preserved by hook updates.

    """

    hooks: dict[str, list[_HookGroup]]
    other: str


@final
class Constants:
    """Group module-level constants.

    Attributes:
        sample_pid: Grouped module constant.

    """

    sample_pid: Final[int] = 4242


def _feed_stdin(
    *,
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
) -> None:
    """Route a JSON payload through a module's stdin binding.

    Args:
        module: Module.
        monkeypatch: Monkeypatch.
        payload: Payload.

    """
    monkeypatch.setattr(
        target=module,
        name="stdin",
        value=StringIO(
            initial_value=payload
            if isinstance(payload, str)
            else dumps(obj=payload),
        ),
    )


def _entry(
    *,
    agent: str = "claude-code",
    agent_session: str = "agent-1",
    backend: str = "",
    pid: int = 0,
    session: str = "shared-s1",
) -> SessionEntry:
    """Build a session row for provider-state tests.

    Args:
        agent: Agent identifier.
        agent_session: Agent session identifier.
        backend: Backend kind.
        pid: Process identifier.
        session: Backend session name.

    Returns:
        The resulting value.

    """
    return SessionEntry(
        id="s1",
        command="zsh",
        session=session,
        uuid="u1",
        host_id="h1",
        agent=agent,
        agent_session=agent_session,
        backend=backend,
        pid=pid,
    )


def test_claude_install_uninstall(tmp_path: Path) -> None:
    """Install and remove Claude hook entries idempotently.

    Args:
        tmp_path: Tmp path.

    """
    launcher: Path = tmp_path / "shared"
    path: Path = ClaudeHooks.install(home=tmp_path, launcher=launcher)
    expect(actual=path, expected=tmp_path / ".claude" / "settings.json")
    data: dict[str, dict[str, list[dict[str, str]]]] = loads(
        s=path.read_text(encoding="utf-8"),
    )
    hooks: dict[str, list[dict[str, str]]] = data["hooks"]
    check(condition="PreToolUse" in hooks, reason="tool hook installed")
    check(condition="SessionEnd" in hooks, reason="session end hook installed")
    matcher_entries: list[dict[str, str]] = hooks["PreToolUse"]
    expect(actual=matcher_entries[0]["matcher"], expected="Bash")
    _ = ClaudeHooks.install(home=tmp_path, launcher=launcher)
    again: dict[str, dict[str, list[dict[str, str]]]] = loads(
        s=path.read_text(encoding="utf-8"),
    )
    expect(actual=len(again["hooks"]["PreToolUse"]), expected=1)
    ClaudeHooks.uninstall(home=tmp_path)
    cleaned: dict[str, dict[str, list[dict[str, str]]]] = loads(
        s=path.read_text(encoding="utf-8"),
    )
    check(condition="hooks" not in cleaned, reason="owned hooks fully removed")
    ClaudeHooks.uninstall(home=tmp_path)


def test_claude_run_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Consume a Claude hook payload and persist the session id.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    _feed_stdin(
        module=claude_module,
        monkeypatch=monkeypatch,
        payload="{invalid",
    )
    expect(actual=ClaudeHooks.run_hook(home=tmp_path), expected=0)
    _feed_stdin(
        module=claude_module,
        monkeypatch=monkeypatch,
        payload={"hook_event_name": "Stop"},
    )
    expect(actual=ClaudeHooks.run_hook(home=tmp_path), expected=0)
    env_file: Path = tmp_path / "env.sh"
    _ = env_file.write_text(data="", encoding="utf-8")
    monkeypatch.setenv(name="CLAUDE_ENV_FILE", value=str(object=env_file))
    _feed_stdin(
        module=claude_module,
        monkeypatch=monkeypatch,
        payload={"hook_event_name": "SessionStart", "session_id": "claude-1"},
    )
    expect(actual=ClaudeHooks.run_hook(home=tmp_path), expected=0)
    session_path: Path = ClaudeHooks.session_path(home=tmp_path)
    stored: str = session_path.read_text(encoding="utf-8").strip()
    expect(actual=stored, expected="claude-1")
    env_content: str = env_file.read_text(encoding="utf-8")
    check(
        condition="CLAUDE_SESSION_ID" in env_content,
        reason="env file exports the session variable",
    )
    check(
        condition="LAKE_AGENT=claude-code" in env_content,
        reason="agent marker exported",
    )


def test_codex_install_uninstall(tmp_path: Path) -> None:
    """Install and remove Codex hook entries.

    Args:
        tmp_path: Tmp path.

    """
    launcher: Path = tmp_path / "shared"
    path: Path = CodexHooks.install(home=tmp_path, launcher=launcher)
    content: str = path.read_text(encoding="utf-8")
    check(condition="--codex-hook" in content, reason="hook installed")
    _ = CodexHooks.install(home=tmp_path, launcher=launcher)
    CodexHooks.uninstall(home=tmp_path)
    check(
        condition="--codex-hook" not in path.read_text(encoding="utf-8"),
        reason="hook removed",
    )
    CodexHooks.uninstall(home=tmp_path)
    check(
        condition=CodexHooks.session_path(home=tmp_path).name != "",
        reason="session path resolves",
    )


def test_codex_run_hook_pretool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rewrite Codex PreToolUse commands with attribution exports.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    _feed_stdin(module=codex_module, monkeypatch=monkeypatch, payload="{bad")
    expect(actual=CodexHooks.run_hook(home=tmp_path), expected=0)
    _feed_stdin(
        module=codex_module,
        monkeypatch=monkeypatch,
        payload={"session_id": "codex-1", "hook_event_name": "Stop"},
    )
    expect(actual=CodexHooks.run_hook(home=tmp_path), expected=0)
    output: StringIO = StringIO()
    monkeypatch.setattr(target=codex_module, name="stdout", value=output)
    _feed_stdin(
        module=codex_module,
        monkeypatch=monkeypatch,
        payload={
            "session_id": "codex-1",
            "hook_event_name": "PreToolUse",
            "tool_input": {"command": "make test", "description": "run"},
        },
    )
    expect(actual=CodexHooks.run_hook(home=tmp_path), expected=0)
    emitted: dict[str, dict[str, dict[str, str]]] = loads(s=output.getvalue())
    updated: dict[str, str] = emitted["hookSpecificOutput"]["updatedInput"]
    check(
        condition="CODEX_SESSION_ID=codex-1" in updated["command"],
        reason="command rewritten with session env",
    )
    check(
        condition="make test" in updated["command"],
        reason="original command preserved",
    )
    expect(actual=updated["description"], expected="run")


def test_cursor_install_uninstall(tmp_path: Path) -> None:
    """Install and remove Cursor hooks preserving foreign entries.

    Args:
        tmp_path: Tmp path.

    """
    config: Path = tmp_path / ".cursor" / "hooks.json"
    config.parent.mkdir(parents=True)
    _ = config.write_text(
        data=dumps(
            obj={
                "version": 1,
                "hooks": {
                    "sessionStart": [{"command": "foreign-tool"}],
                    "other": "not-a-list",
                },
            },
        ),
        encoding="utf-8",
    )
    launcher: Path = tmp_path / "shared"
    path: Path = CursorHooks.install(home=tmp_path, launcher=launcher)
    payload: dict[str, dict[str, list[dict[str, str]]]] = loads(
        s=path.read_text(encoding="utf-8"),
    )
    entries: list[dict[str, str]] = payload["hooks"]["sessionStart"]
    check(
        condition=any(
            entry.get("command") == "foreign-tool" for entry in entries
        ),
        reason="foreign hook preserved",
    )
    check(
        condition=any(
            "--cursor-hook" in entry.get("command", "") for entry in entries
        ),
        reason="shared hook installed",
    )
    expect(
        actual=len(payload["hooks"]),
        expected=len(cursor_module.Constants.events),
    )
    CursorHooks.uninstall(home=tmp_path)
    cleaned: dict[str, dict[str, list[dict[str, str]]]] = loads(
        s=path.read_text(encoding="utf-8"),
    )
    start_entries: list[dict[str, str]] = cleaned["hooks"].get(
        "sessionStart",
        [],
    )
    check(
        condition=all(
            "--cursor-hook" not in entry.get("command", "")
            for entry in start_entries
        ),
        reason="shared entries removed",
    )
    CursorHooks.uninstall(home=tmp_path)
    empty: Path = tmp_path / "empty-home"
    CursorHooks.uninstall(home=empty)


def test_cursor_run_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Process Cursor hook events into Lake session state.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    _feed_stdin(
        module=cursor_module,
        monkeypatch=monkeypatch,
        payload="{broken",
    )
    expect(actual=CursorHooks.run_hook(home=tmp_path), expected=0)
    _feed_stdin(module=cursor_module, monkeypatch=monkeypatch, payload=[1, 2])
    expect(actual=CursorHooks.run_hook(home=tmp_path), expected=0)
    _feed_stdin(
        module=cursor_module,
        monkeypatch=monkeypatch,
        payload={"hook_event_name": "stop"},
    )
    expect(actual=CursorHooks.run_hook(home=tmp_path), expected=0)
    output: StringIO = StringIO()
    monkeypatch.setattr(target=cursor_module, name="stdout", value=output)
    _feed_stdin(
        module=cursor_module,
        monkeypatch=monkeypatch,
        payload={
            "hook_event_name": "sessionStart",
            "session_id": "cursor-1",
            "workspace_roots": ["/repo"],
        },
    )
    expect(actual=CursorHooks.run_hook(home=tmp_path), expected=0)
    response: dict[str, dict[str, str]] = loads(
        s=output.getvalue().strip().splitlines()[-1],
    )
    expect(actual=response["env"]["LAKE_AGENT"], expected="cursor")
    expect(actual=response["env"]["LAKE_AGENT_SESSION"], expected="cursor-1")
    database: LakeDatabase = LakeDatabase(home=tmp_path)
    sessions: tuple[SessionEntry, ...] = database.sessions()
    expect(actual=len(sessions), expected=1)
    entry: SessionEntry = sessions[0]
    expect(actual=entry.agent, expected="cursor")
    expect(actual=entry.workspace, expected="/repo")
    check(condition=not entry.reconnectable, reason="hook sessions stay local")
    output.seek(0)
    output.truncate(0)
    _feed_stdin(
        module=cursor_module,
        monkeypatch=monkeypatch,
        payload={
            "hook_event_name": "beforeShellExecution",
            "session_id": "cursor-1",
            "command": "ls -la",
        },
    )
    expect(actual=CursorHooks.run_hook(home=tmp_path), expected=0)
    last: dict[str, str] = loads(s=output.getvalue().strip().splitlines()[-1])
    expect(actual=last["permission"], expected="allow")
    updated: SessionEntry = database.sessions()[0]
    expect(actual=updated.agent_state, expected="EXECUTING")
    expect(actual=updated.current_command, expected="ls -la")
    _feed_stdin(
        module=cursor_module,
        monkeypatch=monkeypatch,
        payload={
            "hook_event_name": "sessionEnd",
            "conversation_id": "cursor-1",
        },
    )
    expect(actual=CursorHooks.run_hook(home=tmp_path), expected=0)
    ended: SessionEntry = database.sessions()[0]
    expect(actual=ended.agent_state, expected="DEAD")


def test_devin_install_uninstall(tmp_path: Path) -> None:
    """Install and remove Devin hooks preserving foreign content.

    Args:
        tmp_path: Tmp path.

    """
    config: Path = tmp_path / ".config" / "devin" / "config.json"
    config.parent.mkdir(parents=True)
    _ = config.write_text(
        data=dumps(
            obj={
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"type": "command", "command": "mine"}]},
                    ],
                },
                "other": "keep",
            },
        ),
        encoding="utf-8",
    )
    launcher: Path = tmp_path / "shared"
    path: Path = DevinHooks.install(home=tmp_path, launcher=launcher)
    payload: _DevinConfig = loads(
        s=path.read_text(encoding="utf-8"),
    )
    starts: list[_HookGroup] = payload["hooks"]["SessionStart"]
    check(
        condition=any(
            "mine" in hook.get("command", "")
            for entry in starts
            for hook in entry.get("hooks", [])
        ),
        reason="foreign hook preserved",
    )
    check(
        condition="--devin-hook" in path.read_text(encoding="utf-8"),
        reason="devin hook installed",
    )
    expect(actual=payload["other"], expected="keep")
    DevinHooks.uninstall(home=tmp_path)
    cleaned: _DevinConfig = loads(
        s=path.read_text(encoding="utf-8"),
    )
    starts = cleaned["hooks"]["SessionStart"]
    check(
        condition=all(
            "--devin-hook" not in hook.get("command", "")
            for entry in starts
            for hook in entry.get("hooks", [])
        ),
        reason="owned entries removed",
    )
    DevinHooks.uninstall(home=tmp_path)
    empty: Path = tmp_path / "empty"
    DevinHooks.uninstall(home=empty)
    _ = (empty / ".config" / "devin").mkdir(parents=True)
    _ = config.parent.mkdir(parents=True, exist_ok=True)


def test_devin_run_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Record Devin session identity in the agent context.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    _feed_stdin(
        module=devin_module,
        monkeypatch=monkeypatch,
        payload="{broken",
    )
    expect(actual=DevinHooks.run_hook(home=tmp_path), expected=0)
    _feed_stdin(
        module=devin_module,
        monkeypatch=monkeypatch,
        payload={"hook_event_name": "SessionStart"},
    )
    expect(actual=DevinHooks.run_hook(home=tmp_path), expected=0)
    _feed_stdin(
        module=devin_module,
        monkeypatch=monkeypatch,
        payload={"hook_event_name": "SessionStart", "session_id": "devin-1"},
    )
    expect(actual=DevinHooks.run_hook(home=tmp_path), expected=0)
    database: LakeDatabase = LakeDatabase(home=tmp_path)
    context: tuple[str, str] | None = database.agent_context(
        workspace=str(object=Path.cwd()),
    )
    check(condition=context is not None, reason="agent context persisted")
    if context is not None:
        provider, session_id = context
        expect(actual=provider, expected="devin")
        expect(actual=session_id, expected="devin-1")


def test_provider_state_record(tmp_path: Path) -> None:
    """Update matching sessions and ignore the rest.

    Args:
        tmp_path: Tmp path.

    """
    ProviderState.record(
        home=tmp_path,
        agent="claude-code",
        agent_session="none",
        hook_event="UnknownEvent",
    )
    database: LakeDatabase = LakeDatabase(home=tmp_path)
    check(
        condition=database.sessions() == (),
        reason="unknown events make no changes",
    )
    entry: SessionEntry = _entry(agent="claude-code", agent_session="a1")
    database.replace_sessions(entries=(entry,))
    ProviderState.record(
        home=tmp_path,
        agent="claude-code",
        agent_session="other",
        hook_event="PreToolUse",
    )
    unchanged: SessionEntry = database.sessions()[0]
    expect(actual=unchanged.agent_state, expected="IDLE")
    ProviderState.record(
        home=tmp_path,
        agent="claude-code",
        agent_session="a1",
        hook_event="PreToolUse",
    )
    updated: SessionEntry = database.sessions()[0]
    expect(actual=updated.agent_state, expected="EXECUTING")
    check(
        condition=updated.last_activity_at != "",
        reason="activity timestamp refreshed",
    )
    ProviderState.record(
        home=tmp_path,
        agent="claude-code",
        agent_session="a1",
        hook_event="SessionEnd",
    )
    expect(actual=database.sessions()[0].agent_state, expected="DEAD")


def test_lifecycle_blocks(tmp_path: Path) -> None:
    """Install and remove POSIX and PowerShell lifecycle blocks.

    Args:
        tmp_path: Tmp path.

    """
    block: str = LifecycleInstaller.block(command="/opt/shared")
    check(
        condition=LifecycleInstaller.begin in block,
        reason="block carries begin marker",
    )
    check(
        condition="--lifecycle-start" in block,
        reason="block invokes lifecycle start",
    )
    check(condition="/opt/shared" in block, reason="command embedded")
    powershell: str = LifecycleInstaller.powershell_block(
        command="C:\\shared.cmd",
    )
    check(
        condition="--lifecycle-start" in powershell,
        reason="powershell block invokes lifecycle",
    )
    zshrc: Path = tmp_path / ".zshrc"
    _ = zshrc.write_text(data="export PATH=old\n", encoding="utf-8")
    LifecycleInstaller.install_file(path=zshrc, command="/opt/shared")
    first: str = zshrc.read_text(encoding="utf-8")
    check(condition="export PATH=old" in first, reason="content preserved")
    LifecycleInstaller.install_file(path=zshrc, command="/opt/shared")
    second: str = zshrc.read_text(encoding="utf-8")
    expect(actual=second.count(LifecycleInstaller.begin), expected=1)
    LifecycleInstaller.remove_file(path=zshrc)
    remaining: str = zshrc.read_text(encoding="utf-8")
    check(
        condition=LifecycleInstaller.begin not in remaining,
        reason="block removed",
    )
    check(condition="export PATH=old" in remaining, reason="rc preserved")
    LifecycleInstaller.remove_file(path=tmp_path / "missing")
    profile: Path = tmp_path / "profile.ps1"
    LifecycleInstaller.install_powershell(path=profile, command="shared")
    LifecycleInstaller.install_powershell(path=profile, command="shared")
    content: str = profile.read_text(encoding="utf-8")
    expect(actual=content.count(LifecycleInstaller.begin), expected=1)


def test_web_startup(tmp_path: Path) -> None:
    """Enable and disable per-user web autostart entries.

    Args:
        tmp_path: Tmp path.

    """
    startup: WebStartup = WebStartup(home=tmp_path)
    initial: StartupState = startup.state()
    check(condition=not initial.enabled, reason="disabled before install")
    state: StartupState = startup.enable(
        command=("/usr/bin/shared", "--web", "--no-browser"),
    )
    check(condition=state.enabled, reason="startup entry enabled")
    check(condition=state.mechanism != "", reason="mechanism reported")
    check(
        condition=Path(state.location).exists(),
        reason="startup file written",
    )
    again: StartupState = startup.state()
    check(condition=again.enabled, reason="state reports enabled")
    disabled: StartupState = startup.disable()
    check(condition=not disabled.enabled, reason="disable removes entry")
    again_disabled: StartupState = startup.disable()
    check(condition=not again_disabled.enabled, reason="disable is idempotent")


def test_host_repository(tmp_path: Path) -> None:
    """Persist, update, and remove remote host definitions.

    Args:
        tmp_path: Tmp path.

    """
    repository: HostRepository = HostRepository(home=tmp_path)
    expect(actual=repository.read(), expected=())
    repository.upsert(host=RemoteHost(name="alpha", target="a@host"))
    hosts: tuple[RemoteHost, ...] = repository.read()
    expect(actual=len(hosts), expected=1)
    expect(actual=hosts[0].name, expected="alpha")
    check(condition=hosts[0].id != "", reason="host id generated")
    repository.upsert(
        host=RemoteHost(name="alpha", target="b@host2", enabled=False),
    )
    updated: tuple[RemoteHost, ...] = repository.read()
    expect(actual=len(updated), expected=1)
    expect(actual=updated[0].target, expected="b@host2")
    check(condition=not updated[0].enabled, reason="enabled flag updated")
    repository.remove(name="alpha")
    expect(actual=repository.read(), expected=())
    repository.remove(name="alpha")


def test_remote_aggregator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aggregate statuses with error envelopes for failed hosts.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    repository: HostRepository = HostRepository(home=tmp_path)
    repository.upsert(host=RemoteHost(name="up", target="a@up"))
    repository.upsert(host=RemoteHost(name="down", target="a@down"))
    repository.upsert(
        host=RemoteHost(name="off", target="a@off", enabled=False),
    )

    def fake_run(
        *,
        args: tuple[str, ...],
        **_kwargs: object,
    ) -> CompletedProcess[str]:
        """Return scripted SSH results for remote hosts.

        Args:
            args: SSH command arguments.
            **_kwargs: Ignored subprocess keyword arguments.

        Returns:
            The scripted remote-host subprocess result.

        """
        if any("up" in part for part in args):
            return CompletedProcess(
                args=args,
                returncode=0,
                stdout='{"ok": true}',
            )
        return CompletedProcess(
            args=args,
            returncode=255,
            stdout="",
            stderr="unreachable",
        )

    monkeypatch.setattr(target=hosts_module, name="run", value=fake_run)
    aggregator: RemoteAggregator = RemoteAggregator(repository=repository)
    statuses: tuple[tuple[RemoteHost, str], ...] = aggregator.statuses()
    expect(actual=len(statuses), expected=2)
    by_name: dict[str, str] = {host.name: result for host, result in statuses}
    expect(actual=by_name["up"], expected='{"ok": true}')
    failure: dict[str, str] = loads(s=by_name["down"])
    check(condition="error" in failure, reason="failure wraps an envelope")


def test_resource_helpers() -> None:
    """Format connections and listening ports from sockets."""
    connections_sockets: list[NetworkConnection] = [
        NetworkConnection(
            fd=1,
            family=2,
            type=1,
            laddr=NetworkAddress(ip="127.0.0.1", port=8080),
            raddr=(),
            status="LISTEN",
        ),
        NetworkConnection(
            fd=2,
            family=2,
            type=1,
            laddr=NetworkAddress(ip="10.0.0.1", port=9),
            raddr=NetworkAddress(ip="10.0.0.2", port=80),
            status="ESTABLISHED",
        ),
        NetworkConnection(
            fd=3,
            family=2,
            type=1,
            laddr=(),
            raddr=(),
            status="CLOSED",
        ),
    ]
    connections: tuple[str, ...] = ResourceCollector.connections(
        sockets=connections_sockets,
    )
    check(
        condition=any("127.0.0.1:8080" in item for item in connections),
        reason="listen endpoint formatted",
    )
    check(
        condition=any("-> 10.0.0.2:80" in item for item in connections),
        reason="remote endpoint formatted",
    )
    ports: tuple[int, ...] = ResourceCollector.listening_ports(
        sockets=connections_sockets,
    )
    expect(actual=ports, expected=(8080,))
    empty: ResourceSnapshot = ResourceCollector.empty(partial=True)
    check(condition=empty.partial, reason="partial flag propagated")
    expect(actual=empty.process_count, expected=0)


def test_resource_collect_live() -> None:
    """Collect telemetry for the current process tree."""
    entry: SessionEntry = _entry(backend="process", pid=getpid())
    snapshot: ResourceSnapshot = ResourceCollector.collect(entry=entry)
    check(
        condition=snapshot.process_count > 0,
        reason="live process tree collected",
    )
    dead: SessionEntry = _entry(backend="process", pid=0)
    empty: ResourceSnapshot = ResourceCollector.collect(entry=dead)
    expect(actual=empty.process_count, expected=0)
    missing: SessionEntry = _entry(backend="process", pid=2000000001)
    partial: ResourceSnapshot = ResourceCollector.collect(entry=missing)
    check(condition=partial.partial, reason="missing root reports partial")
    tmux_entry: SessionEntry = _entry(
        backend="tmux",
        session="no-such-session-xyz",
        pid=Constants.sample_pid,
    )
    tmux_root: int = ResourceCollector.root_pid(entry=tmux_entry)
    expect(actual=tmux_root, expected=Constants.sample_pid)


def test_daemon_control_no_daemon(tmp_path: Path) -> None:
    """Report absent daemons without raising.

    Args:
        tmp_path: Tmp path.

    """
    check(
        condition=not DaemonControl.ping(home=tmp_path),
        reason="ping fails without daemon",
    )
    check(
        condition=not DaemonControl.stop(home=tmp_path),
        reason="stop fails without daemon",
    )
