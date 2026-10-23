# Copyright (c) 2026 Project contributors.
"""Tests for the shared-session service and repository."""

from __future__ import annotations

from dataclasses import replace
from os import getpid
from subprocess import CalledProcessError, CompletedProcess
from typing import TYPE_CHECKING, Final, final

import pytest

from tools.lake_terminal.lake_terminal import core as core_module
from tools.lake_terminal.lake_terminal.core import (
    ExecResult,
    LakeTerminal,
    SessionRepository,
)
from tools.lake_terminal.lake_terminal.identity import ProcessIdentity
from tools.lake_terminal.lake_terminal.models import SessionEntry

from .helpers import check, expect

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@final
class Constants:
    """Group module-level constants.

    Attributes:
        marker_start: Grouped module constant.
        marker_end: Grouped module constant.

    """

    marker_start: Final[str] = "__LAKE_START_"
    marker_end: Final[str] = "__LAKE_END_"


def _entry(
    *,
    session_id: str = "aa11bb",
    session: str = "",
    backend: str = "tmux",
    pid: int = 0,
    process_started_token: str = "",
) -> SessionEntry:
    """Build a session row with the fields core tests need.

    Args:
        session_id: Session identifier.
        session: Backend session name.
        backend: Backend kind.
        pid: Process identifier.
        process_started_token: Process identity token.

    Returns:
        The resulting value.

    """
    resolved_session: str = session or f"shared-{session_id}"
    return SessionEntry(
        id=session_id,
        command=f"lake {session_id}",
        session=resolved_session,
        uuid=f"uuid-{session_id}",
        host_id="h1",
        backend=backend,
        pid=pid,
        process_started_token=process_started_token,
    )


def _no_descendants(**_kwargs: object) -> tuple[()]:
    """Stand in for a process-tree walk with no children.

    Args:
        **_kwargs: Ignored process-inspection keyword arguments.

    Returns:
        The resulting value.

    """
    return ()


