"""Tests for the assertion helpers' failure paths."""

from __future__ import annotations

import pytest

from .helpers import check, expect, present


def test_expect_raises_on_mismatch() -> None:
    """Unequal values raise ``AssertionError`` with both operands."""
    with pytest.raises(expected_exception=AssertionError, match="expected"):
        expect(actual=1, expected=2)


def test_check_raises_on_false() -> None:
    """A false condition raises ``AssertionError`` with the reason."""
    with pytest.raises(expected_exception=AssertionError, match="nope"):
        check(condition=False, reason="nope")


def test_present_raises_on_none() -> None:
    """``None`` raises ``AssertionError`` instead of passing through."""
    with pytest.raises(expected_exception=AssertionError, match="None"):
        _: None = present(value=None)
