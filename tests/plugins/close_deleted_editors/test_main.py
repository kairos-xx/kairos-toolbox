# Copyright (c) 2026 Project contributors.
"""Behavioral tests for the extension installer dispatcher."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from plugins.close_deleted_editors import __main__ as dispatcher

if TYPE_CHECKING:
    from types import ModuleType


def expect(*, actual: object, expected: object) -> None:
    """Fail the test when two values differ.

    Args:
        actual: Actual.
        expected: Expected.

    Raises:
        AssertionError: When the assertion fails.

    """
    if actual != expected:
        msg: str = f"expected {expected!r}; got {actual!r}"
        raise AssertionError(msg)


def test_load_installer() -> None:
    """Load the sibling installer module through the public dispatcher."""
    module: ModuleType = dispatcher.load_installer()
    expect(actual=module.__name__.endswith("_install"), expected=True)


@pytest.mark.parametrize(
    "arglist",
    [
        ("--list",),
        ("list",),
        ("run", "list"),
    ],
)
def test_main_list_variants(arglist: tuple[str, ...]) -> None:
    """Normalize supported list forms and execute the installer.

    Args:
        arglist: Dispatcher arguments to normalize.

    """
    expect(actual=dispatcher.main(arguments=arglist), expected=0)


def test_main_help() -> None:
    """Forward help to the installer parser."""
    with pytest.raises(expected_exception=SystemExit) as raised:
        _ = dispatcher.main(arguments=("--help",))
    expect(actual=raised.value.code, expected=0)
