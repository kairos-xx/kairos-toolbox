# Copyright (c) 2026 Project contributors.
"""Tests for identity, buffers, privacy, process, and detection helpers."""

from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from json import dumps, loads
from os import getpid
from sqlite3 import connect
from subprocess import CompletedProcess
from typing import TYPE_CHECKING

import pytest
from district import notifications
from district.agent import (
    AgentConfiguration,
    AgentDetector,
    AgentType,
)
from district.buffer import OutputRingBuffer
from district.events import (
    DistrictEvent,
    EventDetails,
    EventTypes,
)
from district.identity import (
    IdentityStore,
    ProcessIdentity,
)
from district.models import SessionEntry
from district.monitoring import (
    Monitor,
    SessionSnapshot,
)
from district.notifications import (
    NotificationRule,
    NotificationRules,
    Notifier,
)
from district.privacy import PrivacyPolicy, Redactor
from district.process import run
from district.product import state_directory
from district.providers import ProviderRegistry
from district.storage import (
    Constants as StorageConstants,
)
from district.storage import DistrictDatabase

from .helpers import check, expect

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType


def test_process_identity_current_process() -> None:
    """Capture a non-empty start token for a live process."""
    identity: ProcessIdentity = ProcessIdentity.capture(pid=getpid())
    expect(actual=identity.pid, expected=getpid())
    check(
        condition=identity.started_token != "",
        reason="live process exposes a start token",
    )


def test_identity_store(tmp_path: Path) -> None:
    """Persist one stable host identifier across reads.

    Args:
        tmp_path: Tmp path.

    """
    store: IdentityStore = IdentityStore(home=tmp_path)
    first: str = store.host_id()
    second: str = IdentityStore(home=tmp_path).host_id()
    expect(actual=first, expected=second)
    check(
        condition=IdentityStore.new_id() != first,
        reason="new ids are unique",
    )
    check(
        condition=len(IdentityStore.machine_fingerprint()) > 0,
        reason="fingerprint generated",
    )


def test_identity_store_empty_file(tmp_path: Path) -> None:
    """Regenerate the host id when the file is empty.

    Args:
        tmp_path: Tmp path.

    """
    directory: Path = tmp_path / ".district"
    directory.mkdir(exist_ok=True)
    host_path: Path = directory / "host_id"
    _ = host_path.write_text(data="", encoding="utf-8")
    value: str = IdentityStore(home=tmp_path).host_id()
    check(condition=value != "", reason="empty file regenerated")


def test_ring_buffer_bounds() -> None:
    """Evict the oldest chunks beyond the byte budget."""
    buffer: OutputRingBuffer = OutputRingBuffer(limit_bytes=8)
    buffer.append(text="abcd")
    buffer.append(text="efgh")
    buffer.append(text="ij")
    expect(actual=buffer.text(), expected="efghij")
    empty: OutputRingBuffer = OutputRingBuffer(limit_bytes=1)
    expect(actual=empty.text(), expected="")


def test_privacy_policy_defaults(tmp_path: Path) -> None:
    """Return defaults without a privacy file.

    Args:
        tmp_path: Tmp path.

    """
    policy: PrivacyPolicy = PrivacyPolicy.load(home=tmp_path)
    expect(actual=policy.history_days, expected=30)
    expect(actual=policy.event_days, expected=90)
    check(
        condition=not policy.persist_output,
        reason="output persistence off by default",
    )


def test_privacy_policy_values(tmp_path: Path) -> None:
    """Normalise persisted privacy values.

    Args:
        tmp_path: Tmp path.

    """
    directory: Path = tmp_path / ".district"
    directory.mkdir(exist_ok=True)
    _ = (directory / "privacy.json").write_text(
        data=dumps(
            obj={
                "history_days": 2,
                "event_days": "bad",
                "persist_output": True,
                "output_limit_bytes": 10,
                "redact_patterns": ["custom"],
            },
        ),
        encoding="utf-8",
    )
    policy: PrivacyPolicy = PrivacyPolicy.load(home=tmp_path)
    expect(actual=policy.history_days, expected=2)
    expect(actual=policy.event_days, expected=90)
    check(condition=policy.persist_output, reason="persist flag honoured")
    expect(actual=policy.output_limit_bytes, expected=4096)
    expect(actual=policy.redact_patterns, expected=("custom",))


