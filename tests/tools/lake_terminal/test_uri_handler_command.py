# Copyright (c) 2026 Project contributors.
"""Tests for how the OS URI handler invokes Lake Terminal."""

from __future__ import annotations

from dataclasses import replace
from io import StringIO
from shlex import quote
from sys import executable
from typing import TYPE_CHECKING

import pytest

from tools.lake_terminal.lake_terminal import cli as cli_module
from tools.lake_terminal.lake_terminal import installer as installer_module
from tools.lake_terminal.lake_terminal import url_handler
from tools.lake_terminal.lake_terminal.cli import Cli
from tools.lake_terminal.lake_terminal.core import (
    LakeTerminal,
    SessionRepository,
)
from tools.lake_terminal.lake_terminal.installer import Layout

from .helpers import check, expect

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    """Return a live layout rooted in scratch directories.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        The layout.

    """
    return Layout(
        home=tmp_path / "home",
        bin_dir=tmp_path / "bin dir",
        lib_dir=tmp_path / "lib",
        alias="lake",
        mode="live",
        source_root=tmp_path / "tools",
        python=executable,
    )


@pytest.mark.parametrize("mode", ["live", "copy", "pyz"])
def test_handler_runs_the_installed_launcher(
    layout: Layout,
    mode: str,
) -> None:
    """Every install mode is reached through its launcher.

    A ``python -m`` command cannot work from a ``.pyz`` (the package layout
    differs) and needs hand-built import paths elsewhere; the launcher already
    knows both.

    Args:
        layout: Scratch layout.
        mode: Install mode under test.

    """
    command: str = installer_module.handler_command(
        layout=replace(layout, mode=mode),
        uri_argument="%u",
    )
    expect(
        actual=command,
        expected=f"{quote(s=str(object=layout.launcher))} --handle-uri %u",
    )
    check(condition="PYTHONPATH" not in command, reason=command)
    check(condition=" -m " not in command, reason=command)


def test_macos_placeholder_keeps_a_separating_space(layout: Layout) -> None:
    """AppleScript appends the quoted URL, so the command must end in a space.

    Args:
        layout: Scratch layout.

    """
    command: str = installer_module.handler_command(
        layout=layout,
        uri_argument="",
    )
    check(condition=command.endswith("--handle-uri "), reason=repr(command))


def test_windows_handler_goes_through_cmd(
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows registers a ``cmd.exe`` wrapper around the ``.cmd`` launcher.

    Args:
        layout: Scratch layout.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setattr(
        target=installer_module,
        name="system",
        value=lambda: "Windows",
    )
    command: str = installer_module.handler_command(
        layout=layout,
        uri_argument="%1",
    )
    expect(
        actual=command,
        expected=(f'cmd.exe /d /s /c ""{layout.launcher}" --handle-uri "%1""'),
    )


def test_cli_handle_uri_routes_to_the_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--handle-uri URI`` runs the Arrow URI handler and returns its status.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    monkeypatch.setattr(target=cli_module, name="stderr", value=StringIO())
    seen: list[tuple[str, ...]] = []

    def fake(*, arguments: tuple[str, ...]) -> int:
        """Record the routed arguments and return a status.

        Args:
            arguments: Arguments passed to the handler.

        Returns:
            Exit status ``3`` so routing is observable.

        """
        seen.append(arguments)
        return 3

    monkeypatch.setattr(target=url_handler, name="main", value=fake)
    cli: Cli = Cli(
        service=LakeTerminal(repository=SessionRepository(home=tmp_path)),
    )
    expect(
        actual=cli.run(arguments=("--handle-uri", "lake-terminal://x/y")),
        expected=3,
    )
    expect(actual=seen, expected=[("lake-terminal://x/y",)])
    expect(actual=cli.run(arguments=("--handle-uri",)), expected=2)
    expect(actual=cli.run(arguments=("--handle-uri", "a", "b")), expected=2)
