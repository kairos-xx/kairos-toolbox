# Copyright (c) 2026 Project contributors.
"""Cover ConPTY-backed session paths and daemon-control branches."""

from __future__ import annotations

from ctypes import (
    POINTER,
    Array,
    c_char,
    c_void_p,
    wintypes,
)
from ctypes import cast as ctypes_cast
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
from types import SimpleNamespace
from typing import TYPE_CHECKING, ClassVar, Final, cast

import pytest
from district import conpty as conpty_module
from district import core as core_module
from district import (
    daemon_control as daemon_control_module,
)
from district.conpty import ConPtyInfo
from district.core import (
    District,
    ExecResult,
    SessionRepository,
)
from district.daemon_control import DaemonControl
from district.identity import ProcessIdentity
from district.models import SessionEntry
from district.privacy import PrivacyPolicy
from district.runtime import (
    Capabilities,
    RuntimePlatform,
)

from .helpers import check, expect

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from subprocess import Popen

    from _typeshed import Incomplete


def _entry(
    *,
    session_id: str = "aa11bb",
    backend: str = "tmux",
    pid: int = 0,
    process_started_token: str = "",
    initiator: str = "user",
) -> SessionEntry:
    """Build a session row with the fields these tests need.

    Args:
        session_id: Session identifier.
        backend: Backend kind.
        pid: Process identifier.
        process_started_token: Process identity token.
        initiator: Session initiator.

    Returns:
        The resulting value.

    """
    return SessionEntry(
        id=session_id,
        command=f"district {session_id}",
        session=f"shared-{session_id}",
        uuid=f"uuid-{session_id}",
        host_id="h1",
        backend=backend,
        pid=pid,
        process_started_token=process_started_token,
        initiator=initiator,
        created_at="2026-01-01T00:00:00+00:00",
    )


def _runtime(*, backend: str = "tmux") -> RuntimePlatform:
    """Build a runtime descriptor.

    Args:
        backend: Session backend kind.

    Returns:
        Runtime descriptor.

    """
    return RuntimePlatform(
        os="linux",
        architecture="x86_64",
        shell_location="shell",
        backend=backend,
        capabilities=Capabilities(
            persistent_sessions=True,
            read_only_capture=True,
            process_tree=True,
            clipboard=True,
            clickable_copy=True,
            notifications=True,
            editor_agent_bridge=True,
            interactive_reconnect=True,
        ),
    )


def _capture_identity(*, pid: int) -> ProcessIdentity:
    """Return a deterministic process identity.

    Args:
        pid: Process identifier.

    Returns:
        Process identity with a fixed token.

    """
    marker: str = "tok"
    return ProcessIdentity(pid=pid, started_token=marker)


def _raising_identity(*, pid: int) -> ProcessIdentity:
    """Simulate identity capture failing for a dead process.

    Args:
        pid: Process identifier.

    Returns:
        Process identity with a fixed token.

    Raises:
        OSError: Always.

    """
    del pid
    raise OSError


def _no_descendants(**kwargs: object) -> tuple[()]:
    """Return no descendant processes.

    Args:
        **kwargs: Inspector options.

    Returns:
        An empty tuple.

    """
    del kwargs
    return ()


def _ping_true(*, home: Path) -> bool:
    """Report the daemon as running.

    Args:
        home: Home.

    Returns:
        True.

    """
    del home
    return True


def _ping_false(*, home: Path) -> bool:
    """Report the daemon as stopped.

    Args:
        home: Home.

    Returns:
        False.

    """
    del home
    return False


def _write_len(text: str) -> int:
    """Return the length of text written to stdout.

    Args:
        text: Text written.

    Returns:
        Bytes written.

    """
    return len(text)


def _marker_output(
    self: _FakeConPtyClient,
    *,
    session_uuid: str,
) -> str:
    """Return baseline then marker-framed command output.

    Args:
        self: Fake client.
        session_uuid: Session UUID.

    Returns:
        Framed output.

    """
    del session_uuid
    if not self.writes:
        return "base"
    return "base\n__DISTRICT_START_tok__\nline-out\n__DISTRICT_END_tok__:0\n"


class _FakeConPtyClient:
    """Deterministic stand-in for the daemon-side ConPTY client.

    Attributes:
        instances: List of all created instances.
        alive_result: Result to return from alive().

    """

    instances: ClassVar[list[_FakeConPtyClient]] = []
    alive_result: ClassVar[bool | Exception] = True

    def __init__(self, *, home: Path) -> None:
        """Record construction.

        Args:
            home: Home.

        """
        del home
        self.outputs: list[str] = []
        self.writes: list[str] = []
        self.attach_result: int = 5
        _FakeConPtyClient.instances.append(self)

    @classmethod
    def alive(cls, *, session_uuid: str) -> bool:
        """Return the configured liveness.

        Args:
            session_uuid: Session UUID.

        Returns:
            Whether the session is alive.

        Raises:
            Exception: Configured failure.

        """
        del session_uuid
        if isinstance(cls.alive_result, Exception):
            raise cls.alive_result
        return cls.alive_result

    @staticmethod
    def create(
        *,
        session_uuid: str,
        cwd: str,
        command: tuple[str, ...],
    ) -> ConPtyInfo:
        """Return deterministic metadata.

        Args:
            session_uuid: Session UUID.
            cwd: Working directory.
            command: Initial command.

        Returns:
            Pseudo-console metadata.

        """
        del cwd, command
        return ConPtyInfo(session_uuid=session_uuid, pid=9, alive=True)

    @staticmethod
    def kill(*, session_uuid: str) -> bool:
        """Record a kill request.

        Args:
            session_uuid: Session UUID.

        Returns:
            Whether the session ended.

        """
        del session_uuid
        return True

    def attach(self, *, session_uuid: str) -> int:
        """Return the configured attach result.

        Args:
            session_uuid: Session UUID.

        Returns:
            Backend exit status.

        """
        del session_uuid
        return self.attach_result

    def output(self, *, session_uuid: str) -> str:
        """Return queued console output.

        Args:
            session_uuid: Session UUID.

        Returns:
            Buffered output.

        """
        del session_uuid
        if len(self.outputs) > 1:
            return self.outputs.pop(0)
        return self.outputs[0] if self.outputs else ""

    def write(self, *, session_uuid: str, text: str) -> int:
        """Record written input.

        Args:
            session_uuid: Session UUID.
            text: Input text.

        Returns:
            Bytes written.

        """
        del session_uuid
        self.writes.append(text)
        return len(text)


