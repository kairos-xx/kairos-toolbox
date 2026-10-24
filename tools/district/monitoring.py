# Copyright (c) 2026 Project contributors.
"""Non-invasive session monitoring and process inspection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from os import kill
from pathlib import Path
from signal import SIG_DFL
from typing import TYPE_CHECKING, Final

from .conpty import ConPtyClient
from .identity import ProcessIdentity
from .process import run
from .process_tree import (
    ProcessInspector,
    ProcessNode,
)

if TYPE_CHECKING:
    from subprocess import CompletedProcess

    from .models import SessionEntry


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionSnapshot:
    """Live observable state for one registered session.

    Attributes:
        id: Id.
        state: State.
        clients: Clients.
        current_command: Current command.
        output: Output.
        age_seconds: Age seconds.
        reconnectable: Reconnectable.
        process_count: Process count.
        workload_pid: Workload pid.

    """

    id: str
    state: str
    clients: int
    current_command: str
    output: str
    age_seconds: int
    reconnectable: bool
    process_count: int = 0
    workload_pid: int = 0


class Monitor:
    """Inspect sessions without sending terminal input.

    Attributes:
        capture_lines: Capture lines.
        shell_names: Shell names.

    """

    capture_lines: Final[int] = 30
    shell_names: Final[frozenset[str]] = frozenset(
        {"bash", "zsh", "sh", "fish", "pwsh", "powershell", "powershell.exe"},
    )

    @classmethod
    def snapshot(cls, *, entry: SessionEntry) -> SessionSnapshot:
        """Capture current state for one persisted session row.

        Args:
            entry: Entry.

        Returns:
            The resulting value.

        """
        clients: int = 0
        command: str = ""
        output: str = ""
        live: bool = False
        if entry.backend == "tmux" and entry.session:
            try:
                state: CompletedProcess[str] = run(
                    args=(
                        "tmux",
                        "display-message",
                        "-p",
                        "-t",
                        entry.session,
                        "#{session_attached}\t#{pane_current_command}",
                    ),
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if state.returncode == 0 and state.stdout:
                    live = True
                    parts: list[str] = state.stdout.strip().split(
                        sep="\t",
                        maxsplit=1,
                    )
                    clients = (
                        int(parts[0]) if parts and parts[0].isdigit() else 0
                    )
                    command = parts[1] if len(parts) > 1 else ""
                    captured: CompletedProcess[str] = run(
                        args=(
                            "tmux",
                            "capture-pane",
                            "-p",
                            "-t",
                            entry.session,
                            "-S",
                            f"-{cls.capture_lines}",
                        ),
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    output = (
                        captured.stdout.rstrip()
                        if captured.returncode == 0 and captured.stdout
                        else ""
                    )
            except OSError:
                live = False
        elif entry.backend == "hook":
            live = entry.agent_state.upper() != "DEAD"
            command = entry.current_command
        elif entry.backend == "conpty" and entry.uuid:
            try:
                client: ConPtyClient = ConPtyClient(home=Path.home())
                live = client.alive(session_uuid=entry.uuid)
                output = client.output(session_uuid=entry.uuid).rstrip()
                command = entry.current_command
            except (ConnectionError, OSError, EOFError):
                live = False
        elif entry.pid > 0:
            live = cls.process_matches(entry=entry)
        descendants: tuple[ProcessNode, ...] = (
            ProcessInspector.descendants(root_pid=entry.pid)
            if live and entry.pid > 0
            else ()
        )
        workload: ProcessNode | None = descendants[-1] if descendants else None
        if (
            not command or command in cls.shell_names
        ) and workload is not None:
            command = workload.command
        state_name: str = cls.state(
            live=live,
            command=command,
            explicit=entry.agent_state,
        )
        created: datetime | None = None
        try:
            created = (
                datetime.fromisoformat(entry.created_at)
                if entry.created_at
                else None
            )
        except ValueError:
            created = None
        age: int = (
            max(0, int((datetime.now(tz=UTC) - created).total_seconds()))
            if created
            else 0
        )
        return SessionSnapshot(
            id=entry.id,
            state=state_name,
            clients=clients,
            current_command=command or entry.current_command,
            output=output,
            age_seconds=age,
            reconnectable=entry.reconnectable,
            process_count=len(descendants),
            workload_pid=workload.pid if workload is not None else 0,
        )

    @classmethod
    def state(cls, *, live: bool, command: str, explicit: str) -> str:
        """Resolve provider state with process evidence as fallback.

        Args:
            live: Live.
            command: Command.
            explicit: Explicit.

        Returns:
            The resulting value.

        """
        if not live:
            return "DEAD"
        normalized: str = explicit.upper().strip()
        if normalized in {
            "EXECUTING",
            "THINKING",
            "WAITING_FOR_TOOL",
            "WAITING_FOR_USER",
        }:
            return normalized
        return (
            "ACTIVE" if command and command not in cls.shell_names else "IDLE"
        )

    @staticmethod
    def process_matches(*, entry: SessionEntry) -> bool:
        """Return whether a PID is live and still identifies the same process.

        Args:
            entry: Entry.

        Returns:
            The resulting value.

        """
        try:
            kill(entry.pid, SIG_DFL)
            if not entry.process_started_token:
                return True
            identity: ProcessIdentity = ProcessIdentity.capture(pid=entry.pid)
        except OSError:
            return False
        else:
            return identity.started_token == entry.process_started_token
