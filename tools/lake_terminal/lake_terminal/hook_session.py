# Copyright (c) 2026 Project contributors.
"""Hook-backed sessions for agents whose shells Lake cannot own."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex
from typing import TYPE_CHECKING, Final

from .identity import IdentityStore
from .lifecycle import CommandLifecycle
from .models import SessionEntry
from .platform import RuntimePlatform
from .storage import LakeDatabase

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .json_config import JsonValue


class Constants:
    """Group hook-session constants.

    Attributes:
        backend: Backend label of non-reconnectable hook sessions.
        id_bytes: Random bytes used for a short session identifier.
        failure_exit: Exit code recorded for a failed tool call.

    """

    backend: Final[str] = "hook"
    id_bytes: Final[int] = 3
    failure_exit: Final[int] = 1


class HookSessions:
    """Record provider-native tool activity as an observable session.

    Claude Code and Devin execute shell commands in their own non-interactive
    processes, so there is no PTY to reconnect to. A hook session still gives
    those executions one honest identity: provider, provider session,
    workspace, state, and command history.
    """

    @staticmethod
    def ensure(
        *,
        home: Path,
        agent: str,
        agent_session: str,
        workspace: str,
        create: bool = True,
    ) -> SessionEntry | None:
        """Return the hook session for a provider session, creating it.

        Args:
            home: Lake home directory.
            agent: Canonical provider identifier.
            agent_session: Provider session identifier.
            workspace: Working directory reported by the provider.
            create: Whether a missing session may be created.

        Returns:
            The session, or None when absent and ``create`` is false.

        """
        found: list[SessionEntry] = []
        now: str = datetime.now(tz=UTC).isoformat()
        runtime: RuntimePlatform = RuntimePlatform.detect()
        host_id: str = IdentityStore(home=home).host_id()

        def transform(
            entries: tuple[SessionEntry, ...],
        ) -> tuple[SessionEntry, ...]:
            """Add or refresh the matching hook session atomically.

            Args:
                entries: Current canonical sessions.

            Returns:
                Sessions including the hook session.

            """
            existing: SessionEntry | None = next(
                (
                    item
                    for item in entries
                    if item.backend == Constants.backend
                    and item.agent == agent
                    and item.agent_session == agent_session
                ),
                None,
            )
            if existing is not None:
                refreshed: SessionEntry = replace(
                    existing,
                    cwd=workspace or existing.cwd,
                    workspace=workspace or existing.workspace,
                    last_activity_at=now,
                )
                found.append(refreshed)
                return tuple(
                    refreshed if item.id == existing.id else item
                    for item in entries
                )
            if not create:
                return entries
            reserved: frozenset[str] = frozenset(item.id for item in entries)
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
                invocation=f"{agent}-hook",
                agent=agent,
                agent_session=agent_session,
                backend=Constants.backend,
                operating_system=runtime.os,
                architecture=runtime.architecture,
                surface=RuntimePlatform.surface(),
                editor=RuntimePlatform.editor(),
                workspace=workspace,
                attribution=f"{agent}-native-hook",
                reconnectable=False,
                last_activity_at=now,
                uuid=IdentityStore.new_id(),
                host_id=host_id,
                agent_state="IDLE",
            )
            found.append(created)
            return (*entries, created)

        _ = LakeDatabase(home=home).update_sessions(transform=transform)
        return found[0] if found else None

    @staticmethod
    def command_started(
        *,
        home: Path,
        entry: SessionEntry,
        tool_use_id: str,
        command: str,
        cwd: str,
    ) -> None:
        """Record the start of a provider shell tool call.

        Args:
            home: Lake home directory.
            entry: Hook session owning the command.
            tool_use_id: Provider tool-use identifier, correlating the
                later completion hook.
            command: Shell command.
            cwd: Working directory.

        """
        _ = CommandLifecycle(home=home).start(
            session_id=entry.id,
            command=command,
            cwd=cwd or entry.cwd or str(object=Path.cwd()),
            command_id=tool_use_id,
        )

    @staticmethod
    def command_finished(
        *,
        home: Path,
        entry: SessionEntry,
        tool_use_id: str,
        failed: bool,
        response: Mapping[str, JsonValue] | None = None,
    ) -> None:
        """Record completion of a provider shell tool call.

        Args:
            home: Lake home directory.
            entry: Hook session owning the command.
            tool_use_id: Identifier given to :meth:`command_started`.
            failed: Whether the provider reported the call as failed.
            response: Provider tool response, read for an explicit exit code.

        """
        exit_code: int = Constants.failure_exit if failed else 0
        for key in ("exit_code", "exitCode", "returncode"):
            value: JsonValue = (response or {}).get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                exit_code = value
                break
        CommandLifecycle(home=home).finish(
            session_id=entry.id,
            command_id=tool_use_id,
            exit_code=exit_code,
        )