def _patch_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    backend: str = "tmux",
) -> None:
    """Point platform detection and identity capture at deterministic fakes.

    Args:
        monkeypatch: Monkeypatch.
        backend: Session backend kind.

    """
    monkeypatch.setattr(
        target=core_module.RuntimePlatform,
        name="detect",
        value=staticmethod(lambda: _runtime(backend=backend)),
    )
    monkeypatch.setattr(
        target=core_module.RuntimePlatform,
        name="surface",
        value=staticmethod(lambda: "cli"),
    )
    monkeypatch.setattr(
        target=core_module.RuntimePlatform,
        name="editor",
        value=staticmethod(lambda: ""),
    )
    monkeypatch.setattr(
        target=core_module.ProcessIdentity,
        name="capture",
        value=staticmethod(_capture_identity),
    )
    monkeypatch.setattr(
        target=core_module.ProcessInspector,
        name="descendants",
        value=_no_descendants,
    )


def test_daemon_control_start_and_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise daemon start, pid-file writing, and platform spawn branches.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Monkeypatch.

    """
    monkeypatch.setattr(
        target=DaemonControl,
        name="ping",
        value=staticmethod(_ping_true),
    )
    expect(actual=DaemonControl.start(home=tmp_path), expected=True)
    calls: list[dict[str, object]] = []

    def fake_popen(**kwargs: object) -> Popen[bytes]:
        """Record spawn arguments.

        Args:
            **kwargs: Popen options.

        Returns:
            Fake process.

        """
        calls.append(kwargs)
        return cast(typ="Popen[bytes]", val=SimpleNamespace(pid=4321))

    monkeypatch.setattr(
        target=daemon_control_module,
        name="Popen",
        value=fake_popen,
    )
    monkeypatch.setattr(
        target=DaemonControl,
        name="ping",
        value=staticmethod(_ping_false),
    )
    _ = (tmp_path / ".district").mkdir()
    expect(actual=DaemonControl.start(home=tmp_path), expected=True)
    pid_path: Path = tmp_path / ".district" / "daemon.pid"
    expect(
        actual=pid_path.read_text(encoding="ascii").strip(),
        expected="4321",
    )
    _ = DaemonControl.spawn()
    expect(actual=calls[-1]["start_new_session"], expected=True)
    monkeypatch.setattr(
        target=daemon_control_module,
        name="os_name",
        value="nt",
    )
    _ = DaemonControl.spawn()
    check(condition="creationflags" in calls[-1], reason="windows spawn")


def test_new_id_retries_collisions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the reserved-identifier retry loop.

    Args:
        monkeypatch: Monkeypatch.

    """
    entries: tuple[SessionEntry, ...] = (
        SessionEntry(id="dup", command="shared dup", session="shared-dup"),
    )
    values: Iterator[str] = iter(("dup", "fresh"))

    def fake_token(*, nbytes: int) -> str:
        """Return the next reserved identifier.

        Args:
            nbytes: Requested entropy.

        Returns:
            Next candidate id.

        """
        del nbytes
        return next(values)

    monkeypatch.setattr(
        target=core_module,
        name="token_hex",
        value=fake_token,
    )
    expect(actual=District.new_id(entries=entries), expected="fresh")


def test_create_dispatches_conpty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route session creation through the ConPTY backend when detected.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Monkeypatch.

    """
    _patch_runtime(monkeypatch=monkeypatch, backend="conpty")
    service: District = District(
        repository=SessionRepository(home=tmp_path),
    )

    def fake_create_conpty(**kwargs: object) -> int:
        """Return a fixed ConPTY attach status.

        Args:
            **kwargs: Creation options.

        Returns:
            Fixed exit status.

        """
        del kwargs
        return 7

    monkeypatch.setattr(
        target=service,
        name="create_conpty",
        value=fake_create_conpty,
    )
    expect(actual=service.create(), expected=7)


def test_create_tmux_failure_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise tmux creation retry, error, and exhaustion branches.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Monkeypatch.

    """
    _patch_runtime(monkeypatch=monkeypatch)
    service: District = District(
        repository=SessionRepository(home=tmp_path),
    )
    monkeypatch.setattr(target=service, name="reconcile", value=lambda: ())

    def fail_run(
        *,
        args: tuple[str, ...],
        **kwargs: object,
    ) -> CompletedProcess[str]:
        """Return a failed tmux invocation.

        Args:
            args: Command arguments.
            **kwargs: Other subprocess options.

        Returns:
            Failed process result.

        """
        del kwargs
        return CompletedProcess(args=args, returncode=1, stdout="", stderr="")

    monkeypatch.setattr(target=core_module, name="run", value=fail_run)

    def missing(*, session: str) -> bool:
        """Report every session as absent.

        Args:
            session: Session name.

        Returns:
            False.

        """
        del session
        return False

    def present(*, session: str) -> bool:
        """Report every session as present.

        Args:
            session: Session name.

        Returns:
            True.

        """
        del session
        return True

    monkeypatch.setattr(
        target=service,
        name="exists",
        value=missing,
    )
    with pytest.raises(expected_exception=CalledProcessError):
        _ = service.create()
    monkeypatch.setattr(
        target=service,
        name="exists",
        value=present,
    )
    with pytest.raises(
        expected_exception=RuntimeError,
        match="unique district session id",
    ):
        _ = service.create()