def _stub_descendants(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid full system process-table walks during reconcile.

    Args:
        monkeypatch: Monkeypatch.

    """
    monkeypatch.setattr(
        target=core_module.ProcessInspector,
        name="descendants",
        value=_no_descendants,
    )


def _service(tmp_path: Path) -> LakeTerminal:
    """Build a service against a scratch home directory.

    Args:
        tmp_path: Tmp path.

    Returns:
        The resulting value.

    """
    return LakeTerminal(repository=SessionRepository(home=tmp_path))


class _FakeTmux:
    """Deterministic tmux subprocess dispatcher for tests.

    Attributes:
        sessions: Live session names.
        pane_pid: Pane process identifier text.
        pane_command: Active pane command text.
        capture: Scrollback text returned by capture-pane.
        on_send_keys: Callback for literal send-keys payloads.

    """

    def __init__(
        self,
        *,
        existing: set[str] | None = None,
        pane_pid: str = "0",
        pane_command: str = "zsh",
        capture: str = "",
        on_send_keys: Callable[[str], None] | None = None,
    ) -> None:
        """Store dispatcher state.

        Args:
            existing: Existing.
            pane_pid: Pane pid.
            pane_command: Pane command.
            capture: Capture.
            on_send_keys: On send keys.

        """
        self.sessions: set[str] = set() if existing is None else existing
        self.pane_pid: str = pane_pid
        self.pane_command: str = pane_command
        self.capture: str = capture
        self.on_send_keys: Callable[[str], None] | None = on_send_keys

    def run(
        self,
        *,
        args: tuple[str, ...],
        **_kwargs: object,
    ) -> CompletedProcess[str]:
        """Dispatch one tmux invocation.

        Args:
            args: Tmux command arguments.
            **_kwargs: Ignored subprocess keyword arguments.

        Returns:
            The resulting value.

        """
        action: str = args[1] if len(args) > 1 else ""
        handlers: dict[
            str,
            Callable[[tuple[str, ...]], CompletedProcess[str]],
        ] = {
            "has-session": self._has,
            "list-sessions": self._list,
            "new-session": self._new,
            "kill-session": self._kill,
            "display-message": self._display,
            "send-keys": self._send,
            "capture-pane": self._capture,
        }
        handler: Callable[[tuple[str, ...]], CompletedProcess[str]] | None = (
            handlers.get(action)
        )
        if handler is None:
            return CompletedProcess(args=args, returncode=0)
        return handler(args)

    @staticmethod
    def _after(args: tuple[str, ...], flag: str) -> str:
        """Return the value following a tmux flag.

        Args:
            args: Args.
            flag: Flag.

        Returns:
            The resulting value.

        """
        index: int = args.index(flag)
        return args[index + 1] if len(args) > index + 1 else ""

    def _has(self, args: tuple[str, ...]) -> CompletedProcess[str]:
        """Handle has-session.

        Args:
            args: Args.

        Returns:
            The resulting value.

        """
        target: str = self._after(args=args, flag="-t")
        return CompletedProcess(
            args=args,
            returncode=0 if target in self.sessions else 1,
        )

    def _list(self, args: tuple[str, ...]) -> CompletedProcess[str]:
        """Handle list-sessions.

        Args:
            args: Args.

        Returns:
            The resulting value.

        """
        return CompletedProcess(
            args=args,
            returncode=0,
            stdout="\n".join(sorted(self.sessions)),
        )

    def _new(self, args: tuple[str, ...]) -> CompletedProcess[str]:
        """Handle new-session.

        Args:
            args: Args.

        Returns:
            The resulting value.

        """
        self.sessions.add(self._after(args=args, flag="-s"))
        return CompletedProcess(args=args, returncode=0)

    def _kill(self, args: tuple[str, ...]) -> CompletedProcess[str]:
        """Handle kill-session.

        Args:
            args: Args.

        Returns:
            The resulting value.

        """
        self.sessions.discard(self._after(args=args, flag="-t"))
        return CompletedProcess(args=args, returncode=0)

    def _display(self, args: tuple[str, ...]) -> CompletedProcess[str]:
        """Handle display-message.

        Args:
            args: Args.

        Returns:
            The resulting value.

        """
        field: str = args[len(args) - 1] if args else ""
        value: str = (
            self.pane_pid if "pane_pid" in field else self.pane_command
        )
        return CompletedProcess(
            args=args,
            returncode=0,
            stdout=f"{value}\n",
        )

    def _send(self, args: tuple[str, ...]) -> CompletedProcess[str]:
        """Handle send-keys.

        Args:
            args: Args.

        Returns:
            The resulting value.

        """
        if self.on_send_keys is not None and "-l" in args and args:
            self.on_send_keys(args[len(args) - 1])
        return CompletedProcess(args=args, returncode=0)

    def _capture(self, args: tuple[str, ...]) -> CompletedProcess[str]:
        """Handle capture-pane.

        Args:
            args: Args.

        Returns:
            The resulting value.

        """
        return CompletedProcess(
            args=args,
            returncode=0,
            stdout=self.capture,
        )


def _fake_tmux(
    *,
    existing: set[str] | None = None,
    pane_pid: str = "0",
    pane_command: str = "zsh",
    capture: str = "",
    on_send_keys: Callable[[str], None] | None = None,
) -> Callable[..., CompletedProcess[str]]:
    """Return a fake tmux subprocess dispatcher.

    Args:
        existing: Existing.
        pane_pid: Pane pid.
        pane_command: Pane command.
        capture: Capture.
        on_send_keys: On send keys.

    Returns:
        The resulting value.

    """
    return _FakeTmux(
        existing=existing,
        pane_pid=pane_pid,
        pane_command=pane_command,
        capture=capture,
        on_send_keys=on_send_keys,
    ).run


def test_repository_round_trip(tmp_path: Path) -> None:
    """Add, read, update, and remove sessions atomically.

    Args:
        tmp_path: Tmp path.

    """
    repository: SessionRepository = SessionRepository(home=tmp_path)
    expect(actual=repository.read(), expected=())
    entry: SessionEntry = _entry()
    repository.add(entry=entry)
    expect(actual=repository.read(), expected=(entry,))
    updated: tuple[SessionEntry, ...] = repository.update(
        transform=lambda items: tuple(
            replace(item, agent_state="DEAD") for item in items
        ),
    )
    expect(actual=updated[0].agent_state, expected="DEAD")
    other: SessionEntry = _entry(session_id="cc22dd", session="shared-cc22dd")
    repository.replace(entries=(other,))
    expect(actual=repository.read(), expected=(other,))
    repository.remove(session_id="cc22dd")
    expect(actual=repository.read(), expected=())
    repository.remove(session_id="missing")
    expect(actual=repository.database.sessions(), expected=())


def test_exists_and_live_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Probe tmux session existence and live session names.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    service: LakeTerminal = _service(tmp_path=tmp_path)
    monkeypatch.setattr(
        target=core_module,
        name="run",
        value=_fake_tmux(existing={"shared-aa11bb"}),
    )
    _stub_descendants(monkeypatch=monkeypatch)
    check(
        condition=service.exists(session="shared-aa11bb"),
        reason="existing session reported live",
    )
    check(
        condition=not service.exists(session="shared-zz"),
        reason="missing session reported dead",
    )
    names: frozenset[str] | None = service.live_session_names()
    expected_names: frozenset[str] = frozenset({"shared-aa11bb"})
    expect(actual=names, expected=expected_names)

    def failing_run(
        *,
        args: tuple[str, ...],
        **_kwargs: object,
    ) -> CompletedProcess[str]:
        """Simulate a missing tmux executable.

        Args:
            args: Ignored tmux command arguments.
            **_kwargs: Ignored subprocess keyword arguments.

        Returns:
            A completed process if the simulated command were to succeed.

        Raises:
            OSError: Always, to emulate an unavailable tmux executable.

        """
        del args
        msg: str = "tmux missing"
        raise OSError(msg)

    monkeypatch.setattr(target=core_module, name="run", value=failing_run)
    check(
        condition=not service.exists(session="shared-aa11bb"),
        reason="tmux failure means not live",
    )
    expect(actual=service.live_session_names(), expected=None)

    def empty_run(
        *,
        args: tuple[str, ...],
        **_kwargs: object,
    ) -> CompletedProcess[str]:
        """Return an unsuccessful tmux invocation with no output.

        Args:
            args: Ignored tmux command arguments.
            **_kwargs: Ignored subprocess keyword arguments.

        Returns:
            The empty unsuccessful subprocess result.

        """
        del args
        return CompletedProcess(args=(), returncode=1, stdout="")

    monkeypatch.setattr(target=core_module, name="run", value=empty_run)
    empty_names: frozenset[str] = frozenset()
    expect(actual=service.live_session_names(), expected=empty_names)


def test_new_id(tmp_path: Path) -> None:
    """Generate collision-free short session identifiers.

    Args:
        tmp_path: Tmp path.

    """
    service: LakeTerminal = _service(tmp_path=tmp_path)
    first: str = service.new_id(entries=())
    expect(actual=len(first), expected=6)
    collision: SessionEntry = _entry(session_id=first)
    second: str = service.new_id(entries=(collision,))
    check(condition=second != first, reason="ids avoid existing sessions")


def test_reconcile_drops_stale_tmux(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remove tmux sessions whose backend no longer exists.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    service: LakeTerminal = _service(tmp_path=tmp_path)
    repository: SessionRepository = SessionRepository(home=tmp_path)
    live_entry: SessionEntry = _entry(
        session_id="live01",
        session="shared-live01",
    )
    stale_entry: SessionEntry = _entry(
        session_id="dead01",
        session="shared-dead01",
    )
    repository.replace(entries=(live_entry, stale_entry))
    monkeypatch.setattr(
        target=core_module,
        name="run",
        value=_fake_tmux(existing={"shared-live01"}),
    )
    _stub_descendants(monkeypatch=monkeypatch)
    entries: tuple[SessionEntry, ...] = service.reconcile()
    expect(actual=len(entries), expected=1)
    expect(actual=entries[0].id, expected="live01")


def test_reconcile_process_backend(tmp_path: Path) -> None:
    """Keep process-backend sessions only while the pid token matches.

    Args:
        tmp_path: Tmp path.

    """
    service: LakeTerminal = _service(tmp_path=tmp_path)
    repository: SessionRepository = SessionRepository(home=tmp_path)
    identity: ProcessIdentity = ProcessIdentity.capture(pid=getpid())
    live: SessionEntry = _entry(
        session_id="proc01",
        backend="process",
        pid=getpid(),
        process_started_token=identity.started_token,
    )
    wrong_token: SessionEntry = _entry(
        session_id="proc02",
        backend="process",
        pid=getpid(),
        process_started_token=f"{identity.started_token}-mismatch",
    )
    dead_pid: SessionEntry = _entry(
        session_id="proc03",
        backend="process",
        pid=0,
    )
    repository.replace(entries=(live, wrong_token, dead_pid))
    entries: tuple[SessionEntry, ...] = service.reconcile()
    expect(actual=len(entries), expected=1)
    expect(actual=entries[0].id, expected="proc01")


def test_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove registry rows only when the backend is truly gone.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    service: LakeTerminal = _service(tmp_path=tmp_path)
    repository: SessionRepository = SessionRepository(home=tmp_path)
    service.cleanup(session_id="missing")
    entry: SessionEntry = _entry()
    repository.add(entry=entry)
    monkeypatch.setattr(
        target=core_module,
        name="run",
        value=_fake_tmux(existing={"shared-aa11bb"}),
    )
    _stub_descendants(monkeypatch=monkeypatch)
    service.cleanup(session_id="aa11bb")
    expect(actual=len(repository.read()), expected=1)
    monkeypatch.setattr(target=core_module, name="run", value=_fake_tmux())
    service.cleanup(session_id="aa11bb")
    expect(actual=repository.read(), expected=())


def test_status_and_attach(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Look up live sessions and attach through tmux.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    service: LakeTerminal = _service(tmp_path=tmp_path)
    repository: SessionRepository = SessionRepository(home=tmp_path)
    with pytest.raises(expected_exception=KeyError):
        _ = service.status(session_id="nope")
    repository.add(entry=_entry())
    monkeypatch.setattr(
        target=core_module,
        name="run",
        value=_fake_tmux(existing={"shared-aa11bb"}),
    )
    _stub_descendants(monkeypatch=monkeypatch)
    entry: SessionEntry = service.status(session_id="aa11bb")
    expect(actual=entry.id, expected="aa11bb")
    expect(actual=service.attach(session_id="aa11bb"), expected=0)
    with pytest.raises(expected_exception=KeyError):
        _ = service.attach(session_id="gone")


def test_kill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Destroy the tmux backend and registry row together.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    service: LakeTerminal = _service(tmp_path=tmp_path)
    repository: SessionRepository = SessionRepository(home=tmp_path)
    repository.add(entry=_entry())
    sessions: set[str] = {"shared-aa11bb"}
    monkeypatch.setattr(
        target=core_module,
        name="run",
        value=_fake_tmux(existing=sessions),
    )
    _stub_descendants(monkeypatch=monkeypatch)
    service.kill(session_id="aa11bb")
    expect(actual=repository.read(), expected=())
    check(
        condition="shared-aa11bb" not in sessions,
        reason="tmux session killed",
    )
    with pytest.raises(expected_exception=KeyError):
        service.kill(session_id="aa11bb")


def test_send(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject literal text and key presses into a tmux pane.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    service: LakeTerminal = _service(tmp_path=tmp_path)
    repository: SessionRepository = SessionRepository(home=tmp_path)
    repository.add(entry=_entry())
    identity: ProcessIdentity = ProcessIdentity.capture(pid=getpid())
    repository.add(
        entry=_entry(
            session_id="cc22dd",
            session="proc-cc22dd",
            backend="process",
            pid=getpid(),
            process_started_token=identity.started_token,
        ),
    )
    sent: list[tuple[str, ...]] = []
    tmux: _FakeTmux = _FakeTmux(existing={"shared-aa11bb"})

    def _record(
        *,
        args: tuple[str, ...],
        **kwargs: object,
    ) -> CompletedProcess[str]:
        """Record send-keys invocations then dispatch to fake tmux.

        Args:
            args: Tmux command arguments.
            **kwargs: Subprocess keyword arguments.

        Returns:
            The resulting value.

        """
        if len(args) > 1 and args[1] == "send-keys":
            sent.append(args)
        return tmux.run(args=args, **kwargs)

    monkeypatch.setattr(
        target=core_module,
        name="run",
        value=_record,
    )
    _stub_descendants(monkeypatch=monkeypatch)
    service.send(session_id="aa11bb", text="y", keys=("Enter",))
    service.send(session_id="aa11bb", keys=("C-c",))
    expect(
        actual=sent,
        expected=[
            (
                "tmux",
                "send-keys",
                "-t",
                "shared-aa11bb",
                "-l",
                "--",
                "y",
            ),
            ("tmux", "send-keys", "-t", "shared-aa11bb", "Enter"),
            ("tmux", "send-keys", "-t", "shared-aa11bb", "C-c"),
        ],
    )
    with pytest.raises(expected_exception=KeyError):
        service.send(session_id="gone", keys=("Enter",))
    with pytest.raises(
        expected_exception=RuntimeError,
        match="only supports tmux",
    ):
        service.send(session_id="cc22dd", text="x")
    with pytest.raises(
        expected_exception=ValueError,
        match="Unsupported key name",
    ):
        service.send(session_id="aa11bb", keys=("rm",))


def test_create_full_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create a tmux session end to end with a stubbed backend.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    service: LakeTerminal = _service(tmp_path=tmp_path)
    repository: SessionRepository = SessionRepository(home=tmp_path)
    sessions: set[str] = set()
    monkeypatch.setattr(
        target=core_module,
        name="run",
        value=_fake_tmux(existing=sessions, pane_pid="2000000001"),
    )
    _stub_descendants(monkeypatch=monkeypatch)
    monkeypatch.setenv(name="LAKE_AGENT", value="devin")
    monkeypatch.setenv(name="LAKE_AGENT_SESSION", value="devin-1")
    status: int = service.create(
        initial_command=("echo", "hi"),
        initiator="agent",
    )
    expect(actual=status, expected=0)
    check(condition=len(sessions) == 1, reason="tmux session created")
    entries: tuple[SessionEntry, ...] = repository.read()
    expect(actual=len(entries), expected=1)
    entry: SessionEntry = entries[0]
    expect(actual=entry.agent, expected="devin")
    expect(actual=entry.agent_session, expected="devin-1")
    expect(actual=entry.backend, expected="tmux")
    expect(actual=entry.pid, expected=2000000001)
    check(
        condition=entry.initial_command == "echo hi",
        reason="initial command recorded",
    )


def test_create_conpty_when_daemon_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return the daemon failure status without creating state.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    service: LakeTerminal = _service(tmp_path=tmp_path)

    def _start_fails(*, home: Path) -> bool:
        """Report a daemon that cannot be spawned.

        Args:
            home: Home.

        Returns:
            The resulting value.

        """
        _ = home
        return False

    monkeypatch.setattr(
        target=core_module.DaemonControl,
        name="start",
        value=_start_fails,
    )
    status: int = service.create_conpty(initial_command=(), initiator="user")
    expect(actual=status, expected=127)


def test_ensure_agent_creates_and_reuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attach to an agent session or create one when absent.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    service: LakeTerminal = _service(tmp_path=tmp_path)
    repository: SessionRepository = SessionRepository(home=tmp_path)
    sessions: set[str] = set()
    monkeypatch.setattr(
        target=core_module,
        name="run",
        value=_fake_tmux(existing=sessions, pane_pid="2000000001"),
    )
    _stub_descendants(monkeypatch=monkeypatch)
    monkeypatch.setenv(name="LAKE_AGENT", value="devin")
    monkeypatch.setenv(name="LAKE_AGENT_SESSION", value="devin-9")
    status: int = service.ensure_agent()
    expect(actual=status, expected=0)
    entries: tuple[SessionEntry, ...] = repository.read()
    expect(actual=len(entries), expected=1)
    expect(actual=entries[0].initiator, expected="agent")
    sent: list[str] = []
    monkeypatch.setattr(
        target=core_module,
        name="run",
        value=_fake_tmux(existing=sessions, on_send_keys=sent.append),
    )
    _stub_descendants(monkeypatch=monkeypatch)
    status = service.ensure_agent(initial_command=("make", "test"))
    expect(actual=status, expected=0)
    expect(actual=len(repository.read()), expected=1)
    check(
        condition=any("make test" in line for line in sent),
        reason="initial command sent to live pane",
    )


def test_execute_with_stubbed_tmux(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run one command through the exec marker protocol.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    service: LakeTerminal = _service(tmp_path=tmp_path)
    repository: SessionRepository = SessionRepository(home=tmp_path)
    repository.add(entry=_entry())
    token_box: list[str] = []

    def capture_send(line: str) -> None:
        """Capture the execution token from a send-keys payload.

        Args:
            line: Literal command line sent to the tmux pane.

        """
        if Constants.marker_start in line:
            start: int = line.index(Constants.marker_start) + len(
                Constants.marker_start,
            )
            token_box.append(line[start : line.index("__", start)])

    def fake_run(
        *,
        args: tuple[str, ...],
        **kwargs: object,
    ) -> CompletedProcess[str]:
        """Emulate tmux execution-marker traffic.

        Args:
            args: Tmux command arguments.
            **kwargs: Forwarded subprocess keyword arguments.

        Returns:
            The scripted subprocess result.

        """
        action: str = args[1] if len(args) > 1 else ""
        if action == "send-keys" and "-l" in args:
            capture_send(line=next(iter(reversed(args))))
            return CompletedProcess(args=args, returncode=0)
        if action == "capture-pane":
            token: str = token_box[len(token_box) - 1]
            return CompletedProcess(
                args=args,
                returncode=0,
                stdout=(
                    f"{Constants.marker_start}{token}__\n"
                    "output-line\n"
                    f"{Constants.marker_end}{token}__:7\n"
                ),
            )
        return _fake_tmux(existing={"shared-aa11bb"})(args=args, **kwargs)

    monkeypatch.setattr(target=core_module, name="run", value=fake_run)
    result: ExecResult = service.execute(
        session_id="aa11bb",
        command="echo output-line",
    )
    expect(actual=result.exit_status, expected=7)
    expect(actual=result.output, expected="output-line")
    entry: SessionEntry = repository.read()[0]
    expect(actual=entry.agent_state, expected="IDLE")
    expect(actual=entry.current_command_id, expected="")


def test_execute_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Time out when the pane never emits the end marker.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    service: LakeTerminal = _service(tmp_path=tmp_path)
    repository: SessionRepository = SessionRepository(home=tmp_path)
    repository.add(entry=_entry())
    monkeypatch.setattr(
        target=core_module,
        name="run",
        value=_fake_tmux(existing={"shared-aa11bb"}, capture="nothing"),
    )
    _stub_descendants(monkeypatch=monkeypatch)
    monkeypatch.setattr(
        target=core_module.Constants,
        name="exec_timeout_seconds",
        value=0.3,
    )
    monkeypatch.setattr(
        target=core_module.Constants,
        name="exec_poll_seconds",
        value=0.05,
    )
    with pytest.raises(expected_exception=TimeoutError):
        service.execute(session_id="aa11bb", command="sleep")


def test_execute_disappearing_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise KeyError when the backend dies mid-command.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    service: LakeTerminal = _service(tmp_path=tmp_path)
    repository: SessionRepository = SessionRepository(home=tmp_path)
    repository.add(entry=_entry())

    def fake_run(
        *,
        args: tuple[str, ...],
        check: bool = False,
        **_kwargs: object,
    ) -> CompletedProcess[str]:
        """Emulate a tmux session disappearing during execution.

        Args:
            args: Tmux command arguments.
            check: Whether subprocess failure should raise.
            **_kwargs: Ignored subprocess keyword arguments.

        Returns:
            The scripted failed subprocess result.

        Raises:
            CalledProcessError: When a checked pane operation is attempted.

        """
        action: str = args[1] if len(args) > 1 else ""
        if action in {"send-keys", "capture-pane"} and check:
            raise CalledProcessError(returncode=1, cmd=args)
        return CompletedProcess(args=args, returncode=1)

    monkeypatch.setattr(target=core_module, name="run", value=fake_run)
    with pytest.raises(expected_exception=KeyError):
        service.execute(session_id="aa11bb", command="x")


def test_default_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build the default service from the HOME environment.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    service: LakeTerminal = core_module.default_service()
    expect(actual=service.list_entries(), expected=())


@pytest.mark.parametrize(
    ("variable", "agent"),
    [
        ("CLAUDE_CODE_SESSION_ID", "claude-code"),
        ("CLAUDE_SESSION_ID", "claude-code"),
        ("DEVIN_SESSION_ID", "devin"),
        ("CURSOR_SESSION_ID", "cursor"),
        ("CODEX_SESSION_ID", "codex"),
        ("CODEX_THREAD_ID", "codex"),
    ],
)
def test_create_adopts_native_session_variable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    agent: str,
) -> None:
    """Attribute agent sessions from provider variables without LAKE_AGENT*.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.
        variable: Provider-native session variable.
        agent: Expected canonical provider.

    """
    service: LakeTerminal = _service(tmp_path=tmp_path)
    sessions: set[str] = set()
    monkeypatch.setattr(
        target=core_module,
        name="run",
        value=_fake_tmux(existing=sessions, pane_pid="2000000001"),
    )
    _stub_descendants(monkeypatch=monkeypatch)
    monkeypatch.setenv(name=variable, value="native-77")
    expect(actual=service.create(initiator="agent"), expected=0)
    entry: SessionEntry = SessionRepository(home=tmp_path).read()[0]
    expect(actual=entry.agent, expected=agent)
    expect(actual=entry.agent_session, expected="native-77")


def test_create_records_claude_desktop_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report Claude Code Desktop as its own surface.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    service: LakeTerminal = _service(tmp_path=tmp_path)
    monkeypatch.setattr(
        target=core_module,
        name="run",
        value=_fake_tmux(existing=set(), pane_pid="2000000001"),
    )
    _stub_descendants(monkeypatch=monkeypatch)
    monkeypatch.setenv(name="CLAUDECODE", value="1")
    monkeypatch.setenv(name="CLAUDE_CODE_SESSION_ID", value="d-1")
    monkeypatch.setenv(name="CLAUDE_CODE_ENTRYPOINT", value="claude-desktop")
    expect(actual=service.create(initiator="agent"), expected=0)
    entry: SessionEntry = SessionRepository(home=tmp_path).read()[0]
    expect(actual=entry.surface, expected="desktop")


def test_ensure_agent_reuses_session_by_native_variable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reuse the live session that matches the native provider session.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    service: LakeTerminal = _service(tmp_path=tmp_path)
    sessions: set[str] = set()
    monkeypatch.setattr(
        target=core_module,
        name="run",
        value=_fake_tmux(existing=sessions, pane_pid="2000000001"),
    )
    _stub_descendants(monkeypatch=monkeypatch)
    monkeypatch.setenv(name="DEVIN_SESSION_ID", value="d-1")
    expect(actual=service.ensure_agent(), expected=0)
    monkeypatch.setenv(name="DEVIN_SESSION_ID", value="d-2")
    expect(actual=service.ensure_agent(), expected=0)
    found: tuple[SessionEntry, ...] = SessionRepository(home=tmp_path).read()
    expect(
        actual=sorted(entry.agent_session for entry in found),
        expected=["d-1", "d-2"],
    )
