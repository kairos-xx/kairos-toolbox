# Copyright (c) 2026 Project contributors.
"""Coverage for the shared expectation helpers."""

from __future__ import annotations

import pytest

from .helpers import check, expect, present


def test_expect_passes_on_equal_values() -> None:
    """Equal values never raise."""
    expect(actual=1, expected=1)


def test_expect_raises_on_mismatch() -> None:
    """Different values raise ``AssertionError``."""
    with pytest.raises(expected_exception=AssertionError, match="expected"):
        expect(actual=1, expected=2)


def test_check_passes_on_true() -> None:
    """A true condition never raises."""
    check(condition=True, reason="never")


def test_check_raises_on_false() -> None:
    """A false condition raises ``AssertionError`` with the reason."""
    with pytest.raises(expected_exception=AssertionError, match="because"):
        check(condition=False, reason="because")


def test_present_returns_the_value() -> None:
    """A present value is returned unchanged."""
    expect(actual=present(value=3), expected=3)


def test_present_raises_on_none() -> None:
    """``None`` raises ``AssertionError``."""
    none: None = None
    with pytest.raises(expected_exception=AssertionError, match="None"):
        _: None = present(value=none)
