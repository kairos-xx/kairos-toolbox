# Copyright (c) 2026 Project contributors.
"""Lake Terminal session lifecycle and canonical SQLite session state."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from operator import attrgetter
from os import environ, getpid, getppid
from pathlib import Path
from platform import node
from secrets import token_hex
from shlex import join, quote
from subprocess import CalledProcessError, CompletedProcess
from sys import executable, stdout
from time import monotonic, sleep
from typing import TYPE_CHECKING, Final, Protocol

from .conpty import ConPtyClient, ConPtyInfo
from .daemon_control import DaemonControl
from .events import (
    EventDetails,
    EventTypes,
    LakeEvent,
)
from .identity import (
    IdentityStore,
    ProcessIdentity,
)
from .models import SessionEntry
from .platform import RuntimePlatform
from .privacy import Redactor
from .process import run
from .process_tree import (
    ProcessInspector,
    ProcessNode,
)
from .recorder import EventRecorder
from .storage import LakeDatabase

if TYPE_CHECKING:
    from collections.abc import Callable


class SessionBackend(Protocol):
    """Define the lifecycle contract for execution backends."""

    def alive(self, *, session_id: str) -> bool:
        """Return whether a backend session is alive.

        Args:
            session_id: Backend session identifier.

        Returns:
            Whether the session is alive.

        """
        ...

    def capture(self, *, session_id: str) -> str:
        """Capture current backend output.

        Args:
            session_id: Backend session identifier.

        Returns:
            Captured backend output.

        """
        ...

    def kill(self, *, session_id: str) -> None:
        """Terminate a backend session.

        Args:
            session_id: Backend session identifier.

        """
        ...


class Constants:
    """Group immutable application constants.

    Attributes:
        id_bytes: Number of random bytes used for short IDs.
        session_prefix: Prefix used for tmux session names.
        config_directory_name: Lake Terminal configuration directory name.
        exec_poll_seconds: Delay between command completion checks.
        exec_timeout_seconds: Maximum command execution wait.
        create_attempts: Maximum tmux session creation attempts.
        respawn_delay_seconds: Delay before respawning a root shell.
        shell_names: Known interactive shell process names.
        sendable_keys: Key names accepted for pane input injection.

    """

    id_bytes: Final[int] = 3
    session_prefix: Final[str] = "lake-"
    config_directory_name: Final[str] = ".lake_terminal"
    exec_poll_seconds: Final[float] = 0.1
    exec_timeout_seconds: Final[float] = 120.0
    create_attempts: Final[int] = 5
    respawn_delay_seconds: Final[int] = 1
    shell_names: Final[frozenset[str]] = frozenset(
        {
            "bash",
            "csh",
            "dash",
            "fish",
            "ksh",
            "sh",
            "tcsh",
            "zsh",
        },
    )
    sendable_keys: Final[frozenset[str]] = frozenset(
        {
            "BSpace",
            "DC",
            "Down",
            "End",
            "Enter",
            "Escape",
            "Home",
            "Left",
            "PageDown",
            "PageUp",
            "Right",
            "Space",
            "Tab",
            "Up",
            *(f"C-{letter}" for letter in "abcdefghijklmnopqrstuvwxyz"),
        },
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecResult:
    """Captured result of one command run inside a lake session.

    Attributes:
        output: Captured command output.
        exit_status: Command exit status.

    """

    output: str
    exit_status: int


@dataclass(frozen=True, slots=True, kw_only=True)
class _ExecutionContext:
    """Persisted metadata for one active command execution.

    Attributes:
        entry: Original session entry.
        active_entry: Session entry while the command is executing.
        command_id: Stable command identifier.
        command: Redacted persisted command.
        started_at: Command start timestamp.

    """

    entry: SessionEntry
    active_entry: SessionEntry
    command_id: str
    command: str
    started_at: datetime


class SessionRepository:
    """Expose transactional SQLite-backed session operations."""

    def __init__(self, *, home: Path) -> None:
        """Initialize the canonical session repository.

        Args:
            home: User home directory.

        """
        self.home: Path = home
        self._database: LakeDatabase = LakeDatabase(home=home)

    @property
    def database(self) -> LakeDatabase:
        """Return the canonical SQLite store.

        Returns:
            Canonical database.

        """
        return self._database

    def read(self) -> tuple[SessionEntry, ...]:
        """Return all canonical session rows.

        Returns:
            Canonical session entries.

        """
        return self._database.sessions()

    def add(self, *, entry: SessionEntry) -> None:
        """Add one session atomically.

        Args:
            entry: Session entry.

        """
        _ = self.update(
            transform=lambda entries: (*entries, entry),
        )

    def remove(self, *, session_id: str) -> None:
        """Remove one session if present.

        Args:
            session_id: Public session identifier.

        """
        _ = self.update(
            transform=lambda entries: tuple(
                entry for entry in entries if entry.id != session_id
            ),
        )

    def replace(self, *, entries: tuple[SessionEntry, ...]) -> None:
        """Replace the complete canonical session set.

        Args:
            entries: Replacement session entries.

        """
        self._database.replace_sessions(entries=entries)

    def update(
        self,
        *,
        transform: Callable[
            [tuple[SessionEntry, ...]],
            tuple[SessionEntry, ...],
        ],
    ) -> tuple[SessionEntry, ...]:
        """Atomically transform the canonical session set.

        Args:
            transform: Session-set transformation.

        Returns:
            Updated session entries.

        """
        return self._database.update_sessions(transform=transform)


class LakeTerminal:
    """Create, attach, list, reconcile, and kill lake sessions."""

    def __init__(self, *, repository: SessionRepository) -> None:
        """Initialize the session service.

        Args:
            repository: Canonical session repository.

        """
        self.repository: SessionRepository = repository
        self._identity: IdentityStore = IdentityStore(
            home=repository.home,
        )
        self.recorder: EventRecorder = EventRecorder(
            home=repository.home,
        )

    @staticmethod
    def exists(*, session: str) -> bool:
        """Return whether tmux reports a named session as live.

        Args:
            session: Tmux session name.

        Returns:
            Whether the session exists.

        """
        try:
            return (
                run(
                    args=("tmux", "has-session", "-t", session),
                    check=False,
                    capture_output=True,
                ).returncode
                == 0
            )
        except OSError:
            return False

    @staticmethod
    def new_id(*, entries: tuple[SessionEntry, ...]) -> str:
        """Generate a collision-free short identifier.

        Args:
            entries: Existing session entries.

        Returns:
            New short session identifier.

        """
        reserved: frozenset[str] = frozenset(entry.id for entry in entries)
        while (candidate := token_hex(nbytes=Constants.id_bytes)) in reserved:
            continue
        return candidate

    @staticmethod
    def live_session_names() -> frozenset[str] | None:
        """Return all live tmux session names in one query.

        Returns:
            Live tmux session names, or None when unavailable.

        """
        try:
            result: CompletedProcess[str] = run(
                args=(
                    "tmux",
                    "list-sessions",
                    "-F",
                    "#{session_name}",
                ),
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return None
        if result.returncode != 0:
            return frozenset()
        return frozenset(result.stdout.split())

    @staticmethod
    def pane_pid(*, session: str) -> int:
        """Return the active pane process ID when tmux exposes it.

        Args:
            session: Tmux session name.

        Returns:
            Active pane process identifier, or zero.

        """
        try:
            result: CompletedProcess[str] = run(
                args=(
                    "tmux",
                    "display-message",
                    "-p",
                    "-t",
                    session,
                    "#{pane_pid}",
                ),
                check=False,
                capture_output=True,
                text=True,
            )
        except TypeError:
            return 0
        value: str = result.stdout.strip()
        return (
            int(value) if (result.returncode == 0 and value.isdigit()) else 0
        )

    @staticmethod
    def pane_is_shell(*, session: str) -> bool:
        """Return whether the active tmux pane is an interactive shell.

        Args:
            session: Tmux session name.

        Returns:
            Whether the active process is a known shell.

        """
        result: CompletedProcess[str] = run(
            args=(
                "tmux",
                "display-message",
                "-p",
                "-t",
                session,
                "#{pane_current_command}",
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False
        return result.stdout.strip() in Constants.shell_names

    @staticmethod
    def parse_exec(
        *,
        captured: str,
        start: str,
        end: str,
    ) -> ExecResult | None:
        """Extract one exec result between sentinel lines in scrollback.

        Args:
            captured: Captured terminal output.
            start: Start sentinel.
            end: End sentinel.

        Returns:
            Parsed execution result when both sentinels are present.

        """
        lines: list[str] = captured.splitlines()
        end_index: int = -1
        exit_status: int = 0
        for index in range(
            len(lines) - 1,
            -1,
            -1,
        ):
            line: str = lines[index].strip()
            if not line.startswith(f"{end}:"):
                continue
            try:
                exit_status = int(
                    line.split(
                        sep=":",
                        maxsplit=1,
                    )[1],
                )
            except ValueError:
                continue
            end_index = index
            break
        if end_index < 0:
            return None
        for index in range(
            end_index - 1,
            -1,
            -1,
        ):
            if lines[index].strip() == start:
                return ExecResult(
                    output="\n".join(
                        lines[index + 1 : end_index],
                    ),
                    exit_status=exit_status,
                )
        return None

    def create(
        self,
        *,
        initial_command: tuple[str, ...] = (),
        initiator: str = "user",
    ) -> int:
        """Create a new session, register it, and attach the caller.

        Args:
            initial_command: Initial command.
            initiator: Session initiator.

        Returns:
            Backend exit status.

        Raises:
            RuntimeError: When a unique session cannot be allocated.

        """
        runtime: RuntimePlatform = RuntimePlatform.detect()
        if runtime.backend == "conpty":
            return self.create_conpty(
                initial_command=initial_command,
                initiator=initiator,
            )
        entry: SessionEntry = self.spawn(
            initial_command=initial_command,
            initiator=initiator,
        )
        _ = stdout.write(
            "".join(
                (
                    f"Lake session {entry.id}\n",
                    (f"Attach elsewhere with: lake {entry.id}\n"),
                ),
            ),
        )
        try:
            return run(
                args=(
                    "tmux",
                    "attach-session",
                    "-t",
                    entry.session,
                ),
                check=False,
            ).returncode
        finally:
            _ = self.reconcile()

    def spawn(
        self,
        *,
        initial_command: tuple[str, ...] = (),
        initiator: str = "user",
        cwd: str = "",
    ) -> SessionEntry:
        """Create one detached lake session and register it.

        Args:
            initial_command: Initial command.
            initiator: Session initiator.
            cwd: Pane working directory; defaults to the current directory.

        Returns:
            Registered session entry.

        Raises:
            RuntimeError: When a unique session cannot be allocated, or the
                platform backend cannot spawn detached sessions.

        """
        runtime: RuntimePlatform = RuntimePlatform.detect()
        if runtime.backend == "conpty":
            msg = "Detached spawn is not supported on this platform."
            raise RuntimeError(msg)
        workdir: str = cwd or str(object=Path.cwd())
        entries: tuple[SessionEntry, ...] = self.reconcile()
        session_id: str = ""
        session_name: str = ""
        session_uuid: str = IdentityStore.new_id()
        for _attempt in range(Constants.create_attempts):
            session_id = self.new_id(entries=entries)
            session_name = f"{Constants.session_prefix}{session_id}"
            created: CompletedProcess[str] = run(
                args=(
                    "tmux",
                    "new-session",
                    "-d",
                    "-s",
                    session_name,
                    "-c",
                    workdir,
                    self._session_command(
                        session_id=session_id,
                        session_uuid=session_uuid,
                        initial_command=initial_command,
                    ),
                ),
                check=False,
            )
            if created.returncode == 0:
                break
            if not self.exists(session=session_name):
                created.check_returncode()
        else:
            msg: str = "Could not allocate a unique lake session id."
            raise RuntimeError(msg)
        _ = run(
            args=(
                "tmux",
                "set-option",
                "-t",
                session_name,
                "remain-on-exit",
                "off",
            ),
            check=True,
        )
        _ = run(
            args=(
                "tmux",
                "set-option",
                "-t",
                session_name,
                "exit-empty",
                "on",
            ),
            check=True,
        )
        _ = run(
            args=(
                "tmux",
                "set-hook",
                "-t",
                session_name,
                "session-closed",
                (
                    f"run-shell '{quote(s=executable)} "
                    f"-m lake_terminal --cleanup {session_id}'"
                ),
            ),
            check=True,
        )
        pane_pid: int = self.pane_pid(
            session=session_name,
        )
        process_identity: ProcessIdentity = ProcessIdentity.capture(
            pid=pane_pid if pane_pid > 0 else getpid(),
        )
        host_id: str = self._identity.host_id()
        agent: str = environ.get("LAKE_AGENT", "")
        agent_session: str = environ.get(
            "LAKE_AGENT_SESSION",
            "",
        )
        if initiator == "agent" and (not agent or not agent_session):
            context: tuple[str, str] | None = (
                self.repository.database.agent_context(
                    workspace=workdir,
                )
            )
            if context is not None:
                context_agent: str
                context_session: str
                context_agent, context_session = context
                agent = agent or context_agent
                agent_session = agent_session or context_session
        entry: SessionEntry = SessionEntry(
            id=session_id,
            command=f"lake {session_id}",
            session=session_name,
            created_at=datetime.now(tz=UTC).isoformat(),
            initiator=initiator,
            pid=process_identity.pid,
            parent_pid=getppid(),
            hostname=node(),
            cwd=workdir,
            shell_location=environ.get("SHELL", "/bin/zsh"),
            term=environ.get("TERM", ""),
            terminal_program=environ.get(
                "TERM_PROGRAM",
                "",
            ),
            tty=environ.get("TTY", ""),
            invocation=join(
                split_command=("lake", *initial_command),
            ),
            initial_command=join(
                split_command=initial_command,
            ),
            agent=agent if initiator == "agent" else "",
            agent_session=(agent_session if initiator == "agent" else ""),
            backend="tmux",
            operating_system=runtime.os,
            architecture=runtime.architecture,
            surface=RuntimePlatform.surface(),
            editor=RuntimePlatform.editor(),
            workspace=environ.get(
                "LAKE_WORKSPACE",
                workdir,
            ),
            attribution=environ.get(
                "LAKE_ATTRIBUTION",
                ("environment" if initiator == "agent" else "user"),
            ),
            reconnectable=True,
            last_activity_at=datetime.now(
                tz=UTC,
            ).isoformat(),
            uuid=session_uuid,
            host_id=host_id,
            process_started_token=(process_identity.started_token),
            agent_state="IDLE",
        )
        self.repository.add(entry=entry)
        self.recorder.record(
            event=LakeEvent.create(
                event_type=EventTypes.session_created,
                host_id=host_id,
                details=EventDetails(
                    session_uuid=session_uuid,
                    session_id=session_id,
                    agent=entry.agent,
                    agent_session=entry.agent_session,
                    state="IDLE",
                    cwd=entry.cwd,
                    surface=entry.surface,
                    editor=entry.editor,
                ),
            ),
        )
        return entry

    def ensure_agent(
        self,
        *,
        initial_command: tuple[str, ...] = (),
    ) -> int:
        """Attach to the newest live agent session, creating one if needed.

        Args:
            initial_command: Initial command.

        Returns:
            Backend exit status.

        """
        agent: str = (
            environ.get(
                "LAKE_AGENT",
                "",
            )
            .strip()
            .lower()
        )
        agents: list[SessionEntry] = [
            entry
            for entry in self.reconcile()
            if entry.initiator == "agent"
            and (not agent or entry.agent == agent)
        ]
        if not agents:
            return self.create(
                initial_command=initial_command,
                initiator="agent",
            )
        entry: SessionEntry = max(
            agents,
            key=attrgetter("created_at"),
        )
        _ = stdout.write(
            (
                f"Lake session {entry.id}\n"
                f"Attach elsewhere with: lake {entry.id}\n"
            ),
        )
        if entry.backend == "conpty":
            if initial_command:
                _ = self.execute(
                    session_id=entry.id,
                    command=join(
                        split_command=initial_command,
                    ),
                )
            return ConPtyClient(
                home=self.repository.home,
            ).attach(
                session_uuid=entry.uuid,
            )
        if initial_command and self.pane_is_shell(
            session=entry.session,
        ):
            _ = run(
                args=(
                    "tmux",
                    "send-keys",
                    "-t",
                    entry.session,
                    "-l",
                    "--",
                    join(
                        split_command=initial_command,
                    ),
                ),
                check=True,
            )
            _ = run(
                args=(
                    "tmux",
                    "send-keys",
                    "-t",
                    entry.session,
                    "Enter",
                ),
                check=True,
            )
        return run(
            args=(
                "tmux",
                "attach-session",
                "-t",
                entry.session,
            ),
            check=False,
        ).returncode

    def attach(self, *, session_id: str) -> int:
        """Attach the current terminal to an existing lake session.

        Args:
            session_id: Public session identifier.

        Returns:
            Backend exit status.

        """
        entry: SessionEntry = self._require_entry(
            session_id=session_id,
        )
        self.recorder.record(
            event=LakeEvent.create(
                event_type=EventTypes.session_attached,
                host_id=(entry.host_id or self._identity.host_id()),
                details=EventDetails(
                    session_uuid=entry.uuid,
                    session_id=entry.id,
                    agent=entry.agent,
                    agent_session=entry.agent_session,
                    state=entry.agent_state,
                    cwd=entry.cwd,
                    surface=entry.surface,
                    editor=entry.editor,
                ),
            ),
        )
        if entry.backend == "conpty":
            return ConPtyClient(
                home=self.repository.home,
            ).attach(
                session_uuid=entry.uuid,
            )
        return run(
            args=(
                "tmux",
                "attach-session",
                "-t",
                entry.session,
            ),
            check=False,
        ).returncode

    def kill(self, *, session_id: str) -> None:
        """Destroy one session and remove its persisted session row.

        Args:
            session_id: Public session identifier.

        """
        entry: SessionEntry = self._require_entry(
            session_id=session_id,
        )
        try:
            if entry.backend == "conpty":
                _ = ConPtyClient(
                    home=self.repository.home,
                ).kill(
                    session_uuid=entry.uuid,
                )
            else:
                _ = run(
                    args=(
                        "tmux",
                        "kill-session",
                        "-t",
                        entry.session,
                    ),
                    check=True,
                )
        finally:
            self.repository.remove(
                session_id=session_id,
            )
            self.recorder.record(
                event=LakeEvent.create(
                    event_type=EventTypes.session_exited,
                    host_id=(entry.host_id or self._identity.host_id()),
                    details=EventDetails(
                        session_uuid=entry.uuid,
                        session_id=entry.id,
                        agent=entry.agent,
                        agent_session=entry.agent_session,
                        state="DEAD",
                        cwd=entry.cwd,
                        surface=entry.surface,
                        editor=entry.editor,
                    ),
                ),
            )

    def send(
        self,
        *,
        session_id: str,
        text: str = "",
        keys: tuple[str, ...] = (),
    ) -> None:
        """Inject literal input and key presses into a session pane.

        Args:
            session_id: Public session identifier.
            text: Literal text typed into the pane.
            keys: Key names pressed after the text.

        Raises:
            RuntimeError: When the backend cannot accept input.
            ValueError: When a key name is not sendable.

        """
        rejected: tuple[str, ...] = tuple(
            key for key in keys if key not in Constants.sendable_keys
        )
        if rejected:
            msg: str = f"Unsupported key name: {rejected[0]!r}."
            raise ValueError(msg)
        entry: SessionEntry = self._require_entry(
            session_id=session_id,
        )
        if entry.backend != "tmux":
            msg = "Input injection only supports tmux sessions."
            raise RuntimeError(msg)
        if text:
            _ = run(
                args=(
                    "tmux",
                    "send-keys",
                    "-t",
                    entry.session,
                    "-l",
                    "--",
                    text,
                ),
                check=True,
            )
        for key in keys:
            _ = run(
                args=(
                    "tmux",
                    "send-keys",
                    "-t",
                    entry.session,
                    key,
                ),
                check=True,
            )

    def execute(
        self,
        *,
        session_id: str,
        command: str,
    ) -> ExecResult:
        """Run one shell command inside a persistent session and capture it.

        Args:
            session_id: Public session identifier.
            command: Shell command.

        Returns:
            Captured command result.

        Raises:
            TimeoutError: When execution exceeds the configured timeout.
            KeyError: When the session disappears during execution.

        """
        context: _ExecutionContext = self._start_execute(
            session_id=session_id,
            command=command,
        )
        if context.entry.backend == "conpty":
            conpty_result: ExecResult = self._execute_conpty_io(
                entry=context.entry,
                command=command,
            )
            return self._complete_execute(
                context=context,
                result=conpty_result,
            )
        token: str = token_hex(
            nbytes=Constants.id_bytes,
        )
        start_marker: str = f"__LAKE_START_{token}__"
        end_marker: str = f"__LAKE_END_{token}__"
        line: str = (
            f"echo {start_marker}; {command}; "
            "__lake_rc=$?; "
            f"echo {end_marker}:$__lake_rc"
        )
        try:
            _ = run(
                args=(
                    "tmux",
                    "send-keys",
                    "-t",
                    context.entry.session,
                    "-l",
                    "--",
                    line,
                ),
                check=True,
            )
            _ = run(
                args=(
                    "tmux",
                    "send-keys",
                    "-t",
                    context.entry.session,
                    "Enter",
                ),
                check=True,
            )
            deadline: float = monotonic() + Constants.exec_timeout_seconds
            while monotonic() < deadline:
                captured: str = run(
                    args=(
                        "tmux",
                        "capture-pane",
                        "-t",
                        context.entry.session,
                        "-p",
                        "-J",
                        "-S",
                        "-",
                    ),
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
                parsed_result: ExecResult | None = self.parse_exec(
                    captured=captured,
                    start=start_marker,
                    end=end_marker,
                )
                if parsed_result is not None:
                    return self._complete_execute(
                        context=context,
                        result=parsed_result,
                    )
                sleep(Constants.exec_poll_seconds)
        except CalledProcessError:
            if self.exists(
                session=context.entry.session,
            ):
                raise
            raise KeyError(session_id) from None
        msg: str = f"Command in lake session {session_id} timed out."
        raise TimeoutError(msg)

    def cleanup(self, *, session_id: str) -> None:
        """Remove lifecycle metadata after the session shell exits.

        Args:
            session_id: Public session identifier.

        """
        entry: SessionEntry | None = next(
            (item for item in self.repository.read() if item.id == session_id),
            None,
        )
        if entry is None or self.exists(session=entry.session):
            return
        self.repository.remove(
            session_id=session_id,
        )

    def list_entries(self) -> tuple[SessionEntry, ...]:
        """Return all currently live registered sessions.

        Returns:
            Live session entries.

        """
        return self.reconcile()

    def reconcile(self) -> tuple[SessionEntry, ...]:
        """Reconcile canonical state against live backend/process identity.

        Returns:
            Live canonical session entries.

        """
        current: tuple[SessionEntry, ...] = self.repository.read()
        live: list[SessionEntry] = []
        stale: list[SessionEntry] = []
        for entry in current:
            alive: bool = self.entry_alive(
                entry=entry,
            )
            (live if alive else stale).append(entry)
        result: tuple[SessionEntry, ...] = tuple(
            live,
        )
        for entry in result:
            descendants: tuple[ProcessNode, ...] = (
                ProcessInspector.descendants(
                    root_pid=entry.pid,
                )
                if entry.pid > 0
                else ()
            )
            self.repository.database.observe_processes(
                session_uuid=entry.uuid,
                processes=tuple(
                    (
                        process.pid,
                        process.started_token,
                        entry.current_command_id,
                    )
                    for process in descendants
                ),
            )
        if result != current:
            self.repository.replace(
                entries=result,
            )
        for entry in stale:
            self.recorder.record(
                event=LakeEvent.create(
                    event_type=EventTypes.session_stale,
                    host_id=(entry.host_id or self._identity.host_id()),
                    details=EventDetails(
                        session_uuid=entry.uuid,
                        session_id=entry.id,
                        agent=entry.agent,
                        agent_session=entry.agent_session,
                        state="STALE",
                        cwd=entry.cwd,
                        surface=entry.surface,
                        editor=entry.editor,
                    ),
                ),
            )
        return result

    def status(self, *, session_id: str) -> SessionEntry:
        """Return metadata for one live session.

        Args:
            session_id: Public session identifier.

        Returns:
            Live session entry.

        """
        return self._require_entry(
            session_id=session_id,
        )

    def create_conpty(
        self,
        *,
        initial_command: tuple[str, ...],
        initiator: str,
    ) -> int:
        """Create a daemon-owned persistent Windows PowerShell ConPTY.

        Args:
            initial_command: Initial command.
            initiator: Session initiator.

        Returns:
            Backend exit status.

        """
        if not DaemonControl.start(
            home=self.repository.home,
        ):
            return 127
        entries: tuple[SessionEntry, ...] = self.reconcile()
        session_id: str = self.new_id(
            entries=entries,
        )
        session_uuid: str = IdentityStore.new_id()
        runtime: RuntimePlatform = RuntimePlatform.detect()
        client: ConPtyClient = ConPtyClient(
            home=self.repository.home,
        )
        info: ConPtyInfo = client.create(
            session_uuid=session_uuid,
            cwd=str(object=Path.cwd()),
            command=(
                "powershell.exe",
                "-NoLogo",
            ),
        )
        identity: ProcessIdentity = ProcessIdentity.capture(
            pid=info.pid,
        )
        entry: SessionEntry = SessionEntry(
            id=session_id,
            command=f"lake {session_id}",
            session=session_uuid,
            created_at=datetime.now(
                tz=UTC,
            ).isoformat(),
            initiator=initiator,
            pid=info.pid,
            parent_pid=getpid(),
            hostname=node(),
            cwd=str(object=Path.cwd()),
            shell_location="powershell.exe",
            term=environ.get("TERM", ""),
            terminal_program=environ.get(
                "TERM_PROGRAM",
                "",
            ),
            tty="",
            invocation=join(
                split_command=(
                    "lake",
                    *initial_command,
                ),
            ),
            initial_command=join(
                split_command=initial_command,
            ),
            agent=(
                environ.get("LAKE_AGENT", "") if initiator == "agent" else ""
            ),
            agent_session=(
                environ.get(
                    "LAKE_AGENT_SESSION",
                    "",
                )
                if initiator == "agent"
                else ""
            ),
            backend="conpty",
            operating_system=runtime.os,
            architecture=runtime.architecture,
            surface=RuntimePlatform.surface(),
            editor=RuntimePlatform.editor(),
            workspace=environ.get(
                "LAKE_WORKSPACE",
                str(object=Path.cwd()),
            ),
            attribution=environ.get(
                "LAKE_ATTRIBUTION",
                ("environment" if initiator == "agent" else "user"),
            ),
            reconnectable=True,
            last_activity_at=datetime.now(
                tz=UTC,
            ).isoformat(),
            uuid=session_uuid,
            host_id=self._identity.host_id(),
            process_started_token=(identity.started_token),
            agent_state="IDLE",
        )
        self.repository.add(entry=entry)
        self.recorder.record(
            event=LakeEvent.create(
                event_type=EventTypes.session_created,
                host_id=entry.host_id,
                details=EventDetails(
                    session_uuid=entry.uuid,
                    session_id=entry.id,
                    agent=entry.agent,
                    agent_session=entry.agent_session,
                    state="IDLE",
                    cwd=entry.cwd,
                    surface=entry.surface,
                    editor=entry.editor,
                ),
            ),
        )
        _ = stdout.write(
            (
                f"Lake session {session_id}\n"
                "Attach elsewhere with: "
                f"lake {session_id}\n"
            ),
        )
        if initial_command:
            _ = self.execute(
                session_id=session_id,
                command=join(
                    split_command=initial_command,
                ),
            )
        return client.attach(
            session_uuid=session_uuid,
        )

    def entry_alive(
        self,
        *,
        entry: SessionEntry,
    ) -> bool:
        """Return whether one canonical session still has a live backend.

        Args:
            entry: Session entry.

        Returns:
            Whether the session is alive.

        """
        if entry.backend == "tmux":
            return self.exists(
                session=entry.session,
            )
        if entry.backend == "conpty":
            try:
                return ConPtyClient(
                    home=self.repository.home,
                ).alive(
                    session_uuid=entry.uuid,
                )
            except (
                ConnectionError,
                OSError,
                EOFError,
            ):
                return False
        if entry.pid <= 0:
            return False
        try:
            identity: ProcessIdentity = ProcessIdentity.capture(
                pid=entry.pid,
            )
        except OSError:
            return False
        return (
            not entry.process_started_token
            or identity.started_token == entry.process_started_token
        )

    @staticmethod
    def _session_command(
        *,
        session_id: str,
        session_uuid: str = "",
        initial_command: tuple[str, ...] = (),
    ) -> str:
        """Build the tmux-owned root-shell lifecycle command.

        Args:
            session_id: Public session identifier.
            session_uuid: Stable session UUID.
            initial_command: Initial command.

        Returns:
            Root-shell command.

        """
        shell: str = environ.get(
            "SHELL",
            "/bin/zsh",
        )
        session_name: str = f"{Constants.session_prefix}{session_id}"
        prefix: str = (
            f"{join(split_command=initial_command)}; "
            if initial_command
            else ""
        )
        script: str = (
            "export LAKE_SESSION_ID="
            f"{quote(s=session_id)}; "
            "export LAKE_SESSION_UUID="
            f"{quote(s=session_uuid)}; "
            f"{prefix}"
            f"while :; do {quote(s=shell)} -l; "
            "tmux detach-client -s "
            f"{quote(s=session_name)} "
            "2>/dev/null; "
            f"sleep {Constants.respawn_delay_seconds}; "
            "done"
        )
        return f"{quote(s=shell)} -lc {quote(s=script)}"

    def _execute_conpty_io(
        self,
        *,
        entry: SessionEntry,
        command: str,
    ) -> ExecResult:
        """Execute one command through the daemon-owned PowerShell ConPTY.

        Args:
            entry: Session entry.
            command: Command to execute.

        Returns:
            Captured command result.

        Raises:
            TimeoutError: When execution exceeds the configured timeout.

        """
        client: ConPtyClient = ConPtyClient(
            home=self.repository.home,
        )
        token: str = token_hex(
            nbytes=Constants.id_bytes,
        )
        start_marker: str = f"__LAKE_START_{token}__"
        end_marker: str = f"__LAKE_END_{token}__"
        baseline: str = client.output(
            session_uuid=entry.uuid,
        )
        line: str = (
            f"Write-Output '{start_marker}'; "
            f"& {{ {command} }}; "
            "$__lake_rc=$LASTEXITCODE; "
            "if ($null -eq $__lake_rc) "
            "{ $__lake_rc=0 }; "
            f"Write-Output "
            f"'{end_marker}:'$__lake_rc`r`n"
        )
        _ = client.write(
            session_uuid=entry.uuid,
            text=line,
        )
        deadline: float = monotonic() + Constants.exec_timeout_seconds
        while monotonic() < deadline:
            output: str = client.output(
                session_uuid=entry.uuid,
            )
            captured: str = output.removeprefix(
                baseline,
            )
            parsed_result: ExecResult | None = self.parse_exec(
                captured=captured,
                start=start_marker,
                end=end_marker,
            )
            if parsed_result is not None:
                return parsed_result
            sleep(Constants.exec_poll_seconds)
        msg: str = f"Command in lake session {entry.id} timed out."
        raise TimeoutError(msg)

    def _complete_execute(
        self,
        *,
        context: _ExecutionContext,
        result: ExecResult,
    ) -> ExecResult:
        """Persist common completion state for every persistent backend.

        Args:
            context: Active command execution metadata.
            result: Captured execution result.

        Returns:
            Captured execution result.

        """
        finished_at: datetime = datetime.now(
            tz=UTC,
        )
        idle_entry: SessionEntry = replace(
            context.active_entry,
            current_command="",
            current_command_id="",
            agent_state="IDLE",
            last_activity_at=finished_at.isoformat(),
        )
        _ = self.repository.update(
            transform=lambda entries: tuple(
                (idle_entry if item.id == context.entry.id else item)
                for item in entries
            ),
        )
        duration: float = (finished_at - context.started_at).total_seconds()
        self.repository.database.command_finished(
            command_id=context.command_id,
            finished_at=finished_at.isoformat(),
            exit_code=result.exit_status,
            duration_seconds=duration,
        )
        self.recorder.record(
            event=LakeEvent.create(
                event_type=EventTypes.command_finished,
                host_id=(context.entry.host_id or self._identity.host_id()),
                details=EventDetails(
                    session_uuid=context.entry.uuid,
                    session_id=context.entry.id,
                    command_id=context.command_id,
                    agent=context.entry.agent,
                    agent_session=(context.entry.agent_session),
                    state="IDLE",
                    cwd=context.entry.cwd,
                    command=context.command,
                    exit_code=result.exit_status,
                    duration_seconds=duration,
                    surface=context.entry.surface,
                    editor=context.entry.editor,
                ),
            ),
        )
        return result

    def _start_execute(
        self,
        *,
        session_id: str,
        command: str,
    ) -> _ExecutionContext:
        """Persist command-start state shared by every backend.

        Args:
            session_id: Public session identifier.
            command: Command to execute.

        Returns:
            Active execution context.

        """
        entry: SessionEntry = self._require_entry(
            session_id=session_id,
        )
        started_at: datetime = datetime.now(
            tz=UTC,
        )
        command_id: str = IdentityStore.new_id()
        persisted_command: str = Redactor.redact(
            value=command,
        )
        active_entry: SessionEntry = replace(
            entry,
            current_command=persisted_command,
            current_command_id=command_id,
            agent_state="EXECUTING",
            last_activity_at=started_at.isoformat(),
        )
        _ = self.repository.update(
            transform=lambda entries: tuple(
                (active_entry if item.id == session_id else item)
                for item in entries
            ),
        )
        host_id: str = entry.host_id or self._identity.host_id()
        self.repository.database.command_started(
            command_id=command_id,
            session_uuid=entry.uuid,
            session_id=entry.id,
            host_id=host_id,
            agent=entry.agent,
            agent_session=entry.agent_session,
            surface=entry.surface,
            editor=entry.editor,
            command=persisted_command,
            cwd=entry.cwd,
            started_at=started_at.isoformat(),
        )
        self.recorder.record(
            event=LakeEvent.create(
                event_type=EventTypes.command_started,
                host_id=host_id,
                details=EventDetails(
                    session_uuid=entry.uuid,
                    session_id=entry.id,
                    command_id=command_id,
                    agent=entry.agent,
                    agent_session=entry.agent_session,
                    state="EXECUTING",
                    cwd=entry.cwd,
                    command=persisted_command,
                    surface=entry.surface,
                    editor=entry.editor,
                ),
            ),
        )
        return _ExecutionContext(
            entry=entry,
            active_entry=active_entry,
            command_id=command_id,
            command=persisted_command,
            started_at=started_at,
        )

    def _require_entry(
        self,
        *,
        session_id: str,
    ) -> SessionEntry:
        """Resolve one live entry.

        Args:
            session_id: Public session identifier.

        Returns:
            Live session entry.

        Raises:
            KeyError: When the session does not exist.

        """
        for entry in self.reconcile():
            if entry.id == session_id:
                return entry
        raise KeyError(session_id)


def default_service() -> LakeTerminal:
    """Build the command-line service from the current environment.

    Returns:
        Configured Lake Terminal service.

    """
    return LakeTerminal(
        repository=SessionRepository(
            home=Path(
                environ.get(
                    "HOME",
                    str(object=Path.home()),
                ),
            ),
        ),
    )
