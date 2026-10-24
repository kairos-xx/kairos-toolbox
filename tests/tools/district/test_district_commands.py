# Copyright (c) 2026 Project contributors.
"""Tests for the district command layer behind the web command palette."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast, final, override

import pytest
from district.core import (
    District,
    ExecResult,
    SessionRepository,
)
from district.district_commands import (
    CommandError,
    CommandResult,
    Danger,
    DistrictCommands,
)
from district.hosts import HostRepository, RemoteHost
from district.models import SessionEntry
from district.storage import DistrictDatabase

from .helpers import check, expect, section

if TYPE_CHECKING:
    from pathlib import Path


@final
class Constants:
    """Group test constants.

    Attributes:
        destructive: Commands that must ask for confirmation.
        not_found: Status for an unknown command.
        bad_request: Status for invalid input.
        conflict: Status for a missing confirmation.
        failing: Exit status reported for a failing command.

    """

    destructive: Final[frozenset[str]] = frozenset(
        {"daemon-stop", "kill", "kill-all", "purge"},
    )
    not_found: Final[int] = 404
    bad_request: Final[int] = 400
    conflict: Final[int] = 409
    failing: Final[int] = 3


class _Service(District):
    """Record what the command layer asks of the session service.

    Attributes:
        calls: Each call as its name and arguments.

    """

    def __init__(self, *, repository: SessionRepository) -> None:
        """Create the recording service.

        Args:
            repository: Session repository.

        """
        super().__init__(repository=repository)
        self.calls: list[tuple[str, dict[str, object]]] = []

    @override
    def list_entries(self) -> tuple[SessionEntry, ...]:
        """List stored sessions without consulting tmux.

        Returns:
            Every stored session.

        """
        return self.repository.read()

    @override
    def spawn(
        self,
        *,
        initial_command: tuple[str, ...] = (),
        initiator: str = "user",
        cwd: str = "",
    ) -> SessionEntry:
        """Register a session instead of starting one.

        Args:
            initial_command: Initial command.
            initiator: Session initiator.
            cwd: Working directory.

        Returns:
            The registered entry.

        """
        self.calls.append(
            (
                "spawn",
                {
                    "initial_command": initial_command,
                    "initiator": initiator,
                    "cwd": cwd,
                },
            ),
        )
        entry: SessionEntry = SessionEntry(
            id="n1n1n1",
            command="district n1n1n1",
            session="district-n1n1n1",
            initiator=initiator,
            cwd=cwd,
        )
        self.repository.add(entry=entry)
        return entry

    @override
    def kill(self, *, session_id: str) -> None:
        """Record a kill.

        Args:
            session_id: Session id.

        """
        self.calls.append(("kill", {"session_id": session_id}))

    @override
    def execute(self, *, session_id: str, command: str) -> ExecResult:
        """Return a canned result.

        Args:
            session_id: Session id.
            command: Shell command.

        Returns:
            Output and an exit status that depends on the command.

        """
        self.calls.append(
            ("execute", {"session_id": session_id, "command": command}),
        )
        return ExecResult(
            output="hello\n",
            exit_status=Constants.failing if "fail" in command else 0,
        )

    @override
    def send(
        self,
        *,
        session_id: str,
        text: str = "",
        keys: tuple[str, ...] = (),
    ) -> None:
        """Record input.

        Args:
            session_id: Session id.
            text: Literal text.
            keys: Key names.

        """
        self.calls.append(
            (
                "send",
                {"session_id": session_id, "text": text, "keys": keys},
            ),
        )


def _setup(*, home: Path) -> tuple[_Service, DistrictCommands]:
    """Build a recording service and the command layer over it.

    Args:
        home: Scratch home directory.

    Returns:
        The service and the commands.

    """
    service: _Service = _Service(repository=SessionRepository(home=home))
    return service, DistrictCommands(service=service, home=home)


def _entry(
    *,
    session_id: str,
    initiator: str,
    agent: str = "",
) -> SessionEntry:
    """Build a registered-session fixture.

    Args:
        session_id: Public id.
        initiator: ``agent`` or ``user``.
        agent: Attributed agent name.

    Returns:
        The entry.

    """
    return SessionEntry(
        id=session_id,
        command=f"district {session_id}",
        session=f"district-{session_id}",
        initiator=initiator,
        agent=agent,
        cwd="/work",
    )


def _widgets(*, result: CommandResult, kind: str) -> list[dict[str, object]]:
    """Pick the widgets of one type out of a result.

    Args:
        result: Command result.
        kind: Widget type name.

    Returns:
        The matching widgets, rendered as JSON.

    """
    widgets: list[object] = cast(
        typ="list[object]",
        val=result.as_json()["widgets"],
    )
    return [
        section(value=item)
        for item in widgets
        if section(value=item)["type"] == kind
    ]


def test_every_catalogued_command_can_be_run_and_names_are_unique() -> None:
    """The catalogue and the dispatcher cannot drift apart."""
    names: list[str] = [spec.name for spec in DistrictCommands.catalog()]
    expect(actual=len(names), expected=len(set(names)))
    expect(actual=set(names), expected=set(DistrictCommands.runnable()))


def test_only_commands_that_destroy_state_ask_for_confirmation() -> None:
    """Exactly the destructive commands are marked destructive."""
    marked: set[str] = {
        spec.name
        for spec in DistrictCommands.catalog()
        if spec.danger is Danger.destructive
    }
    expect(actual=marked, expected=set(Constants.destructive))


def test_an_unknown_command_is_not_found(tmp_path: Path) -> None:
    """Running a name that is not in the catalogue is a 404.

    Args:
        tmp_path: Scratch home directory.

    """
    _, commands = _setup(home=tmp_path)
    with pytest.raises(expected_exception=CommandError) as raised:
        _ = commands.run(name="rm-rf", values={}, confirmed=False)
    expect(actual=raised.value.status, expected=Constants.not_found)


def test_a_missing_required_value_names_the_field(tmp_path: Path) -> None:
    """Leaving a required field empty is a 400 that says which one.

    Args:
        tmp_path: Scratch home directory.

    """
    _, commands = _setup(home=tmp_path)
    with pytest.raises(expected_exception=CommandError) as raised:
        _ = commands.run(name="exec", values={"session": "a"}, confirmed=False)
    expect(actual=raised.value.status, expected=Constants.bad_request)
    check(
        condition="Command" in raised.value.message,
        reason="the message should name the missing field",
    )


def test_a_destructive_command_does_nothing_without_confirmation(
    tmp_path: Path,
) -> None:
    """The service is never touched until the caller confirms.

    Args:
        tmp_path: Scratch home directory.

    """
    service, commands = _setup(home=tmp_path)
    with pytest.raises(expected_exception=CommandError) as raised:
        _ = commands.run(
            name="kill",
            values={"session": "a1b2c3"},
            confirmed=False,
        )
    expect(actual=raised.value.status, expected=Constants.conflict)
    expect(actual=service.calls, expected=[])


def test_a_confirmed_kill_reaches_the_service(tmp_path: Path) -> None:
    """Confirmation lets the destructive command through.

    Args:
        tmp_path: Scratch home directory.

    """
    service, commands = _setup(home=tmp_path)
    service.repository.add(
        entry=_entry(session_id="a1b2c3", initiator="user"),
    )
    result: CommandResult = commands.run(
        name="kill",
        values={"session": "a1b2c3"},
        confirmed=True,
    )
    expect(actual=result.ok, expected=True)
    expect(actual=service.calls, expected=[("kill", {"session_id": "a1b2c3"})])


def test_sessions_separate_agent_districts_from_your_own(
    tmp_path: Path,
) -> None:
    """The summary counts each kind and the table lists every district.

    Args:
        tmp_path: Scratch home directory.

    """
    service, commands = _setup(home=tmp_path)
    service.repository.add(
        entry=_entry(session_id="aaaaaa", initiator="agent", agent="devin"),
    )
    service.repository.add(
        entry=_entry(session_id="bbbbbb", initiator="user"),
    )
    result: CommandResult = commands.run(
        name="sessions",
        values={},
        confirmed=False,
    )
    metrics: dict[str, object] = _widgets(result=result, kind="metrics")[0]
    labels: list[str] = [
        str(object=section(value=item)["label"])
        for item in cast(typ="list[object]", val=metrics["items"])
    ]
    expect(actual=labels, expected=["Districts", "Agent-run", "Your own"])
    table: dict[str, object] = _widgets(result=result, kind="table")[0]
    expect(actual=len(cast(typ="list[object]", val=table["rows"])), expected=2)


def test_the_sessions_filter_can_show_only_your_own_districts(
    tmp_path: Path,
) -> None:
    """Choosing "own" hides every agent-run district.

    Args:
        tmp_path: Scratch home directory.

    """
    service, commands = _setup(home=tmp_path)
    service.repository.add(
        entry=_entry(session_id="aaaaaa", initiator="agent", agent="devin"),
    )
    service.repository.add(
        entry=_entry(session_id="bbbbbb", initiator="user"),
    )
    result: CommandResult = commands.run(
        name="sessions",
        values={"kind": "own"},
        confirmed=False,
    )
    table: dict[str, object] = _widgets(result=result, kind="table")[0]
    rows: list[object] = cast(typ="list[object]", val=table["rows"])
    expect(
        actual=[section(value=row)["id"] for row in rows],
        expected=["bbbbbb"],
    )


def test_history_lists_recent_commands_with_their_outcome(
    tmp_path: Path,
) -> None:
    """Rows carry the exit code and honour the limit and filter.

    Args:
        tmp_path: Scratch home directory.

    """
    _, commands = _setup(home=tmp_path)
    database: DistrictDatabase = DistrictDatabase(home=tmp_path)
    for index, command in enumerate(iterable=("ls", "make test", "make lint")):
        database.command_started(
            command_id=f"c{index}",
            session_uuid="u",
            session_id="aaaaaa",
            host_id="h",
            agent="devin",
            agent_session="s",
            surface="cli",
            editor="",
            command=command,
            cwd="/work",
            started_at=f"2026-01-01T00:00:0{index}+00:00",
        )
        database.command_finished(
            command_id=f"c{index}",
            finished_at=f"2026-01-01T00:00:0{index}+00:00",
            exit_code=Constants.failing if command == "make test" else 0,
            duration_seconds=1.5,
        )
    result: CommandResult = commands.run(
        name="history",
        values={"filter": "make", "limit": "10"},
        confirmed=False,
    )
    table: dict[str, object] = _widgets(result=result, kind="table")[0]
    rows: list[object] = cast(typ="list[object]", val=table["rows"])
    expect(
        actual=[section(value=row)["command"] for row in rows],
        expected=["make lint", "make test"],
    )
    expect(
        actual=[section(value=row)["exit_code"] for row in rows],
        expected=[0, Constants.failing],
    )


def test_health_reports_each_check(tmp_path: Path) -> None:
    """The self-test appears as a pass or fail list.

    Args:
        tmp_path: Scratch home directory.

    """
    _, commands = _setup(home=tmp_path)
    result: CommandResult = commands.run(
        name="health",
        values={},
        confirmed=False,
    )
    items: list[object] = cast(
        typ="list[object]",
        val=_widgets(result=result, kind="checks")[0]["items"],
    )
    names: list[object] = [section(value=item)["name"] for item in items]
    check(
        condition="python_runtime" in names,
        reason="the interpreter check should be listed",
    )


def test_a_new_district_is_your_own_and_starts_where_you_ask(
    tmp_path: Path,
) -> None:
    """Creating a district from the web is never agent-attributed.

    Args:
        tmp_path: Scratch home directory.

    """
    service, commands = _setup(home=tmp_path)
    result: CommandResult = commands.run(
        name="new",
        values={"command": "htop -d 5", "cwd": str(object=tmp_path)},
        confirmed=False,
    )
    expect(
        actual=service.calls,
        expected=[
            (
                "spawn",
                {
                    "initial_command": ("htop", "-d", "5"),
                    "initiator": "user",
                    "cwd": str(object=tmp_path),
                },
            ),
        ],
    )
    facts: dict[str, object] = _widgets(result=result, kind="facts")[0]
    hrefs: list[object] = [
        section(value=item)["href"]
        for item in cast(typ="list[object]", val=facts["items"])
    ]
    check(
        condition="#/session/n1n1n1" in hrefs,
        reason="the new district should link to its own page",
    )


def test_a_new_district_refuses_a_directory_that_does_not_exist(
    tmp_path: Path,
) -> None:
    """A bad working directory is rejected before anything starts.

    Args:
        tmp_path: Scratch home directory.

    """
    service, commands = _setup(home=tmp_path)
    with pytest.raises(expected_exception=CommandError) as raised:
        _ = commands.run(
            name="new",
            values={"cwd": str(object=tmp_path / "missing")},
            confirmed=False,
        )
    expect(actual=raised.value.status, expected=Constants.bad_request)
    expect(actual=service.calls, expected=[])


def test_exec_shows_the_output_and_marks_a_failure(tmp_path: Path) -> None:
    """A non-zero exit makes the result not ok and still shows the output.

    Args:
        tmp_path: Scratch home directory.

    """
    service, commands = _setup(home=tmp_path)
    service.repository.add(
        entry=_entry(session_id="a1b2c3", initiator="user"),
    )
    result: CommandResult = commands.run(
        name="exec",
        values={"session": "a1b2c3", "command": "fail now"},
        confirmed=False,
    )
    expect(actual=result.ok, expected=False)
    console: dict[str, object] = _widgets(result=result, kind="console")[0]
    expect(actual=console["text"], expected="hello\n")


def test_send_passes_text_and_the_requested_key(tmp_path: Path) -> None:
    """Text goes first and the key follows, with Enter only when asked.

    Args:
        tmp_path: Scratch home directory.

    """
    service, commands = _setup(home=tmp_path)
    service.repository.add(
        entry=_entry(session_id="a1b2c3", initiator="user"),
    )
    _ = commands.run(
        name="send",
        values={
            "session": "a1b2c3",
            "text": "ls",
            "key": "C-c",
            "enter": "1",
        },
        confirmed=False,
    )
    expect(
        actual=service.calls,
        expected=[
            (
                "send",
                {
                    "session_id": "a1b2c3",
                    "text": "ls",
                    "keys": ("C-c", "Enter"),
                },
            ),
        ],
    )


def test_settings_are_grouped_into_facts(tmp_path: Path) -> None:
    """The settings report becomes titled groups of labelled values.

    Args:
        tmp_path: Scratch home directory.

    """
    _, commands = _setup(home=tmp_path)
    result: CommandResult = commands.run(
        name="settings",
        values={},
        confirmed=False,
    )
    check(
        condition=len(_widgets(result=result, kind="facts")) > 1,
        reason="several settings groups are expected",
    )


def test_hosts_lists_configured_machines_without_contacting_them(
    tmp_path: Path,
) -> None:
    """Hosts come from the configuration, so listing them is instant.

    Args:
        tmp_path: Scratch home directory.

    """
    _, commands = _setup(home=tmp_path)
    repository: HostRepository = HostRepository(home=tmp_path)
    repository.upsert(host=RemoteHost(name="box", target="me@box"))
    repository.upsert(
        host=RemoteHost(name="old", target="me@old", enabled=False),
    )
    result: CommandResult = commands.run(
        name="hosts",
        values={},
        confirmed=False,
    )
    rows: list[object] = cast(
        typ="list[object]",
        val=_widgets(result=result, kind="table")[0]["rows"],
    )
    expect(
        actual=[
            (section(value=row)["name"], section(value=row)["status"])
            for row in rows
        ],
        expected=[("box", "enabled"), ("old", "disabled")],
    )
