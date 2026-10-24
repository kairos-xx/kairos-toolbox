# Copyright (c) 2026 Project contributors.
"""Long-lived local control-plane daemon for subscriptions and buffers."""

from __future__ import annotations

from json import JSONDecodeError, dumps, loads
from threading import Thread
from time import sleep
from typing import TYPE_CHECKING, Final, Protocol, cast

from .buffer import OutputRingBuffer
from .conpty import (
    ConPtyInfo,
    ConPtyManager,
    WindowsConPty,
)
from .core import District, SessionRepository
from .daemon_control import DaemonControl
from .events import (
    DistrictEvent,
    EventDetails,
    EventTypes,
)
from .identity import IdentityStore
from .ipc import EventServer
from .monitoring import Monitor, SessionSnapshot
from .privacy import PrivacyPolicy
from .recorder import EventRecorder

if TYPE_CHECKING:
    from pathlib import Path

    from .models import SessionEntry


class _DaemonAction(Protocol):
    """Describe one daemon lifecycle action."""

    def __call__(self, *, home: Path) -> bool:
        """Run the daemon lifecycle action.

        Args:
            home: Home directory.

        Returns:
            Whether the action succeeded.

        """
        ...


type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)


class DistrictDaemon:
    """Own local IPC, live output buffers, and background state sampling.

    Attributes:
        start: Start the daemon when it is not already running.
        ping: Check whether the daemon is accepting IPC requests.
        stop: Ask the daemon to shut down.

    """

    def __init__(self, *, home: Path) -> None:
        """Initialize daemon resources.

        Args:
            home: Home.

        """
        self._home: Path = home
        self._policy: PrivacyPolicy = PrivacyPolicy.load(home=home)
        self._buffers: dict[str, OutputRingBuffer] = {}
        self._previous_output: dict[str, str] = {}
        self._previous_state: dict[str, str] = {}
        self._identity: IdentityStore = IdentityStore(home=home)
        self.recorder: EventRecorder = EventRecorder(home=home)
        self.service: District = District(
            repository=SessionRepository(home=home),
        )
        self.conpty: ConPtyManager | None = (
            ConPtyManager(policy=self._policy)
            if WindowsConPty.capabilities().available
            else None
        )
        self.server: EventServer = EventServer(
            home=home,
            on_publish=self.on_publish,
            handler=self.handle,
        )

    start: Final[_DaemonAction] = DaemonControl.start
    ping: Final[_DaemonAction] = DaemonControl.ping
    stop: Final[_DaemonAction] = DaemonControl.stop

    def run(self) -> int:
        """Serve until stopped.

        Returns:
            The daemon exit status.

        """
        _ = Thread(
            target=self._sample,
            daemon=True,
        ).start()
        self.server.serve()
        return 0

    def sample_once(self) -> None:
        """Sample all local sessions once and emit meaningful changes."""
        try:
            entries: tuple[SessionEntry, ...] = self.service.list_entries()
            for entry in entries:
                self._sample_entry(entry=entry)
        except (OSError, ValueError, TypeError):
            return

    def on_publish(self, payload: str) -> None:
        """Update ephemeral ring buffers from live output events.

        Args:
            payload: Payload.

        """
        try:
            value: JsonValue = cast(
                typ="JsonValue",
                val=loads(s=payload),
            )
        except JSONDecodeError:
            return
        if not isinstance(value, dict):
            return
        event_type: JsonValue = value.get("event_type")
        if event_type != EventTypes.output_activity:
            return
        session_uuid: str = self._json_string(
            value=value.get("session_uuid"),
        )
        output: str = self._json_string(
            value=value.get("payload"),
        )
        if not session_uuid or not output:
            return
        buffer: OutputRingBuffer = self._buffers.setdefault(
            session_uuid,
            OutputRingBuffer(
                limit_bytes=self._policy.output_limit_bytes,
            ),
        )
        buffer.append(text=output + "\n")

    def handle(self, request: dict[str, str]) -> str | None:
        """Handle daemon-specific IPC operations.

        Args:
            request: Request.

        Returns:
            The operation response when the operation is handled.

        """
        operation: str = request.get("op", "")
        buffer_result: str | None = self._handle_buffer(
            operation=operation,
            request=request,
        )
        if buffer_result is not None:
            return buffer_result
        if not operation.startswith("conpty-"):
            return None
        manager: ConPtyManager | None = self.conpty
        if manager is None:
            return "UNAVAILABLE"
        return self._handle_conpty(
            manager=manager,
            operation=operation,
            request=request,
        )

    @classmethod
    def _conpty_create(
        cls,
        *,
        manager: ConPtyManager,
        request: dict[str, str],
    ) -> str:
        """Create a ConPTY session.

        Args:
            manager: Active ConPTY manager.
            request: Request payload.

        Returns:
            Serialized ConPTY process information.

        """
        command: tuple[str, ...] = cls._command(
            value=request.get("command", "[]"),
        )
        if not command:
            command = (
                "powershell.exe",
                "-NoLogo",
            )
        info: ConPtyInfo = manager.create(
            session_uuid=request.get("session_uuid", ""),
            command=command,
            cwd=request.get("cwd", ""),
        )
        return dumps(
            obj={
                "pid": info.pid,
                "alive": info.alive,
            },
        )

    @classmethod
    def _command(cls, *, value: str) -> tuple[str, ...]:
        """Decode a serialized ConPTY command.

        Args:
            value: Serialized JSON command.

        Returns:
            Command arguments, or an empty tuple for an invalid value.

        """
        try:
            decoded: JsonValue = cast(
                typ="JsonValue",
                val=loads(s=value),
            )
        except JSONDecodeError:
            return ()
        if not isinstance(decoded, list):
            return ()
        command: list[str] = []
        for item in decoded:
            argument: str | None = cls._command_argument(
                value=item,
            )
            if argument is not None:
                command.append(argument)
        return tuple(command)

    @staticmethod
    def _conpty_write(
        *,
        manager: ConPtyManager,
        request: dict[str, str],
    ) -> str:
        """Write text to a ConPTY session.

        Args:
            manager: Active ConPTY manager.
            request: Request payload.

        Returns:
            Number of characters written as text.

        """
        try:
            count: int = manager.write(
                session_uuid=request.get("session_uuid", ""),
                text=request.get("text", ""),
            )
        except KeyError:
            return "0"
        return str(object=count)

    @staticmethod
    def _command_argument(*, value: JsonValue) -> str | None:
        """Convert one JSON command argument to text.

        Args:
            value: JSON command argument.

        Returns:
            Text argument, or ``None`` for structured JSON values.

        """
        if value is None:
            return "None"
        if isinstance(value, bool):
            return str(object=value)
        if isinstance(value, (str, int, float)):
            return str(object=value)
        return None

    @staticmethod
    def _json_string(*, value: JsonValue) -> str:
        """Convert a JSON scalar to text.

        Args:
            value: JSON value.

        Returns:
            Text representation, or an empty string for structured values.

        """
        if value is None:
            return ""
        if isinstance(value, (str, bool, int, float)):
            return str(object=value)
        return ""

    def _sample(self) -> None:
        """Continuously emit meaningful state/output changes."""
        while True:
            self.sample_once()
            sleep(0.5)

    def _sample_entry(self, *, entry: SessionEntry) -> None:
        """Sample one session and record state and output changes.

        Args:
            entry: Session registry entry to sample.

        """
        snapshot: SessionSnapshot = Monitor.snapshot(entry=entry)
        self._record_state_change(
            entry=entry,
            snapshot=snapshot,
        )
        self._record_output_change(
            entry=entry,
            snapshot=snapshot,
        )

    def _record_state_change(
        self,
        *,
        entry: SessionEntry,
        snapshot: SessionSnapshot,
    ) -> None:
        """Record a session state transition when one occurred.

        Args:
            entry: Session registry entry.
            snapshot: Current session snapshot.

        """
        previous_state: str = self._previous_state.get(
            entry.uuid,
            "",
        )
        if previous_state and snapshot.state != previous_state:
            event_type: str = (
                EventTypes.session_idle
                if snapshot.state == "IDLE"
                else EventTypes.session_resumed
            )
            self.recorder.record(
                event=DistrictEvent.create(
                    event_type=event_type,
                    host_id=entry.host_id or self._identity.host_id(),
                    details=EventDetails(
                        session_uuid=entry.uuid,
                        session_id=entry.id,
                        agent=entry.agent,
                        agent_session=entry.agent_session,
                        state=snapshot.state,
                        cwd=entry.cwd,
                        command=snapshot.current_command,
                        surface=entry.surface,
                        editor=entry.editor,
                    ),
                ),
            )
        self._previous_state[entry.uuid] = snapshot.state

    def _record_output_change(
        self,
        *,
        entry: SessionEntry,
        snapshot: SessionSnapshot,
    ) -> None:
        """Record changed session output.

        Args:
            entry: Session registry entry.
            snapshot: Current session snapshot.

        """
        previous_output: str = self._previous_output.get(
            entry.uuid,
            "",
        )
        if not snapshot.output or snapshot.output == previous_output:
            return
        self.recorder.record(
            event=DistrictEvent.create(
                event_type=EventTypes.output_activity,
                host_id=entry.host_id or self._identity.host_id(),
                details=EventDetails(
                    session_uuid=entry.uuid,
                    session_id=entry.id,
                    agent=entry.agent,
                    agent_session=entry.agent_session,
                    state=snapshot.state,
                    cwd=entry.cwd,
                    surface=entry.surface,
                    editor=entry.editor,
                    payload=snapshot.output,
                ),
            ),
        )
        self._previous_output[entry.uuid] = snapshot.output

    def _handle_buffer(
        self,
        *,
        operation: str,
        request: dict[str, str],
    ) -> str | None:
        """Handle a buffer IPC request.

        Args:
            operation: Requested operation.
            request: Request payload.

        Returns:
            Buffered output when handled, otherwise ``None``.

        """
        if operation != "buffer":
            return None
        session_uuid: str = request.get("session_uuid", "")
        buffer: OutputRingBuffer | None = self._buffers.get(
            session_uuid,
        )
        return buffer.text() if buffer is not None else ""

    def _handle_conpty(
        self,
        *,
        manager: ConPtyManager,
        operation: str,
        request: dict[str, str],
    ) -> str | None:
        """Dispatch one ConPTY IPC operation.

        Args:
            manager: Active ConPTY manager.
            operation: Requested operation.
            request: Request payload.

        Returns:
            The operation response when recognized.

        """
        if operation == "conpty-create":
            return self._conpty_create(
                manager=manager,
                request=request,
            )
        if operation == "conpty-output":
            return manager.output(
                session_uuid=request.get("session_uuid", ""),
            )
        if operation == "conpty-write":
            return self._conpty_write(
                manager=manager,
                request=request,
            )
        if operation == "conpty-kill":
            killed: bool = manager.kill(
                session_uuid=request.get("session_uuid", ""),
            )
            return "OK" if killed else "MISSING"
        if operation == "conpty-alive":
            info: ConPtyInfo | None = manager.info(
                session_uuid=request.get("session_uuid", ""),
            )
            return "1" if info is not None and info.alive else "0"
        return None
