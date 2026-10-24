"""Host- and guest-agnostic QEMU VM orchestration."""

from __future__ import annotations

from contextlib import suppress
from subprocess import TimeoutExpired
from typing import TYPE_CHECKING, Self

from tools.conjure.config import GuestConfig, QemuConfig, SshConfig
from tools.conjure.network import (
    LoopbackPortAllocator,
    QemuNetworkArguments,
)
from tools.conjure.process import QemuProcess
from tools.conjure.qemu_snapshot import QemuSnapshotManager
from tools.conjure.transport import SshTransport

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

    from tools.conjure.models import CommandResult


class QemuVm:
    """Own a QEMU VM and its generic SSH transport."""

    _guest_config: GuestConfig
    _qemu_config: QemuConfig
    _ssh_config: SshConfig
    _process: QemuProcess
    _transport: SshTransport | None
    _host_port: int | None
    _disk: QemuSnapshotManager | None

    def __init__(
        self,
        *,
        qemu: QemuConfig,
        ssh: SshConfig,
        guest: GuestConfig | None = None,
    ) -> None:
        """Initialize an idle VM controller.

        Args:
            qemu: QEMU process configuration.
            ssh: SSH endpoint configuration.
            guest: Guest lifecycle configuration; defaults are used when
                omitted.

        """
        self._qemu_config = qemu
        self._ssh_config = ssh
        self._guest_config = guest if guest is not None else GuestConfig()
        self._process = QemuProcess(config=qemu)
        self._transport = None
        self._host_port = None
        self._disk = None

    @property
    def is_running(self) -> bool:
        """Return whether QEMU is alive.

        Returns:
            ``True`` while the guest process is alive.

        """
        return self._process.is_running

    @property
    def host_port(self) -> int | None:
        """Return the resolved forwarded SSH port.

        Returns:
            Resolved host-side SSH port.

        """
        return self._host_port

    def start(self) -> None:
        """Start QEMU, configure forwarding, and await SSH.

        With a baseline snapshot configured, the disk is first returned to
        that snapshot (or the snapshot is taken on the very first run), so
        the guest always boots from its original state.
        """
        self._reset_disk()
        host_port: int = (
            self._ssh_config.port
            if self._ssh_config.port is not None
            else LoopbackPortAllocator.allocate()
        )
        self._host_port = host_port
        self._transport = SshTransport(
            config=self._ssh_config.with_port(port=host_port),
        )
        self._process.start(
            extra_arguments=(
                QemuNetworkArguments.build(
                    qemu=self._qemu_config,
                    guest=self._guest_config,
                    host_port=host_port,
                )
                if self._qemu_config.automatic_host_forward
                else ()
            ),
        )
        try:
            self._transport.wait_until_ready(
                timeout_seconds=self._qemu_config.startup_timeout_seconds,
            )
        except BaseException:
            self._force_close()
            raise

    def run(
        self,
        *,
        command: tuple[str, ...],
        check: bool = False,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        """Execute one command in the guest.

        Args:
            command: Command vector to execute.
            check: Whether to raise on a non-zero exit status.
            timeout_seconds: Optional command timeout in seconds.

        Returns:
            Captured stdout, stderr, and exit status.

        """
        return self._require_transport().execute(
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
        """Upload a file or directory to the guest.

        Args:
            source: Source path to copy.
            destination: Destination path for the copy.
            recursive: Copy directories recursively.

        """
        self._require_transport().upload(
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
        """Download a file or directory from the guest.

        Args:
            source: Source path to copy.
            destination: Destination path for the copy.
            recursive: Copy directories recursively.

        """
        self._require_transport().download(
            source=source,
            destination=destination,
            recursive=recursive,
        )

    def close(self) -> None:
        """Request configured guest shutdown and guarantee QEMU cleanup."""
        if not self._process.is_running:
            self._clear()
            return
        if self._guest_config.shutdown_command is not None:
            with suppress(OSError, TimeoutExpired):
                self._require_transport().execute(
                    command=self._guest_config.shutdown_command,
                    check=False,
                    timeout_seconds=10.0,
                )
        if (
            self._guest_config.shutdown_command is None
            or not self._process.wait(
                timeout_seconds=self._qemu_config.shutdown_timeout_seconds,
            )
        ):
            self._force_close()
        else:
            self._process.reap()
            self._clear()

    def __enter__(self) -> Self:
        """Start and return this VM.

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
        """Close this VM.

        Args:
            exc_type: Propagated exception type, when an error occurred.
            exc_value: Propagated exception instance, when an error occurred.
            traceback: Propagated exception traceback, when an error occurred.

        """
        del exc_type, exc_value, traceback
        self.close()

    def _force_close(self) -> None:
        """Terminate and, if necessary, kill QEMU."""
        self._process.terminate()
        if not self._process.wait(
            timeout_seconds=self._qemu_config.terminate_timeout_seconds,
        ):
            self._process.kill()
            self._process.wait(
                timeout_seconds=self._qemu_config.terminate_timeout_seconds,
            )
        self._process.reap()
        self._clear()

    def _reset_disk(self) -> None:
        """Return the disk to its baseline snapshot before booting.

        The first call captures the current state as the baseline; later
        calls discard whatever the previous run left behind.
        """
        name: str | None = self._qemu_config.baseline_snapshot
        image: Path | None = self._qemu_config.snapshot_image
        if name is None or image is None:
            return
        disk: QemuSnapshotManager = QemuSnapshotManager(
            image=image,
            qemu_img=self._qemu_config.qemu_img,
        )
        _ = disk.reset_to(name=name)
        self._disk = disk

    def _restore_disk(self) -> None:
        """Put the stopped guest's disk back to its baseline snapshot."""
        disk: QemuSnapshotManager | None = self._disk
        name: str | None = self._qemu_config.baseline_snapshot
        self._disk = None
        if disk is not None and name is not None:
            _ = disk.revert(name=name)

    def _clear(self) -> None:
        """Clear resolved runtime state and restore the baseline disk."""
        self._process.reap()
        self._transport = None
        self._host_port = None
        self._restore_disk()

    def _require_transport(self) -> SshTransport:
        """Return the active SSH transport.

        Returns:
            Active SSH transport.

        Raises:
            RuntimeError: when the runner is not open.

        """
        if self._transport is None:
            msg: str = "QEMU VM has not been started."
            raise RuntimeError(msg)
        return self._transport