def test_create_resolves_agent_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apply persisted agent identity during agent-initiated creation.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Monkeypatch.

    """
    _patch_runtime(monkeypatch=monkeypatch)
    SessionRepository(home=tmp_path).database.set_agent_context(
        provider="claude",
        session_id="sess-1",
        workspace=str(object=Path.cwd()),
    )
    service: District = District(
        repository=SessionRepository(home=tmp_path),
    )
    monkeypatch.setattr(target=service, name="reconcile", value=lambda: ())

    def fixed_id(*, entries: tuple[SessionEntry, ...]) -> str:
        """Return a fixed session id.

        Args:
            entries: Existing sessions.

        Returns:
            Fixed id.

        """
        del entries
        return "abc123"

    monkeypatch.setattr(
        target=service,
        name="new_id",
        value=fixed_id,
    )

    def fixed_pane_pid(*, session: str) -> int:
        """Return a fixed pane pid.

        Args:
            session: Session name.

        Returns:
            Fixed pid.

        """
        del session
        return 123

    monkeypatch.setattr(
        target=service,
        name="pane_pid",
        value=fixed_pane_pid,
    )
    monkeypatch.delenv(name="DISTRICT_AGENT", raising=False)
    monkeypatch.delenv(name="DISTRICT_AGENT_SESSION", raising=False)
    monkeypatch.delenv(name="DISTRICT_WORKSPACE", raising=False)
    monkeypatch.delenv(name="DISTRICT_ATTRIBUTION", raising=False)

    def ok_run(
        *,
        args: tuple[str, ...],
        **kwargs: object,
    ) -> CompletedProcess[str]:
        """Return successful tmux operations.

        Args:
            args: Command arguments.
            **kwargs: Other subprocess options.

        Returns:
            Successful process result.

        """
        del kwargs
        return CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(target=core_module, name="run", value=ok_run)
    expect(actual=service.create(initiator="agent"), expected=0)
    entry: SessionEntry = service.repository.read()[0]
    expect(actual=entry.agent, expected="claude")
    expect(actual=entry.agent_session, expected="sess-1")


def test_ensure_agent_conpty_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attach through the ConPTY client for an existing agent session.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Monkeypatch.

    """
    entry: SessionEntry = _entry(backend="conpty", initiator="agent")
    repository: SessionRepository = SessionRepository(home=tmp_path)
    repository.add(entry=entry)
    service: District = District(repository=repository)
    monkeypatch.setattr(
        target=service,
        name="reconcile",
        value=lambda: (entry,),
    )

    def ok_execute(**kwargs: object) -> ExecResult:
        """Return a successful command result.

        Args:
            **kwargs: Execute options.

        Returns:
            Successful result.

        """
        del kwargs
        return ExecResult(output="o", exit_status=0)

    monkeypatch.setattr(
        target=service,
        name="execute",
        value=ok_execute,
    )
    monkeypatch.delenv(name="DISTRICT_AGENT", raising=False)
    _FakeConPtyClient.instances.clear()
    monkeypatch.setattr(
        target=core_module,
        name="ConPtyClient",
        value=_FakeConPtyClient,
    )
    expect(
        actual=service.ensure_agent(initial_command=("echo", "hi")),
        expected=5,
    )
    check(
        condition=len(_FakeConPtyClient.instances) == 1,
        reason="client created",
    )


def test_attach_and_kill_conpty_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route attach and kill through the ConPTY client.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Monkeypatch.

    """
    repository: SessionRepository = SessionRepository(home=tmp_path)
    repository.add(entry=_entry(backend="conpty"))
    service: District = District(repository=repository)

    def require_first(*, session_id: str) -> SessionEntry:
        """Return the first stored session entry.

        Args:
            session_id: Requested session identifier.

        Returns:
            First entry.

        """
        del session_id
        return repository.read()[0]

    monkeypatch.setattr(
        target=service,
        name="_require_entry",
        value=require_first,
    )
    _FakeConPtyClient.instances.clear()
    monkeypatch.setattr(
        target=core_module,
        name="ConPtyClient",
        value=_FakeConPtyClient,
    )
    expect(actual=service.attach(session_id="aa11bb"), expected=5)
    service.kill(session_id="aa11bb")
    expect(actual=repository.read(), expected=())


def test_execute_conpty_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capture a ConPTY command through the marker protocol.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Monkeypatch.

    """
    repository: SessionRepository = SessionRepository(home=tmp_path)
    repository.add(entry=_entry(backend="conpty"))
    service: District = District(repository=repository)
    _FakeConPtyClient.instances.clear()
    monkeypatch.setattr(
        target=core_module,
        name="ConPtyClient",
        value=_FakeConPtyClient,
    )

    def fixed_token(*, nbytes: int) -> str:
        """Return a fixed marker token.

        Args:
            nbytes: Requested entropy.

        Returns:
            Fixed token.

        """
        del nbytes
        return "tok"

    monkeypatch.setattr(
        target=core_module,
        name="token_hex",
        value=fixed_token,
    )
    monkeypatch.setattr(
        target=_FakeConPtyClient,
        name="output",
        value=_marker_output,
    )
    result: ExecResult = service.execute(
        session_id="aa11bb",
        command="echo hi",
    )
    expect(actual=result.output, expected="line-out")
    expect(actual=result.exit_status, expected=0)
    idle: SessionEntry = repository.read()[0]
    expect(actual=idle.agent_state, expected="IDLE")


