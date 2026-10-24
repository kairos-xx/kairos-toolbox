# Copyright (c) 2026 Project contributors.
"""Cover the package ``__main__`` dispatch entry point."""

from __future__ import annotations

from sys import modules
from types import ModuleType
from typing import TYPE_CHECKING, Final

from district import __main__ as package_main
from district import cli as cli_module
from district import installer as installer_module

from tests.conftest import sibling_tree

from .helpers import expect

if TYPE_CHECKING:
    import pytest


class Constants:
    """Group test constants.

    Attributes:
        status: Fake exit status.

    """

    status: Final[int] = 7


def test_main_dispatches_run_to_installer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route ``run`` arguments to the installer entry point.

    Args:
        monkeypatch: Monkeypatch.

    """
    captured: list[tuple[str, ...] | None] = []

    def fake_install(*, arguments: tuple[str, ...] | None = None) -> int:
        """Record the installer invocation.

        Args:
            arguments: Arguments.

        Returns:
            Status.

        """
        captured.append(arguments)
        return Constants.status

    monkeypatch.setattr(
        target=installer_module,
        name="main",
        value=fake_install,
    )
    expect(
        actual=package_main.main(arguments=("run", "install", "--dry-run")),
        expected=Constants.status,
    )
    expect(actual=captured, expected=[("install", "--dry-run")])


def test_main_dispatches_other_to_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route non-``run`` arguments to the CLI entry point.

    Args:
        monkeypatch: Monkeypatch.

    """
    captured: list[tuple[str, ...] | None] = []

    def fake_cli(*, arguments: tuple[str, ...] | None = None) -> int:
        """Record the CLI invocation.

        Args:
            arguments: Arguments.

        Returns:
            Status.

        """
        captured.append(arguments)
        return Constants.status

    monkeypatch.setattr(target=cli_module, name="main", value=fake_cli)
    expect(
        actual=package_main.main(arguments=("--status",)),
        expected=Constants.status,
    )
    expect(actual=captured, expected=[("--status",)])


def test_main_defaults_to_argv_and_inserts_source_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read ``sys.argv`` and insert the source root when absent.

    Args:
        monkeypatch: Monkeypatch.

    """
    captured: list[tuple[str, ...] | None] = []

    def fake_cli(*, arguments: tuple[str, ...] | None = None) -> int:
        """Record the CLI invocation.

        Args:
            arguments: Arguments.

        Returns:
            Status.

        """
        captured.append(arguments)
        return Constants.status

    fake_module: ModuleType = ModuleType(
        name="district.cli",
    )
    monkeypatch.setattr(
        target=fake_module,
        name="main",
        value=fake_cli,
        raising=False,
    )
    monkeypatch.setitem(
        dic=modules,
        name="district.cli",
        value=fake_module,
    )
    monkeypatch.setattr(
        target=package_main,
        name="argv",
        value=("prog", "--status"),
    )
    monkeypatch.setattr(target=package_main, name="__package__", value="")
    root: str = str(object=sibling_tree().project.parent)
    fresh: list[str] = [entry for entry in package_main.path if entry != root]
    monkeypatch.setattr(target=package_main, name="path", value=fresh)
    expect(actual=package_main.main(), expected=Constants.status)
    expect(actual=captured, expected=[("--status",)])
    expect(actual=fresh[0], expected=root)
