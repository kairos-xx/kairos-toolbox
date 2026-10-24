"""Assertion helpers that avoid ``assert`` in test sources."""

from __future__ import annotations


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


def check(*, condition: bool, reason: str) -> None:
    """Fail the test when a condition does not hold.

    Args:
        condition: Condition.
        reason: Reason.

    Raises:
        AssertionError: When the assertion fails.

    """
    if not condition:
        raise AssertionError(reason)


def present[T](*, value: T | None) -> T:
    """Return ``value`` after failing the test when it is ``None``.

    Args:
        value: Value that must exist.

    Returns:
        The same value, narrowed to exclude ``None``.

    Raises:
        AssertionError: When the value is ``None``.

    """
    if value is None:
        msg: str = "expected a value; got None"
        raise AssertionError(msg)
    return value
