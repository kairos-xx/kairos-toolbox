"""Typed values returned by qemu-runner."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class CommandResult:
    """Represent one completed guest command.

    Attributes:
        command: Original guest command argument vector.
        return_code: Guest process exit status.
        stdout: Captured standard output.
        stderr: Captured standard error.

    """

    command: tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str
