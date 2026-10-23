# Copyright (c) 2026 Project contributors.
"""Run child processes without shell interpretation."""

from __future__ import annotations

from subprocess import CompletedProcess
from subprocess import run as _run
from typing import Final


class Constants:
    """Process-runner constants.

    Attributes:
        text_encoding: Text encoding.

    """

    text_encoding: Final[str] = "utf-8"


def run(
    *,
    args: tuple[str, ...],
    check: bool,
    capture_output: bool = False,
    text: bool = False,
) -> CompletedProcess[str]:
    """Execute an argument vector directly and optionally capture output.

    Args:
        args: Args.
        check: Check.
        capture_output: Capture output.
        text: Text.

    Returns:
        The resulting value.

    Raises:
        ValueError: When the operation cannot complete.

    """
    if not args:
        msg: str = "Process argument vector cannot be empty."
        raise ValueError(msg)
    return _run(
        args=args,
        check=check,
        capture_output=capture_output,
        text=True if capture_output else text,
        encoding=Constants.text_encoding if capture_output else None,
    )
