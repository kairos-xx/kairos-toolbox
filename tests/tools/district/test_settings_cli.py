# Copyright (c) 2026 Project contributors.
"""Tests for ``--settings``, ``--version`` and update CLI wiring."""

from __future__ import annotations

from io import StringIO
from json import dumps, loads
from sys import executable
from time import time
from typing import TYPE_CHECKING

import pytest
from district import cli as cli_module
from district import settings, update
from district.cli import Cli
from district.core import (
    District,
    SessionRepository,
)
from district.installer import Layout, write_manifest
from district.product import Product, state_directory
from district.update import UpdateState

from .helpers import check, expect, section

if TYPE_CHECKING:
    from pathlib import Path

    from district.json_config import JsonValue


def _releases_payload(**_: object) -> JsonValue:
    """Return one fake GitHub release payload.

    Returns:
        A single release with no assets.

    Args:
            _: Test parameter.

    """
    return [
        {
            "tag_name": "district/v4.0.0",
            "html_url": "u",
            "prerelease": False,
            "draft": False,
            "assets": [],
        },
    ]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return a scratch home exported as ``HOME``.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch.

    Returns:
        The scratch home.

    """
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    return tmp_path


class FakeStderr(StringIO):
    """A stderr stand-in with a configurable TTY flag.

    Attributes:
        tty: Whether this stream pretends to be a terminal.

    """

    tty: bool = False

    def isatty(self) -> bool:
        """Report the configured TTY state.

        Returns:
            Whether this stream pretends to be a terminal.

        """
        return self.tty


@pytest.fixture
def term(monkeypatch: pytest.MonkeyPatch) -> tuple[StringIO, FakeStderr]:
    """Replace the CLI's stdout/stderr with in-memory streams.

    Args:
        monkeypatch: Pytest monkeypatch.

    Returns:
        The stdout and stderr streams.

    """
    out: StringIO = StringIO()
    err: FakeStderr = FakeStderr()
    monkeypatch.setattr(target=cli_module, name="stdout", value=out)
    monkeypatch.setattr(target=cli_module, name="stderr", value=err)
    return out, err


def make_cli(*, home: Path) -> Cli:
    """Build a CLI over a scratch repository.

    Args:
        home: Scratch home.

    Returns:
        The CLI.

    """
    return Cli(
        service=District(repository=SessionRepository(home=home)),
    )


def test_settings_without_install(home: Path) -> None:
    """A source run reports no install and default policies.

    Args:
        home: Scratch District home directory.

    """
    document: dict[str, object] = settings.settings_document(home=home)
    expect(actual=document["version"], expected=Product.version)
    expect(
        actual=document["product"],
        expected={"name": Product.name, "command": Product.command},
    )
    expect(actual=document["install"], expected=None)
    updates: dict[str, object] = section(value=document["updates"])
    expect(actual=updates["enabled"], expected=True)
    expect(actual=updates["check"], expected="daily")
    expect(actual=updates["channel"], expected="stable")
    expect(actual=updates["tag_prefix"], expected="district/v")
    expect(actual=updates["update_available"], expected=False)
    expect(
        actual=updates["config_file"],
        expected=str(object=state_directory(home=home) / "update.json"),
    )
    paths: dict[str, object] = section(value=document["paths"])
    expect(
        actual=paths["database"],
        expected=str(
            object=state_directory(home=home) / Product.database_name,
        ),
    )
    privacy: dict[str, object] = section(value=document["privacy"])
    expect(actual=privacy["history_days"], expected=30)


def test_settings_reports_install_and_update_state(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Install facts, cached update state and env overrides appear.

    Args:
        home: Scratch District home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    layout: Layout = Layout(
        home=home,
        bin_dir=tmp_path / "bin",
        lib_dir=tmp_path / "lib",
        alias="laketest",
        mode="pyz",
        source_root=tmp_path / "x.pyz",
        python=executable,
    )
    write_manifest(layout=layout, keys=frozenset({"launcher", "mcp"}))
    UpdateState(
        checked_at=time(),
        latest="9.9.9",
        release_url="https://example.test/r",
    ).save(home=home)
    monkeypatch.setenv(name="DISTRICT_UPDATE_REPO", value="env/repo")
    document: dict[str, object] = settings.settings_document(home=home)
    install: dict[str, object] = section(value=document["install"])
    expect(actual=install["mode"], expected="pyz")
    expect(actual=install["alias"], expected="laketest")
    expect(actual=install["components"], expected=["launcher", "mcp"])
    expect(actual=install["payload"], expected="district.pyz")
    expect(actual=install["launcher"], expected=str(object=layout.launcher))
    updates: dict[str, object] = section(value=document["updates"])
    expect(actual=updates["repository"], expected="env/repo")
    expect(actual=updates["repository_from_env"], expected=True)
    expect(actual=updates["latest"], expected="9.9.9")
    expect(actual=updates["update_available"], expected=True)
    expect(actual=updates["release_url"], expected="https://example.test/r")


def test_settings_disabled_by_env(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The kill-switch variable disables updates in the report.

    Args:
        home: Scratch District home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setenv(name="DISTRICT_DISABLE_UPDATE_CHECK", value="1")
    updates: dict[str, object] = section(
        value=settings.settings_document(home=home)["updates"],
    )
    expect(actual=updates["enabled"], expected=False)


def test_settings_never_leaks_secret_values(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Environment entries are presence booleans only.

    Args:
        home: Scratch District home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setenv(name="GH_TOKEN", value="ghp_SECRETSECRETSECRET")
    document: dict[str, object] = settings.settings_document(home=home)
    environment: dict[str, object] = section(value=document["environment"])
    expect(actual=environment["GH_TOKEN"], expected=True)
    expect(actual=environment["GITHUB_TOKEN"], expected=False)
    for value in environment.values():
        check(condition=isinstance(value, bool), reason=repr(value))
    check(
        condition="SECRETSECRET" not in dumps(obj=document),
        reason="token leaked",
    )


def test_cli_version(home: Path, term: tuple[StringIO, FakeStderr]) -> None:
    """``--version`` prints the product version.

    Args:
        home: Scratch District home directory.
        term: Captured output and error streams.

    """
    out, _ = term
    expect(
        actual=make_cli(home=home).run(arguments=("--version",)),
        expected=0,
    )
    expect(
        actual=out.getvalue(),
        expected=f"district {Product.version}\n",
    )


def test_cli_settings_prints_pure_json(
    home: Path,
    term: tuple[StringIO, FakeStderr],
) -> None:
    """``--settings`` emits parseable JSON and nothing else.

    Args:
        home: Scratch District home directory.
        term: Captured output and error streams.

    """
    out, err = term
    err.tty = True
    expect(
        actual=make_cli(home=home).run(arguments=("--settings",)),
        expected=0,
    )
    parsed: dict[str, object] = loads(s=out.getvalue())
    expect(actual=parsed["version"], expected=Product.version)
    expect(actual=err.getvalue(), expected="")


@pytest.mark.parametrize("flag", ["--check-updates", "--update-check"])
def test_cli_check_updates_aliases(
    flag: str,
    home: Path,
    term: tuple[StringIO, FakeStderr],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both spellings reach the forced-check implementation.

    Args:
        flag: Test parameter.
        home: Scratch District home directory.
        term: Captured output and error streams.
        monkeypatch: Pytest monkeypatch fixture.

    """
    out, _ = term
    seen: list[Path] = []

    def fake(*, home: Path, stream: object = None) -> int:
        """Return fake.

        Args:
            home: Home.
            stream: Stream.

        Returns:
            The resulting value.

        """
        _ = stream
        seen.append(home)
        return 0

    monkeypatch.setattr(target=update, name="check_updates", value=fake)
    expect(actual=make_cli(home=home).run(arguments=(flag,)), expected=0)
    expect(actual=seen, expected=[home])
    _ = out


