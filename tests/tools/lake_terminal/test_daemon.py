# Copyright (c) 2026 Project contributors.
"""Tests for the long-lived local control-plane daemon."""

from __future__ import annotations

from json import dumps, loads
from pathlib import Path
from tempfile import mkdtemp
from threading import Thread
from time import sleep
from typing import Final, cast, final

import pytest

from tools.lake_terminal.lake_terminal import daemon as daemon_module
from tools.lake_terminal.lake_terminal.conpty import (
    ConPtyClient,
    ConPtyInfo,
    ConPtyManager,
    WindowsConPty,
)
from tools.lake_terminal.lake_terminal.daemon import LakeDaemon
from tools.lake_terminal.lake_terminal.events import EventTypes
from tools.lake_terminal.lake_terminal.ipc import EventServer
from tools.lake_terminal.lake_terminal.models import SessionEntry
from tools.lake_terminal.lake_terminal.monitoring import SessionSnapshot
from tools.lake_terminal.lake_terminal.privacy import PrivacyPolicy
from tools.lake_terminal.lake_terminal.storage import LakeDatabase

from .helpers import check, expect


@final
class Constants:
    """Group module-level constants.

    Attributes:
        wait_seconds: Grouped module constant.

    """

    wait_seconds: Final[float] = 5.0


def _entry() -> SessionEntry:
    """Build one session row for daemon tests.

    Returns:
        The resulting value.

    """
    return SessionEntry(
        id="d1",
        command="shared d1",
        session="shared-d1",
        uuid="uuid-d1",
        host_id="h1",
        backend="process",
        pid=0,
    )


def _snapshot(
    *,
    state: str = "IDLE",
    output: str = "",
) -> SessionSnapshot:
    """Build one monitor snapshot.

    Args:
        state: Session state.
        output: Captured output.

    Returns:
        The resulting value.

    """
    return SessionSnapshot(
        id="d1",
        state=state,
        clients=0,
        current_command="",
        output=output,
        age_seconds=0,
        reconnectable=True,
        process_count=0,
        workload_pid=0,
    )


class _FakeConPtyManager:
    """Minimal ConPtyManager stand-in for IPC dispatch tests."""

    def __init__(self) -> None:
        """Initialise an empty session table."""
        self.sessions: dict[str, tuple[str, tuple[str, ...]]] = {}
        self.outputs: dict[str, str] = {}

    def create(
        self,
        *,
        session_uuid: str,
        command: tuple[str, ...],
        cwd: str,
    ) -> ConPtyInfo:
        """Record one session creation.

        Args:
            session_uuid: Session uuid.
            command: Command.
            cwd: Cwd.

        Returns:
            The resulting value.

        """
        self.sessions[session_uuid] = (cwd, command)
        self.outputs[session_uuid] = "hello-output"
        return ConPtyInfo(session_uuid=session_uuid, pid=4321, alive=True)

    def write(self, *, session_uuid: str, text: str) -> int:
        """Record written text.

        Args:
            session_uuid: Session uuid.
            text: Text.

        Returns:
            The resulting value.

        Raises:
            KeyError: When the session is unknown.

        """
        if session_uuid not in self.sessions:
            raise KeyError(session_uuid)
        return len(text)

    def output(self, *, session_uuid: str) -> str:
        """Return buffered output.

        Args:
            session_uuid: Session uuid.

        Returns:
            The resulting value.

        """
        return self.outputs.get(session_uuid, "")

    def kill(self, *, session_uuid: str) -> bool:
        """Remove one session.

        Args:
            session_uuid: Session uuid.

        Returns:
            The resulting value.

        """
        return self.sessions.pop(session_uuid, None) is not None

    def info(self, *, session_uuid: str) -> ConPtyInfo | None:
        """Return session information.

        Args:
            session_uuid: Session uuid.

        Returns:
            The resulting value.

        """
        return (
            ConPtyInfo(session_uuid=session_uuid, pid=4321, alive=True)
            if session_uuid in self.sessions
            else None
        )