def test_privacy_policy_invalid(tmp_path: Path) -> None:
    """Fall back to defaults on invalid or non-object JSON.

    Args:
        tmp_path: Tmp path.

    """
    directory: Path = tmp_path / ".district"
    directory.mkdir(exist_ok=True)
    path: Path = directory / "privacy.json"
    _ = path.write_text(data="{broken", encoding="utf-8")
    expect(
        actual=PrivacyPolicy.load(home=tmp_path).history_days,
        expected=30,
    )
    _ = path.write_text(data="[]", encoding="utf-8")
    expect(
        actual=PrivacyPolicy.load(home=tmp_path).history_days,
        expected=30,
    )


def test_redactor_patterns() -> None:
    """Redact credentials, authorisation headers, and token shapes."""
    text: str = (
        "api_key = abc123 Authorization: Bearer tok99 "
        "sk_live_abcdefgh12345 keep"
    )
    redacted: str = Redactor.redact(value=text)
    check(condition="abc123" not in redacted, reason="api key redacted")
    check(condition="tok99" not in redacted, reason="bearer redacted")
    check(condition="sk_live" not in redacted, reason="token redacted")
    check(condition="keep" in redacted, reason="plain text preserved")


def test_redactor_extra_and_invalid() -> None:
    """Apply custom patterns and skip invalid expressions."""
    redacted: str = Redactor.redact(
        value="code hunter7 here",
        extra_patterns=("hunter[0-9]", "([broken"),
    )
    check(condition="hunter7" not in redacted, reason="custom redaction")
    check(condition="here" in redacted, reason="invalid pattern skipped")


def test_run_process() -> None:
    """Execute a command vector and capture output."""
    result: CompletedProcess[str] = run(
        args=("echo", "shared-test"),
        check=True,
        capture_output=True,
    )
    expect(actual=result.stdout.strip(), expected="shared-test")
    with pytest.raises(expected_exception=ValueError, match="empty"):
        _ = run(args=(), check=True)


def _entry(
    *,
    pid: int = 0,
    backend: str = "",
    agent_state: str = "",
    current_command: str = "",
    created_at: str | None = None,
) -> SessionEntry:
    """Build a session entry with test defaults.

    Args:
        pid: Process identifier.
        backend: Backend kind.
        agent_state: Agent state.
        current_command: Current command.
        created_at: Creation timestamp.

    Returns:
        The resulting value.

    """
    base: SessionEntry = SessionEntry(
        id="s1",
        command="zsh",
        session="shared-s1",
        pid=pid,
        backend=backend,
        agent_state=agent_state,
        current_command=current_command,
    )
    if created_at is None:
        return base
    return replace(base, created_at=created_at)


def test_monitor_state() -> None:
    """Resolve snapshot states from live evidence."""
    expect(
        actual=Monitor.state(live=False, command="x", explicit=""),
        expected="DEAD",
    )
    expect(
        actual=Monitor.state(live=True, command="", explicit="executing"),
        expected="EXECUTING",
    )
    expect(
        actual=Monitor.state(live=True, command="node", explicit=""),
        expected="ACTIVE",
    )
    expect(
        actual=Monitor.state(live=True, command="zsh", explicit=""),
        expected="IDLE",
    )


def test_monitor_process_matches() -> None:
    """Match a live PID against its captured start token."""
    entry: SessionEntry = _entry(pid=getpid(), backend="process")
    check(
        condition=Monitor.process_matches(entry=entry),
        reason="live pid matches",
    )
    dead: SessionEntry = _entry(pid=2_000_000_000, backend="process")
    check(
        condition=not Monitor.process_matches(entry=dead),
        reason="missing pid rejected",
    )


def test_monitor_snapshot_hook() -> None:
    """Snapshot a hook-backed session from explicit state."""
    entry: SessionEntry = _entry(
        backend="hook",
        agent_state="EXECUTING",
        current_command="tool",
        created_at="bad-date",
    )
    snapshot: SessionSnapshot = Monitor.snapshot(entry=entry)
    expect(actual=snapshot.state, expected="EXECUTING")
    expect(actual=snapshot.current_command, expected="tool")
    expect(actual=snapshot.age_seconds, expected=0)


