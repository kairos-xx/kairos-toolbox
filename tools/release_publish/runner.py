"""Deterministic subprocess execution."""

from __future__ import annotations

from dataclasses import dataclass
from subprocess import CompletedProcess, run
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True, kw_only=True)
class CommandResult:
    """Completed command output.

    Attributes:
        command: Executed command arguments.
        returncode: Process exit code.
        stdout: Captured standard output.
        stderr: Captured standard error.

    """

    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class Runner(Protocol):
    """Command execution boundary."""

    def run(self, *, command: tuple[str, ...], cwd: Path) -> CommandResult:
        """Execute one command.

        Args:
            command: Command arguments.
            cwd: Working directory.

        Returns:
            The completed command output.

        Raises:
            RuntimeError: If the command exits non-zero.

        """
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class SubprocessRunner:
    """Production subprocess runner."""

    @staticmethod
    def run(*, command: tuple[str, ...], cwd: Path) -> CommandResult:
        """Execute a command without a shell.

        Args:
            command: Command arguments.
            cwd: Working directory.

        Returns:
            The completed command output.

        Raises:
            RuntimeError: If the command exits non-zero.

        """
        completed: CompletedProcess[str] = run(
            args=command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
        result: CommandResult = CommandResult(
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        if result.returncode != 0:
            msg: str = (
                f"Command failed "
                f"({result.returncode}): {' '.join(command)}\n{result.stderr}"
            )
            raise RuntimeError(msg)
        return result
