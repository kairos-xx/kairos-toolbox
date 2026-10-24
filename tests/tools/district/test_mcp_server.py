# Copyright (c) 2026 Project contributors.
"""District Terminal MCP stdio server tests."""

from __future__ import annotations

from io import BytesIO
from json import dumps, loads
from typing import TYPE_CHECKING, cast

from district.cli import Cli
from district.core import (
    District,
    ExecResult,
    SessionRepository,
)
from district.events import DistrictEvent
from district.mcp_server import (
    DistrictMcpServer,
    JsonObject,
    JsonValue,
)
from district.models import SessionEntry
from district.monitoring import (
    Monitor,
    SessionSnapshot,
)

from .helpers import check, expect

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from district.storage import DistrictDatabase


def _frame(*, payload: JsonObject) -> bytes:
    """Encode one MCP request in Content-Length framing.

    Args:
        payload: JSON-RPC request object.

    Returns:
        Framed request bytes.

    """
    body: bytes = dumps(obj=payload).encode(encoding="utf-8")
    return (
        b"Content-Length: "
        + str(object=len(body)).encode(encoding="ascii")
        + b"\r\nContent-Type: application/json\r\n\r\n"
        + body
    )


def _responses(*, output: bytes) -> list[JsonObject]:
    """Decode all framed MCP responses from server output.

    Args:
        output: Raw server output bytes.

    Returns:
        Parsed response objects.

    """
    results: list[JsonObject] = []
    stream: BytesIO = BytesIO(initial_bytes=output)
    while header := stream.readline():
        length: int = int(header.decode(encoding="utf-8").partition(":")[2])
        _ = stream.readline()
        results.append(
            cast(typ="JsonObject", val=loads(s=stream.read(length))),
        )
    return results


def _service(*, tmp_path: Path) -> District:
    """Build a District Terminal service over a temporary home.

    Args:
        tmp_path: Temporary home directory.

    Returns:
        The session service.

    """
    return District(repository=SessionRepository(home=tmp_path))


def _exchange(
    *,
    tmp_path: Path,
    requests: list[JsonObject],
    service: District | None = None,
) -> list[JsonObject]:
    """Serve framed requests and collect the responses.

    Args:
        tmp_path: Temporary home directory.
        requests: JSON-RPC request objects.
        service: Optional preconfigured session service.

    Returns:
        Response objects, one per non-notification request.

    """
    raw: bytes = b"".join(_frame(payload=request) for request in requests)
    output: BytesIO = BytesIO()
    server: DistrictMcpServer = DistrictMcpServer(
        service=service or _service(tmp_path=tmp_path),
        reader=BytesIO(initial_bytes=raw),
        writer=output,
    )
    expect(actual=server.serve(), expected=0)
    return _responses(output=output.getvalue())


def _tool_text(*, result: JsonValue) -> str:
    """Extract text content from a tool result payload.

    Args:
        result: Tool result payload.

    Returns:
        First text content block.

    """
    content: list[JsonObject] = cast(
        typ="list[JsonObject]",
        val=cast(typ="JsonObject", val=result)["content"],
    )
    return str(object=content[0]["text"])


def _tool_call(
    *,
    identifier: int,
    name: str,
    arguments: JsonObject,
) -> JsonObject:
    """Build a tools/call request object.

    Args:
        identifier: Request identifier.
        name: Tool name.
        arguments: Tool arguments.

    Returns:
        JSON-RPC request object.

    """
    return {
        "jsonrpc": "2.0",
        "id": identifier,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def test_initialize_and_tools_list(tmp_path: Path) -> None:
    """Initialize and tools/list round-trip over stdio framing.

    Args:
        tmp_path: Temporary home directory.

    """
    responses: list[JsonObject] = _exchange(
        tmp_path=tmp_path,
        requests=[
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {},
            },
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        ],
    )
    expect(actual=len(responses), expected=3)
    init_result: JsonObject = cast(
        typ="JsonObject",
        val=responses[0]["result"],
    )
    expect(
        actual=init_result["protocolVersion"],
        expected=DistrictMcpServer.protocol_version,
    )
    tools_result: JsonObject = cast(
        typ="JsonObject",
        val=responses[1]["result"],
    )
    tools: list[JsonObject] = cast(
        typ="list[JsonObject]",
        val=tools_result["tools"],
    )
    names: list[JsonValue] = [tool["name"] for tool in tools]
    expect(
        actual=names,
        expected=[
            "district_exec",
            "district_sessions",
            "district_new",
            "district_kill",
            "district_send",
            "district_output",
            "district_events",
            "district_commands",
        ],
    )
    expect(actual=responses[2]["result"], expected={})