def test_monitor_snapshot_dead_hook() -> None:
    """Mark dead hook sessions as dead."""
    entry: SessionEntry = _entry(backend="hook", agent_state="DEAD")
    expect(actual=Monitor.snapshot(entry=entry).state, expected="DEAD")


def test_monitor_snapshot_process() -> None:
    """Snapshot a process-backed session via the live PID."""
    entry: SessionEntry = _entry(
        backend="process",
        pid=getpid(),
        created_at="2024-01-01T00:00:00+00:00",
        current_command="",
    )
    snapshot: SessionSnapshot = Monitor.snapshot(entry=entry)
    check(
        condition=snapshot.state in {"IDLE", "ACTIVE", "EXECUTING"},
        reason="live process resolves a state",
    )
    check(
        condition=snapshot.age_seconds >= 0,
        reason="age computed from timestamp",
    )


def test_agent_configuration_roundtrip(tmp_path: Path) -> None:
    """Create defaults on first load and parse them back.

    Args:
        tmp_path: Tmp path.

    """
    agents: tuple[AgentType, ...] = AgentConfiguration.load(home=tmp_path)
    names: frozenset[str] = frozenset(agent.identifier for agent in agents)
    check(condition="codex" in names, reason="codex signature written")
    check(
        condition="claude-code" in names,
        reason="claude signature written",
    )
    config: Path = tmp_path / ".district" / "agents.json"
    check(condition=config.is_file(), reason="defaults persisted")
    again: tuple[AgentType, ...] = AgentConfiguration.load(home=tmp_path)
    expect(actual=len(again), expected=len(agents))


def test_agent_configuration_errors(tmp_path: Path) -> None:
    """Reject malformed agent configuration documents.

    Args:
        tmp_path: Tmp path.

    """
    directory: Path = tmp_path / ".district"
    directory.mkdir(exist_ok=True)
    config: Path = directory / "agents.json"
    _ = config.write_text(data="{broken", encoding="utf-8")
    with pytest.raises(expected_exception=ValueError, match="JSON"):
        _ = AgentConfiguration.load(home=tmp_path)
    _ = config.write_text(data="[]", encoding="utf-8")
    with pytest.raises(expected_exception=TypeError, match="object"):
        _ = AgentConfiguration.load(home=tmp_path)
    _ = config.write_text(data="{}", encoding="utf-8")
    with pytest.raises(expected_exception=TypeError, match="agents"):
        _ = AgentConfiguration.load(home=tmp_path)
    _ = config.write_text(
        data=dumps(obj={"agents": {"x": 5}}),
        encoding="utf-8",
    )
    with pytest.raises(expected_exception=TypeError, match="object"):
        _ = AgentConfiguration.load(home=tmp_path)
    _ = config.write_text(
        data=dumps(obj={"agents": {"x": {"processes": "y"}}}),
        encoding="utf-8",
    )
    with pytest.raises(expected_exception=TypeError, match="array"):
        _ = AgentConfiguration.load(home=tmp_path)
    _ = config.write_text(
        data=dumps(obj={"agents": {"x": {"name": 3}}}),
        encoding="utf-8",
    )
    with pytest.raises(expected_exception=TypeError, match="fields"):
        _ = AgentConfiguration.load(home=tmp_path)


def test_agent_detection_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detect agent markers through environment variables.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    monkeypatch.delenv(name="DISTRICT_SESSION_ID", raising=False)
    monkeypatch.delenv(name="DISTRICT_DISABLE_AGENT", raising=False)
    monkeypatch.delenv(name="CLAUDECODE", raising=False)
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    _ = AgentConfiguration.load(home=tmp_path)
    monkeypatch.setenv(name="CLAUDECODE", value="1")
    check(
        condition=AgentDetector.is_agent_shell(),
        reason="env marker detects agent",
    )
    monkeypatch.setenv(name="DISTRICT_SESSION_ID", value="existing")
    check(
        condition=not AgentDetector.is_agent_shell(),
        reason="managed sessions are not re-detected",
    )


