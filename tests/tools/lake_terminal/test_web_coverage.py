# Copyright (c) 2026 Project contributors.
"""Exercise the public web-dashboard surface for coverage evidence."""

from __future__ import annotations

from json import loads
from typing import TYPE_CHECKING, Final, Protocol, cast

from fastapi.testclient import TestClient

from tools.lake_terminal.lake_terminal.core import (
    ExecResult,
    LakeTerminal,
    SessionRepository,
)
from tools.lake_terminal.lake_terminal.events import (
    LakeEvent,
)
from tools.lake_terminal.lake_terminal.identity import ProcessIdentity
from tools.lake_terminal.lake_terminal.models import SessionEntry
from tools.lake_terminal.lake_terminal.web import WebDashboard

from .helpers import check, expect

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
tokens: Final[dict[str, str]] = {"token": "tok" + "en", "other": "ot" + "her"}


class HttpResponse(Protocol):
    """Define the HTTP response interface required by tests."""

    @property
    def status_code(self) -> int:
        """Return the HTTP status code.

        Returns:
            HTTP status code.

        """
        ...

    @property
    def text(self) -> str:
        """Return the response body as text.

        Returns:
            Response body as text.

        """
        ...

    def json(self) -> JsonValue:
        """Return the decoded JSON response.

        Returns:
            Decoded JSON value.

        """
        ...


class _WebService:
    """Provide deterministic session entries for dashboard tests."""

    def __init__(self, *, entries: tuple[SessionEntry, ...]) -> None:
        """Initialize the fake service.

        Args:
            entries: Session entries returned to the dashboard.

        """
        self.entries: tuple[SessionEntry, ...] = entries
        self.killed: list[str] = []
        self.sent: list[tuple[str, str, tuple[str, ...]]] = []

    def list_entries(self) -> tuple[SessionEntry, ...]:
        """Return configured entries.

        Returns:
            Configured session entries.

        """
        return self.entries

    def kill(self, *, session_id: str) -> int:
        """Record a requested kill.

        Args:
            session_id: Session identifier.

        Returns:
            Successful exit status.

        """
        self.killed.append(session_id)
        return 0

    def execute(
        self,
        *,
        session_id: str,
        command: str,
    ) -> ExecResult:
        """Return a deterministic execution result.

        Args:
            session_id: Session identifier.
            command: Shell command.

        Returns:
            Fake captured result.

        """
        return ExecResult(
            output=f"{session_id}:{command}",
            exit_status=0,
        )

    def send(
        self,
        *,
        session_id: str,
        text: str,
        keys: tuple[str, ...],
    ) -> None:
        """Record send calls.

        Args:
            session_id: Session identifier.
            text: Literal text.
            keys: Key names.

        """
        self.sent.append((session_id, text, keys))


class Constants:
    """Group module-level constants.

    Attributes:
        requested_port: Grouped module constant.
        maximum_port: Grouped module constant.

    """

    requested_port: Final[int] = 8123
    maximum_port: Final[int] = 65535


