# Copyright (c) 2026 Project contributors.
"""Integrate Cursor embedded-agent hooks with Lake Terminal observability."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from json import JSONDecodeError, dump, dumps, load, loads
from pathlib import Path
from secrets import token_hex
from sys import stdin, stdout
from typing import Final, TypedDict, cast

from .core import SessionRepository
from .events import (
    EventDetails,
    EventTypes,
    LakeEvent,
)
from .identity import IdentityStore
from .models import SessionEntry
from .platform import RuntimePlatform
from .privacy import Redactor
from .recorder import EventRecorder

type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)


class Constants:
    """Group Cursor hook integration constants.

    Attributes:
        config_relative: Config relative.
        command_marker: Command marker.
        agent: Agent.
        surface: Surface.
        events: Events.
        id_bytes: Number of random bytes used for short session identifiers.

    """

    config_relative: Final[Path] = Path(".cursor/hooks.json")
    command_marker: Final[str] = "--cursor-hook"
    agent: Final[str] = "cursor"
    surface: Final[str] = "cursor-embedded"
    id_bytes: Final[int] = 3
    events: Final[tuple[str, ...]] = (
        "sessionStart",
        "sessionEnd",
        "beforeShellExecution",
        "afterShellExecution",
        "afterAgentThought",
        "afterAgentResponse",
        "stop",
    )


class _Hook(TypedDict):
    """Represent one Cursor command hook.

    Attributes:
        command: Command.

    """

    command: str


class _Config(TypedDict):
    """Represent the Cursor user hook configuration.

    Attributes:
        version: Version.
        hooks: Hooks.

    """

    version: int
    hooks: dict[str, list[_Hook]]


class CursorHooks:
    """Install and process native Cursor embedded-agent hooks."""

    @classmethod
    def install(cls, *, home: Path, launcher: Path) -> Path:
        """Install Lake Terminal's native Cursor hooks alongside others.

        Args:
            home: Home.
            launcher: Launcher.

        Returns:
            The resulting value.

        """
        path: Path = cls.config_path(home=home)
        payload: _Config = cls._read(path=path)
        command: str = f'"{launcher}" {Constants.command_marker}'
        hooks: dict[str, list[_Hook]] = payload["hooks"]
        for event in Constants.events:
            entries: list[_Hook] = hooks.setdefault(event, [])
            entries[:] = [
                entry
                for entry in entries
                if Constants.command_marker not in entry.get("command", "")
            ]
            entries.append({"command": command})
        cls._write(path=path, payload=payload)
        return path

    @classmethod
    def uninstall(cls, *, home: Path) -> None:
        """Remove only Lake Terminal-owned Cursor hook entries.

        Args:
            home: Home.

        """
        path: Path = cls.config_path(home=home)
        if not path.is_file():
            return
        payload: _Config = cls._read(path=path)
        for event in tuple(payload["hooks"]):
            entries: list[_Hook] = payload["hooks"][event]
            entries[:] = [
                entry
                for entry in entries
                if Constants.command_marker not in entry.get("command", "")
            ]
            if not entries:
                del payload["hooks"][event]
        cls._write(path=path, payload=payload)

    @classmethod
    def run_hook(cls, *, home: Path) -> int:
        """Consume one Cursor hook payload and update Lake state fail-open.

        Args:
            home: Home.

        Returns:
            The resulting value.

        """
        try:
            raw: str = stdin.read()
            decoded: JsonValue = cast(
                typ="JsonValue",
                val=loads(s=raw),
            )
            if not isinstance(decoded, dict):
                cls._respond(event="")
                return 0
            payload: dict[str, JsonValue] = decoded
            event: str = cls._string(
                value=payload.get("hook_event_name"),
            )
            session: str = cls._session(payload=payload)
            if not session:
                cls._respond(event=event)
                return 0
            entry: SessionEntry = cls._ensure_session(
                home=home,
                session=session,
                payload=payload,
            )
            cls._record(
                home=home,
                entry=entry,
                event=event,
                payload=payload,
            )
            cls._respond(
                event=event,
                session=session,
            )
        except (JSONDecodeError, OSError, TypeError, ValueError):
            _ = stdout.write("{}\n")
        return 0

    @staticmethod
    def config_path(*, home: Path) -> Path:
        """Return the native Cursor user hooks path.

        Args:
            home: Home.

        Returns:
            The resulting value.

        """
        return home / Constants.config_relative

    @classmethod
    def _ensure_session(
        cls,
        *,
        home: Path,
        session: str,
        payload: dict[str, JsonValue],
    ) -> SessionEntry:
        """Create or refresh one process-independent embedded-agent session.

        Args:
            home: Home.
            session: Session.
            payload: Payload.

        Returns:
            The resulting value.

        """
        repository: SessionRepository = SessionRepository(home=home)
        current: tuple[SessionEntry, ...] = repository.read()
        existing: SessionEntry | None = next(
            (
                entry
                for entry in current
                if entry.agent == Constants.agent
                and entry.agent_session == session
            ),
            None,
        )
        now: str = datetime.now(tz=UTC).isoformat()
        workspace: str = cls._workspace(payload=payload)
        event: str = cls._string(
            value=payload.get("hook_event_name"),
        )
        if existing is not None:
            updated: SessionEntry = replace(
                existing,
                workspace=workspace or existing.workspace,
                cwd=workspace or existing.cwd,
                last_activity_at=now,
                agent_state=(
                    "DEAD" if event == "sessionEnd" else existing.agent_state
                ),
            )
            _ = repository.update(
                transform=lambda entries: tuple(
                    updated if item.id == existing.id else item
                    for item in entries
                ),
            )
            return updated
        runtime: RuntimePlatform = RuntimePlatform.detect()
        reserved: frozenset[str] = frozenset(item.id for item in current)
        short_id: str = token_hex(nbytes=Constants.id_bytes)
        while short_id in reserved:
            short_id = token_hex(nbytes=Constants.id_bytes)
        created: SessionEntry = SessionEntry(
            id=short_id,
            command="",
            session="",
            created_at=now,
            initiator="agent",
            cwd=workspace,
            shell_location=runtime.shell_location,
            invocation="cursor-hook",
            agent=Constants.agent,
            agent_session=session,
            backend="hook",
            operating_system=runtime.os,
            architecture=runtime.architecture,
            surface=Constants.surface,
            editor="cursor",
            workspace=workspace,
            attribution="cursor-native-hook",
            reconnectable=False,
            last_activity_at=now,
            uuid=IdentityStore.new_id(),
            host_id=IdentityStore(home=home).host_id(),
            agent_state="IDLE",
        )
        repository.add(entry=created)
        return created

    @classmethod
    def _record(
        cls,
        *,
        home: Path,
        entry: SessionEntry,
        event: str,
        payload: dict[str, JsonValue],
    ) -> None:
        """Record normalized state from one Cursor hook event.

        Args:
            home: Home.
            entry: Entry.
            event: Event.
            payload: Payload.

        """
        state_map: Final[dict[str, str]] = {
            "sessionStart": "IDLE",
            "beforeShellExecution": "EXECUTING",
            "afterShellExecution": "IDLE",
            "afterAgentThought": "THINKING",
            "afterAgentResponse": "IDLE",
            "stop": "WAITING_FOR_USER",
            "sessionEnd": "DEAD",
        }
        state: str = state_map.get(event, entry.agent_state)
        command: str = Redactor.redact(
            value=cls._string(
                value=payload.get("command"),
            ),
        )
        now: str = datetime.now(tz=UTC).isoformat()
        updated: SessionEntry = replace(
            entry,
            agent_state=state,
            current_command=command if state == "EXECUTING" else "",
            last_activity_at=now,
        )
        repository: SessionRepository = SessionRepository(home=home)
        _ = repository.update(
            transform=lambda entries: tuple(
                updated if item.id == entry.id else item for item in entries
            ),
        )
        EventRecorder(home=home).record(
            event=LakeEvent.create(
                event_type=cls._event_type(event=event),
                host_id=updated.host_id,
                details=EventDetails(
                    session_uuid=updated.uuid,
                    session_id=updated.id,
                    agent=updated.agent,
                    agent_session=updated.agent_session,
                    state=state,
                    cwd=updated.cwd,
                    command=command,
                    surface=updated.surface,
                    editor=updated.editor,
                ),
            ),
        )

    @classmethod
    def _read(cls, *, path: Path) -> _Config:
        """Read a Cursor hooks file or return an empty native configuration.

        Args:
            path: Path.

        Returns:
            The resulting value.

        """
        if not path.is_file():
            return cls._empty_config()
        try:
            with path.open(mode="r", encoding="utf-8") as source:
                raw: JsonValue = cast(
                    typ="JsonValue",
                    val=load(fp=source),
                )
        except JSONDecodeError:
            return cls._empty_config()
        if not isinstance(raw, dict):
            return cls._empty_config()
        hooks_value: JsonValue = raw.get("hooks")
        if not isinstance(hooks_value, dict):
            return cls._empty_config()
        normalized: dict[str, list[_Hook]] = {}
        for key, value in hooks_value.items():
            entries: list[_Hook] | None = cls._hooks(
                value=value,
            )
            if entries is not None:
                normalized[key] = entries
        return {
            "version": 1,
            "hooks": normalized,
        }

    @classmethod
    def _session(cls, *, payload: dict[str, JsonValue]) -> str:
        """Resolve the Cursor session identifier.

        Args:
            payload: Cursor hook payload.

        Returns:
            The session or conversation identifier.

        """
        session: str = cls._string(
            value=payload.get("session_id"),
        )
        if session:
            return session
        return cls._string(
            value=payload.get("conversation_id"),
        )

    @classmethod
    def _workspace(cls, *, payload: dict[str, JsonValue]) -> str:
        """Resolve a workspace path from a Cursor hook payload.

        Args:
            payload: Cursor hook payload.

        Returns:
            The preferred workspace path.

        """
        workspace_roots: JsonValue = payload.get("workspace_roots")
        if isinstance(workspace_roots, list) and workspace_roots:
            first: JsonValue = workspace_roots[0]
            workspace: str = CursorHooks._string(value=first)
            if workspace:
                return workspace
        return cls._string(
            value=payload.get("cwd"),
        )

    @staticmethod
    def _respond(*, event: str, session: str = "") -> None:
        """Emit a schema-valid non-blocking response for each hook category.

        Args:
            event: Event.
            session: Session.

        """
        response: dict[str, JsonValue] = {}
        if event == "sessionStart":
            response["env"] = {
                "LAKE_AGENT": Constants.agent,
                "LAKE_AGENT_SESSION": session,
                "LAKE_AGENT_SURFACE": Constants.surface,
                "LAKE_EDITOR": "cursor",
            }
        elif event == "beforeShellExecution":
            response["permission"] = "allow"
        _ = stdout.write(
            dumps(
                obj=response,
                separators=(",", ":"),
            )
            + "\n",
        )

    @staticmethod
    def _write(*, path: Path, payload: _Config) -> None:
        """Atomically write the native Cursor hooks configuration.

        Args:
            path: Path.
            payload: Payload.

        """
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path = path.with_suffix(
            suffix=".json.lake-tmp",
        )
        with temporary.open(mode="w", encoding="utf-8") as target:
            dump(
                obj=payload,
                fp=target,
                indent=2,
            )
            _ = target.write("\n")
        _ = temporary.replace(target=path)

    @staticmethod
    def _event_type(*, event: str) -> str:
        """Return the normalized Lake Terminal event type.

        Args:
            event: Cursor hook event name.

        Returns:
            The corresponding Lake Terminal event type.

        """
        if event == "beforeShellExecution":
            return EventTypes.command_started
        if event == "afterShellExecution":
            return EventTypes.command_finished
        return EventTypes.agent_state

    @staticmethod
    def _empty_config() -> _Config:
        """Return a new empty Cursor hook configuration.

        Returns:
            A new empty configuration.

        """
        return {
            "version": 1,
            "hooks": {},
        }

    @staticmethod
    def _hooks(*, value: JsonValue) -> list[_Hook] | None:
        """Normalize one Cursor hook collection.

        Args:
            value: Raw JSON hook collection.

        Returns:
            Normalized hooks, or ``None`` for a non-list value.

        """
        if not isinstance(value, list):
            return None
        entries: list[_Hook] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            command: JsonValue = item.get("command")
            if isinstance(command, str):
                entries.append({"command": command})
        return entries

    @staticmethod
    def _string(*, value: JsonValue) -> str:
        """Convert a JSON scalar value to text.

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