def test_tool_sessions_empty(tmp_path: Path) -> None:
    """district_sessions reports an empty state without live sessions.

    Args:
        tmp_path: Temporary home directory.

    """
    responses: list[JsonObject] = _exchange(
        tmp_path=tmp_path,
        requests=[
            _tool_call(
                identifier=1,
                name="district_sessions",
                arguments={},
            ),
        ],
    )
    expect(
        actual=_tool_text(result=responses[0]["result"]),
        expected="No live district sessions.",
    )


def test_tool_sessions_lists_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """district_sessions renders live session rows.

    Args:
        tmp_path: Temporary home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    service: District = _service(tmp_path=tmp_path)
    entry: SessionEntry = SessionEntry(
        id="abc",
        command="zsh",
        session="district-abc",
        agent="devin",
        backend="tmux",
        cwd="/repo",
        agent_state="EXECUTING",
    )

    def _entries() -> tuple[SessionEntry, ...]:
        """Return the single fake live entry.

        Returns:
            Fake live session entries.

        """
        return (entry,)

    monkeypatch.setattr(
        target=service,
        name="list_entries",
        value=_entries,
    )
    responses: list[JsonObject] = _exchange(
        tmp_path=tmp_path,
        service=service,
        requests=[
            _tool_call(
                identifier=1,
                name="district_sessions",
                arguments={},
            ),
        ],
    )
    text: str = _tool_text(result=responses[0]["result"])
    check(condition="abc" in text, reason="session id must render")
    check(condition="devin" in text, reason="agent must render")


def test_tool_exec_uses_single_live_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """district_exec resolves the sole live session and reports the result.

    Args:
        tmp_path: Temporary home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    service: District = _service(tmp_path=tmp_path)
    entry: SessionEntry = SessionEntry(
        id="abc",
        command="zsh",
        session="district-abc",
    )

    def _entries() -> tuple[SessionEntry, ...]:
        """Return the single fake live entry.

        Returns:
            Fake live session entries.

        """
        return (entry,)

    def _execute(**_kwargs: str) -> ExecResult:
        """Return a fake successful execution.

        Args:
            _kwargs: Ignored execution arguments.

        Returns:
            Fake captured result.

        """
        return ExecResult(output="pong\n", exit_status=0)

    monkeypatch.setattr(
        target=service,
        name="list_entries",
        value=_entries,
    )
    monkeypatch.setattr(
        target=service,
        name="execute",
        value=_execute,
    )
    responses: list[JsonObject] = _exchange(
        tmp_path=tmp_path,
        service=service,
        requests=[
            _tool_call(
                identifier=1,
                name="district_exec",
                arguments={"command": "echo pong"},
            ),
        ],
    )
    result: JsonObject = cast(
        typ="JsonObject",
        val=responses[0]["result"],
    )
    expect(actual=result["isError"], expected=False)
    expect(actual=_tool_text(result=result), expected="pong\n[exit 0]")
    structured: JsonObject = cast(
        typ="JsonObject",
        val=result["structuredContent"],
    )
    expect(actual=structured["exit_status"], expected=0)
    expect(actual=structured["session_id"], expected="abc")


