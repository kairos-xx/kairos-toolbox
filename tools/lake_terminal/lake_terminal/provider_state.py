# Copyright (c) 2026 Project contributors.
"""Correlate provider lifecycle hooks with canonical Lake session state."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from .events import (
    EventDetails,
    EventTypes,
    LakeEvent,
)
from .recorder import EventRecorder
from .storage import LakeDatabase

if TYPE_CHECKING:
    from pathlib import Path

    from .models import SessionEntry


class ProviderState:
    """Translate provider lifecycle events into normalized agent states."""

    _states: Final[dict[str, str]] = {
        "SessionStart": "IDLE",
        "UserPromptSubmit": "THINKING",
        "PreToolUse": "EXECUTING",
        "PermissionRequest": "WAITING_FOR_USER",
        "PostToolUse": "THINKING",
        "PostToolUseFailure": "THINKING",
        "Stop": "IDLE",
        "StopFailure": "IDLE",
        "SessionEnd": "DEAD",
    }

    @classmethod
    def record(
        cls,
        *,
        home: Path,
        agent: str,
        agent_session: str,
        hook_event: str,
    ) -> None:
        """Apply one provider event to matching Lake sessions.

        Args:
            home: Home.
            agent: Agent.
            agent_session: Agent session.
            hook_event: Hook event.

        """
        state: str | None = cls._states.get(hook_event)
        if state is None:
            return
        database: LakeDatabase = LakeDatabase(home=home)
        now: str = datetime.now(tz=UTC).isoformat()
        matching: tuple[SessionEntry, ...] = tuple(
            entry
            for entry in database.sessions()
            if entry.agent == agent and entry.agent_session == agent_session
        )
        if not matching:
            return
        ids: frozenset[str] = frozenset(entry.uuid for entry in matching)
        _ = database.update_sessions(
            transform=lambda entries: tuple(
                replace(entry, agent_state=state, last_activity_at=now)
                if entry.uuid in ids
                else entry
                for entry in entries
            ),
        )
        recorder: EventRecorder = EventRecorder(home=home)
        for entry in matching:
            recorder.record(
                event=LakeEvent.create(
                    event_type=EventTypes.agent_state,
                    host_id=entry.host_id,
                    details=EventDetails(
                        session_uuid=entry.uuid,
                        session_id=entry.id,
                        agent=entry.agent,
                        agent_session=entry.agent_session,
                        state=state,
                        cwd=entry.cwd,
                        surface=entry.surface,
                        editor=entry.editor,
                        payload=hook_event,
                    ),
                ),
            )
