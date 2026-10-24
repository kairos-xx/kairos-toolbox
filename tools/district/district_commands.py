# Copyright (c) 2026 Project contributors.
"""Run district commands for the web console and return display widgets."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from shlex import split as shell_split
from subprocess import CalledProcessError
from typing import TYPE_CHECKING, Final, Protocol, cast, final

from .daemon_control import DaemonControl
from .district_reports import DistrictReports
from .hosts import HostRepository
from .privacy import PrivacyPolicy
from .product import state_directory
from .selftest import SelfTest
from .settings import settings_document
from .storage import DistrictDatabase
from .widgets import (
    CommandResult,
    Console,
    Fact,
    Facts,
    Metric,
    Metrics,
    Notice,
    Tone,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .core import District, ExecResult
    from .json_config import JsonValue
    from .models import SessionEntry


class ParamKind(StrEnum):
    """Say what kind of input a command parameter takes.

    Attributes:
        text: Free text.
        number: A whole number.
        session: The id of a live district.
        choice: One of a fixed list.
        flag: A yes or no switch.

    """

    text = "text"
    number = "number"
    session = "session"
    choice = "choice"
    flag = "flag"


class Danger(StrEnum):
    """Say how much a command changes.

    Attributes:
        safe: Only reads.
        change: Changes something that can be undone.
        destructive: Removes something; the caller must confirm.

    """

    safe = "safe"
    change = "change"
    destructive = "destructive"


class Group(StrEnum):
    """Group commands for the palette.

    Attributes:
        observe: Look at lakes and what they did.
        act: Create, drive or stop a district.
        system: Inspect or maintain the installation.

    """

    observe = "observe"
    act = "act"
    system = "system"


class CommandError(Exception):
    """Report a command that could not run, with an HTTP-style status.

    Attributes:
        message: What went wrong, in words a person can act on.
        status: HTTP-style status: 400, 404, 409, 502 or 504.

    """

    def __init__(self, *, message: str, status: int) -> None:
        """Create the error.

        Args:
            message: What went wrong.
            status: HTTP-style status.

        """
        super().__init__(message)
        self.message: str = message
        self.status: int = status


@dataclass(frozen=True, slots=True, kw_only=True)
class CommandParam:
    """One input of a command.

    Attributes:
        name: Key the value is sent under.
        label: Name shown beside the input.
        kind: What kind of input it is.
        required: Whether the command cannot run without it.
        default: Value used when the input is left empty.
        choices: Allowed values of a ``choice`` input.
        placeholder: Hint shown in an empty input.
        minimum: Smallest allowed ``number``.
        maximum: Largest allowed ``number``.

    """

    name: str
    label: str
    kind: ParamKind = ParamKind.text
    required: bool = False
    default: str = ""
    choices: tuple[str, ...] = field(default=())
    placeholder: str = ""
    minimum: int = 0
    maximum: int = 0

    def as_json(self) -> dict[str, JsonValue]:
        """Render the parameter.

        Returns:
            A JSON-compatible mapping.

        """
        choices: list[JsonValue] = [*self.choices]
        return {
            "name": self.name,
            "label": self.label,
            "kind": self.kind.value,
            "required": self.required,
            "default": self.default,
            "choices": choices,
            "placeholder": self.placeholder,
            "minimum": self.minimum,
            "maximum": self.maximum,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class CommandSpec:
    """Describe one command in the palette.

    Attributes:
        name: Stable command name.
        title: Name shown to people.
        summary: One line saying what it does.
        group: Palette group.
        danger: How much it changes.
        params: Its inputs, in order.

    """

    name: str
    title: str
    summary: str
    group: Group
    danger: Danger = Danger.safe
    params: tuple[CommandParam, ...] = field(default=())

    def as_json(self) -> dict[str, JsonValue]:
        """Render the specification.

        Returns:
            A JSON-compatible mapping.

        """
        return {
            "name": self.name,
            "title": self.title,
            "summary": self.summary,
            "group": self.group.value,
            "danger": self.danger.value,
            "params": [param.as_json() for param in self.params],
        }


@final
class CommandConstants:
    """Group command-layer constants.

    Attributes:
        not_found: Status for an unknown command or district.
        bad_request: Status for invalid input.
        conflict: Status for a missing confirmation.
        bad_gateway: Status for a backend that refused the request.
        timeout: Status for a command that ran too long.
        history_limit: Rows shown when the caller does not choose.
        limit_maximum: Most rows a caller may ask for.
        kinds: Which lakes ``sessions`` can show.

    """

    not_found: Final[int] = 404
    bad_request: Final[int] = 400
    conflict: Final[int] = 409
    bad_gateway: Final[int] = 502
    timeout: Final[int] = 504
    history_limit: Final[int] = 25
    limit_maximum: Final[int] = 200
    kinds: Final[tuple[str, ...]] = ("all", "agents", "own")


class Handler(Protocol):
    """Describe a bound method that runs one command."""

    def __call__(
        self,
        *,
        values: Mapping[str, str],
    ) -> CommandResult:
        """Run the command.

        Args:
            values: Validated input.

        Returns:
            The result to display.

        """
        ...


@final
class DistrictCommands:
    """Run the district commands the web console offers.

    Attributes:
        prefix: Prefix of the methods that implement commands.

    """

    prefix: Final[str] = "_run_"

    def __init__(self, *, service: District, home: Path) -> None:
        """Bind the commands to a session service.

        Args:
            service: Session service.
            home: Home directory holding the district state.

        """
        self._service: District = service
        self._home: Path = home

    @classmethod
    def runnable(cls) -> tuple[str, ...]:
        """List the names that have an implementation.

        Returns:
            Command names.

        """
        return tuple(spec.name for spec in cls.catalog())

    @staticmethod
    def catalog() -> tuple[CommandSpec, ...]:
        """List every command the console can run.

        Returns:
            The specifications, in palette order.

        """
        session: CommandParam = CommandParam(
            name="session",
            label="District",
            kind=ParamKind.session,
            required=True,
        )
        text: CommandParam = CommandParam(
            name="filter",
            label="Contains",
            placeholder="words to look for",
        )
        limit: CommandParam = CommandParam(
            name="limit",
            label="Rows",
            kind=ParamKind.number,
            default=str(object=CommandConstants.history_limit),
            minimum=1,
            maximum=CommandConstants.limit_maximum,
        )
        return (
            CommandSpec(
                name="sessions",
                title="Districts",
                summary="Every live district, agent-run or your own.",
                group=Group.observe,
                params=(
                    CommandParam(
                        name="kind",
                        label="Show",
                        kind=ParamKind.choice,
                        default="all",
                        choices=CommandConstants.kinds,
                    ),
                    text,
                ),
            ),
            CommandSpec(
                name="history",
                title="Command history",
                summary="The commands that ran, newest first.",
                group=Group.observe,
                params=(text, limit),
            ),
            CommandSpec(
                name="events",
                title="Activity",
                summary="What lakes and agents did, newest first.",
                group=Group.observe,
                params=(text, limit),
            ),
            CommandSpec(
                name="hosts",
                title="Remote hosts",
                summary="The machines district can reach.",
                group=Group.observe,
            ),
            CommandSpec(
                name="new",
                title="New district",
                summary="Start a district of your own.",
                group=Group.act,
                danger=Danger.change,
                params=(
                    CommandParam(
                        name="command",
                        label="Run",
                        placeholder="optional, e.g. htop",
                    ),
                    CommandParam(
                        name="cwd",
                        label="Directory",
                        placeholder="optional, defaults to home",
                    ),
                ),
            ),
            CommandSpec(
                name="exec",
                title="Run a command",
                summary="Run a command in a district and show its output.",
                group=Group.act,
                danger=Danger.change,
                params=(
                    session,
                    CommandParam(
                        name="command",
                        label="Command",
                        required=True,
                    ),
                ),
            ),
            CommandSpec(
                name="send",
                title="Send input",
                summary="Type into a district without running a command.",
                group=Group.act,
                danger=Danger.change,
                params=(
                    session,
                    CommandParam(name="text", label="Text"),
                    CommandParam(
                        name="key",
                        label="Key",
                        placeholder="e.g. C-c, M-x, F5",
                    ),
                    CommandParam(
                        name="enter",
                        label="Press Enter after",
                        kind=ParamKind.flag,
                    ),
                ),
            ),
            CommandSpec(
                name="kill",
                title="Stop a district",
                summary="End a district and everything running in it.",
                group=Group.act,
                danger=Danger.destructive,
                params=(session,),
            ),
            CommandSpec(
                name="kill-all",
                title="Stop every district",
                summary="End all lakes, agent-run and your own.",
                group=Group.act,
                danger=Danger.destructive,
            ),
            CommandSpec(
                name="health",
                title="Health check",
                summary="Check this installation end to end.",
                group=Group.system,
            ),
            CommandSpec(
                name="settings",
                title="Settings",
                summary="Every effective setting and where it comes from.",
                group=Group.system,
            ),
            CommandSpec(
                name="storage",
                title="Storage",
                summary="What the district database holds.",
                group=Group.system,
            ),
            CommandSpec(
                name="daemon-start",
                title="Start the daemon",
                summary="Start the background session daemon.",
                group=Group.system,
                danger=Danger.change,
            ),
            CommandSpec(
                name="daemon-stop",
                title="Stop the daemon",
                summary="Stop the background session daemon.",
                group=Group.system,
                danger=Danger.destructive,
            ),
            CommandSpec(
                name="purge",
                title="Apply retention",
                summary="Delete history older than the privacy policy keeps.",
                group=Group.system,
                danger=Danger.destructive,
            ),
        )

    def prepare(
        self,
        *,
        name: str,
        values: Mapping[str, str],
    ) -> tuple[CommandSpec, dict[str, str]]:
        """Look up a command and validate its input without running it.

        Args:
            name: Command name.
            values: Input values by parameter name.

        Returns:
            The specification and the validated values.

        Raises:
            CommandError: When the command is unknown or its input is not
                valid.

        """
        spec: CommandSpec | None = next(
            (item for item in self.catalog() if item.name == name),
            None,
        )
        if spec is None:
            raise CommandError(
                message=f"There is no command called {name!r}.",
                status=CommandConstants.not_found,
            )
        return spec, self._clean(spec=spec, values=values)

    def run(
        self,
        *,
        name: str,
        values: Mapping[str, str],
        confirmed: bool,
    ) -> CommandResult:
        """Validate and run one command.

        Args:
            name: Command name.
            values: Input values by parameter name.
            confirmed: Whether the caller confirmed a destructive command.

        Returns:
            The result to display.

        Raises:
            CommandError: When the command is unknown, its input is invalid,
                a destructive command was not confirmed, or it failed.

        """
        spec, clean = self.prepare(name=name, values=values)
        if spec.danger is Danger.destructive and not confirmed:
            raise CommandError(
                message=f"{spec.title} needs your confirmation.",
                status=CommandConstants.conflict,
            )
        return self._table()[name](values=clean)

    @staticmethod
    def _number(*, param: CommandParam, value: str) -> int:
        """Read a bounded whole number.

        Args:
            param: The parameter being read.
            value: Its text.

        Returns:
            The number, kept within the parameter's bounds.

        Raises:
            CommandError: When the text is not a whole number.

        """
        try:
            number: int = int(value)
        except ValueError as error:
            raise CommandError(
                message=f"{param.label} must be a whole number.",
                status=CommandConstants.bad_request,
            ) from error
        return max(param.minimum, min(param.maximum, number))

    def _table(self) -> dict[str, Handler]:
        """Map each command name to the method that runs it.

        Returns:
            The dispatch table.

        """
        return {
            "sessions": self._run_sessions,
            "history": self._run_history,
            "events": self._run_events,
            "hosts": self._run_hosts,
            "new": self._run_new,
            "exec": self._run_exec,
            "send": self._run_send,
            "kill": self._run_kill,
            "kill-all": self._run_kill_all,
            "health": self._run_health,
            "settings": self._run_settings,
            "storage": self._run_storage,
            "daemon-start": self._run_daemon_start,
            "daemon-stop": self._run_daemon_stop,
            "purge": self._run_purge,
        }

    def _clean(
        self,
        *,
        spec: CommandSpec,
        values: Mapping[str, str],
    ) -> dict[str, str]:
        """Check a command's input and fill in defaults.

        Args:
            spec: The command being run.
            values: Raw input values.

        Returns:
            The validated values, one per parameter.

        Raises:
            CommandError: When a value is missing or not allowed.

        """
        clean: dict[str, str] = {}
        for param in spec.params:
            value: str = values.get(param.name, "").strip() or param.default
            if param.required and not value:
                raise CommandError(
                    message=f"{param.label} is required.",
                    status=CommandConstants.bad_request,
                )
            if param.kind is ParamKind.choice and value not in param.choices:
                raise CommandError(
                    message=f"{param.label} must be one of "
                    f"{', '.join(param.choices)}.",
                    status=CommandConstants.bad_request,
                )
            if param.kind is ParamKind.number:
                value = str(object=self._number(param=param, value=value))
            clean[param.name] = value
        return clean

    def _district(self, *, session_id: str) -> SessionEntry:
        """Find a live district.

        Args:
            session_id: Public district id.

        Returns:
            The district's entry.

        Raises:
            CommandError: When no live district has that id.

        """
        found: SessionEntry | None = next(
            (
                entry
                for entry in self._service.list_entries()
                if entry.id == session_id
            ),
            None,
        )
        if found is None:
            raise CommandError(
                message=f"There is no live district {session_id!r}.",
                status=CommandConstants.not_found,
            )
        return found

    def _run_sessions(self, *, values: Mapping[str, str]) -> CommandResult:
        """List the live lakes.

        Args:
            values: Validated input.

        Returns:
            The result.

        """
        return DistrictReports.sessions(
            entries=self._service.list_entries(),
            kind=values["kind"],
            text=values["filter"],
        )

    def _run_history(self, *, values: Mapping[str, str]) -> CommandResult:
        """List recent commands.

        Args:
            values: Validated input.

        Returns:
            The result.

        """
        return DistrictReports.history(
            rows=DistrictDatabase(home=self._home).command_json(),
            text=values["filter"],
            limit=int(values["limit"]),
        )

    def _run_events(self, *, values: Mapping[str, str]) -> CommandResult:
        """List recent events.

        Args:
            values: Validated input.

        Returns:
            The result.

        """
        return DistrictReports.events(
            rows=DistrictDatabase(home=self._home).event_json(
                filters=tuple(values["filter"].split()),
            ),
            limit=int(values["limit"]),
        )

    def _run_hosts(self, *, values: Mapping[str, str]) -> CommandResult:
        """List remote hosts.

        Args:
            values: Validated input.

        Returns:
            The result.

        """
        _ = values
        return DistrictReports.hosts(
            rows=tuple(
                (host.name, host.target, host.command, host.enabled)
                for host in HostRepository(home=self._home).read()
            ),
        )

    def _run_health(self, *, values: Mapping[str, str]) -> CommandResult:
        """Run the self-test.

        Args:
            values: Validated input.

        Returns:
            The result.

        """
        _ = values
        return DistrictReports.health(report=SelfTest.report(home=self._home))

    def _run_settings(self, *, values: Mapping[str, str]) -> CommandResult:
        """Show the settings report.

        Args:
            values: Validated input.

        Returns:
            The result.

        """
        _ = values
        return DistrictReports.settings(
            document=cast(
                typ="Mapping[str, JsonValue]",
                val=settings_document(home=self._home),
            ),
        )

    def _run_storage(self, *, values: Mapping[str, str]) -> CommandResult:
        """Describe the database.

        Args:
            values: Validated input.

        Returns:
            The result.

        """
        _ = values
        database: DistrictDatabase = DistrictDatabase(home=self._home)
        file: Path = state_directory(home=self._home) / "district.db"
        return DistrictReports.storage(
            counts=database.row_counts(),
            integrity=database.integrity(),
            schema_version=database.schema_version(),
            database_bytes=file.stat().st_size if file.exists() else 0,
        )

    def _run_new(self, *, values: Mapping[str, str]) -> CommandResult:
        """Start a district that belongs to the person, not to an agent.

        Args:
            values: Validated input.

        Returns:
            The result, linking to the new district.

        Raises:
            CommandError: When the command or directory is invalid, or the
                backend cannot start a district.

        """
        try:
            parts: tuple[str, ...] = tuple(shell_split(s=values["command"]))
        except ValueError as error:
            raise CommandError(
                message=f"Run is not a valid command: {error}",
                status=CommandConstants.bad_request,
            ) from error
        cwd: str = values["cwd"]
        if cwd and not Path(cwd).expanduser().is_dir():
            raise CommandError(
                message=f"{cwd!r} is not a directory.",
                status=CommandConstants.bad_request,
            )
        try:
            entry: SessionEntry = self._service.spawn(
                initial_command=parts,
                initiator="user",
                cwd=str(object=Path(cwd).expanduser()) if cwd else "",
            )
        except (RuntimeError, CalledProcessError, OSError) as error:
            raise CommandError(
                message=f"Could not start a district: {error}",
                status=CommandConstants.bad_gateway,
            ) from error
        return CommandResult(
            command="new",
            title="New district",
            ok=True,
            widgets=(
                Notice(
                    tone=Tone.good,
                    text=f"District {entry.id} is running.",
                ),
                Facts(
                    title="District",
                    items=(
                        Fact(
                            label="District",
                            value=entry.id,
                            href=f"#/session/{entry.id}",
                        ),
                        Fact(
                            label="Attach from a terminal",
                            value=f"district {entry.id}",
                        ),
                        Fact(label="Directory", value=entry.cwd or "home"),
                        Fact(
                            label="Started with",
                            value=" ".join(parts) or "a shell",
                        ),
                    ),
                ),
            ),
        )

    def _run_exec(self, *, values: Mapping[str, str]) -> CommandResult:
        """Run a shell command in a district.

        Args:
            values: Validated input.

        Returns:
            The result with the command's output.

        Raises:
            CommandError: When the district is gone or the command times out.

        """
        entry: SessionEntry = self._district(session_id=values["session"])
        try:
            outcome: ExecResult = self._service.execute(
                session_id=entry.id,
                command=values["command"],
            )
        except TimeoutError as error:
            raise CommandError(
                message="The command took too long to finish.",
                status=CommandConstants.timeout,
            ) from error
        except KeyError as error:
            raise CommandError(
                message=f"District {entry.id} ended.",
                status=CommandConstants.not_found,
            ) from error
        succeeded: bool = outcome.exit_status == 0
        return CommandResult(
            command="exec",
            title="Run a command",
            ok=succeeded,
            widgets=(
                Metrics(
                    items=(
                        Metric(
                            label="Result",
                            value=(
                                "Succeeded"
                                if succeeded
                                else f"Failed ({outcome.exit_status})"
                            ),
                            tone=Tone.good if succeeded else Tone.bad,
                        ),
                    ),
                ),
                Console(title=values["command"], text=outcome.output),
            ),
        )

    def _run_send(self, *, values: Mapping[str, str]) -> CommandResult:
        """Type into a district.

        Args:
            values: Validated input.

        Returns:
            The result.

        Raises:
            CommandError: When there is nothing to send, the key is not
                valid, the district is gone, or the backend refused.

        """
        entry: SessionEntry = self._district(session_id=values["session"])
        keys: list[str] = [values["key"]] if values["key"] else []
        if values["enter"] == "1":
            keys.append("Enter")
        if not values["text"] and not keys:
            raise CommandError(
                message="Give some text or a key to send.",
                status=CommandConstants.bad_request,
            )
        try:
            self._service.send(
                session_id=entry.id,
                text=values["text"],
                keys=tuple(keys),
            )
        except ValueError as error:
            raise CommandError(
                message=str(object=error),
                status=CommandConstants.bad_request,
            ) from error
        except KeyError as error:
            raise CommandError(
                message=f"District {entry.id} ended.",
                status=CommandConstants.not_found,
            ) from error
        except (RuntimeError, CalledProcessError, OSError) as error:
            raise CommandError(
                message=f"The district did not accept the input: {error}",
                status=CommandConstants.bad_gateway,
            ) from error
        return CommandResult(
            command="send",
            title="Send input",
            ok=True,
            widgets=(
                Notice(tone=Tone.good, text=f"Sent to district {entry.id}."),
            ),
        )

    def _run_kill(self, *, values: Mapping[str, str]) -> CommandResult:
        """Stop one district.

        Args:
            values: Validated input.

        Returns:
            The result.

        Raises:
            CommandError: When the district is gone or cannot be stopped.

        """
        entry: SessionEntry = self._district(session_id=values["session"])
        try:
            self._service.kill(session_id=entry.id)
        except (KeyError, CalledProcessError, OSError) as error:
            raise CommandError(
                message=f"Could not stop district {entry.id}: {error}",
                status=CommandConstants.bad_gateway,
            ) from error
        return CommandResult(
            command="kill",
            title="Stop a district",
            ok=True,
            widgets=(
                Notice(tone=Tone.good, text=f"District {entry.id} stopped."),
            ),
        )

    def _run_kill_all(self, *, values: Mapping[str, str]) -> CommandResult:
        """Stop every district, carrying on past individual failures.

        Args:
            values: Validated input.

        Returns:
            The result, counting what stopped and what did not.

        """
        _ = values
        stopped: int = 0
        failed: int = 0
        for entry in self._service.list_entries():
            try:
                self._service.kill(session_id=entry.id)
            except (KeyError, CalledProcessError, OSError):
                failed += 1
                continue
            stopped += 1
        return CommandResult(
            command="kill-all",
            title="Stop every district",
            ok=failed == 0,
            widgets=(
                Metrics(
                    items=(
                        Metric(label="Stopped", value=str(object=stopped)),
                        Metric(
                            label="Could not stop",
                            value=str(object=failed),
                            tone=Tone.bad if failed else Tone.good,
                        ),
                    ),
                ),
            ),
        )

    def _run_daemon_start(self, *, values: Mapping[str, str]) -> CommandResult:
        """Start the daemon.

        Args:
            values: Validated input.

        Returns:
            The result.

        """
        _ = values
        started: bool = DaemonControl.start(home=self._home)
        return CommandResult(
            command="daemon-start",
            title="Start the daemon",
            ok=started,
            widgets=(
                Notice(
                    tone=Tone.good if started else Tone.bad,
                    text=(
                        "The daemon is running."
                        if started
                        else "The daemon did not start."
                    ),
                ),
            ),
        )

    def _run_daemon_stop(self, *, values: Mapping[str, str]) -> CommandResult:
        """Stop the daemon.

        Args:
            values: Validated input.

        Returns:
            The result.

        """
        _ = values
        stopped: bool = DaemonControl.stop(home=self._home)
        return CommandResult(
            command="daemon-stop",
            title="Stop the daemon",
            ok=stopped,
            widgets=(
                Notice(
                    tone=Tone.good if stopped else Tone.warn,
                    text=(
                        "The daemon stopped."
                        if stopped
                        else "The daemon was not running."
                    ),
                ),
            ),
        )

    def _run_purge(self, *, values: Mapping[str, str]) -> CommandResult:
        """Delete history older than the privacy policy keeps.

        Args:
            values: Validated input.

        Returns:
            The result, counting what was deleted.

        """
        _ = values
        commands, events = DistrictDatabase(home=self._home).purge(
            policy=PrivacyPolicy.load(home=self._home),
        )
        return CommandResult(
            command="purge",
            title="Apply retention",
            ok=True,
            widgets=(
                Metrics(
                    items=(
                        Metric(
                            label="Commands deleted",
                            value=str(object=commands),
                        ),
                        Metric(
                            label="Events deleted",
                            value=str(object=events),
                        ),
                    ),
                ),
            ),
        )