def test_agent_detection_no_markers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return no detection without markers or matching ancestors.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    monkeypatch.delenv(name="DISTRICT_SESSION_ID", raising=False)
    monkeypatch.delenv(name="DISTRICT_DISABLE_AGENT", raising=False)
    for marker in (
        "CODEX_SESSION_ID",
        "CODEX_THREAD_ID",
        "CODEX_SANDBOX",
        "CLAUDE_SESSION_ID",
        "CLAUDECODE",
        "CURSOR_SESSION_ID",
        "CURSOR_AGENT",
        "DEVIN_SESSION_ID",
    ):
        monkeypatch.delenv(name=marker, raising=False)
    monkeypatch.setattr(
        target=AgentDetector,
        name="ancestor_commands",
        value=lambda: (),
    )
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    _ = AgentConfiguration.load(home=tmp_path)
    detected: bool = AgentDetector.is_agent_shell()
    expect(actual=detected, expected=False)


def test_agent_ancestor_commands() -> None:
    """Walk real process ancestry through ps."""
    commands: tuple[str, ...] = AgentDetector.ancestor_commands()
    check(
        condition=len(commands) > 0,
        reason="ancestor commands discovered",
    )


def test_agent_match_signatures() -> None:
    """Match ancestor commands against signature fields."""
    signature: AgentType = AgentType(
        identifier="x",
        name="X",
        enabled=True,
        processes=("coderun",),
        process_contains=("agentd",),
        command_contains=("token",),
        command_matches=("agent run",),
        environment=(),
    )
    check(
        condition=AgentDetector.matches(
            command="/usr/local/bin/coderun --flag",
            agent=signature,
        ),
        reason="executable name matches",
    )
    check(
        condition=AgentDetector.matches(
            command="/opt/agentd-helper",
            agent=signature,
        ),
        reason="substring matches",
    )
    check(
        condition=AgentDetector.matches(
            command="tool --use token",
            agent=signature,
        ),
        reason="command token matches",
    )
    check(
        condition=AgentDetector.matches(
            command="/opt/bin/agent run --headless",
            agent=signature,
        ),
        reason="command phrase matches",
    )
    check(
        condition=not AgentDetector.matches(
            command="/opt/bin/agent --flag",
            agent=signature,
        ),
        reason="phrase needs the full token pair",
    )
    check(
        condition=not AgentDetector.matches(
            command="/bin/ls -la",
            agent=signature,
        ),
        reason="unrelated command rejected",
    )


def test_agent_detection_devin_ide_ancestors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject Devin IDE binaries while accepting the agent host process.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    monkeypatch.delenv(name="DISTRICT_SESSION_ID", raising=False)
    monkeypatch.delenv(name="DISTRICT_DISABLE_AGENT", raising=False)
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    _ = AgentConfiguration.load(home=tmp_path)
    monkeypatch.setattr(
        target=AgentDetector,
        name="ancestor_commands",
        value=lambda: (
            "/Applications/Devin.app/Contents/MacOS/Devin",
            "/Applications/Devin.app/Contents/Frameworks/"
            "Devin Helper (Plugin).app/Contents/MacOS/Devin Helper (Plugin)",
        ),
    )
    check(
        condition=AgentDetector.detect() is None,
        reason="Devin IDE ancestry is not an agent",
    )
    monkeypatch.setattr(
        target=AgentDetector,
        name="ancestor_commands",
        value=lambda: (
            "/Applications/Devin.app/Resources/devin/bin/devin acp",
            "/Applications/Devin.app/Contents/MacOS/Devin",
        ),
    )
    expect(
        actual=AgentDetector.detect(),
        expected=("devin", "process_tree"),
    )


def test_agent_detection_disable_opt_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Honour DISTRICT_DISABLE_AGENT as an interactive opt-out.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    monkeypatch.delenv(name="DISTRICT_SESSION_ID", raising=False)
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    _ = AgentConfiguration.load(home=tmp_path)
    monkeypatch.setenv(name="CLAUDECODE", value="1")
    check(
        condition=AgentDetector.is_agent_shell(),
        reason="env marker detects agent",
    )
    monkeypatch.setenv(name="DISTRICT_DISABLE_AGENT", value="1")
    check(
        condition=not AgentDetector.is_agent_shell(),
        reason="opt-out env suppresses detection",
    )