def test_execute_conpty_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise a timeout when the ConPTY end marker never appears.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Monkeypatch.

    """
    repository: SessionRepository = SessionRepository(home=tmp_path)
    repository.add(entry=_entry(backend="conpty"))
    service: District = District(repository=repository)
    monkeypatch.setattr(
        target=core_module,
        name="ConPtyClient",
        value=_FakeConPtyClient,
    )
    monkeypatch.setattr(
        target=core_module.Constants,
        name="exec_timeout_seconds",
        value=-1.0,
    )
    with pytest.raises(expected_exception=TimeoutError, match="timed out"):
        _ = service.execute(session_id="aa11bb", command="sleep")


def test_execute_tmux_called_process_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Propagate or remap subprocess failures during tmux execution.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Monkeypatch.

    """
    repository: SessionRepository = SessionRepository(home=tmp_path)
    repository.add(entry=_entry())
    service: District = District(repository=repository)

    def raising_run(
        *,
        args: tuple[str, ...],
        **kwargs: object,
    ) -> CompletedProcess[str]:
        """Raise a subprocess failure.

        Args:
            args: Command arguments.
            **kwargs: Other subprocess options.

        Raises:
            CalledProcessError: Always.

        Returns:
            Never returns.

        """
        del args, kwargs
        raise CalledProcessError(returncode=1, cmd="tmux")

    monkeypatch.setattr(target=core_module, name="run", value=raising_run)

    def present(*, session: str) -> bool:
        """Report every session as present.

        Args:
            session: Session name.

        Returns:
            True.

        """
        del session
        return True

    def missing(*, session: str) -> bool:
        """Report every session as absent.

        Args:
            session: Session name.

        Returns:
            False.

        """
        del session
        return False

    monkeypatch.setattr(
        target=service,
        name="exists",
        value=present,
    )
    with pytest.raises(expected_exception=CalledProcessError):
        _ = service.execute(session_id="aa11bb", command="ls")
    monkeypatch.setattr(
        target=service,
        name="exists",
        value=missing,
    )
    with pytest.raises(expected_exception=KeyError):
        _ = service.execute(session_id="aa11bb", command="ls")


def test_create_conpty_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise ConPTY session creation and its daemon-start fallback.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Monkeypatch.

    """
    _patch_runtime(monkeypatch=monkeypatch, backend="conpty")
    service: District = District(
        repository=SessionRepository(home=tmp_path),
    )
    monkeypatch.setattr(
        target=core_module.DaemonControl,
        name="start",
        value=staticmethod(_ping_false),
    )
    expect(
        actual=service.create_conpty(
            initial_command=(),
            initiator="user",
        ),
        expected=127,
    )
    monkeypatch.setattr(
        target=core_module.DaemonControl,
        name="start",
        value=staticmethod(_ping_true),
    )
    _FakeConPtyClient.instances.clear()
    monkeypatch.setattr(
        target=core_module,
        name="ConPtyClient",
        value=_FakeConPtyClient,
    )
    executed: list[str] = []

    def recording_execute(**kwargs: object) -> ExecResult:
        """Record each command sent to the session.

        Args:
            **kwargs: Execute options.

        Returns:
            Successful result.

        """
        executed.append(str(object=kwargs["command"]))
        return ExecResult(output="", exit_status=0)

    monkeypatch.setattr(
        target=service,
        name="execute",
        value=recording_execute,
    )
    monkeypatch.setattr(
        target=core_module,
        name="stdout",
        value=SimpleNamespace(write=_write_len),
    )
    expect(
        actual=service.create_conpty(
            initial_command=("echo", "hi"),
            initiator="user",
        ),
        expected=5,
    )
    entry: SessionEntry = service.repository.read()[0]
    expect(actual=entry.backend, expected="conpty")
    check(condition=bool(executed), reason="initial command ran")


def test_pane_pid_and_shell_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise pane pid and shell-detection edge branches.

    Args:
        monkeypatch: Monkeypatch.

    """

    def ok_run(
        *,
        args: tuple[str, ...],
        **kwargs: object,
    ) -> CompletedProcess[str]:
        """Return a numeric pane pid.

        Args:
            args: Command arguments.
            **kwargs: Other subprocess options.

        Returns:
            Process result with a pid.

        """
        del kwargs
        return CompletedProcess(
            args=args,
            returncode=0,
            stdout="42\n",
            stderr="",
        )

    monkeypatch.setattr(target=core_module, name="run", value=ok_run)
    expect(actual=District.pane_pid(session="s"), expected=42)

    def type_run(
        *,
        args: tuple[str, ...],
        **kwargs: object,
    ) -> CompletedProcess[str]:
        """Raise a typing error.

        Args:
            args: Command arguments.
            **kwargs: Other subprocess options.

        Raises:
            TypeError: Always.

        Returns:
            Never returns.

        """
        del args, kwargs
        raise TypeError

    monkeypatch.setattr(target=core_module, name="run", value=type_run)
    expect(actual=District.pane_pid(session="s"), expected=0)

    def bad_run(
        *,
        args: tuple[str, ...],
        **kwargs: object,
    ) -> CompletedProcess[str]:
        """Return a failed pane query.

        Args:
            args: Command arguments.
            **kwargs: Other subprocess options.

        Returns:
            Failed process result.

        """
        del kwargs
        return CompletedProcess(args=args, returncode=1, stdout="", stderr="")

    monkeypatch.setattr(target=core_module, name="run", value=bad_run)
    expect(actual=District.pane_is_shell(session="s"), expected=False)

    def text_run(
        *,
        args: tuple[str, ...],
        **kwargs: object,
    ) -> CompletedProcess[str]:
        """Return the active pane command.

        Args:
            args: Command arguments.
            **kwargs: Other subprocess options.

        Returns:
            Process result with a shell name.

        """
        del kwargs
        return CompletedProcess(
            args=args,
            returncode=0,
            stdout="zsh\n",
            stderr="",
        )

    monkeypatch.setattr(target=core_module, name="run", value=text_run)
    expect(actual=District.pane_is_shell(session="s"), expected=True)


def test_helper_failure_paths() -> None:
    """Exercise the assertion-helper failure branches."""
    with pytest.raises(expected_exception=AssertionError, match="expected 2"):
        expect(actual=1, expected=2)
    with pytest.raises(expected_exception=AssertionError, match="boom"):
        check(condition=False, reason="boom")


def test_parse_exec_branches() -> None:
    """Exercise marker parsing for malformed, missing, and valid output."""
    captured: str = (
        "noise\n"
        "__DISTRICT_END_x__:notanint\n"
        "__DISTRICT_START_x__\n"
        "value\n"
        "__DISTRICT_END_x__:0\n"
    )
    expect(
        actual=District.parse_exec(
            captured=captured,
            start="__DISTRICT_START_x__",
            end="__DISTRICT_END_x__",
        ),
        expected=ExecResult(output="value", exit_status=0),
    )
    expect(
        actual=District.parse_exec(
            captured="nothing here",
            start="__DISTRICT_START_x__",
            end="__DISTRICT_END_x__",
        ),
        expected=None,
    )
    expect(
        actual=District.parse_exec(
            captured="__DISTRICT_END_x__:0\n",
            start="__DISTRICT_START_x__",
            end="__DISTRICT_END_x__",
        ),
        expected=None,
    )


