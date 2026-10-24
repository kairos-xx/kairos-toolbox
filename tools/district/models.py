# Copyright (c) 2026 Project contributors.
"""Canonical District Terminal data models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionEntry:
    """Describe one discoverable District Terminal execution session.

    Attributes:
        id: Id.
        command: Command.
        session: Session.
        created_at: Created at.
        initiator: Initiator.
        pid: Pid.
        parent_pid: Parent pid.
        hostname: Hostname.
        cwd: Cwd.
        shell_location: Shell.
        term: Term.
        terminal_program: Terminal program.
        tty: Tty.
        invocation: Invocation.
        initial_command: Initial command.
        agent: Agent.
        agent_session: Agent session.
        backend: Backend.
        operating_system: Operating system.
        architecture: Architecture.
        surface: Surface.
        editor: Editor.
        workspace: Workspace.
        attribution: Attribution.
        reconnectable: Reconnectable.
        current_command: Current command.
        last_activity_at: Last activity at.
        uuid: Uuid.
        host_id: Host id.
        process_started_token: Process started token.
        current_command_id: Current command id.
        agent_state: Agent state.

    """

    id: str
    command: str
    session: str
    created_at: str = ""
    initiator: str = ""
    pid: int = 0
    parent_pid: int = 0
    hostname: str = ""
    cwd: str = ""
    shell_location: str = ""
    term: str = ""
    terminal_program: str = ""
    tty: str = ""
    invocation: str = ""
    initial_command: str = ""
    agent: str = ""
    agent_session: str = ""
    backend: str = "tmux"
    operating_system: str = ""
    architecture: str = ""
    surface: str = "cli"
    editor: str = ""
    workspace: str = ""
    attribution: str = ""
    reconnectable: bool = True
    current_command: str = ""
    last_activity_at: str = ""
    uuid: str = ""
    host_id: str = ""
    process_started_token: str = ""
    current_command_id: str = ""
    agent_state: str = "IDLE"