def test_tool_exec_reports_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """district_exec surfaces validation, session, and timeout failures.

    Args:
        tmp_path: Temporary home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    service: District = _service(tmp_path=tmp_path)

    def _entries() -> tuple[SessionEntry, ...]:
        """Return no live entries.

        Returns:
            Empty session entries.

        """
        return ()

    monkeypatch.setattr(
        target=service,
        name="list_entries",
        value=_entries,
    )

    def _timeout(**_kwargs: str) -> ExecResult:
        """Raise the session timeout error.

        Args:
            _kwargs: Ignored execution arguments.

        Returns:
            Never returns.

        Raises:
            TimeoutError: Always.

        """
        raise TimeoutError

    monkeypatch.setattr(target=service, name="execute", value=_timeout)
    responses: list[JsonObject] = _exchange(
        tmp_path=tmp_path,
        service=service,
        requests=[
            _tool_call(
                identifier=index,
                name=name,
                arguments=arguments,
            )
            for index, (name, arguments) in enumerate(
                iterable=cast(
                    typ="tuple[tuple[str, JsonObject], ...]",
                    val=(
                        ("district_exec", {}),
                        ("district_exec", {"command": "true"}),
                        (
                            "district_exec",
                            {"command": "true", "session_id": "zz"},
                        ),
                        (
                            "district_exec",
                            {"command": "true", "session_id": 3},
                        ),
                        ("district_nope", {}),
                    ),
                ),
                start=1,
            )
        ],
    )
    expect(actual=len(responses), expected=5)
    for response in responses:
        result: JsonObject = cast(
            typ="JsonObject",
            val=response["result"],
        )
        expect(actual=result["isError"], expected=True)


def test_tool_output_reads_pane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """district_output returns the captured pane output for one session.

    Args:
        tmp_path: Temporary home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    service: District = _service(tmp_path=tmp_path)
    entry: SessionEntry = SessionEntry(
        id="abc",
        command="zsh",
        session="district-abc",
        backend="tmux",
    )

    def _entries() -> tuple[SessionEntry, ...]:
        """Return the single fake live entry.

        Returns:
            Fake live session entries.

        """
        return (entry,)

    def _snapshot(*, entry: SessionEntry) -> SessionSnapshot:
        """Return a fake snapshot with captured output.

        Args:
            entry: Session entry.

        Returns:
            Fake session snapshot.

        """
        return SessionSnapshot(
            id=entry.id,
            state="IDLE",
            clients=1,
            current_command="zsh",
            output="user@host ~ %",
            age_seconds=5,
            reconnectable=True,
            process_count=1,
            workload_pid=42,
        )

    monkeypatch.setattr(
        target=service,
        name="list_entries",
        value=_entries,
    )
    monkeypatch.setattr(
        target=Monitor,
        name="snapshot",
        value=_snapshot,
    )
    responses: list[JsonObject] = _exchange(
        tmp_path=tmp_path,
        service=service,
        requests=[
            _tool_call(
                identifier=1,
                name="district_output",
                arguments={},
            ),
            _tool_call(
                identifier=2,
                name="district_output",
                arguments={"session_id": "zz"},
            ),
        ],
    )
    first: JsonObject = cast(
        typ="JsonObject",
        val=responses[0]["result"],
    )
    expect(actual=first["isError"], expected=False)
    check(
        condition="user@host ~ %" in _tool_text(result=first),
        reason="pane output must render",
    )
    second: JsonObject = cast(
        typ="JsonObject",
        val=responses[1]["result"],
    )
    expect(actual=second["isError"], expected=True)


def test_tool_events_lists_activity(tmp_path: Path) -> None:
    """district_events renders recorded events with filter and limit.

    Args:
        tmp_path: Temporary home directory.

    """
    service: District = _service(tmp_path=tmp_path)
    service.repository.database.append_event(
        event=DistrictEvent(
            id="evt-1",
            event_type="SESSION_CREATED",
            occurred_at="2026-01-01T00:00:00+00:00",
            host_id="host-1",
            session_id="abc123",
            agent="devin",
        ),
    )
    service.repository.database.append_event(
        event=DistrictEvent(
            id="evt-2",
            event_type="COMMAND_FINISHED",
            occurred_at="2026-01-01T00:01:00+00:00",
            host_id="host-1",
            session_id="zz",
            agent="claude",
            command="pytest",
        ),
    )
    responses: list[JsonObject] = _exchange(
        tmp_path=tmp_path,
        service=service,
        requests=[
            _tool_call(
                identifier=1,
                name="district_events",
                arguments={"filter": "devin"},
            ),
            _tool_call(
                identifier=2,
                name="district_events",
                arguments={"limit": 5},
            ),
            _tool_call(
                identifier=3,
                name="district_events",
                arguments={"limit": 0},
            ),
        ],
    )
    filtered: str = _tool_text(result=responses[0]["result"])
    check(
        condition="SESSION_CREATED" in filtered,
        reason="matching event must render",
    )
    check(
        condition="COMMAND_FINISHED" not in filtered,
        reason="non-matching event must be excluded",
    )
    all_events: str = _tool_text(result=responses[1]["result"])
    check(
        condition="COMMAND_FINISHED" in all_events,
        reason="unfiltered events must render",
    )
    limited: JsonObject = cast(
        typ="JsonObject",
        val=responses[2]["result"],
    )
    expect(actual=limited["isError"], expected=True)