def test_entry_alive_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise backend-specific liveness checks.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Monkeypatch.

    """
    service: District = District(
        repository=SessionRepository(home=tmp_path),
    )

    def live_only(*, session: str) -> bool:
        """Report only the named session as present.

        Args:
            session: Session name.

        Returns:
            Whether the session is live.

        """
        return session == "live"

    monkeypatch.setattr(
        target=service,
        name="exists",
        value=live_only,
    )
    expect(
        actual=service.entry_alive(entry=_entry()),
        expected=False,
    )
    _FakeConPtyClient.instances.clear()
    monkeypatch.setattr(
        target=core_module,
        name="ConPtyClient",
        value=_FakeConPtyClient,
    )
    expect(
        actual=service.entry_alive(entry=_entry(backend="conpty")),
        expected=True,
    )
    _FakeConPtyClient.alive_result = OSError("gone")
    expect(
        actual=service.entry_alive(entry=_entry(backend="conpty")),
        expected=False,
    )
    _FakeConPtyClient.alive_result = True
    expect(
        actual=service.entry_alive(entry=_entry(backend="process", pid=0)),
        expected=False,
    )
    monkeypatch.setattr(
        target=core_module.ProcessIdentity,
        name="capture",
        value=staticmethod(_raising_identity),
    )
    expect(
        actual=service.entry_alive(entry=_entry(backend="process", pid=9)),
        expected=False,
    )
    monkeypatch.setattr(
        target=core_module.ProcessIdentity,
        name="capture",
        value=staticmethod(_capture_identity),
    )
    expect(
        actual=service.entry_alive(
            entry=_entry(backend="process", pid=9),
        ),
        expected=True,
    )
    marker: str = "other"
    expect(
        actual=service.entry_alive(
            entry=_entry(
                backend="process",
                pid=9,
                process_started_token=marker,
            ),
        ),
        expected=False,
    )


class NativeConstants:
    """Constants for the fake-kernel tests.

    Attributes:
        err_code: Fake last-error code.
        marker: Sentinel marker value.
        attr_list_size: Fake attribute-list size.
        pseudo_handle: Fake pseudo-console handle value.
        wait_timeout: Windows wait-timeout result.
        resize_error: Fake resize failure code.
        write_len: Fake write length.
        two: Two handles closed.
        four: Four handles closed.
        fake_pid: Fake process id.
        child_pid: Fake child pid.
        hr_fail: Fake failing HRESULT.
        three: Three written bytes.
        spawn_flag: Pseudo-console flag for spawn fakes.

    """

    err_code: Final[int] = 5
    marker: Final[int] = 42
    attr_list_size: Final[int] = 64
    pseudo_handle: Final[int] = 99
    wait_timeout: Final[int] = 0x00000102
    resize_error: Final[int] = 7
    write_len: Final[int] = 3
    two: Final[int] = 2
    four: Final[int] = 4
    fake_pid: Final[int] = 55
    child_pid: Final[int] = 77
    hr_fail: Final[int] = -5
    three: Final[int] = 3
    spawn_flag: Final[int] = 0


def _set_dword(ptr: object, value: int) -> None:
    """Write a DWORD through a byref pointer.

    Args:
        ptr: byref CArgObject.
        value: Value to store.

    """
    ctypes_cast(
        obj=cast(typ="int", val=ptr),
        typ=POINTER(wintypes.DWORD),
    ).contents.value = value


class _KernelFake:
    """Scripted kernel32 stand-in for WindowsConPty branches.

    Attributes:
        calls: Recorded native function names.
        pipes_fail_at: Which CreatePipe call returns failure (0 = none).
        pseudo_hr: HRESULT returned by CreatePseudoConsole.
        init_fails: InitializeProcThreadAttributeList always fails.
        update_fails: UpdateProcThreadAttribute fails.
        create_fails: CreateProcessW fails.
        read_payload: Bytes returned by ReadFile.
        write_ok: WriteFile succeeds.
        resize_hr: ResizePseudoConsole result.
        wait_result: WaitForSingleObject result.
        terminated: TerminateProcess invoked.
        closed_handles: CloseHandle invocations.
        hpc_closed: ClosePseudoConsole invoked.

    """

    def __init__(self) -> None:
        """Initialize success defaults and bind native entry points."""
        self.calls: list[str] = []
        self.pipes_fail_at: int = 0
        self.pseudo_hr: int = -1
        self.init_fails: bool = False
        self.update_fails: bool = False
        self.create_fails: bool = False
        self.read_payload: bytes = b"out"
        self.write_ok: bool = True
        self.resize_hr: int = 0
        self.wait_result: int = NativeConstants.wait_timeout
        self.terminated: bool = False
        self.closed_handles: int = 0
        self.hpc_closed: bool = False
        self._pipe_calls: int = 0
        self.CreatePipe: Callable[..., int] = self._create_pipe
        self.CloseHandle: Callable[..., int] = self._close_handle
        self.CreatePseudoConsole: Callable[..., int] = self._create_pseudo
        self.ClosePseudoConsole: Callable[..., int] = self._close_pseudo
        self.InitializeProcThreadAttributeList: Callable[..., int] = (
            self._init_attrs
        )
        self.UpdateProcThreadAttribute: Callable[..., int] = self._update_attrs
        self.DeleteProcThreadAttributeList: Callable[..., int] = (
            self._delete_attrs
        )
        self.CreateProcessW: Callable[..., int] = self._create_process
        self.ReadFile: Callable[..., int] = self._read_file
        self.WriteFile: Callable[..., int] = self._write_file
        self.ResizePseudoConsole: Callable[..., int] = self._resize
        self.WaitForSingleObject: Callable[..., int] = self._wait
        self.TerminateProcess: Callable[..., int] = self._terminate

    def _create_pipe(self, *args: object) -> int:
        """Scripted CreatePipe.

        Args:
            *args: Native arguments.

        Returns:
            Success flag.

        """
        del args
        self.calls.append("CreatePipe")
        self._pipe_calls += 1
        return 0 if self._pipe_calls == self.pipes_fail_at else 1

    def _close_handle(self, *args: object) -> int:
        """Record handle closures.

        Args:
            *args: Native arguments.

        Returns:
            Success.

        """
        del args
        self.calls.append("CloseHandle")
        self.closed_handles += 1
        return 1

    def _create_pseudo(self, *args: object) -> int:
        """Scripted CreatePseudoConsole.

        Args:
            *args: Native arguments.

        Returns:
            HRESULT.

        """
        self.calls.append("CreatePseudoConsole")
        if self.pseudo_hr == 0:
            ctypes_cast(
                obj=cast(typ="int", val=args[4]),
                typ=POINTER(c_void_p),
            ).contents.value = NativeConstants.pseudo_handle
        return self.pseudo_hr

    def _close_pseudo(self, *args: object) -> int:
        """Record pseudo-console closure.

        Args:
            *args: Native arguments.

        Returns:
            Zero.

        """
        del args
        self.calls.append("ClosePseudoConsole")
        self.hpc_closed = True
        return 0

    def _init_attrs(self, *args: object) -> int:
        """Scripted attribute-list init (size query then fill).

        Args:
            *args: Native arguments.

        Returns:
            Success flag.

        """
        self.calls.append("InitializeProcThreadAttributeList")
        if self.init_fails:
            return 0
        if args[0] is None:
            _set_dword(ptr=args[3], value=NativeConstants.attr_list_size)
        return 1

    def _update_attrs(self, *args: object) -> int:
        """Scripted attribute update.

        Args:
            *args: Native arguments.

        Returns:
            Success flag.

        """
        del args
        self.calls.append("UpdateProcThreadAttribute")
        return 0 if self.update_fails else 1

    def _delete_attrs(self, *args: object) -> int:
        """Record attribute-list deletion.

        Args:
            *args: Native arguments.

        Returns:
            Zero.

        """
        del args
        self.calls.append("DeleteProcThreadAttributeList")
        return 0

    def _create_process(self, *args: object) -> int:
        """Scripted process creation.

        Args:
            *args: Native arguments.

        Returns:
            Success flag.

        """
        del args
        self.calls.append("CreateProcessW")
        return 0 if self.create_fails else 1

    def _read_file(self, *args: object) -> int:
        """Scripted pipe read writing into the caller buffer.

        Args:
            *args: Native arguments.

        Returns:
            Success flag.

        """
        self.calls.append("ReadFile")
        if not self.read_payload:
            _set_dword(ptr=args[3], value=0)
            return 1
        buffer: Array[c_char] = cast(typ="Array[c_char]", val=args[1])
        data: bytes = self.read_payload
        for i in range(len(data)):
            buffer[i] = data[i : i + 1]
        _set_dword(ptr=args[3], value=len(data))
        return 1

    def _write_file(self, *args: object) -> int:
        """Scripted pipe write.

        Args:
            *args: Native arguments.

        Returns:
            Success flag.

        """
        self.calls.append("WriteFile")
        if not self.write_ok:
            return 0
        size: object = args[2]
        if not isinstance(size, int):
            return 0
        _set_dword(ptr=args[3], value=size)
        return 1

    def _resize(self, *args: object) -> int:
        """Scripted console resize.

        Args:
            *args: Native arguments.

        Returns:
            HRESULT.

        """
        del args
        self.calls.append("ResizePseudoConsole")
        return self.resize_hr

    def _wait(self, *args: object) -> int:
        """Scripted process wait.

        Args:
            *args: Native arguments.

        Returns:
            Wait result.

        """
        del args
        self.calls.append("WaitForSingleObject")
        return self.wait_result

    def _terminate(self, *args: object) -> int:
        """Record process termination.

        Args:
            *args: Native arguments.

        Returns:
            Success.

        """
        del args
        self.calls.append("TerminateProcess")
        self.terminated = True
        return 1


class _KernelMissingSymbol:
    """kernel32 stand-in missing a required ConPTY symbol."""

    def __init__(self) -> None:
        """Bind the single available symbol."""
        self.CreatePseudoConsole: Callable[..., int] = self._pseudo

    @staticmethod
    def _pseudo(*args: object) -> int:
        """Only present symbol.

        Args:
            *args: Native arguments.

        Returns:
            Zero.

        """
        del args
        return 0


def _conpty_instance() -> conpty_module.WindowsConPty:
    """Build a WindowsConPty with deterministic fake handles.

    Returns:
        Pseudo-console instance with dummy handles.

    """
    return conpty_module.WindowsConPty(
        hpc=NativeConstants.marker,
        input_write=NativeConstants.two,
        output_read=NativeConstants.three,
        process_handle=NativeConstants.four,
        pid=NativeConstants.fake_pid,
    )


def _ok_factory(kernel: _KernelFake) -> Callable[..., _KernelFake]:
    """Return a factory producing the scripted kernel.

    Args:
        kernel: Scripted fake kernel.

    Returns:
        WinDLL-shaped factory.

    """

    def factory(*, name: str, use_last_error: bool) -> _KernelFake:
        """Return the scripted kernel.

        Args:
            name: Requested DLL name.
            use_last_error: Preserve last-error flag.

        Returns:
            Fake kernel.

        """
        del name, use_last_error
        return kernel

    return factory


def _capabilities_available() -> conpty_module.ConPtyCapabilities:
    """Report ConPTY as available.

    Returns:
        Available capability flag.

    """
    return conpty_module.ConPtyCapabilities(available=True)


def _capabilities_unavailable() -> conpty_module.ConPtyCapabilities:
    """Report ConPTY as unavailable.

    Returns:
        Unavailable capability flag.

    """
    return conpty_module.ConPtyCapabilities(available=False)


def _patch_kernel(
    monkeypatch: pytest.MonkeyPatch,
    kernel: _KernelFake,
) -> None:
    """Point WindowsConPty at a scripted fake kernel.

    Args:
        monkeypatch: Monkeypatch.
        kernel: Scripted fake kernel.

    """
    monkeypatch.setitem(
        dic=conpty_module.ctypes_symbols,
        name="WinDLL",
        value=_ok_factory(kernel=kernel),
    )
    monkeypatch.setattr(
        target=conpty_module.WindowsConPty,
        name="capabilities",
        value=staticmethod(_capabilities_available),
    )


def test_ctypes_symbol_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise last_error and win_dll_factory fallback branches.

    Args:
        monkeypatch: Monkeypatch.

    """
    monkeypatch.delitem(
        dic=conpty_module.ctypes_symbols,
        name="get_last_error",
        raising=False,
    )
    expect(actual=conpty_module.last_error(), expected=0)
    monkeypatch.setitem(
        dic=conpty_module.ctypes_symbols,
        name="get_last_error",
        value=lambda: NativeConstants.err_code,
    )
    expect(
        actual=conpty_module.last_error(),
        expected=NativeConstants.err_code,
    )
    monkeypatch.setitem(
        dic=conpty_module.ctypes_symbols,
        name="get_last_error",
        value=lambda: "notint",
    )
    expect(actual=conpty_module.last_error(), expected=0)
    monkeypatch.delitem(
        dic=conpty_module.ctypes_symbols,
        name="WinDLL",
        raising=False,
    )
    expect(actual=conpty_module.win_dll_factory(), expected=None)
    monkeypatch.setitem(
        dic=conpty_module.ctypes_symbols,
        name="WinDLL",
        value=NativeConstants.marker,
    )
    expect(actual=conpty_module.win_dll_factory(), expected=None)
    sentinel: object = object()

    def sentinel_factory(*, name: str, use_last_error: bool) -> object:
        """Return the sentinel.

        Args:
            name: Requested DLL name.
            use_last_error: Preserve last-error flag.

        Returns:
            Sentinel.

        """
        del name, use_last_error
        return sentinel

    monkeypatch.setitem(
        dic=conpty_module.ctypes_symbols,
        name="WinDLL",
        value=sentinel_factory,
    )
    factory: Callable[..., object] | None = conpty_module.win_dll_factory()
    check(condition=callable(factory), reason="factory callable")
    if factory is None:
        return
    expect(
        actual=factory(name="kernel32", use_last_error=True),
        expected=sentinel,
    )


