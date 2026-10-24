"""Immutable execution-backend configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final, final


@final
class Constants:
    """Group module-level constants.

    Attributes:
        max_port: Grouped module constant.

    """

    max_port: Final[int] = 65535


@dataclass(frozen=True, slots=True, kw_only=True)
class GuestConfig:
    """Describe guest lifecycle independently of its operating system.

    Attributes:
        shutdown_command: Optional command for graceful guest shutdown.
        ssh_port: SSH service port inside the guest.

    """

    shutdown_command: tuple[str, ...] | None = None
    ssh_port: int = 22

    def __post_init__(self) -> None:
        """Validate guest configuration."""
        if not 1 <= self.ssh_port <= Constants.max_port:
            msg: str = "Guest SSH port must be between 1 and 65535."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class QemuConfig:
    """Describe a host-agnostic QEMU process.

    Attributes:
        executable: QEMU system executable available on the current host.
        arguments: Host/architecture-specific QEMU arguments.
        network_device: Guest-compatible virtual NIC model.
        network_id: Identifier for generated QEMU user networking.
        automatic_host_forward: Automatically add loopback SSH forwarding.
        startup_timeout_seconds: Maximum SSH readiness wait.
        shutdown_timeout_seconds: Maximum graceful shutdown wait.
        terminate_timeout_seconds: Maximum wait after process termination.
        snapshot_image: qcow2 disk to return to its original state, if any.
        baseline_snapshot: Name of the snapshot holding that original state.
            The VM boots from it and is put back to it after shutdown; the
            first run captures the disk's current state under this name.
        qemu_img: ``qemu-img`` executable used for the baseline; searched on
            ``PATH`` when omitted.

    """

    executable: Path
    arguments: tuple[str, ...]
    network_device: str = "virtio-net-pci"
    network_id: str = "conjure_net"
    automatic_host_forward: bool = True
    startup_timeout_seconds: float = 120.0
    shutdown_timeout_seconds: float = 30.0
    terminate_timeout_seconds: float = 5.0
    snapshot_image: Path | None = None
    baseline_snapshot: str | None = None
    qemu_img: Path | None = None

    def __post_init__(self) -> None:
        """Validate QEMU configuration."""
        if (self.baseline_snapshot is None) != (self.snapshot_image is None):
            msg: str = (
                "QEMU snapshot_image and baseline_snapshot must be set "
                "together."
            )
            raise ValueError(msg)
        if not self.network_device:
            msg = "QEMU network device must not be empty."
            raise ValueError(msg)
        if not self.network_id:
            msg = "QEMU network identifier must not be empty."
            raise ValueError(msg)
        if any(
            timeout <= 0.0
            for timeout in (
                self.startup_timeout_seconds,
                self.shutdown_timeout_seconds,
                self.terminate_timeout_seconds,
            )
        ):
            msg = "QEMU timeouts must be greater than zero."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class SshConfig:
    """Describe SSH authentication without assuming a guest OS.

    Attributes:
        username: Guest account used for execution.
        identity_file: Private authentication key.
        host: QEMU forwarding endpoint.
        port: Optional explicit host port. QEMU allocates one when omitted.
        ssh_executable: Host OpenSSH client.
        scp_executable: Host SCP client.
        known_hosts_file: Optional isolated known-hosts database.
        connect_timeout_seconds: Per-attempt SSH timeout.
        poll_interval_seconds: Readiness polling delay.

    """

    username: str
    identity_file: Path
    host: str = "127.0.0.1"
    port: int | None = None
    ssh_executable: Path = Path("ssh")
    scp_executable: Path = Path("scp")
    known_hosts_file: Path | None = None
    connect_timeout_seconds: int = 5
    poll_interval_seconds: float = 0.5

    def with_port(self, *, port: int) -> SshConfig:
        """Return this configuration with a resolved host port.

        Args:
            port: Resolved host-side SSH port.

        Returns:
            Copy of this configuration with the port set.

        """
        return SshConfig(
            username=self.username,
            identity_file=self.identity_file,
            host=self.host,
            port=port,
            ssh_executable=self.ssh_executable,
            scp_executable=self.scp_executable,
            known_hosts_file=self.known_hosts_file,
            connect_timeout_seconds=self.connect_timeout_seconds,
            poll_interval_seconds=self.poll_interval_seconds,
        )

    def __post_init__(self) -> None:
        """Validate SSH configuration."""
        if not self.username:
            msg: str = "SSH username must not be empty."
            raise ValueError(msg)
        if self.port is not None and (
            not 1 <= self.port <= Constants.max_port
        ):
            msg = "SSH port must be between 1 and 65535."
            raise ValueError(msg)
        if self.connect_timeout_seconds <= 0:
            msg = "SSH connect timeout must be positive."
            raise ValueError(msg)
        if self.poll_interval_seconds <= 0.0:
            msg = "SSH poll interval must be positive."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class DockerConfig:
    """Describe a disposable Docker execution environment.

    Attributes:
        image: Container image to run.
        docker_executable: Host Docker client.
        create_arguments: Additional ``docker create`` arguments.
        command: Optional command overriding the image entrypoint.
        working_directory: Container working directory.
        remove_on_close: Remove the container when the runner closes.
        stop_timeout_seconds: Grace period before the container is killed.

    """

    image: str
    docker_executable: Path = Path("docker")
    create_arguments: tuple[str, ...] = ()
    command: tuple[str, ...] = ()
    working_directory: str | None = None
    remove_on_close: bool = True
    stop_timeout_seconds: int = 10

    def __post_init__(self) -> None:
        """Validate Docker configuration."""
        if not self.image:
            msg: str = "Docker image must not be empty."
            raise ValueError(msg)
        if self.stop_timeout_seconds <= 0:
            msg = "Docker stop timeout must be positive."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalConfig:
    """Describe execution directly on the current host.

    Attributes:
        working_directory: Optional host working directory for commands.

    """

    working_directory: Path | None = None
