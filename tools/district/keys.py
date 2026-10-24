# Copyright (c) 2026 Project contributors.
"""Validate key names a client may press inside a session pane."""

from __future__ import annotations

from re import Pattern
from re import compile as compile_pattern
from typing import Final, final


@final
class KeyGrammar:
    """Decide whether a string is exactly one multiplexer key name.

    A fixed list cannot cover the chords a phone keyboard needs (``C-M-a``,
    ``M-Left``, ``S-Tab``, ``F5``), and ``send-keys`` takes every name as a
    single argument, so a name is safe exactly when it is one well-formed
    key. Modifiers must appear in the order ``C-``, ``M-``, ``S-`` so every
    chord has one spelling, and ``S-`` applies only to named keys because
    a shifted letter is simply the capital letter.

    Attributes:
        named: Keys that have a name rather than a character.
        accepts_pattern: The compiled grammar.

    """

    named: Final[tuple[str, ...]] = (
        "BSpace",
        "BTab",
        "DC",
        "Down",
        "End",
        "Enter",
        "Escape",
        "F1",
        "F2",
        "F3",
        "F4",
        "F5",
        "F6",
        "F7",
        "F8",
        "F9",
        "F10",
        "F11",
        "F12",
        "Home",
        "IC",
        "Left",
        "PageDown",
        "PageUp",
        "Right",
        "Space",
        "Tab",
        "Up",
    )
    accepts_pattern: Final[Pattern[str]] = compile_pattern(
        pattern=(
            r"(?:[a-z0-9]"
            r"|(?:C-)?(?:M-)?[a-z0-9\\\]\^_@/]"
            r"|(?:C-)?(?:M-)?(?:S-)?(?:" + "|".join(named) + r"))"
        ),
    )

    @classmethod
    def accepts(cls, *, key: str) -> bool:
        """Report whether ``key`` is exactly one well-formed key name.

        Args:
            key: Candidate key name.

        Returns:
            True when the multiplexer may be asked to press it.

        """
        return cls.accepts_pattern.fullmatch(string=key) is not None