def test_on_publish_updates_buffers(tmp_path: Path) -> None:
    """Route output events into per-session ring buffers.

    Args:
        tmp_path: Tmp path.

    """
    daemon: LakeDaemon = LakeDaemon(home=tmp_path)
    daemon.on_publish(payload="{broken")
    daemon.on_publish(payload=dumps(obj=["not", "dict"]))
    daemon.on_publish(payload=dumps(obj={"event_type": "other"}))
    daemon.on_publish(
        payload=dumps(
            obj={
                "event_type": EventTypes.output_activity,
                "session_uuid": "",
                "payload": "x",
            },
        ),
    )
    expect(
        actual=daemon.handle(request={"op": "buffer", "session_uuid": "u1"}),
        expected="",
    )
    daemon.on_publish(
        payload=dumps(
            obj={
                "event_type": EventTypes.output_activity,
                "session_uuid": "u1",
                "payload": "line-one",
            },
        ),
    )
    buffered: str | None = daemon.handle(
        request={"op": "buffer", "session_uuid": "u1"},
    )
    check(
        condition=buffered is not None and "line-one" in buffered,
        reason="output event buffered for replay",
    )


def test_handle_operations(tmp_path: Path) -> None:
    """Dispatch buffer and ConPTY IPC operations.

    Args:
        tmp_path: Tmp path.

    """
    daemon: LakeDaemon = LakeDaemon(home=tmp_path)
    expect(actual=daemon.handle(request={"op": "mystery"}), expected=None)
    expect(
        actual=daemon.handle(request={"op": "conpty-create"}),
        expected="UNAVAILABLE",
    )
    manager: _FakeConPtyManager = _FakeConPtyManager()
    daemon.conpty = cast(typ="ConPtyManager", val=manager)
    created: str | None = daemon.handle(
        request={
            "op": "conpty-create",
            "session_uuid": "s-1",
            "command": dumps(obj=["powershell.exe", "-NoLogo"]),
            "cwd": "C:\\work",
        },
    )
    check(
        condition=created is not None and '"pid": 4321' in created,
        reason="conpty create returns process info",
    )
    expect(
        actual=daemon.handle(
            request={"op": "conpty-output", "session_uuid": "s-1"},
        ),
        expected="hello-output",
    )
    expect(
        actual=daemon.handle(
            request={
                "op": "conpty-write",
                "session_uuid": "s-1",
                "text": "dir",
            },
        ),
        expected="3",
    )
    expect(
        actual=daemon.handle(
            request={
                "op": "conpty-write",
                "session_uuid": "missing",
                "text": "x",
            },
        ),
        expected="0",
    )
    expect(
        actual=daemon.handle(
            request={"op": "conpty-alive", "session_uuid": "s-1"},
        ),
        expected="1",
    )
    expect(
        actual=daemon.handle(
            request={"op": "conpty-alive", "session_uuid": "missing"},
        ),
        expected="0",
    )
    expect(
        actual=daemon.handle(
            request={"op": "conpty-kill", "session_uuid": "s-1"},
        ),
        expected="OK",
    )
    expect(
        actual=daemon.handle(
            request={"op": "conpty-kill", "session_uuid": "s-1"},
        ),
        expected="MISSING",
    )


def test_command_decoding_edge_cases(tmp_path: Path) -> None:
    """Treat malformed serialized commands as empty.

    Args:
        tmp_path: Tmp path.

    """
    daemon: LakeDaemon = LakeDaemon(home=tmp_path)
    daemon.conpty = cast(typ="ConPtyManager", val=_FakeConPtyManager())
    response: str | None = daemon.handle(
        request={
            "op": "conpty-create",
            "session_uuid": "s-2",
            "command": "{invalid",
            "cwd": "",
        },
    )
    check(
        condition=response is not None and '"pid": 4321' in response,
        reason="invalid command falls back to default shell",
    )


def test_sample_once_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sample sessions without crashing on errors.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    daemon: LakeDaemon = LakeDaemon(home=tmp_path)
    daemon.sample_once()

    def _raise() -> tuple[SessionEntry, ...]:
        """Fail like a backend outage.

        Returns:
            The resulting value.

        Raises:
            OSError: Always, to emulate a backend outage.

        """
        msg: str = "backend gone"
        raise OSError(msg)

    monkeypatch.setattr(
        target=daemon.service,
        name="list_entries",
        value=_raise,
    )
    daemon.sample_once()