def test_conpty_capabilities_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise capabilities detection on nt and non-nt hosts.

    Args:
        monkeypatch: Monkeypatch.

    """
    monkeypatch.setattr(target=conpty_module, name="os_name", value="posix")
    expect(
        actual=conpty_module.WindowsConPty.capabilities().reason,
        expected="not-windows",
    )
    monkeypatch.setattr(target=conpty_module, name="os_name", value="nt")
    monkeypatch.delitem(
        dic=conpty_module.ctypes_symbols,
        name="WinDLL",
        raising=False,
    )
    expect(
        actual=conpty_module.WindowsConPty.capabilities().reason,
        expected="conpty-api-unavailable",
    )

    def missing_factory(
        *,
        name: str,
        use_last_error: bool,
    ) -> _KernelMissingSymbol:
        """Return a kernel missing ConPTY symbols.

        Args:
            name: Requested DLL name.
            use_last_error: Preserve last-error flag.

        Returns:
            Incomplete fake kernel.

        """
        del name, use_last_error
        return _KernelMissingSymbol()

    monkeypatch.setitem(
        dic=conpty_module.ctypes_symbols,
        name="WinDLL",
        value=missing_factory,
    )
    expect(
        actual=conpty_module.WindowsConPty.capabilities().reason,
        expected="conpty-api-unavailable",
    )
    kernel: _KernelFake = _KernelFake()
    monkeypatch.setitem(
        dic=conpty_module.ctypes_symbols,
        name="WinDLL",
        value=_ok_factory(kernel=kernel),
    )
    expect(
        actual=conpty_module.WindowsConPty.capabilities().available,
        expected=True,
    )


def test_conpty_kernel_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise kernel32 loader unavailable and success branches.

    Args:
        monkeypatch: Monkeypatch.

    """
    monkeypatch.delitem(
        dic=conpty_module.ctypes_symbols,
        name="WinDLL",
        raising=False,
    )
    with pytest.raises(expected_exception=OSError, match="DLL loading"):
        _ = conpty_module.WindowsConPty.kernel32()
    sentinel: object = object()

    def sentinel_factory(*, name: str, use_last_error: bool) -> object:
        """Return the sentinel.

        Args:
            name: Requested DLL name.
            use_last_error: Preserve last-error flag.

        Returns:
            Sentinel.

        """
        del name, use_last_error
        return sentinel

    monkeypatch.setitem(
        dic=conpty_module.ctypes_symbols,
        name="WinDLL",
        value=sentinel_factory,
    )
    expect(
        actual=conpty_module.WindowsConPty.kernel32(),
        expected=sentinel,
    )


