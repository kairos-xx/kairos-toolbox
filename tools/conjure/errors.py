"""Typed errors shared by every execution backend."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.conjure.models import CommandResult


class QemuRunnerError(RuntimeError):
    """Report a backend lifecycle or tooling failure."""


class QemuStartError(QemuRunnerError):
    """Report a QEMU process that cannot stay alive."""


class SshTimeoutError(QemuRunnerError):
    """Report SSH readiness exceeding its overall deadline."""


class TransferError(QemuRunnerError):
    """Report a failed file transfer."""


class RemoteCommandError(QemuRunnerError):
    """Report a checked command that exited unsuccessfully.

    Attributes:
        result: Captured failing command result.

    """

    result: CommandResult

    def __init__(self, *, result: CommandResult) -> None:
        """Initialize the error with the failing command result.

        Args:
            result: Captured command output and exit status.

        """
        self.result = result
        super().__init__(
            f"Remote command exited with {result.return_code}: "
            f"{result.command!r}",
        )