def test_sample_entry_records_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emit state and output events only on real changes.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    daemon: LakeDaemon = LakeDaemon(home=tmp_path)
    entry: SessionEntry = _entry()
    snapshots: list[SessionSnapshot] = [
        _snapshot(state="IDLE", output="first"),
        _snapshot(state="EXECUTING", output="first"),
        _snapshot(state="EXECUTING", output="second"),
        _snapshot(state="EXECUTING", output="second"),
    ]
    index: list[int] = [0]

    def _next_snapshot(*, entry: SessionEntry) -> SessionSnapshot:
        """Return the next scripted snapshot.

        Args:
            entry: Entry.

        Returns:
            The resulting value.

        """
        del entry
        snap: SessionSnapshot = snapshots[index[0]]
        index[0] = min(index[0] + 1, len(snapshots) - 1)
        return snap

    monkeypatch.setattr(
        target=daemon_module.Monitor,
        name="snapshot",
        value=_next_snapshot,
    )

    def _one_entry() -> tuple[SessionEntry, ...]:
        """Return the scripted session entry.

        Returns:
            The resulting value.

        """
        return (entry,)

    monkeypatch.setattr(
        target=daemon.service,
        name="list_entries",
        value=_one_entry,
    )
    for _ in range(4):
        daemon.sample_once()
    stored: tuple[str, ...] = LakeDatabase(home=tmp_path).event_json()
    parsed: list[dict[str, str]] = [loads(s=row) for row in stored]
    mine: list[dict[str, str]] = [
        row for row in parsed if row.get("session_uuid") == "uuid-d1"
    ]
    types: list[str] = [row.get("event_type", "") for row in mine]
    check(
        condition=EventTypes.session_resumed in types
        or EventTypes.session_idle in types,
        reason="state transition recorded",
    )
    output_events: list[dict[str, str]] = [
        row
        for row in mine
        if row.get("event_type") == EventTypes.output_activity
    ]
    expect(actual=len(output_events), expected=2)


def test_run_serves_until_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the daemon loop and stop it over IPC.

    Args:
        monkeypatch: Monkeypatch.

    """
    home: Path = Path(mkdtemp(prefix="dmn"))
    _run_serves(home=home, monkeypatch=monkeypatch)


def _run_serves(*, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive the daemon run/stop cycle against a short socket path.

    Args:
        home: Home.
        monkeypatch: Monkeypatch.

    """
    daemon: LakeDaemon = LakeDaemon(home=home)

    def _no_entries() -> tuple[SessionEntry, ...]:
        """Return no sessions for the sampler.

        Returns:
            The resulting value.

        """
        return ()

    monkeypatch.setattr(
        target=daemon.service,
        name="list_entries",
        value=_no_entries,
    )
    thread: Thread = Thread(target=daemon.run, daemon=True)
    thread.start()
    try:
        deadline_ok: bool = False
        for _attempt in range(50):
            if LakeDaemon.ping(home=home):
                deadline_ok = True
                break
            sleep(0.05)
        check(condition=deadline_ok, reason="daemon answered ping")
        check(
            condition=LakeDaemon.stop(home=home),
            reason="stop request accepted",
        )
        thread.join(timeout=Constants.wait_seconds)
        check(condition=not thread.is_alive(), reason="daemon exited on stop")
    finally:
        if thread.is_alive():
            _ = EventServer.request(home=home, operation="stop")
            thread.join(timeout=Constants.wait_seconds)


def test_class_lifecycle_delegates(tmp_path: Path) -> None:
    """Expose DaemonControl actions as class attributes.

    Args:
        tmp_path: Tmp path.

    """
    check(
        condition=not LakeDaemon.ping(home=tmp_path),
        reason="ping false without daemon",
    )
    check(
        condition=not LakeDaemon.stop(home=tmp_path),
        reason="stop false without daemon",
    )


@final
class _FakeWindowsConPty:
    """Provide a controllable pseudo-console for manager tests."""

    def __init__(self) -> None:
        """Initialize fake process state."""
        self.pid: int = 4321
        self.live: bool = True
        self.writes: list[bytes] = []
        self.terminated: bool = False

    @staticmethod
    def read(*, size: int = 4096) -> bytes:
        """Return no pending pseudo-console output.

        Args:
            size: Maximum requested byte count.

        Returns:
            Empty output bytes.

        """
        _ = size
        return b""

    def alive(self) -> bool:
        """Return fake liveness.

        Returns:
            Whether the fake process is live.

        """
        return self.live

    def write(self, *, data: bytes) -> int:
        """Record input bytes.

        Args:
            data: Input bytes.

        Returns:
            Number of bytes accepted.

        """
        self.writes.append(data)
        return len(data)

    def terminate(self) -> None:
        """Mark the fake process terminated."""
        self.live = False
        self.terminated = True


