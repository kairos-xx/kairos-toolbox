# Copyright (c) 2026 Project contributors.
"""Detect interactive shells launched under configured local coding agents."""

from __future__ import annotations

from dataclasses import dataclass
from json import JSONDecodeError, dump, load, loads
from os import environ, getppid
from os import name as os_name
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Final, Protocol, TypedDict, cast

from .agent_identity import AgentIdentity
from .process import run

if TYPE_CHECKING:
    from subprocess import CompletedProcess


class AgentProvider(Protocol):
    """Define installation operations for coding-agent providers."""

    @classmethod
    def install(cls, *, home: Path, launcher: Path) -> Path:
        """Install provider integration.

        Args:
            home: Home directory.
            launcher: District Terminal launcher path.

        Returns:
            Provider integration target path.

        """
        ...

    @classmethod
    def uninstall(cls, *, home: Path) -> None:
        """Remove provider integration owned by District Terminal.

        Args:
            home: Home directory.

        """
        ...


class Constants:
    """Group immutable agent-detection configuration.

    Attributes:
        maximum_ancestors: Maximum ancestors.
        config_directory_name: Config directory name.
        agents_name: Agents name.
        desktop_chat_marker: Path fragment of the Claude Desktop chat app,
            whose processes are never coding-agent shells.

    """

    desktop_chat_marker: Final[str] = "/applications/claude.app/"
    maximum_ancestors: Final[int] = 32
    config_directory_name: Final[str] = ".district"
    agents_name: Final[str] = "agents.json"


