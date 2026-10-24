# Copyright (c) 2026 Project contributors.
"""Normalized observability event definitions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from json import dumps
from typing import Final

from .identity import IdentityStore
from .privacy import Redactor


class EventTypes:
    """Canonical event names shared by CLI, daemon, history, and editor UI.

    Attributes:
        session_created: Session created.
        session_attached: Session attached.
        session_idle: Session idle.
        session_resumed: Session resumed.
        session_exited: Session exited.
        session_stale: Session stale.
        command_started: Command started.
        command_finished: Command finished.
        output_activity: Output activity.
        agent_state: Agent state.

    """

    session_created: Final[str] = "SESSION_CREATED"
    session_attached: Final[str] = "SESSION_ATTACHED"
    session_idle: Final[str] = "SESSION_IDLE"
    session_resumed: Final[str] = "SESSION_RESUMED"
    session_exited: Final[str] = "SESSION_EXITED"
    session_stale: Final[str] = "SESSION_STALE"
    command_started: Final[str] = "COMMAND_STARTED"
    command_finished: Final[str] = "COMMAND_FINISHED"
    output_activity: Final[str] = "OUTPUT_ACTIVITY"
    agent_state: Final[str] = "AGENT_STATE"


@dataclass(frozen=True, slots=True, kw_only=True)
class EventDetails:
    """Optional details associated with an observability event.

    Attributes:
        session_uuid: Session UUID.
        session_id: Session identifier.
        command_id: Command identifier.
        agent: Agent name.
        agent_session: Agent session identifier.
        state: Agent or session state.
        cwd: Current working directory.
        command: Command text.
        exit_code: Command exit code.
        duration_seconds: Command duration in seconds.
        surface: Execution surface.
        editor: Editor name.
        payload: Event payload.

    """

    session_uuid: str = ""
    session_id: str = ""
    command_id: str = ""
    agent: str = ""
    agent_session: str = ""
    state: str = ""
    cwd: str = ""
    command: str = ""
    exit_code: int | None = None
    duration_seconds: float = 0.0
    surface: str = ""
    editor: str = ""
    payload: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class DistrictEvent:
    """One normalized event emitted by a District Terminal execution host.

    Attributes:
        id: Id.
        event_type: Event type.
        occurred_at: Occurred at.
        host_id: Host id.
        session_uuid: Session uuid.
        session_id: Session id.
        command_id: Command id.
        agent: Agent.
        agent_session: Agent session.
        state: State.
        cwd: Cwd.
        command: Command.
        exit_code: Exit code.
        duration_seconds: Duration seconds.
        surface: Surface.
        editor: Editor.
        payload: Payload.

    """

    id: str
    event_type: str
    occurred_at: str
    host_id: str
    session_uuid: str = ""
    session_id: str = ""
    command_id: str = ""
    agent: str = ""
    agent_session: str = ""
    state: str = ""
    cwd: str = ""
    command: str = ""
    exit_code: int | None = None
    duration_seconds: float = 0.0
    surface: str = ""
    editor: str = ""
    payload: str = ""

    @classmethod
    def create(
        cls,
        *,
        event_type: str,
        host_id: str,
        details: EventDetails | None = None,
    ) -> DistrictEvent:
        """Construct a timestamped, redacted event.

        Args:
            event_type: Event type.
            host_id: Host id.
            details: Optional event details.

        Returns:
            The resulting event.

        """
        values: EventDetails = details or EventDetails()
        return cls(
            id=IdentityStore.new_id(),
            event_type=event_type,
            occurred_at=datetime.now(tz=UTC).isoformat(),
            host_id=host_id,
            session_uuid=values.session_uuid,
            session_id=values.session_id,
            command_id=values.command_id,
            agent=values.agent,
            agent_session=values.agent_session,
            state=values.state,
            cwd=values.cwd,
            command=Redactor.redact(value=values.command),
            exit_code=values.exit_code,
            duration_seconds=values.duration_seconds,
            surface=values.surface,
            editor=values.editor,
            payload=Redactor.redact(value=values.payload),
        )

    def json(self) -> str:
        """Serialize the event as compact JSON.

        Returns:
            The resulting value.

        """
        return dumps(
            obj=asdict(obj=self),
            separators=(",", ":"),
            sort_keys=True,
        )
