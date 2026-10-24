"""Ownership of a detached QEMU system process."""

from __future__ import annotations

from subprocess import DEVNULL, Popen, TimeoutExpired
from typing import TYPE_CHECKING

from tools.conjure.errors import QemuStartError

if TYPE_CHECKING:
    from tools.conjure.config import QemuConfig


class QemuProcess:
    """Start, monitor, and reap one QEMU process."""

    _config: QemuConfig
    _process: Popen[bytes] | None

    def __init__(self, *, config: QemuConfig) -> None:
        """Initialize an owner for a not-yet-started QEMU process.

        Args:
            config: QEMU executable and base arguments.

        """
        self._config = config
        self._process = None

    @property
    def is_running(self) -> bool:
        """Return whether QEMU is alive.

        Returns:
            ``True`` while the QEMU process is alive.

        """
        return self._process is not None and self._process.poll() is None

    def start(self, *, extra_arguments: tuple[str, ...] = ()) -> None:
        """Launch QEMU without a shell.

        Args:
            extra_arguments: Additional QEMU arguments appended to the
                configured vector.

        Raises:
            QemuStartError: If QEMU is already running, cannot be spawned,
                or exits immediately.

        """
        if self.is_running:
            msg: str = "QEMU process is already running."
            raise QemuStartError(msg)
        try:
            process: Popen[bytes] = Popen(
                args=(
                    str(object=self._config.executable),
                    *self._config.arguments,
                    *extra_arguments,
                ),
                stdin=DEVNULL,
                stdout=DEVNULL,
                stderr=DEVNULL,
                shell=False,
            )
        except OSError as error:
            msg = f"QEMU failed to start: {error}"
            raise QemuStartError(msg) from error
        self._process = process
        status: int | None = process.poll()
        if status is not None:
            msg = f"QEMU exited immediately with status {status}."
            raise QemuStartError(msg)

    def wait(self, *, timeout_seconds: float) -> bool:
        """Wait for QEMU to exit within a bounded time.

        Args:
            timeout_seconds: Maximum wait duration.

        Returns:
            Whether the process exited before the deadline.

        """
        if self._process is None:
            return True
        try:
            self._process.wait(timeout=timeout_seconds)
        except TimeoutExpired:
            return False
        return True

    def terminate(self) -> None:
        """Request process termination."""
        if self._process is not None:
            self._process.terminate()

    def kill(self) -> None:
        """Kill the process."""
        if self._process is not None:
            self._process.kill()

    def reap(self) -> None:
        """Collect the process and release ownership."""
        process: Popen[bytes] | None = self._process
        if process is not None:
            process.wait()
            self._process = None