def test_tool_commands_lists_history(tmp_path: Path) -> None:
    """district_commands renders recorded commands with a session filter.

    Args:
        tmp_path: Temporary home directory.

    """
    service: District = _service(tmp_path=tmp_path)
    database: DistrictDatabase = service.repository.database
    database.command_started(
        command_id="cmd-1",
        session_uuid="uuid-1",
        session_id="abc123",
        host_id="host-1",
        agent="devin",
        agent_session="sess-1",
        surface="cli",
        editor="",
        command="echo hi",
        cwd="/repo",
        started_at="2026-01-01T00:00:00+00:00",
    )
    database.command_finished(
        command_id="cmd-1",
        finished_at="2026-01-01T00:00:01+00:00",
        exit_code=0,
        duration_seconds=1.0,
    )
    database.command_started(
        command_id="cmd-2",
        session_uuid="uuid-2",
        session_id="other",
        host_id="host-1",
        agent="claude",
        agent_session="sess-2",
        surface="cli",
        editor="",
        command="false",
        cwd="/var",
        started_at="2026-01-01T00:02:00+00:00",
    )
    responses: list[JsonObject] = _exchange(
        tmp_path=tmp_path,
        service=service,
        requests=[
            _tool_call(
                identifier=1,
                name="district_commands",
                arguments={},
            ),
            _tool_call(
                identifier=2,
                name="district_commands",
                arguments={"session_id": "abc123"},
            ),
        ],
    )
    all_commands: str = _tool_text(result=responses[0]["result"])
    check(
        condition="echo hi" in all_commands,
        reason="finished command must render",
    )
    check(
        condition="false" in all_commands,
        reason="running command must render",
    )
    filtered: str = _tool_text(result=responses[1]["result"])
    check(
        condition="echo hi" in filtered,
        reason="session command must render",
    )
    check(
        condition="false" not in filtered,
        reason="other session command must be excluded",
    )


def test_tool_new_creates_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """district_new spawns a detached session and reports its id.

    Args:
        tmp_path: Temporary home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    service: District = _service(tmp_path=tmp_path)
    created: list[tuple[str, ...]] = []

    def _spawn(
        *,
        initial_command: tuple[str, ...],
        initiator: str,
        cwd: str,
    ) -> SessionEntry:
        """Record spawn arguments and return a fake entry.

        Args:
            initial_command: Initial command.
            initiator: Session initiator.
            cwd: Working directory.

        Returns:
            Fake session entry.

        """
        created.append(initial_command)
        check(
            condition=initiator == "agent",
            reason="MCP spawns must be agent-initiated",
        )
        check(condition=cwd == "/repo", reason="cwd must pass through")
        return SessionEntry(
            id="new1",
            command="zsh",
            session="district-new1",
            backend="tmux",
            cwd=cwd,
        )

    monkeypatch.setattr(target=service, name="spawn", value=_spawn)
    responses: list[JsonObject] = _exchange(
        tmp_path=tmp_path,
        service=service,
        requests=[
            _tool_call(
                identifier=1,
                name="district_new",
                arguments={
                    "command": "echo hello",
                    "cwd": "/repo",
                },
            ),
            _tool_call(
                identifier=2,
                name="district_new",
                arguments={"command": 3},
            ),
        ],
    )
    first: JsonObject = cast(
        typ="JsonObject",
        val=responses[0]["result"],
    )
    expect(actual=first["isError"], expected=False)
    check(
        condition="new1" in _tool_text(result=first),
        reason="new session id must render",
    )
    expect(actual=created, expected=[("echo", "hello")])
    second: JsonObject = cast(
        typ="JsonObject",
        val=responses[1]["result"],
    )
    expect(actual=second["isError"], expected=True)


def test_tool_kill_destroys_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """district_kill destroys the resolved session and reports errors.

    Args:
        tmp_path: Temporary home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    service: District = _service(tmp_path=tmp_path)
    entry: SessionEntry = SessionEntry(
        id="abc",
        command="zsh",
        session="district-abc",
        backend="tmux",
    )
    killed: list[str] = []

    def _entries() -> tuple[SessionEntry, ...]:
        """Return the single fake live entry.

        Returns:
            Fake live session entries.

        """
        return (entry,)

    def _kill(*, session_id: str) -> None:
        """Record kills; raise for unknown sessions.

        Args:
            session_id: Session identifier.

        Raises:
            KeyError: For unknown sessions.

        """
        if session_id != "abc":
            raise KeyError(session_id)
        killed.append(session_id)

    monkeypatch.setattr(
        target=service,
        name="list_entries",
        value=_entries,
    )
    monkeypatch.setattr(target=service, name="kill", value=_kill)
    responses: list[JsonObject] = _exchange(
        tmp_path=tmp_path,
        service=service,
        requests=[
            _tool_call(
                identifier=1,
                name="district_kill",
                arguments={},
            ),
            _tool_call(
                identifier=2,
                name="district_kill",
                arguments={"session_id": "zz"},
            ),
        ],
    )
    first: JsonObject = cast(
        typ="JsonObject",
        val=responses[0]["result"],
    )
    expect(actual=first["isError"], expected=False)
    expect(actual=killed, expected=["abc"])
    second: JsonObject = cast(
        typ="JsonObject",
        val=responses[1]["result"],
    )
    expect(actual=second["isError"], expected=True)


