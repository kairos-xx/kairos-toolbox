# Copyright (c) 2026 Project contributors.
"""Tests for the command, host and URI endpoints of the web console."""

from __future__ import annotations

from asyncio import run
from typing import TYPE_CHECKING, Final, Protocol, cast, final

import pytest
from district.core import (
    District,
    SessionRepository,
)
from district.district_commands import DistrictCommands
from district.pairing import PairingAuth
from district.web import WebDashboard
from district.web_api import ApiRoutes
from fastapi import HTTPException
from fastapi.testclient import TestClient

from .helpers import check, expect, section

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from district.uri_registry import UriRegistry


class Response(Protocol):
    """Describe the response surface consumed by these tests."""

    @property
    def status_code(self) -> int:
        """Return the HTTP status code.

        Returns:
            Numeric HTTP status code.

        """
        ...

    @property
    def headers(self) -> Mapping[str, str]:
        """Return response headers.

        Returns:
            Response headers keyed by header name.

        """
        ...

    def json(self) -> dict[str, object]:
        """Return the decoded JSON object.

        Returns:
            Decoded JSON mapping.

        """
        ...


@final
class Constants:
    """Group test constants.

    Attributes:
        ok: Success status.
        bad_request: Status for invalid input.
        unauthorized: Status for an unpaired device.
        not_found: Status for an unknown name.
        conflict: Status for a refused action.
        key: Signing key for the pairing guard test.
        ttl: Lifetime asked of tickets.
        not_implemented: Status when URI support is not installed.

    """

    ok: Final[int] = 200
    bad_request: Final[int] = 400
    unauthorized: Final[int] = 401
    not_found: Final[int] = 404
    conflict: Final[int] = 409
    key: Final[bytes] = b"k" * 32
    ttl: Final[int] = 300
    not_implemented: Final[int] = 501


def _dashboard(*, home: Path) -> WebDashboard:
    """Create a dashboard that is not gated by pairing.

    Args:
        home: Scratch home directory.

    Returns:
        The dashboard.

    """
    return WebDashboard(
        service=District(repository=SessionRepository(home=home)),
        home=home,
    )


def _client(*, home: Path) -> TestClient:
    """Create a client for an open dashboard.

    Args:
        home: Scratch home directory.

    Returns:
        A test client.

    """
    return TestClient(app=_dashboard(home=home).application())


def test_the_host_endpoint_describes_the_machine(tmp_path: Path) -> None:
    """The overview gets processor, memory, disk and district facts.

    Args:
        tmp_path: Scratch home directory.

    """
    body: dict[str, object] = (
        _client(home=tmp_path).get(url="/api/host").json()
    )
    for key in ("cpu", "memory", "disk", "district", "processes", "networks"):
        check(condition=key in body, reason=f"{key} should be reported")


def test_the_catalogue_lists_every_command_with_its_inputs(
    tmp_path: Path,
) -> None:
    """The palette can build its forms from this one response.

    Args:
        tmp_path: Scratch home directory.

    """
    body: dict[str, object] = (
        _client(home=tmp_path).get(url="/api/commands").json()
    )
    commands: list[object] = cast(typ="list[object]", val=body["commands"])
    names: list[object] = [section(value=item)["name"] for item in commands]
    check(
        condition={"sessions", "new", "exec", "send", "health"} <= set(names),
        reason="the core commands should be catalogued",
    )
    exec_spec: dict[str, object] = next(
        section(value=item)
        for item in commands
        if section(value=item)["name"] == "exec"
    )
    params: list[object] = cast(typ="list[object]", val=exec_spec["params"])
    expect(
        actual=[section(value=param)["name"] for param in params],
        expected=["session", "command"],
    )


def test_running_a_command_returns_widgets_not_text(tmp_path: Path) -> None:
    """A result is a list of typed widgets ready to render.

    Args:
        tmp_path: Scratch home directory.

    """
    answer: Response = _client(home=tmp_path).post(
        url="/api/commands/storage",
        json={"values": {}},
    )
    expect(actual=answer.status_code, expected=Constants.ok)
    body: dict[str, object] = answer.json()
    expect(actual=body["command"], expected="storage")
    widgets: list[object] = cast(typ="list[object]", val=body["widgets"])
    check(
        condition=all("type" in section(value=item) for item in widgets),
        reason="every widget declares its type",
    )


def test_an_unknown_command_is_a_404_with_a_message(tmp_path: Path) -> None:
    """The error carries words a person can read.

    Args:
        tmp_path: Scratch home directory.

    """
    answer: Response = _client(home=tmp_path).post(
        url="/api/commands/rm-rf",
        json={"values": {}},
    )
    expect(actual=answer.status_code, expected=Constants.not_found)
    check(
        condition="rm-rf" in str(object=answer.json()["detail"]),
        reason="the message should name the command",
    )


def test_a_destructive_command_needs_confirmation_over_http(
    tmp_path: Path,
) -> None:
    """Without the confirmation flag nothing runs.

    Args:
        tmp_path: Scratch home directory.

    """
    client: TestClient = _client(home=tmp_path)
    refused: Response = client.post(
        url="/api/commands/kill-all",
        json={"values": {}},
    )
    expect(actual=refused.status_code, expected=Constants.conflict)
    allowed: Response = client.post(
        url="/api/commands/kill-all",
        json={"values": {}, "confirmed": True},
    )
    expect(actual=allowed.status_code, expected=Constants.ok)