class _AgentJson(TypedDict, total=False):
    """Persisted JSON shape for one agent detector.

    Attributes:
        enabled: Enabled.
        name: Name.
        processes: Processes.
        process_contains: Process contains.
        command_contains: Command contains.
        command_matches: Command matches.
        environment: Environment.

    """

    enabled: bool
    name: str
    processes: list[str]
    process_contains: list[str]
    command_contains: list[str]
    command_matches: list[str]
    environment: list[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentType:
    """Describe one configurable local coding-agent signature.

    Attributes:
        identifier: Identifier.
        name: Name.
        enabled: Enabled.
        processes: Processes.
        process_contains: Process contains.
        command_contains: Command contains.
        command_matches: Command matches.
        environment: Environment.

    """

    identifier: str
    name: str
    enabled: bool
    processes: tuple[str, ...]
    process_contains: tuple[str, ...]
    command_contains: tuple[str, ...]
    command_matches: tuple[str, ...]
    environment: tuple[str, ...]


type JsonValue = (
    bool
    | int
    | float
    | str
    | list["JsonValue"]
    | dict[str, "JsonValue"]
    | None
)


class AgentConfiguration:
    """Load and create the user-editable agent signature configuration."""

    @classmethod
    def load(cls, *, home: Path) -> tuple[AgentType, ...]:
        """Load configured agent signatures, creating defaults when absent.

        Args:
            home: Home.

        Returns:
            The resulting value.

        Raises:
            TypeError: When the operation cannot complete.
            ValueError: When the operation cannot complete.

        """
        path: Path = (
            home / Constants.config_directory_name / Constants.agents_name
        )
        if not path.exists():
            agents: tuple[AgentType, ...] = cls.default_agents()
            cls.write(path=path, agents=agents)
            return agents
        try:
            with path.open(mode="r", encoding="utf-8") as config_file:
                payload: JsonValue = cast(
                    typ="JsonValue",
                    val=load(fp=config_file),
                )
        except JSONDecodeError as error:
            msg: str = f"Agent configuration contains invalid JSON: {path}"
            raise ValueError(msg) from error
        if not isinstance(payload, dict):
            msg = "Agent configuration root must be a JSON object."
            raise TypeError(msg)
        raw_agents: JsonValue = payload.get("agents")
        if not isinstance(raw_agents, dict):
            msg = "Agent configuration must contain an 'agents' object."
            raise TypeError(msg)
        return tuple(
            cls._parse(identifier=identifier, payload=value)
            for key, value in raw_agents.items()
            if (identifier := key)
        )

    @staticmethod
    def default_agents() -> tuple[AgentType, ...]:
        """Return the built-in signatures written on first use.

        Returns:
            The resulting value.

        """
        return (
            AgentType(
                identifier="codex",
                name="Codex",
                enabled=True,
                processes=("codex",),
                process_contains=("codex",),
                command_contains=("codex",),
                command_matches=(),
                environment=(
                    "CODEX_SESSION_ID",
                    "CODEX_THREAD_ID",
                    "CODEX_SANDBOX",
                ),
            ),
            AgentType(
                identifier="claude-code",
                name="Claude Code",
                enabled=True,
                processes=("claude",),
                process_contains=("claude", "claude-code"),
                command_contains=("claude", "claude-code"),
                command_matches=(),
                environment=("CLAUDE_SESSION_ID", "CLAUDECODE"),
            ),
            AgentType(
                identifier="cursor",
                name="Cursor",
                enabled=True,
                processes=("cursor-agent",),
                process_contains=("cursor-agent",),
                command_contains=("cursor-agent",),
                command_matches=(),
                environment=("CURSOR_SESSION_ID", "CURSOR_AGENT"),
            ),
            AgentType(
                identifier="devin",
                name="Devin",
                enabled=True,
                processes=(),
                process_contains=(),
                command_contains=(),
                command_matches=("devin acp", "devin sandbox"),
                environment=(),
            ),
        )

    @staticmethod
    def write(*, path: Path, agents: tuple[AgentType, ...]) -> None:
        """Write agent signatures as human-editable JSON.

        Args:
            path: Path.
            agents: Agents.

        """
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, dict[str, _AgentJson]] = {
            "agents": {
                agent.identifier: {
                    "enabled": agent.enabled,
                    "name": agent.name,
                    "processes": list(agent.processes),
                    "process_contains": list(agent.process_contains),
                    "command_contains": list(agent.command_contains),
                    "command_matches": list(agent.command_matches),
                    "environment": list(agent.environment),
                }
                for agent in agents
            },
        }
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary_file:
            temporary_path: Path = Path(temporary_file.name)
            dump(obj=payload, fp=temporary_file, indent=2)
            _ = temporary_file.write("\n")
        _ = temporary_path.replace(target=path)

    @staticmethod
    def _parse(*, identifier: str, payload: JsonValue) -> AgentType:
        """Parse one configured agent signature.

        Args:
            identifier: Identifier.
            payload: Payload.

        Returns:
            The resulting value.

        Raises:
            TypeError: When the operation cannot complete.

        """
        if not isinstance(payload, dict):
            msg: str = f"Agent '{identifier}' must be a JSON object."
            raise TypeError(msg)

        def strings(
            *,
            key: str,
            default: tuple[str, ...] | None = None,
        ) -> tuple[str, ...]:
            """Handle strings.

            Args:
                key: Key.
                default: Value used when the key is absent.

            Returns:
                The resulting value.

            Raises:
                TypeError: When the operation cannot complete.

            """
            value: JsonValue = payload.get(key)
            if value is None and default is not None:
                return default
            if not isinstance(value, list) or not all(
                isinstance(element, str) for element in value
            ):
                msg: str = (
                    f"Agent '{identifier}' field '{key}' "
                    "must be a string array."
                )
                raise TypeError(msg)
            return tuple(
                element for element in value if isinstance(element, str)
            )

        name: JsonValue = payload.get("name", identifier)
        enabled: JsonValue = payload.get("enabled", True)
        if (
            not isinstance(name, str)
            or not isinstance(enabled, bool)
            or not name
        ):
            msg = f"Agent '{identifier}' has invalid name/enabled fields."
            raise TypeError(msg)
        return AgentType(
            identifier=identifier,
            name=name,
            enabled=enabled,
            processes=strings(key="processes"),
            process_contains=strings(key="process_contains"),
            command_contains=strings(key="command_contains"),
            command_matches=strings(key="command_matches", default=()),
            environment=strings(key="environment"),
        )