def test_tool_send_injects_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """district_send forwards literal text and key presses to a session.

    Args:
        tmp_path: Temporary home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    service: District = _service(tmp_path=tmp_path)
    entry: SessionEntry = SessionEntry(
        id="abc",
        command="zsh",
        session="district-abc",
        backend="tmux",
    )
    sent: list[tuple[str, str, tuple[str, ...]]] = []

    def _entries() -> tuple[SessionEntry, ...]:
        """Return the single fake live entry.

        Returns:
            Fake live session entries.

        """
        return (entry,)

    def _send(*, session_id: str, text: str, keys: tuple[str, ...]) -> None:
        """Record send calls.

        Args:
            session_id: Session identifier.
            text: Literal text.
            keys: Key names.

        """
        sent.append((session_id, text, keys))

    monkeypatch.setattr(
        target=service,
        name="list_entries",
        value=_entries,
    )
    monkeypatch.setattr(target=service, name="send", value=_send)
    responses: list[JsonObject] = _exchange(
        tmp_path=tmp_path,
        service=service,
        requests=[
            _tool_call(
                identifier=1,
                name="district_send",
                arguments={"text": "y"},
            ),
            _tool_call(
                identifier=2,
                name="district_send",
                arguments={"key": "C-c"},
            ),
            _tool_call(
                identifier=3,
                name="district_send",
                arguments={"key": "rm -rf", "enter": False},
            ),
            _tool_call(
                identifier=4,
                name="district_send",
                arguments={"enter": False},
            ),
        ],
    )
    flags: list[JsonValue] = [
        cast(typ="JsonObject", val=response["result"])["isError"]
        for response in responses
    ]
    expect(actual=flags, expected=[False, False, True, True])
    expect(
        actual=sent,
        expected=[
            ("abc", "y", ("Enter",)),
            ("abc", "", ("C-c",)),
        ],
    )


def test_protocol_error_paths(tmp_path: Path) -> None:
    """Malformed requests and unknown methods produce JSON-RPC errors.

    Args:
        tmp_path: Temporary home directory.

    """
    raw: bytes = (
        b"Content-Length: 8\r\n\r\nnot-json"
        b"Content-Length: 3\r\n\r\n[1]"
        + _frame(
            payload={"jsonrpc": "2.0", "id": 9, "method": "nope/method"},
        )
        + _frame(payload={"jsonrpc": "2.0", "method": "nope/method"})
        + _frame(payload={"jsonrpc": "2.0", "method": "tools/list"})
    )
    output: BytesIO = BytesIO()
    server: DistrictMcpServer = DistrictMcpServer(
        service=_service(tmp_path=tmp_path),
        reader=BytesIO(initial_bytes=raw),
        writer=output,
    )
    expect(actual=server.serve(), expected=0)
    responses: list[JsonObject] = _responses(output=output.getvalue())
    expect(actual=len(responses), expected=3)
    codes: list[JsonValue] = [
        cast(typ="JsonObject", val=response["error"])["code"]
        for response in responses[:2]
    ]
    expect(actual=codes, expected=[-32700, -32600])
    third: JsonObject = cast(
        typ="JsonObject",
        val=responses[2]["error"],
    )
    expect(actual=third["code"], expected=-32601)


def test_serve_stops_on_empty_length(tmp_path: Path) -> None:
    """The serve loop exits on a zero-length frame and at end of stream.

    Args:
        tmp_path: Temporary home directory.

    """
    output: BytesIO = BytesIO()
    server: DistrictMcpServer = DistrictMcpServer(
        service=_service(tmp_path=tmp_path),
        reader=BytesIO(initial_bytes=b"Content-Length: 0\r\n\r\n"),
        writer=output,
    )
    expect(actual=server.serve(), expected=0)
    expect(actual=output.getvalue(), expected=b"")


def test_cli_mcp_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The --mcp flag enters the resident stdio server.

    Args:
        tmp_path: Temporary home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """

    def _serve(_self: DistrictMcpServer) -> int:
        """Skip the blocking stdio loop.

        Args:
            _self: Ignored server instance.

        Returns:
            Fake exit status.

        """
        return 0

    monkeypatch.setattr(
        target=DistrictMcpServer,
        name="serve",
        value=_serve,
    )
    cli: Cli = Cli(service=_service(tmp_path=tmp_path))
    expect(actual=cli.run(arguments=("--mcp",)), expected=0)