def test_a_malformed_body_is_a_400(tmp_path: Path) -> None:
    """Values must be a mapping and the flag a boolean.

    Args:
        tmp_path: Scratch home directory.

    """
    client: TestClient = _client(home=tmp_path)
    bodies: tuple[dict[str, object], ...] = (
        {"values": []},
        {"values": {}, "confirmed": "yes"},
    )
    for body in bodies:
        answer: Response = client.post(
            url="/api/commands/storage",
            json=body,
        )
        expect(actual=answer.status_code, expected=Constants.bad_request)


def test_a_uri_can_be_issued_listed_revoked_and_then_refused(
    tmp_path: Path,
) -> None:
    """The whole ticket lifecycle works over HTTP.

    Args:
        tmp_path: Scratch home directory.

    """
    client: TestClient = _client(home=tmp_path)
    created: Response = client.post(
        url="/api/uris",
        json={
            "command": "health",
            "values": {},
            "ttlSeconds": Constants.ttl,
        },
    )
    expect(actual=created.status_code, expected=Constants.ok)
    issued: dict[str, object] = created.json()
    check(
        condition=str(object=issued["uri"]).startswith("district://"),
        reason="the ticket is a district URI",
    )
    listed: list[object] = cast(
        typ="list[object]",
        val=client.get(url="/api/uris").json()["items"],
    )
    expect(
        actual=[section(value=item)["id"] for item in listed],
        expected=[issued["id"]],
    )
    revoked: Response = client.post(url=f"/api/uris/{issued['id']}/revoke")
    expect(actual=revoked.status_code, expected=Constants.ok)
    expect(actual=revoked.json()["state"], expected="revoked")
    refused: Response = client.post(url=f"/api/uris/{issued['id']}/run")
    expect(actual=refused.status_code, expected=Constants.conflict)


def test_a_uri_runs_once_and_returns_widgets(tmp_path: Path) -> None:
    """Running a ticket shows what the command produced.

    Args:
        tmp_path: Scratch home directory.

    """
    client: TestClient = _client(home=tmp_path)
    issued: dict[str, object] = client.post(
        url="/api/uris",
        json={"command": "storage", "values": {}, "ttlSeconds": Constants.ttl},
    ).json()
    first: Response = client.post(url=f"/api/uris/{issued['id']}/run")
    expect(actual=first.status_code, expected=Constants.ok)
    expect(actual=first.json()["command"], expected="storage")
    again: Response = client.post(url=f"/api/uris/{issued['id']}/run")
    expect(actual=again.status_code, expected=Constants.conflict)


def test_a_pasted_garbage_uri_is_a_400(tmp_path: Path) -> None:
    """Anything that is not a signed district URI is refused.

    Args:
        tmp_path: Scratch home directory.

    """
    answer: Response = _client(home=tmp_path).post(
        url="/api/uris/redeem",
        json={"uri": "district://request/v1/not.signed"},
    )
    expect(actual=answer.status_code, expected=Constants.bad_request)


def test_every_new_endpoint_requires_a_paired_device(tmp_path: Path) -> None:
    """An unpaired caller cannot read the host, run commands or mint URIs.

    Args:
        tmp_path: Scratch home directory.

    """
    dashboard: WebDashboard = _dashboard(home=tmp_path)
    dashboard.auth = PairingAuth(key=Constants.key)
    client: TestClient = TestClient(app=dashboard.application())
    gets: tuple[str, ...] = ("/api/host", "/api/commands", "/api/uris")
    posts: tuple[str, ...] = (
        "/api/commands/storage",
        "/api/commands/kill-all",
        "/api/uris",
        "/api/uris/redeem",
    )
    for path in gets:
        expect(
            actual=client.get(url=path).status_code,
            expected=Constants.unauthorized,
        )
    for path in posts:
        expect(
            actual=client.post(url=path, json={}).status_code,
            expected=Constants.unauthorized,
        )


def _without_bowman() -> UriRegistry:
    """Stand in for a machine that lacks the Bowman package.

    Returns:
        A URI registry when URI support is available.

    Raises:
        ImportError: Always in this stand-in.

    """
    msg: str = "No module named 'bowman'"
    raise ImportError(msg)


def test_the_console_still_works_when_uri_support_is_not_installed(
    tmp_path: Path,
) -> None:
    """Only the URI endpoints depend on the optional package.

    Args:
        tmp_path: Scratch home directory.

    """
    service: District = District(
        repository=SessionRepository(home=tmp_path),
    )
    routes: ApiRoutes = ApiRoutes(
        commands=DistrictCommands(service=service, home=tmp_path),
        uri_factory=_without_bowman,
        home=tmp_path,
    )
    with pytest.raises(expected_exception=HTTPException) as raised:
        run(main=routes.list_uris())
    expect(actual=raised.value.status_code, expected=Constants.not_implemented)
    expect(
        actual=run(main=routes.catalog()).status_code,
        expected=Constants.ok,
    )
