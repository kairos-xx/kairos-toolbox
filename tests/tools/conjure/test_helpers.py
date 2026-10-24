"""Coverage for the shared expectation helpers."""

from __future__ import annotations

import pytest

from .helpers import check, expect, present


def test_expect_failure_raises() -> None:
    """Differing values produce an assertion error."""
    with pytest.raises(
        expected_exception=AssertionError,
        match="expected 2; got 1",
    ):
        expect(actual=1, expected=2)


def test_check_failure_raises() -> None:
    """A false condition produces the provided reason."""
    with pytest.raises(
        expected_exception=AssertionError,
        match="must hold",
    ):
        check(condition=False, reason="must hold")


def test_present_rejects_none() -> None:
    """A missing value produces an assertion error."""
    with pytest.raises(
        expected_exception=AssertionError,
        match="expected a value",
    ):
        present(value=None)


def test_present_returns_value() -> None:
    """Existing values pass through unchanged."""
    expect(actual=present(value="x"), expected="x")
