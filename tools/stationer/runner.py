"""Run release commands through ``conjure`` host sessions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from tools.conjure import (
    LocalConfig,
    LocalRunner,
    RemoteCommandError,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tools.conjure import CommandResult


class HostSession(Protocol):
    """Describe the runner operations the release tools rely on."""

    def start(self) -> None:
        """Activate the session."""
        ...

    def run(
        self,
        *,
        command: tuple[str, ...],
        check: bool = False,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        """Execute one command and wait for it.

        Args:
            command: Command vector to execute.
            check: Whether to raise on a non-zero exit status.
            timeout_seconds: Optional command timeout in seconds.

        Returns:
            Captured stdout, stderr and exit status.

        """
        ...

    def close(self) -> None:
        """Release the session."""
        ...


class HostOpener(Protocol):
    """Create the session that runs a command in a working directory."""

    def __call__(self, *, cwd: Path) -> HostSession:
        """Create a session that has not been started.

        Args:
            cwd: Working directory for the session's commands.

        Returns:
            The session.

        """
        ...


def open_host(*, cwd: Path) -> HostSession:
    """Create the ``conjure`` local runner for ``cwd``.

    Args:
        cwd: Working directory for the session's commands.

    Returns:
        A local runner that has not been started.

    """
    return LocalRunner(config=LocalConfig(working_directory=cwd))


def run_checked(
    *,
    open_host: HostOpener,
    command: tuple[str, ...],
    cwd: Path,
) -> CommandResult:
    """Run one command in ``cwd`` and require it to succeed.

    Args:
        open_host: Creates the host session.
        command: Command arguments.
        cwd: Working directory.

    Returns:
        The completed command output.

    Raises:
        RuntimeError: If the command exits non-zero.

    """
    session: HostSession = open_host(cwd=cwd)
    try:
        session.start()
        return session.run(command=command, check=True)
    except RemoteCommandError as error:
        result: CommandResult = error.result
        msg: str = (
            f"Command failed ({result.return_code}): "
            f"{' '.join(command)}\n{result.stderr}"
        )
        raise RuntimeError(msg) from error
    finally:
        session.close()