def _event(**overrides: object) -> DistrictEvent:
    """Build a shared event with test defaults.

    Args:
        overrides: Overrides.

    Returns:
        The resulting value.

    """
    details: dict[str, object] = {
        "event_type": EventTypes.command_finished,
        "host_id": "h1",
        "details": EventDetails(
            agent="claude",
            command="pytest",
            exit_code=0,
            duration_seconds=1.5,
        ),
    }
    details.update(overrides)
    return DistrictEvent.create(
        event_type=str(object=details["event_type"]),
        host_id=str(object=details["host_id"]),
        details=details["details"]
        if isinstance(details["details"], EventDetails)
        else None,
    )


def test_event_create_and_json() -> None:
    """Create timestamped redacted events and serialise them."""
    event: DistrictEvent = _event()
    check(condition=event.id != "", reason="event id generated")
    check(
        condition=event.occurred_at != "",
        reason="timestamp generated",
    )
    check(
        condition='"agent":"claude"' in event.json(),
        reason="json serialises details",
    )
    redacted_event: DistrictEvent = DistrictEvent.create(
        event_type=EventTypes.output_activity,
        host_id="h",
        details=EventDetails(command="password=abc", payload="token=x"),
    )
    check(
        condition="abc" not in redacted_event.command,
        reason="command redacted at creation",
    )
    check(
        condition='"x"' not in redacted_event.payload
        or "REDACTED" in redacted_event.payload,
        reason="payload redacted at creation",
    )


def test_notification_rule_matching() -> None:
    """Evaluate rule filters against event fields."""
    event: DistrictEvent = _event()
    rule: NotificationRule = NotificationRule()
    check(condition=rule.matches(event=event), reason="default rule matches")
    expect(
        actual=NotificationRule(enabled=False).matches(event=event),
        expected=False,
    )
    expect(
        actual=NotificationRule(event_type="OTHER").matches(event=event),
        expected=False,
    )
    expect(
        actual=NotificationRule(agent="codex").matches(event=event),
        expected=False,
    )
    expect(
        actual=NotificationRule(min_duration_seconds=9.0).matches(
            event=event,
        ),
        expected=False,
    )
    expect(
        actual=NotificationRule(failed_only=True).matches(event=event),
        expected=False,
    )
    failed: DistrictEvent = DistrictEvent.create(
        event_type=EventTypes.command_finished,
        host_id="h",
        details=EventDetails(exit_code=3),
    )
    check(
        condition=NotificationRule(failed_only=True).matches(event=failed),
        reason="failed event matches failed-only rule",
    )


def test_notification_rules_read(tmp_path: Path) -> None:
    """Read configured rules with invalid-content fallbacks.

    Args:
        tmp_path: Tmp path.

    """
    rules: NotificationRules = NotificationRules(home=tmp_path)
    expect(actual=rules.read(), expected=())
    directory: Path = tmp_path / ".district"
    directory.mkdir(exist_ok=True)
    path: Path = directory / "notifications.json"
    _ = path.write_text(data="{bad", encoding="utf-8")
    expect(actual=rules.read(), expected=())
    _ = path.write_text(data="{}", encoding="utf-8")
    expect(actual=rules.read(), expected=())
    _ = path.write_text(
        data=dumps(
            obj=[
                {
                    "event_type": "COMMAND_FINISHED",
                    "agent": "claude",
                    "min_duration_seconds": 2,
                    "failed_only": True,
                    "enabled": True,
                },
            ],
        ),
        encoding="utf-8",
    )
    loaded: tuple[NotificationRule, ...] = rules.read()
    expect(actual=len(loaded), expected=1)
    check(condition=loaded[0].failed_only, reason="rule fields parsed")


