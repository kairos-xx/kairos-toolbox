# Copyright (c) 2026 Project contributors.
"""Tests for the key grammar behind keystroke-level input."""

from __future__ import annotations

from typing import Final, final

import pytest
from district.core import Constants
from district.keys import KeyGrammar

from .helpers import check, expect


@final
class Samples:
    """Group the key names the tests judge.

    Attributes:
        accepted: Names a phone keyboard must be able to send.
        refused: Names that must never reach the multiplexer.

    """

    accepted: Final[tuple[str, ...]] = (
        "C-c",
        "M-x",
        "C-M-a",
        "M-Left",
        "C-Right",
        "S-Tab",
        "C-S-Up",
        "M-BSpace",
        "F1",
        "F12",
        "C-\\",
        "C-]",
        "C-Space",
        "BTab",
        "IC",
        "PageUp",
        "7",
    )
    refused: Final[tuple[str, ...]] = (
        "",
        "F0",
        "F13",
        "c-c",
        "C-",
        "C-C-c",
        "M-C-a",
        "S-a",
        "C-ab",
        "Enter; rm -rf /",
        "-t",
        "C-c C-d",
        "Ctrl-c",
        "\n",
    )


def test_every_key_the_old_whitelist_allowed_is_still_allowed() -> None:
    """Replacing the list with a grammar must not take anything away."""
    missing: list[str] = [
        key
        for key in sorted(Constants.sendable_keys)
        if not KeyGrammar.accepts(key=key)
    ]
    expect(actual=missing, expected=[])


@pytest.mark.parametrize(argnames="key", argvalues=Samples.accepted)
def test_modifier_and_function_keys_are_accepted(key: str) -> None:
    """Chords, function keys and navigation keys can be sent.

    Args:
        key: Key name under test.

    """
    check(
        condition=KeyGrammar.accepts(key=key),
        reason=f"{key!r} should be accepted",
    )


@pytest.mark.parametrize(argnames="key", argvalues=Samples.refused)
def test_malformed_keys_are_refused(key: str) -> None:
    """Anything that is not exactly one key name is refused.

    Args:
        key: Key name under test.

    """
    check(
        condition=not KeyGrammar.accepts(key=key),
        reason=f"{key!r} should be refused",
    )