def test_conpty_manager_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise manager create, reuse, I/O, info, and kill behavior.

    Args:
        monkeypatch: Monkeypatch.

    """
    session: _FakeWindowsConPty = _FakeWindowsConPty()

    def fake_spawn(**_kwargs: object) -> _FakeWindowsConPty:
        """Return the controlled pseudo-console.

        Args:
            _kwargs: Ignored spawn options.

        Returns:
            Controlled pseudo-console.

        """
        return session

    monkeypatch.setattr(target=WindowsConPty, name="spawn", value=fake_spawn)
    manager: ConPtyManager = ConPtyManager(policy=PrivacyPolicy())
    first: ConPtyInfo = manager.create(
        session_uuid="uuid-conpty",
        command=("shell",),
        cwd="/workspace",
    )
    expect(actual=first.pid, expected=4321)
    second: ConPtyInfo = manager.create(
        session_uuid="uuid-conpty",
        command=("ignored",),
        cwd="/ignored",
    )
    expect(actual=second.pid, expected=4321)
    expect(
        actual=manager.write(session_uuid="uuid-conpty", text="hello"),
        expected=5,
    )
    check(
        condition=manager.info(session_uuid="uuid-conpty") is not None,
        reason="info exists",
    )
    expect(actual=manager.output(session_uuid="missing"), expected="")
    check(
        condition=manager.kill(session_uuid="uuid-conpty"),
        reason="session killed",
    )
    check(condition=session.terminated, reason="native session terminated")
    check(
        condition=not manager.kill(session_uuid="missing"),
        reason="missing kill false",
    )
    expect(actual=manager.info(session_uuid="missing"), expected=None)


def test_conpty_client_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise daemon ConPTY client request and validation paths.

    Args:
        tmp_path: Temporary home.
        monkeypatch: Monkeypatch.

    """
    responses: dict[str, str] = {
        "conpty-create": '{"pid":4321,"alive":true}',
        "conpty-output": "output",
        "conpty-write": "5",
        "conpty-kill": "OK",
        "conpty-alive": "1",
    }

    def fake_request(
        *,
        home: Path,
        operation: str,
        values: dict[str, str] | None = None,
    ) -> str:
        """Return a response for one daemon operation.

        Args:
            home: Endpoint home.
            operation: Operation name.
            values: Request values.

        Returns:
            Configured response.

        """
        _ = home, values
        return responses[operation]

    monkeypatch.setattr(
        target=EventServer,
        name="request",
        value=fake_request,
    )
    client: ConPtyClient = ConPtyClient(home=tmp_path)
    info: ConPtyInfo = client.create(
        session_uuid="uuid-client",
        cwd="/workspace",
        command=("shell",),
    )
    expect(actual=info.pid, expected=4321)
    expect(
        actual=client.output(session_uuid="uuid-client"),
        expected="output",
    )
    expect(
        actual=client.write(
            session_uuid="uuid-client",
            text="hello",
        ),
        expected=5,
    )
    check(
        condition=client.kill(session_uuid="uuid-client"),
        reason="kill response",
    )
    check(
        condition=client.alive(session_uuid="uuid-client"),
        reason="alive response",
    )
    expect(
        actual=client.attach(session_uuid="uuid-client"),
        expected=0,
    )
    invalid_responses: tuple[tuple[str, str], ...] = (
        ("[]", "Invalid ConPTY daemon response\\."),
        ('{"pid":true,"alive":true}', "Invalid ConPTY daemon PID\\."),
        ('{"pid":{},"alive":true}', "Invalid ConPTY daemon PID\\."),
        ('{"pid":"bad","alive":true}', "Invalid ConPTY daemon PID\\."),
        (
            '{"pid":1,"alive":"yes"}',
            "Invalid ConPTY daemon alive state\\.",
        ),
    )
    for invalid, expected_message in invalid_responses:
        responses["conpty-create"] = invalid
        with pytest.raises(
            expected_exception=OSError,
            match=expected_message,
        ):
            _ = client.create(
                session_uuid="uuid-client",
                cwd="/workspace",
                command=("shell",),
            )
