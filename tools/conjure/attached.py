"""Attach to an already-running SSH-capable VM."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from tools.conjure.transport import SshTransport

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

    from tools.conjure.config import SshConfig
    from tools.conjure.models import CommandResult


class AttachedVm:
    """Use an existing VM without owning its process lifecycle."""

    _transport: SshTransport
    _is_running: bool

    def __init__(self, *, ssh: SshConfig) -> None:
        """Initialize an attached VM endpoint.

        Args:
            ssh: SSH endpoint configuration for the attached host.

        Raises:
            ValueError: when no SSH port is configured.

        """
        if ssh.port is None:
            msg: str = "Attached VM requires an explicit SSH port."
            raise ValueError(msg)
        self._transport = SshTransport(config=ssh)
        self._is_running = False

    @property
    def is_running(self) -> bool:
        """Return whether this attachment is active.

        Returns:
            ``True`` while the SSH endpoint is active.

        """
        return self._is_running

    def start(self) -> None:
        """Activate this attachment after an SSH probe."""
        self._transport.wait_until_ready(timeout_seconds=30.0)
        self._is_running = True

    def run(
        self,
        *,
        command: tuple[str, ...],
        check: bool = False,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        """Execute a command through the attached VM.

        Args:
            command: Command vector to execute.
            check: Whether to raise on a non-zero exit status.
            timeout_seconds: Optional command timeout in seconds.

        Returns:
            Captured stdout, stderr, and exit status.

        """
        self._require_active()
        return self._transport.execute(
            command=command,
            check=check,
            timeout_seconds=timeout_seconds,
        )

    def upload(
        self,
        *,
        source: Path,
        destination: str,
        recursive: bool | None = None,
    ) -> None:
        """Upload to the attached VM.

        Args:
            source: Source path to copy.
            destination: Destination path for the copy.
            recursive: Copy directories recursively.

        """
        self._require_active()
        self._transport.upload(
            source=source,
            destination=destination,
            recursive=source.is_dir() if recursive is None else recursive,
        )

    def download(
        self,
        *,
        source: str,
        destination: Path,
        recursive: bool = False,
    ) -> None:
        """Download from the attached VM.

        Args:
            source: Source path to copy.
            destination: Destination path for the copy.
            recursive: Copy directories recursively.

        """
        self._require_active()
        self._transport.download(
            source=source,
            destination=destination,
            recursive=recursive,
        )

    def close(self) -> None:
        """Detach without altering the external VM."""
        self._is_running = False

    def __enter__(self) -> Self:
        """Validate connectivity and return this attachment.

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
        """Detach without stopping the external VM.

        Args:
            exc_type: Propagated exception type, when an error occurred.
            exc_value: Propagated exception instance, when an error occurred.
            traceback: Propagated exception traceback, when an error occurred.

        """
        del exc_type, exc_value, traceback
        self.close()

    def _require_active(self) -> None:
        """Require an active attachment.

        Raises:
            RuntimeError: when the runner has been closed.

        """
        if not self._is_running:
            msg: str = "Attached VM has not been started."
            raise RuntimeError(msg)
