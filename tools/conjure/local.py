"""Execution directly on the current host operating system."""

from __future__ import annotations

from pathlib import Path
from shutil import copy2, copytree
from subprocess import CompletedProcess, run
from typing import TYPE_CHECKING, Self

from tools.conjure.config import LocalConfig
from tools.conjure.errors import RemoteCommandError
from tools.conjure.models import CommandResult

if TYPE_CHECKING:
    from types import TracebackType


class LocalRunner:
    """Run commands and copy artifacts on the current host."""

    _config: LocalConfig
    _is_running: bool

    def __init__(self, *, config: LocalConfig | None = None) -> None:
        """Initialize a local runner.

        Args:
            config: Local execution configuration; defaults are used
                when omitted.

        """
        self._config = config if config is not None else LocalConfig()
        self._is_running = False

    @property
    def is_running(self) -> bool:
        """Return whether the local runner is active.

        Returns:
            ``True`` while the runner is open.

        """
        return self._is_running

    def start(self) -> None:
        """Activate local execution."""
        self._is_running = True

    def run(
        self,
        *,
        command: tuple[str, ...],
        check: bool = False,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        """Execute a command directly without a shell.

        Args:
            command: Command vector to execute.
            check: Whether to raise on a non-zero exit status.
            timeout_seconds: Optional command timeout in seconds.

        Returns:
            Captured stdout, stderr, and exit status.

        Raises:
            ValueError: when the command vector is empty.
            RuntimeError: when the runner has been closed.
            RemoteCommandError: when ``check`` is set and the command fails.

        """
        if not self._is_running:
            msg: str = "Local runner has not been started."
            raise RuntimeError(msg)
        if not command:
            msg = "Local command must not be empty."
            raise ValueError(msg)
        completed: CompletedProcess[str] = run(
            args=command,
            cwd=self._config.working_directory,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
        result: CommandResult = CommandResult(
            command=command,
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        if check and result.return_code != 0:
            raise RemoteCommandError(result=result)
        return result

    def upload(
        self,
        *,
        source: Path,
        destination: str,
        recursive: bool | None = None,
    ) -> None:
        """Copy a path within the local host.

        Args:
            source: Source path to copy.
            destination: Destination path for the copy.
            recursive: Copy directories recursively.

        """
        del recursive
        self._copy(source=source, destination=Path(destination))

    def download(
        self,
        *,
        source: str,
        destination: Path,
        recursive: bool = False,
    ) -> None:
        """Copy a path within the local host.

        Args:
            source: Source path to copy.
            destination: Destination path for the copy.
            recursive: Copy directories recursively.

        """
        del recursive
        self._copy(source=Path(source), destination=destination)

    def close(self) -> None:
        """Deactivate local execution."""
        self._is_running = False

    def __enter__(self) -> Self:
        """Start and return this runner.

        Returns:
            This runner.

        """
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the local runner.

        Args:
            exc_type: Propagated exception type, when an error occurred.
            exc_value: Propagated exception instance, when an error occurred.
            traceback: Propagated exception traceback, when an error occurred.

        """
        del exc_type, exc_value, traceback
        self.close()

    @staticmethod
    def _copy(*, source: Path, destination: Path) -> None:
        """Copy a file or directory without assuming a host OS.

        Args:
            source: Source path to copy.
            destination: Destination path for the copy.

        Raises:
            FileNotFoundError: when the source path does not exist.

        """
        if not source.exists():
            raise FileNotFoundError(source)
        if source.is_dir():
            copytree(
                src=source,
                dst=destination,
                dirs_exist_ok=True,
            )
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            copy2(src=source, dst=destination)
