# Copyright (c) 2026 Project contributors.
"""Tests of how the dashboard serves its static console assets."""

from __future__ import annotations

from pathlib import Path
from re import findall
from shutil import copytree
from typing import TYPE_CHECKING, Final, Protocol, cast, final

from district import web as web_module
from district.core import (
    District,
    SessionRepository,
)
from district.product import state_directory
from district.storage import DistrictDatabase
from district.web import WebDashboard
from fastapi.testclient import TestClient

from .helpers import check, expect

if TYPE_CHECKING:
    from collections.abc import Mapping


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

    @property
    def text(self) -> str:
        """Return the decoded response body.

        Returns:
            Decoded response text.

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
        script: Path of the console script.
        revalidate: The ``Cache-Control`` value assets must carry.
        not_modified: Status of an unchanged conditional request.
        no_content: Status of a favicon the server does not ship.

    """

    script: Final[str] = "/assets/js/app.js"
    revalidate: Final[str] = "no-cache"
    not_modified: Final[int] = 304
    no_content: Final[int] = 204


def _client(*, home: Path) -> TestClient:
    """Create a client for a dashboard that is not gated by pairing.

    Args:
        home: Scratch home directory.

    Returns:
        A test client.

    """
    dashboard: WebDashboard = WebDashboard(
        service=District(repository=SessionRepository(home=home)),
        home=home,
    )
    return TestClient(app=dashboard.application())


def test_assets_must_be_revalidated_so_a_phone_never_runs_a_stale_console(
    tmp_path: Path,
) -> None:
    """Every asset response forbids reuse without asking the server first.

    Args:
        tmp_path: Scratch home directory.

    """
    response: Response = _client(home=tmp_path).get(url=Constants.script)
    expect(actual=response.status_code, expected=200)
    expect(
        actual=response.headers["cache-control"],
        expected=Constants.revalidate,
    )


def test_an_unchanged_asset_costs_only_a_not_modified_answer(
    tmp_path: Path,
) -> None:
    """A conditional request still gets the revalidation header.

    Args:
        tmp_path: Scratch home directory.

    """
    client: TestClient = _client(home=tmp_path)
    first: Response = client.get(url=Constants.script)
    again: Response = client.get(
        url=Constants.script,
        headers={"if-none-match": first.headers["etag"]},
    )
    expect(actual=again.status_code, expected=Constants.not_modified)
    expect(
        actual=again.headers["cache-control"],
        expected=Constants.revalidate,
    )


def test_a_page_links_its_scripts_by_version_so_a_stale_copy_is_never_reused(
    tmp_path: Path,
) -> None:
    """The version in each link changes with the assets, and pages revalidate.

    Args:
        tmp_path: Scratch home directory.

    """
    client: TestClient = _client(home=tmp_path)
    response: Response = client.get(url="/")
    expect(actual=response.status_code, expected=200)
    expect(
        actual=response.headers["cache-control"],
        expected=Constants.revalidate,
    )
    versions: list[str] = findall(
        pattern=r'"/assets/(?:js/app\.js|css/styles\.css)\?v=([0-9a-f]{10})"',
        string=response.text,
    )
    expect(actual=len(versions), expected=2)
    expect(actual=len(set(versions)), expected=1)
    console: Response = client.get(url="/console")
    check(
        condition='"/assets/js/console.js?v=' in console.text
        and '"/assets/css/console.css?v=' in console.text,
        reason="the console links are versioned too",
    )


def test_the_asset_version_changes_when_an_asset_does(tmp_path: Path) -> None:
    """Editing any asset gives its links a new address.

    Args:
        tmp_path: Scratch home directory.

    """
    assets: Path = tmp_path / "assets"
    copytree(src=Path(web_module.__file__).parent / "web", dst=assets)
    dashboard: WebDashboard = WebDashboard(
        service=District(repository=SessionRepository(home=tmp_path)),
        home=tmp_path,
        assets=assets,
    )
    before: str = dashboard.asset_version()
    expect(actual=dashboard.asset_version(), expected=before)
    script: Path = assets / "js" / "app.js"
    script.write_text(
        data=script.read_text(encoding="utf-8") + "\n// edited\n",
        encoding="utf-8",
    )
    check(
        condition=dashboard.asset_version() != before,
        reason="an edited asset must change the version",
    )


def _seed(*, home: Path, output: str) -> None:
    """Store two finished commands for session ``s1``.

    Args:
        home: District home directory.
        output: Output stored with the second command.

    """
    database: DistrictDatabase = DistrictDatabase(home=home)
    for number, text in ((1, ""), (2, output)):
        database.command_started(
            command_id=f"c{number}",
            session_uuid="uuid-1",
            session_id="s1",
            host_id="host-1",
            agent="claude-code",
            agent_session="a-1",
            surface="desktop",
            editor="",
            command=f"echo {number}",
            cwd="/w",
            started_at=f"2026-01-01T00:00:0{number}+00:00",
        )
        database.command_finished(
            command_id=f"c{number}",
            finished_at=f"2026-01-01T00:00:0{number}+00:00",
            exit_code=0,
            duration_seconds=0.5,
            output=text,
        )


def test_the_transcript_endpoint_lists_commands_with_output(
    tmp_path: Path,
) -> None:
    """Hook-backed shells are read here, newest last, and never cached.

    Args:
        tmp_path: Scratch home directory.

    """
    _seed(home=tmp_path, output="hello\n")
    response: Response = _client(home=tmp_path).get(
        url="/api/sessions/s1/transcript",
    )
    expect(actual=response.status_code, expected=200)
    expect(actual=response.headers["cache-control"], expected="no-store")
    body: dict[str, object] = response.json()
    expect(actual=body["session"], expected="s1")
    expect(actual=body["persistOutput"], expected=False)
    commands: list[dict[str, object]] = cast(
        typ="list[dict[str, object]]",
        val=body["commands"],
    )
    expect(
        actual=[row["command"] for row in commands],
        expected=["echo 1", "echo 2"],
    )
    expect(
        actual=[row["output"] for row in commands],
        expected=["", "hello\n"],
    )
    expect(
        actual=_client(home=tmp_path)
        .get(url="/api/sessions/none/transcript")
        .json()["commands"],
        expected=[],
    )


def test_the_transcript_reports_whether_the_policy_saves_output(
    tmp_path: Path,
) -> None:
    """The console can tell a missing output from an unsaved one.

    Args:
        tmp_path: Scratch home directory.

    """
    _ = DistrictDatabase(home=tmp_path)
    (state_directory(home=tmp_path) / "privacy.json").write_text(
        data='{"persist_output": true}',
        encoding="utf-8",
    )
    response: Response = _client(home=tmp_path).get(
        url="/api/sessions/s1/transcript",
    )
    expect(actual=response.json()["persistOutput"], expected=True)


def test_a_missing_favicon_is_an_empty_answer_not_a_browser_error(
    tmp_path: Path,
) -> None:
    """Browsers ask for ``/favicon.ico`` unprompted; that must not 404.

    Args:
        tmp_path: Scratch home directory.

    """
    client: TestClient = _client(home=tmp_path)
    answer: Response = client.get(url="/favicon.ico")
    expect(actual=answer.status_code, expected=Constants.no_content)
    expect(actual=answer.text, expected="")