def test_cli_update_dispatch(
    home: Path,
    term: tuple[StringIO, FakeStderr],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--update [VERSION]`` forwards the requested version.

    Args:
        home: Scratch District home directory.
        term: Captured output and error streams.
        monkeypatch: Pytest monkeypatch fixture.

    """
    _ = term
    calls: list[str | None] = []

    def fake(*, home: Path, requested: str | None = None, **_: object) -> int:
        """Return fake.

        Args:
            home: Home.
            requested: Requested.
            _: _.

        Returns:
            The resulting value.

        """
        __: Path = home
        calls.append(requested)
        return 0

    monkeypatch.setattr(target=update, name="run_update", value=fake)
    cli: Cli = make_cli(home=home)
    expect(actual=cli.run(arguments=("--update",)), expected=0)
    expect(actual=cli.run(arguments=("--update", "1.2.3")), expected=0)
    expect(actual=calls, expected=[None, "1.2.3"])
    expect(actual=cli.run(arguments=("--update", "--bogus")), expected=2)
    expect(actual=cli.run(arguments=("--update", "1", "2")), expected=2)


def test_cli_update_refresh_entry(
    home: Path,
    term: tuple[StringIO, FakeStderr],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The detached refresh re-enters through the CLI.

    Args:
        home: Scratch District home directory.
        term: Captured output and error streams.
        monkeypatch: Pytest monkeypatch fixture.

    """
    _ = term
    monkeypatch.setattr(
        target=update,
        name="fetch_json",
        value=_releases_payload,
    )
    expect(
        actual=make_cli(home=home).run(arguments=("--update-refresh",)),
        expected=0,
    )
    expect(actual=UpdateState.load(home=home).latest, expected="4.0.0")


def test_cli_help_documents_updates(
    home: Path,
    term: tuple[StringIO, FakeStderr],
) -> None:
    """The manual lists every new flag and variable.

    Args:
        home: Scratch District home directory.
        term: Captured output and error streams.

    """
    out, _ = term
    _ = make_cli(home=home).run(arguments=("--help",))
    for needle in (
        "UPDATES",
        "--version",
        "--check-updates",
        "--settings",
        "--update",
        "--make-release",
        "update.json",
        "DISTRICT_DISABLE_UPDATE_CHECK",
    ):
        check(condition=needle in out.getvalue(), reason=needle)


@pytest.mark.parametrize(
    ("args", "interactive"),
    [
        ((), True),
        (("--status",), True),
        (("--list",), True),
        (("--watch",), True),
        (("--view", "abc123"), True),
        (("--doctor",), True),
        (("abc123",), True),
        (("echo", "hi"), True),
        (("--status", "--json"), False),
        (("--status", "--json", "--local-only"), False),
        (("abc123", "--exec", "ls"), False),
        (("abc123", "--send", "x"), False),
        (("--exec", "ls"), False),
        (("--session-name", "n"), False),
        (("--subscribe",), False),
        (("--mcp",), False),
        (("--cleanup", "a", "b"), False),
        (("--claude-hook",), False),
        (("--settings",), False),
        (("--version",), False),
        (("test", "integration", "synthetic"), False),
        (("explain", "abc123"), False),
        (("--update-refresh",), False),
    ],
)
def test_notice_applies_only_to_interactive_paths(
    args: tuple[str, ...],
    *,
    interactive: bool,
) -> None:
    """Classify which invocations may show an update notice.

    Args:
        args: Test parameter.
        interactive: Test parameter.

    """
    expect(actual=Cli.update_notice_path(args=args), expected=interactive)


def test_notice_runs_for_interactive_tty(
    home: Path,
    term: tuple[StringIO, FakeStderr],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refresh and notify run for a human on a TTY.

    Args:
        home: Scratch District home directory.
        term: Captured output and error streams.
        monkeypatch: Pytest monkeypatch fixture.

    """
    _, err = term
    err.tty = True
    events: list[str] = []

    def _refresh(**_: object) -> None:
        """Handle refresh.

        Args:
            _: _.

        """
        events.append("refresh")

    def _notify(**_: object) -> None:
        """Handle notify.

        Args:
            _: _.

        """
        events.append("notify")

    monkeypatch.setattr(target=update, name="maybe_refresh", value=_refresh)
    monkeypatch.setattr(target=update, name="notify", value=_notify)
    _ = make_cli(home=home).run(arguments=("--list",))
    expect(actual=events, expected=["refresh", "notify"])


@pytest.mark.parametrize("reason", ["no-tty", "managed", "machine"])
def test_notice_is_suppressed(
    reason: str,
    home: Path,
    term: tuple[StringIO, FakeStderr],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No refresh/notice without a TTY, inside a session, or for machines.

    Args:
        reason: Suppression scenario.
        home: Scratch District home directory.
        term: Captured output and error streams.
        monkeypatch: Pytest monkeypatch fixture.

    """
    _, err = term
    err.tty = reason != "no-tty"
    events: list[str] = []

    def _refresh(**_: object) -> None:
        """Handle refresh.

        Args:
            _: _.

        """
        events.append("refresh")

    def _notify(**_: object) -> None:
        """Handle notify.

        Args:
            _: _.

        """
        events.append("notify")

    monkeypatch.setattr(target=update, name="maybe_refresh", value=_refresh)
    monkeypatch.setattr(target=update, name="notify", value=_notify)
    if reason == "managed":
        monkeypatch.setenv(name="DISTRICT_SESSION_ID", value="abc123")
        args: tuple[str, ...] = ("--list",)
    elif reason == "machine":
        args = ("--status", "--json", "--local-only")
    else:
        args = ("--list",)
    _ = make_cli(home=home).run(arguments=args)
    expect(actual=events, expected=[])