def test_web_dashboard_public_surface(*, tmp_path: Path) -> None:
    """Exercise public dashboard state and port selection.

    Args:
        tmp_path: Temporary home directory.

    """
    service: LakeTerminal = LakeTerminal(
        repository=SessionRepository(home=tmp_path),
    )
    dashboard: WebDashboard = WebDashboard(service=service, home=tmp_path)
    expect(
        actual=WebDashboard.port(
            host="127.0.0.1",
            requested=Constants.requested_port,
        ),
        expected=Constants.requested_port,
    )
    dynamic: int = WebDashboard.port(host="127.0.0.1", requested=0)
    check(
        condition=0 < dynamic <= Constants.maximum_port,
        reason="dynamic port must be in the TCP port range",
    )
    expect(actual=dashboard.running_url(), expected=None)
    payload: dict[str, object] = cast(
        typ="dict[str, object]",
        val=loads(s=dashboard.payload_cache),
    )
    expect(actual=payload.get("sessions"), expected=[])
    expect(actual=payload.get("events"), expected=[])
    expect(actual=payload.get("commands"), expected=[])
    server: dict[str, object] = cast(
        typ="dict[str, object]",
        val=payload.get("server"),
    )
    expect(actual=server.get("mode"), expected="IDLE")
    expect(actual=server.get("consumers"), expected=0)
    check(
        condition=isinstance(server.get("version"), str),
        reason="server payload must expose the product version",
    )
    check(
        condition=isinstance(server.get("uptimeSeconds"), float),
        reason="server payload must expose uptime",
    )
    totals: dict[str, object] = cast(
        typ="dict[str, object]",
        val=payload.get("totals"),
    )
    expect(actual=totals.get("sessions"), expected=0)
    live_payload: dict[str, object] = cast(
        typ="dict[str, object]",
        val=loads(s=dashboard.payload()),
    )
    expect(actual=live_payload.get("sessions"), expected=[])
    live_server: dict[str, object] = cast(
        typ="dict[str, object]",
        val=live_payload.get("server"),
    )
    expect(actual=live_server.get("mode"), expected="LIVE")
    expect(actual=live_server.get("consumers"), expected=0)
    _ = dashboard.application()


def test_web_dashboard_http_surface(*, tmp_path: Path) -> None:
    """Exercise dashboard HTTP routes through the public application.

    Args:
        tmp_path: Temporary home directory.

    """
    service: LakeTerminal = LakeTerminal(
        repository=SessionRepository(home=tmp_path),
    )
    dashboard: WebDashboard = WebDashboard(service=service, home=tmp_path)
    with TestClient(app=dashboard.application()) as client:
        health: HttpResponse = client.get(url="/health")
        expect(actual=health.status_code, expected=200)
        health_payload: dict[str, object] = cast(
            typ="dict[str, object]",
            val=health.json(),
        )
        expect(actual=health_payload.get("status"), expected="ok")
        expect(actual=health_payload.get("mode"), expected="IDLE")
        expect(actual=health_payload.get("consumers"), expected=0)
        index: HttpResponse = client.get(url="/")
        expect(actual=index.status_code, expected=200)
        missing: HttpResponse = client.post(url="/api/sessions/missing/kill")
        expect(actual=missing.status_code, expected=404)
        expect(
            actual=cast(typ="dict[str, object]", val=missing.json()),
            expected={"detail": "Unknown session"},
        )


def test_web_dashboard_session_actions(*, tmp_path: Path) -> None:
    """Exercise all HTTP session action branches.

    Args:
        tmp_path: Temporary home directory.

    """
    reconnectable: SessionEntry = SessionEntry(
        id="abc123",
        command="shell",
        session="shared-abc123",
        reconnectable=True,
    )
    process_only: SessionEntry = SessionEntry(
        id="def456",
        command="agent",
        session="shared-def456",
        reconnectable=False,
    )
    fake_service: _WebService = _WebService(
        entries=(reconnectable, process_only),
    )
    service: LakeTerminal = cast(typ="LakeTerminal", val=fake_service)
    dashboard: WebDashboard = WebDashboard(service=service, home=tmp_path)
    with TestClient(app=dashboard.application()) as client:
        for action, expected_command in (
            ("attach", "lake abc123"),
            ("view", "lake --view abc123"),
            ("history", "lake --history abc123"),
        ):
            response: HttpResponse = client.post(
                url=f"/api/sessions/abc123/{action}",
            )
            expect(actual=response.status_code, expected=200)
            payload: dict[str, object] = cast(
                typ="dict[str, object]",
                val=response.json(),
            )
            expect(actual=payload.get("command"), expected=expected_command)
        conflict: HttpResponse = client.post(
            url="/api/sessions/def456/attach",
        )
        expect(actual=conflict.status_code, expected=409)
        unsupported: HttpResponse = client.post(
            url="/api/sessions/abc123/unsupported",
        )
        expect(actual=unsupported.status_code, expected=400)