def test_notifier_for_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Notify through the platform channel for matching rules.

    Args:
        monkeypatch: Monkeypatch.

    """
    calls: list[tuple[str, ...]] = []

    def fake_run(
        *,
        args: tuple[str, ...],
        **kwargs: bool,
    ) -> CompletedProcess[str]:
        """Record a successful platform notification command.

        Args:
            args: Notification command arguments.
            **kwargs: Notification subprocess keyword arguments.

        Returns:
            A successful subprocess result.

        """
        calls.append((*args, str(object=kwargs)))
        return CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    target: ModuleType = notifications
    monkeypatch.setattr(target=target, name="run", value=fake_run)
    rules: tuple[NotificationRule, ...] = (NotificationRule(),)
    event: DistrictEvent = _event()
    check(
        condition=Notifier.for_event(event=event, rules=rules),
        reason="matching rule delivers",
    )
    check(condition=len(calls) > 0, reason="platform command invoked")
    expect(
        actual=Notifier.for_event(event=event, rules=()),
        expected=False,
    )


def test_notifier_send_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return failure when the platform notifier cannot run.

    Args:
        monkeypatch: Monkeypatch.

    """

    def fake_run(
        *,
        args: tuple[str, ...],
        **kwargs: bool,
    ) -> CompletedProcess[str]:
        """Simulate a missing platform notification command.

        Args:
            args: Notification command arguments.
            **kwargs: Notification subprocess keyword arguments.

        Returns:
            A completed process if the simulated command were to succeed.

        Raises:
            OSError: Always, to emulate an unavailable notifier.

        """
        msg: str = f"missing {args!r} {kwargs!r}"
        raise OSError(msg)

    target: ModuleType = notifications
    monkeypatch.setattr(target=target, name="run", value=fake_run)
    expect(
        actual=Notifier.send(title="t", message="m"),
        expected=False,
    )


def test_database_operational_roundtrip(tmp_path: Path) -> None:
    """Exercise persisted contexts, commands, hosts, events, and web state.

    Args:
        tmp_path: Temporary database home.

    """
    database: DistrictDatabase = DistrictDatabase(home=tmp_path)
    check(condition=database.schema_version() > 0, reason="schema migrated")
    database.set_agent_context(
        provider="claude",
        session_id="agent-1",
        workspace="/workspace",
    )
    expect(
        actual=database.agent_context(workspace="/workspace"),
        expected=("claude", "agent-1"),
    )
    event: DistrictEvent = _event()
    database.append_event(event=event)
    check(condition=bool(database.event_json()), reason="event persisted")
    check(
        condition=bool(database.event_json(filters=("claude",))),
        reason="event filtering works",
    )
    database.command_started(
        command_id="command-1",
        session_uuid="uuid-1",
        session_id="session-1",
        host_id="host-1",
        agent="claude",
        agent_session="agent-1",
        surface="terminal",
        editor="",
        command="pytest",
        cwd="/workspace",
        started_at="2026-01-01T00:00:00+00:00",
    )
    expect(
        actual=database.command_started_at(command_id="command-1"),
        expected="2026-01-01T00:00:00+00:00",
    )
    database.observe_processes(
        session_uuid="uuid-1",
        processes=((123, "token", "command-1"),),
    )
    expect(actual=database.process_count(session_uuid="uuid-1"), expected=1)
    database.command_finished(
        command_id="command-1",
        finished_at="2026-01-01T00:00:01+00:00",
        exit_code=0,
        duration_seconds=1.0,
    )
    check(condition=bool(database.command_json()), reason="command persisted")
    database.upsert_host(
        host_id="host-1",
        name="remote",
        target="example",
        command="ssh example",
        enabled=True,
    )
    expect(
        actual=database.host_rows(),
        expected=(("host-1", "remote", "example", True),),
    )
    check(
        condition=bool(database.host_details()),
        reason="host details persisted",
    )
    database.remove_host(name="remote")
    expect(actual=database.host_rows(), expected=())
    database.set_web_server(
        pid=123,
        started_token="star" + "ted",
        host="127.0.0.1",
        port=8765,
        url="http://127.0.0.1:8765/",
    )
    check(
        condition=database.web_server() is not None,
        reason="web state stored",
    )
    database.clear_web_server()
    expect(actual=database.web_server(), expected=None)
    expect(actual=database.integrity(), expected="ok")
    counts: tuple[int, int, int, int] = database.row_counts()
    check(condition=counts[1] == 1 and counts[2] == 1, reason="row counts")
    purged: tuple[int, int] = database.purge(policy=PrivacyPolicy())
    check(condition=all(value >= 0 for value in purged), reason="purge counts")