def test_conpty_spawn_failure_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise every early-return inside WindowsConPty.spawn.

    Args:
        monkeypatch: Monkeypatch.

    """
    monkeypatch.setattr(
        target=conpty_module.WindowsConPty,
        name="capabilities",
        value=staticmethod(_capabilities_unavailable),
    )
    with pytest.raises(expected_exception=OSError, match="not available"):
        _ = conpty_module.WindowsConPty.spawn(command=("cmd",), cwd=".")
    kernel: _KernelFake = _KernelFake()
    kernel.pipes_fail_at = 1
    _patch_kernel(monkeypatch=monkeypatch, kernel=kernel)
    with pytest.raises(expected_exception=OSError, match="CreatePipe"):
        _ = conpty_module.WindowsConPty.spawn(command=("cmd",), cwd=".")
    kernel = _KernelFake()
    kernel.pipes_fail_at = NativeConstants.two
    _patch_kernel(monkeypatch=monkeypatch, kernel=kernel)
    with pytest.raises(expected_exception=OSError, match="CreatePipe"):
        _ = conpty_module.WindowsConPty.spawn(command=("cmd",), cwd=".")
    expect(actual=kernel.closed_handles, expected=NativeConstants.two)
    kernel = _KernelFake()
    kernel.pseudo_hr = NativeConstants.hr_fail
    _patch_kernel(monkeypatch=monkeypatch, kernel=kernel)
    with pytest.raises(
        expected_exception=OSError,
        match="CreatePseudoConsole",
    ):
        _ = conpty_module.WindowsConPty.spawn(command=("cmd",), cwd=".")
    expect(actual=kernel.closed_handles, expected=NativeConstants.four)
    kernel = _KernelFake()
    kernel.pseudo_hr = 0
    kernel.init_fails = True
    _patch_kernel(monkeypatch=monkeypatch, kernel=kernel)
    with pytest.raises(
        expected_exception=OSError,
        match="InitializeProcThreadAttributeList",
    ):
        _ = conpty_module.WindowsConPty.spawn(command=("cmd",), cwd=".")
    expect(actual=kernel.hpc_closed, expected=True)
    kernel = _KernelFake()
    kernel.pseudo_hr = 0
    kernel.update_fails = True
    _patch_kernel(monkeypatch=monkeypatch, kernel=kernel)
    with pytest.raises(
        expected_exception=OSError,
        match="UpdateProcThreadAttribute",
    ):
        _ = conpty_module.WindowsConPty.spawn(command=("cmd",), cwd=".")
    expect(actual=kernel.hpc_closed, expected=True)
    kernel = _KernelFake()
    kernel.pseudo_hr = 0
    kernel.create_fails = True
    _patch_kernel(monkeypatch=monkeypatch, kernel=kernel)
    with pytest.raises(expected_exception=OSError, match="CreateProcessW"):
        _ = conpty_module.WindowsConPty.spawn(command=("cmd",), cwd=".")
    expect(actual=kernel.hpc_closed, expected=True)
    expect(actual=kernel.closed_handles, expected=NativeConstants.four)


def test_conpty_io_and_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise read, write, resize, alive, and terminate paths.

    Args:
        monkeypatch: Monkeypatch.

    """
    kernel: _KernelFake = _KernelFake()
    _patch_kernel(monkeypatch=monkeypatch, kernel=kernel)
    session: conpty_module.WindowsConPty = _conpty_instance()
    expect(actual=session.read(size=64), expected=b"out")
    kernel.read_payload = b""
    expect(actual=session.read(size=64), expected=b"")
    expect(
        actual=session.write(data=b"abc"),
        expected=NativeConstants.write_len,
    )
    kernel.write_ok = False
    with pytest.raises(expected_exception=OSError, match="WriteFile"):
        _ = session.write(data=b"x")
    kernel.write_ok = True
    _ = session.resize(columns=120, rows=30)
    kernel.resize_hr = NativeConstants.resize_error
    with pytest.raises(
        expected_exception=OSError,
        match="ResizePseudoConsole",
    ):
        _ = session.resize(columns=10, rows=5)
    expect(actual=session.alive(), expected=True)
    kernel.wait_result = 0
    expect(actual=session.alive(), expected=False)
    kernel.wait_result = NativeConstants.wait_timeout
    session.terminate()
    expect(actual=kernel.terminated, expected=True)
    expect(actual=kernel.hpc_closed, expected=True)
    check(
        condition=kernel.closed_handles >= NativeConstants.three,
        reason="handles released",
    )
    session.terminate()