class AgentDetector:
    """Identify whether the current shell belongs to a local agent."""

    @classmethod
    def is_agent_shell(cls) -> bool:
        """Return whether environment or process ancestry identifies an agent.

        Returns:
            The resulting value.

        """
        if environ.get("DISTRICT_SESSION_ID") is not None:
            return False
        if environ.get("DISTRICT_DISABLE_AGENT"):
            return False
        return cls.detect() is not None

    @classmethod
    def detect(cls) -> tuple[str, str] | None:
        """Detect the strongest configurable provider identity.

        Returns:
            Provider identifier and evidence source, or None when unknown.

        """
        home: Path = Path(environ.get("HOME", str(object=Path.home())))
        configured: tuple[AgentType, ...] = AgentConfiguration.load(home=home)
        agents: tuple[AgentType, ...] = tuple(
            agent for agent in configured if agent.enabled
        )
        disabled: frozenset[str] = frozenset(
            agent.identifier for agent in configured if not agent.enabled
        )
        identity: AgentIdentity | None = AgentIdentity.from_environment()
        if identity is not None and identity.agent not in disabled:
            return identity.agent, "environment"
        for agent in agents:
            if any(environ.get(marker) for marker in agent.environment):
                return agent.identifier, "environment"
        commands: tuple[str, ...] = cls.ancestor_commands()
        for agent in agents:
            if any(
                cls.matches(command=command, agent=agent)
                for command in commands
            ):
                return agent.identifier, "process_tree"
        return None

    @classmethod
    def ancestor_commands(cls) -> tuple[str, ...]:
        """Return command lines for the current process ancestry.

        Returns:
            The resulting value.

        """
        if os_name == "nt":
            return cls.windows_ancestor_commands()
        commands: list[str] = []
        pid: int = getppid()
        seen: set[int] = set()
        while (
            pid > 1
            and pid not in seen
            and len(commands) < Constants.maximum_ancestors
        ):
            seen.add(pid)
            result: CompletedProcess[str] = run(
                args=(
                    "ps",
                    "-o",
                    "ppid=",
                    "-o",
                    "command=",
                    "-p",
                    str(object=pid),
                ),
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0 or not (line := result.stdout.strip()):
                break
            parent_text, separator, command = line.partition(" ")
            if not separator:
                break
            try:
                parent_pid: int = int(parent_text)
            except ValueError:
                break
            commands.append(command.lstrip())
            pid = parent_pid
        return tuple(commands)

    @staticmethod
    def windows_ancestor_commands() -> tuple[str, ...]:
        """Return command lines for the current Windows process ancestry.

        Returns:
            Ancestor command lines, nearest first; empty when unreadable.

        """
        script: str = (
            "Get-CimInstance Win32_Process | "
            "Select-Object ProcessId,ParentProcessId,CommandLine | "
            "ConvertTo-Json -Compress"
        )
        try:
            result: CompletedProcess[str] = run(
                args=("powershell.exe", "-NoProfile", "-Command", script),
                check=False,
                capture_output=True,
                text=True,
            )
            rows: JsonValue = cast(typ="JsonValue", val=loads(s=result.stdout))
        except (OSError, JSONDecodeError):
            return ()
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            return ()
        table: dict[int, tuple[int, str]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            pid: JsonValue = row.get("ProcessId")
            parent: JsonValue = row.get("ParentProcessId")
            line: JsonValue = row.get("CommandLine")
            if isinstance(pid, int) and isinstance(parent, int):
                table[pid] = (parent, line if isinstance(line, str) else "")
        commands: list[str] = []
        pid_now: int = getppid()
        seen: set[int] = set()
        while (
            pid_now in table
            and pid_now not in seen
            and len(commands) < Constants.maximum_ancestors
        ):
            seen.add(pid_now)
            pid_now, command = table[pid_now]
            commands.append(command)
        return tuple(commands)

    @staticmethod
    def matches(*, command: str, agent: AgentType) -> bool:
        """Return whether one ancestor command matches an agent signature.

        Args:
            command: Command.
            agent: Agent.

        Returns:
            The resulting value.

        """
        normalized: str = command.lower().replace("\\", "/")
        if Constants.desktop_chat_marker in normalized:
            return False
        executable: str = normalized.split(maxsplit=1)[0].rsplit(
            sep="/",
            maxsplit=1,
        )[-1]
        words: set[str] = {
            word.rsplit(sep="/", maxsplit=1)[-1] for word in normalized.split()
        }
        return (
            executable in {value.lower() for value in agent.processes}
            or any(
                value.lower() in executable for value in agent.process_contains
            )
            or bool(
                words & {value.lower() for value in agent.command_contains},
            )
            or any(
                value.lower() in normalized for value in agent.command_matches
            )
        )