def test_district_state_directory_is_secured(tmp_path: Path) -> None:
    """Create the state directory with owner-only permissions.

    Args:
        tmp_path: Temporary home directory.

    """
    current: Path = state_directory(home=tmp_path)
    expect(actual=current, expected=tmp_path / ".district")
    check(
        condition=current.is_dir(),
        reason="state directory must exist",
    )


def test_provider_registry(tmp_path: Path) -> None:
    """Expose every built-in coding-agent provider.

    Args:
        tmp_path: Temporary directory.

    """
    del tmp_path
    expect(
        actual=tuple(sorted(ProviderRegistry.builtins())),
        expected=("claude", "codex", "cursor", "devin"),
    )


def _run(*, database: DistrictDatabase, number: int, output: str) -> None:
    """Record one finished command for a session.

    Args:
        database: Database to write to.
        number: Distinguishes the command and orders it in time.
        output: Output to store with it.

    """
    database.command_started(
        command_id=f"c{number}",
        session_uuid="uuid-1",
        session_id="s1",
        host_id="host-1",
        agent="claude-code",
        agent_session="agent-1",
        surface="desktop",
        editor="",
        command=f"echo {number}",
        cwd="/w",
        started_at=f"2026-01-01T00:00:{number:02d}+00:00",
    )
    database.command_finished(
        command_id=f"c{number}",
        finished_at=f"2026-01-01T00:00:{number:02d}+00:00",
        exit_code=0,
        duration_seconds=0.5,
        output=output,
    )


def test_command_output_is_stored_beside_the_command_and_read_back(
    tmp_path: Path,
) -> None:
    """A session's transcript lists its newest commands, oldest first.

    Args:
        tmp_path: Scratch directory.

    """
    database: DistrictDatabase = DistrictDatabase(home=tmp_path)
    for number in range(1, 5):
        _run(database=database, number=number, output=f"out {number}")
    _run(database=database, number=5, output="")
    rows: list[dict[str, object]] = [
        loads(s=row)
        for row in database.command_transcript(session_id="s1", limit=3)
    ]
    expect(
        actual=[row["command_id"] for row in rows],
        expected=["c3", "c4", "c5"],
    )
    expect(
        actual=[row["output"] for row in rows],
        expected=["out 3", "out 4", ""],
    )
    expect(actual=rows[0]["exit_code"], expected=0)
    expect(
        actual=database.command_transcript(session_id="other", limit=3),
        expected=(),
    )


def test_old_command_output_is_dropped_beyond_the_retention_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the newest outputs are kept; the commands themselves stay.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setattr(target=StorageConstants, name="output_rows", value=2)
    database: DistrictDatabase = DistrictDatabase(home=tmp_path)
    for number in range(1, 5):
        _run(database=database, number=number, output=f"out {number}")
    rows: list[dict[str, object]] = [
        loads(s=row)
        for row in database.command_transcript(session_id="s1", limit=10)
    ]
    expect(
        actual=[row["command_id"] for row in rows],
        expected=["c1", "c2", "c3", "c4"],
    )
    expect(
        actual=[row["output"] for row in rows],
        expected=["", "", "out 3", "out 4"],
    )


def test_an_existing_database_gains_the_output_table_without_losing_data(
    tmp_path: Path,
) -> None:
    """Opening a database made before output was stored adds the table.

    Args:
        tmp_path: Scratch directory.

    """
    database: DistrictDatabase = DistrictDatabase(home=tmp_path)
    _run(database=database, number=1, output="kept")
    path: Path = (
        state_directory(home=tmp_path) / StorageConstants.database_name
    )
    with closing(thing=connect(database=path)) as raw, raw:
        _ = raw.execute("DROP TABLE command_output")
    reopened: DistrictDatabase = DistrictDatabase(home=tmp_path)
    expect(actual=len(reopened.command_json()), expected=1)
    expect(
        actual=[
            loads(s=row)["output"]
            for row in reopened.command_transcript(session_id="s1", limit=5)
        ],
        expected=[""],
    )
    _run(database=reopened, number=2, output="again")
    expect(
        actual=loads(
            s=reopened.command_transcript(session_id="s1", limit=1)[0],
        )["output"],
        expected="again",
    )