def test_conpty_drain_respawn_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the drain respawn loop including its backoff sleep.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Monkeypatch.

    """

    class _DyingSession:
        """Session that exits immediately after respawn."""

        def __init__(self) -> None:
            """Initialize one output chunk."""
            self.reads: int = 0
            self.alive_calls: int = 0
            self.terminated: bool = False
            self.pid: int = NativeConstants.child_pid

        def alive(self) -> bool:
            """Die after the first respawn cycle.

            Returns:
                Liveness.

            """
            self.alive_calls += 1
            return self.alive_calls < NativeConstants.two

        def read(self, *, size: int = 4096) -> bytes:
            """Return one chunk then nothing.

            Args:
                size: Read size.

            Returns:
                Output bytes.

            """
            del size
            self.reads += 1
            return b"chunk" if self.reads == 1 else b""

        def terminate(self) -> None:
            """Record termination."""
            self.terminated = True

        @staticmethod
        def write(*, data: bytes) -> int:
            """Accept writes.

            Args:
                data: Input.

            Returns:
                Bytes written.

            """
            return len(data)

    first: _DyingSession = _DyingSession()
    spawned: list[_DyingSession] = [first]

    def spawn(**kwargs: object) -> _DyingSession:
        """Return a fresh dying session each spawn.

        Args:
            **kwargs: Spawn options.

        Returns:
            New fake session.

        """
        del kwargs
        s: _DyingSession = _DyingSession()
        spawned.append(s)
        return s

    def no_thread(**kwargs: object) -> object:
        """Avoid starting drain threads.

        Args:
            **kwargs: Thread options.

        Returns:
            Fake thread.

        """
        del kwargs
        return SimpleNamespace(start=lambda: None)

    monkeypatch.setattr(
        target=conpty_module.WindowsConPty,
        name="spawn",
        value=spawn,
    )
    monkeypatch.setattr(
        target=conpty_module,
        name="Thread",
        value=no_thread,
    )
    manager: conpty_module.ConPtyManager = conpty_module.ConPtyManager(
        policy=PrivacyPolicy(),
    )
    _ = manager.create(
        session_uuid="resp",
        cwd=str(object=tmp_path),
        command=("cmd",),
    )
    managed: Incomplete = manager.sessions["resp"]

    def stopper(seconds: float) -> None:
        """Mark the managed session as closing after backoff.

        Args:
            seconds: Backoff duration.

        """
        del seconds
        managed.closing = True

    monkeypatch.setattr(target=conpty_module, name="sleep", value=stopper)
    manager.drain(session_uuid="resp")
    check(condition=len(spawned) > 1, reason="session respawned")
    check(condition=managed.closing, reason="drain closed")