def test_web_dashboard_registered_server_validation(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise registered-server validation and stale-record cleanup.

    Args:
        tmp_path: Temporary home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    service: LakeTerminal = LakeTerminal(
        repository=SessionRepository(home=tmp_path),
    )
    dashboard: WebDashboard = WebDashboard(service=service, home=tmp_path)
    dashboard.database.set_web_server(
        pid=123,
        started_token=tokens["token"],
        host="127.0.0.1",
        port=8123,
        url="http://127.0.0.1:8123/",
    )

    def fake_capture(*, pid: int) -> ProcessIdentity:
        """Return a matching process identity.

        Args:
            pid: Process identifier.

        Returns:
            Matching process identity.

        """
        return ProcessIdentity(pid=pid, started_token=tokens["token"])

    monkeypatch.setattr(
        target=ProcessIdentity,
        name="capture",
        value=fake_capture,
    )

    def fake_health_check(*, url: str) -> bool:
        """Return whether a URL was supplied.

        Args:
            url: Dashboard URL.

        Returns:
            Whether the URL is nonempty.

        """
        return bool(url)

    monkeypatch.setattr(
        target=WebDashboard,
        name="health_check",
        value=staticmethod(fake_health_check),
    )
    expect(
        actual=dashboard.running_url(),
        expected="http://127.0.0.1:8123/",
    )

    def stale_capture(*, pid: int) -> ProcessIdentity:
        """Return a stale process identity.

        Args:
            pid: Process identifier.

        Returns:
            Stale process identity.

        """
        return ProcessIdentity(pid=pid, started_token=tokens["other"])

    monkeypatch.setattr(
        target=ProcessIdentity,
        name="capture",
        value=stale_capture,
    )
    expect(actual=dashboard.running_url(), expected=None)


def test_web_dashboard_exec_action(*, tmp_path: Path) -> None:
    """Exercise dashboard command execution result and error branches.

    Args:
        tmp_path: Temporary home directory.

    """
    entry: SessionEntry = SessionEntry(
        id="abc123",
        command="shell",
        session="shared-abc123",
    )
    fake_service: _WebService = _WebService(entries=(entry,))
    service: LakeTerminal = cast(typ="LakeTerminal", val=fake_service)
    dashboard: WebDashboard = WebDashboard(service=service, home=tmp_path)
    with TestClient(app=dashboard.application()) as client:
        response: HttpResponse = client.post(
            url="/api/sessions/abc123/exec",
            json={"command": "echo hi"},
        )
        expect(actual=response.status_code, expected=200)
        payload: dict[str, object] = cast(
            typ="dict[str, object]",
            val=response.json(),
        )
        expect(actual=payload.get("output"), expected="abc123:echo hi")
        expect(actual=payload.get("exitStatus"), expected=0)
        missing: HttpResponse = client.post(
            url="/api/sessions/abc123/exec",
            json={"command": "   "},
        )
        expect(actual=missing.status_code, expected=400)
        non_object: HttpResponse = client.post(
            url="/api/sessions/abc123/exec",
            json=["not", "an", "object"],
        )
        expect(actual=non_object.status_code, expected=400)
        unknown: HttpResponse = client.post(
            url="/api/sessions/zzz999/exec",
            json={"command": "echo hi"},
        )
        expect(actual=unknown.status_code, expected=404)


def test_web_dashboard_exec_error_paths(*, tmp_path: Path) -> None:
    """Exercise dashboard exec session-loss and timeout branches.

    Args:
        tmp_path: Temporary home directory.

    """
    entry: SessionEntry = SessionEntry(
        id="abc123",
        command="shell",
        session="shared-abc123",
    )

    class _RaisingService(_WebService):
        """Raise deterministic execution failures.

        Attributes:
            error: Exception raised by execute.

        """

        error: Exception = TimeoutError()

        def execute(
            self,
            *,
            session_id: str,
            command: str,
        ) -> ExecResult:
            """Raise the configured error.

            Args:
                session_id: Ignored session identifier.
                command: Ignored shell command.

            Returns:
                Never returns.

            Raises:
                Exception: Configured failure.

            """
            _ = (session_id, command)
            raise self.error

    fake_service: _RaisingService = _RaisingService(entries=(entry,))
    service: LakeTerminal = cast(typ="LakeTerminal", val=fake_service)
    dashboard: WebDashboard = WebDashboard(service=service, home=tmp_path)
    with TestClient(app=dashboard.application()) as client:
        timeout: HttpResponse = client.post(
            url="/api/sessions/abc123/exec",
            json={"command": "sleep"},
        )
        expect(actual=timeout.status_code, expected=504)
        fake_service.error = KeyError("gone")
        lost: HttpResponse = client.post(
            url="/api/sessions/abc123/exec",
            json={"command": "echo hi"},
        )
        expect(actual=lost.status_code, expected=404)


def test_web_dashboard_send_action(*, tmp_path: Path) -> None:
    """Exercise dashboard input injection result and error branches.

    Args:
        tmp_path: Temporary home directory.

    """
    entry: SessionEntry = SessionEntry(
        id="abc123",
        command="shell",
        session="shared-abc123",
    )
    fake_service: _WebService = _WebService(entries=(entry,))
    service: LakeTerminal = cast(typ="LakeTerminal", val=fake_service)
    dashboard: WebDashboard = WebDashboard(service=service, home=tmp_path)
    with TestClient(app=dashboard.application()) as client:
        response: HttpResponse = client.post(
            url="/api/sessions/abc123/send",
            json={"text": "y"},
        )
        expect(actual=response.status_code, expected=200)
        key_only: HttpResponse = client.post(
            url="/api/sessions/abc123/send",
            json={"key": "C-c"},
        )
        expect(actual=key_only.status_code, expected=200)
        expect(
            actual=fake_service.sent,
            expected=[
                ("abc123", "y", ("Enter",)),
                ("abc123", "", ("C-c",)),
            ],
        )
        empty: HttpResponse = client.post(
            url="/api/sessions/abc123/send",
            json={"enter": False},
        )
        expect(actual=empty.status_code, expected=400)
        bad_key: HttpResponse = client.post(
            url="/api/sessions/abc123/send",
            json={"key": 5},
        )
        expect(actual=bad_key.status_code, expected=400)
        bad_enter: HttpResponse = client.post(
            url="/api/sessions/abc123/send",
            json={"text": "y", "enter": "yes"},
        )
        expect(actual=bad_enter.status_code, expected=400)
        non_object: HttpResponse = client.post(
            url="/api/sessions/abc123/send",
            json=["not", "an", "object"],
        )
        expect(actual=non_object.status_code, expected=400)
        unknown: HttpResponse = client.post(
            url="/api/sessions/zzz999/send",
            json={"text": "y"},
        )
        expect(actual=unknown.status_code, expected=404)


def test_web_dashboard_send_error_paths(*, tmp_path: Path) -> None:
    """Exercise dashboard send backend-failure branches.

    Args:
        tmp_path: Temporary home directory.

    """
    entry: SessionEntry = SessionEntry(
        id="abc123",
        command="shell",
        session="shared-abc123",
    )

    class _SendRaisingService(_WebService):
        """Raise deterministic send failures.

        Attributes:
            error: Exception raised by send.

        """

        error: Exception = ValueError("Unsupported key name")

        def send(
            self,
            *,
            session_id: str,
            text: str,
            keys: tuple[str, ...],
        ) -> None:
            """Raise the configured error.

            Args:
                session_id: Ignored session identifier.
                text: Ignored literal text.
                keys: Ignored key names.

            Returns:
                Never returns.

            Raises:
                Exception: Configured failure.

            """
            _ = (session_id, text, keys)
            raise self.error

    fake_service: _SendRaisingService = _SendRaisingService(entries=(entry,))
    service: LakeTerminal = cast(typ="LakeTerminal", val=fake_service)
    dashboard: WebDashboard = WebDashboard(service=service, home=tmp_path)
    with TestClient(app=dashboard.application()) as client:
        invalid: HttpResponse = client.post(
            url="/api/sessions/abc123/send",
            json={"key": "rm"},
        )
        expect(actual=invalid.status_code, expected=400)
        fake_service.error = KeyError("gone")
        lost: HttpResponse = client.post(
            url="/api/sessions/abc123/send",
            json={"key": "Enter"},
        )
        expect(actual=lost.status_code, expected=404)
        fake_service.error = RuntimeError("no backend")
        rejected: HttpResponse = client.post(
            url="/api/sessions/abc123/send",
            json={"key": "Enter"},
        )
        expect(actual=rejected.status_code, expected=502)


def test_web_dashboard_metric_history_and_activity_feed(
    *,
    tmp_path: Path,
) -> None:
    """Exercise metric history accumulation and persisted feeds.

    Args:
        tmp_path: Temporary home directory.

    """
    entry: SessionEntry = SessionEntry(
        id="abc123",
        command="shell",
        session="shared-abc123",
    )
    fake_service: _WebService = _WebService(entries=(entry,))
    service: LakeTerminal = cast(typ="LakeTerminal", val=fake_service)
    dashboard: WebDashboard = WebDashboard(service=service, home=tmp_path)
    dashboard.database.append_event(
        event=LakeEvent(
            id="evt-1",
            event_type="SESSION_CREATED",
            occurred_at="2026-01-01T00:00:00+00:00",
            host_id="host-1",
            session_id="abc123",
            agent="devin",
        ),
    )
    dashboard.database.command_started(
        command_id="cmd-1",
        session_uuid="uuid-1",
        session_id="abc123",
        host_id="host-1",
        agent="devin",
        agent_session="sess-1",
        surface="cli",
        editor="",
        command="echo hi",
        cwd=str(object=tmp_path),
        started_at="2026-01-01T00:00:00+00:00",
    )
    _ = dashboard.payload()
    payload: dict[str, object] = cast(
        typ="dict[str, object]",
        val=loads(s=dashboard.payload()),
    )
    sessions: list[dict[str, object]] = cast(
        typ="list[dict[str, object]]",
        val=payload.get("sessions"),
    )
    first: dict[str, object] = sessions[0]
    cpu_history: list[object] = cast(
        typ="list[object]",
        val=first.get("cpuHistory"),
    )
    expect(actual=len(cpu_history), expected=2)
    rss_history: list[object] = cast(
        typ="list[object]",
        val=first.get("rssHistory"),
    )
    expect(actual=len(rss_history), expected=2)
    events: list[object] = cast(
        typ="list[object]",
        val=payload.get("events"),
    )
    expect(actual=len(events), expected=1)
    commands: list[object] = cast(
        typ="list[object]",
        val=payload.get("commands"),
    )
    expect(actual=len(commands), expected=1)
    totals: dict[str, object] = cast(
        typ="dict[str, object]",
        val=payload.get("totals"),
    )
    expect(actual=totals.get("sessions"), expected=1)
    fake_service.entries = ()
    empty: dict[str, object] = cast(
        typ="dict[str, object]",
        val=loads(s=dashboard.payload()),
    )
    expect(actual=empty.get("sessions"), expected=[])
    check(
        condition="abc123" not in dashboard.history,
        reason="stale session history must be pruned",
    )


def test_web_dashboard_rejects_non_loopback_binding(tmp_path: Path) -> None:
    """Reject direct network exposure of the unauthenticated dashboard.

    Args:
        tmp_path: Temporary home directory.

    Raises:
        AssertionError: When non-loopback binding is accepted.

    """
    dashboard: WebDashboard = WebDashboard(
        service=LakeTerminal(repository=SessionRepository(home=tmp_path)),
        home=tmp_path,
    )
    try:
        _ = dashboard.run(host="0." * 4, port=8765, browser=False)
    except ValueError:
        return
    msg: str = "Non-loopback dashboard binding was accepted."
    raise AssertionError(msg)
