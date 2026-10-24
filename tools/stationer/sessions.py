# Copyright (c) 2026 Project contributors.
"""Open the ``conjure`` runners the platform builders execute in.

Every command the stationer runs outside its own process goes through
a ``tools.conjure`` runner: ``LocalRunner`` for host tools, ``DockerRunner``
for the Linux container, ``AttachedVm`` for a Windows guest that is already
running, and ``QemuVm`` for one that has to be booted first. The builders
depend only on the small :class:`JobSession` protocol, which all of them
satisfy, so tests can substitute a recording fake.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol

from tools.conjure import (
    AttachedVm,
    DockerConfig,
    DockerRunner,
    GuestConfig,
    LocalConfig,
    LocalRunner,
    QemuConfig,
    QemuVm,
    SshConfig,
    UtmRunner,
    VmRegistry,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tools.conjure import CommandResult, Settings
    from tools.stationer.builder import (
        LinuxConfig,
        PosixGuestConfig,
        WindowsConfig,
    )


class SessionConstants:
    """Group VM-selection constants.

    Attributes:
        utm_prefix: ``--vm`` prefix that selects a UTM guest by name.

    """

    utm_prefix: Final[str] = "utm:"


class HostConstants:
    """Group host-command constants.

    Attributes:
        timeout_seconds: Longest a single host command may run.

    """

    timeout_seconds: Final[float] = 1800.0


class JobSession(Protocol):
    """Describe the runner operations the builders rely on."""

    def start(self) -> None:
        """Activate the session (create, boot or connect)."""
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

    def upload(
        self,
        *,
        source: Path,
        destination: str,
        recursive: bool | None = None,
    ) -> None:
        """Copy a host file or directory into the session.

        Args:
            source: Existing host path.
            destination: Destination inside the session.
            recursive: Copy directories recursively.

        """
        ...

    def download(
        self,
        *,
        source: str,
        destination: Path,
        recursive: bool = False,
    ) -> None:
        """Copy a file or directory out of the session.

        Args:
            source: Path inside the session.
            destination: Host destination.
            recursive: Copy directories recursively.

        """
        ...

    def close(self) -> None:
        """Release the session."""
        ...


class StreamingSession(JobSession, Protocol):
    """Describe a session that can hand command output on while it runs."""

    def run(
        self,
        *,
        command: tuple[str, ...],
        check: bool = False,
        timeout_seconds: float | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> CommandResult:
        """Execute one command, optionally streaming its output.

        Args:
            command: Command vector to execute.
            check: Whether to raise on a non-zero exit status.
            timeout_seconds: Optional command timeout in seconds.
            on_output: Receives each output line as it is printed; standard
                error is merged into standard output while it is set.

        Returns:
            Captured output and exit status.

        """
        ...


class HostOpener(Protocol):
    """Create the session that runs a command on the build host."""

    def __call__(self, *, cwd: Path | None = None) -> JobSession:
        """Create a session that has not been started.

        Args:
            cwd: Working directory for the session's commands.

        Returns:
            The session.

        """
        ...


class VmOpener(Protocol):
    """Create the VM session a remote worker runs in."""

    def __call__(
        self,
        *,
        vm: str,
        attach: bool,
        settings: Settings,
    ) -> JobSession:
        """Create a session that has not been started.

        Args:
            vm: Registered VM name, or empty for the VM that
                ``[tool.conjure]`` defines.
            attach: Use the already-running guest the SSH settings describe.
            settings: Resolved ``conjure`` settings.

        Returns:
            The session.

        """
        ...


class ContainerOpener(Protocol):
    """Create the container session of a Linux build."""

    def __call__(self, *, config: LinuxConfig) -> StreamingSession:
        """Create a session that has not been started.

        Args:
            config: Linux Docker configuration.

        Returns:
            The session.

        """
        ...


class SshOpener(Protocol):
    """Create the session of a build on a running SSH guest."""

    def __call__(
        self,
        *,
        config: WindowsConfig | PosixGuestConfig,
    ) -> JobSession:
        """Create a session that has not been started.

        Args:
            config: Guest configuration.

        Returns:
            The session.

        """
        ...


def open_host(*, cwd: Path | None = None) -> JobSession:
    """Create the session that runs host tools in ``cwd``.

    Args:
        cwd: Working directory for the session's commands.

    Returns:
        A local runner that has not been started.

    """
    return LocalRunner(config=LocalConfig(working_directory=cwd))


def open_container(*, config: LinuxConfig) -> StreamingSession:
    """Create the disposable Docker container of a Linux build.

    The container idles on ``sleep infinity`` so commands and file copies
    can be issued to it, and is stopped and removed when the session closes.

    Args:
        config: Linux Docker configuration.

    Returns:
        A Docker runner that has not been started.

    """
    return DockerRunner(
        config=DockerConfig(
            image=config.image,
            create_arguments=("--platform", config.docker_platform),
            command=("sleep", "infinity"),
            working_directory="/work",
        ),
    )


def open_ssh(
    *,
    config: WindowsConfig | PosixGuestConfig,
) -> JobSession:
    """Attach to the running SSH-reachable guest of a build.

    Args:
        config: Guest configuration.

    Returns:
        An attached-VM runner that has not been started.

    """
    return AttachedVm(
        ssh=SshConfig(
            username=config.user,
            identity_file=config.identity_file,
            host=config.host,
            port=config.port,
        ),
    )


def open_vm(*, settings: Settings) -> QemuVm:
    """Create the QEMU VM described by ``[tool.conjure]`` settings.

    Args:
        settings: Resolved ``conjure`` settings; the QEMU executable, the
            SSH user and the SSH key must be set.

    Returns:
        A VM that has not been started.

    Raises:
        ValueError: If a required setting is missing.

    """
    if (
        settings.qemu_executable is None
        or settings.ssh_username is None
        or settings.ssh_identity_file is None
    ):
        msg: str = (
            "[tool.conjure] needs qemu.executable, ssh.username and "
            "ssh.identity_file to define a VM."
        )
        raise ValueError(msg)
    return QemuVm(
        qemu=QemuConfig(
            executable=settings.qemu_executable,
            arguments=settings.qemu_arguments,
            network_device=settings.qemu_network_device,
            network_id=settings.qemu_network_id,
            automatic_host_forward=settings.qemu_automatic_host_forward,
            startup_timeout_seconds=settings.qemu_startup_timeout_seconds,
            shutdown_timeout_seconds=settings.qemu_shutdown_timeout_seconds,
            terminate_timeout_seconds=settings.qemu_terminate_timeout_seconds,
            snapshot_image=settings.qemu_snapshot_image,
            baseline_snapshot=settings.qemu_baseline_snapshot,
            qemu_img=settings.qemu_img_executable,
        ),
        ssh=SshConfig(
            username=settings.ssh_username,
            identity_file=settings.ssh_identity_file,
            host=settings.ssh_host,
            port=settings.ssh_port,
            ssh_executable=settings.ssh_executable,
            scp_executable=settings.scp_executable,
            known_hosts_file=settings.ssh_known_hosts_file,
            connect_timeout_seconds=settings.ssh_connect_timeout_seconds,
            poll_interval_seconds=settings.ssh_poll_interval_seconds,
        ),
        guest=GuestConfig(
            shutdown_command=settings.guest_shutdown_command,
            ssh_port=settings.guest_ssh_port,
        ),
    )


def open_attached(*, settings: Settings) -> AttachedVm:
    """Attach to the running guest ``[tool.conjure]`` describes.

    Args:
        settings: Resolved ``conjure`` settings; the SSH user, key and
            port must be set.

    Returns:
        An attached-VM runner that has not been started.

    Raises:
        ValueError: If a required setting is missing.

    """
    if (
        settings.ssh_username is None
        or settings.ssh_identity_file is None
        or settings.ssh_port is None
    ):
        msg: str = (
            "[tool.conjure] needs ssh.username, ssh.identity_file and "
            "ssh.port to attach to a running VM."
        )
        raise ValueError(msg)
    return AttachedVm(
        ssh=SshConfig(
            username=settings.ssh_username,
            identity_file=settings.ssh_identity_file,
            host=settings.ssh_host,
            port=settings.ssh_port,
            ssh_executable=settings.ssh_executable,
            scp_executable=settings.scp_executable,
            known_hosts_file=settings.ssh_known_hosts_file,
            connect_timeout_seconds=settings.ssh_connect_timeout_seconds,
            poll_interval_seconds=settings.ssh_poll_interval_seconds,
        ),
    )


def open_remote_vm(
    *,
    vm: str,
    attach: bool,
    settings: Settings,
) -> JobSession:
    """Create the VM session of a remote build.

    Args:
        vm: Registered VM name, ``utm:<name>`` for a UTM guest driven
            through ``utmctl``, or empty for the VM that
            ``[tool.conjure]`` defines.
        attach: Use the already-running guest the SSH settings describe.
        settings: Resolved ``conjure`` settings.

    Returns:
        A session that has not been started.

    Raises:
        ValueError: If the VM is not registered or its settings are missing.

    """
    if vm.startswith(SessionConstants.utm_prefix):
        # Power the guest off afterwards only when this build booted it; a
        # guest that was already running is left running.
        return UtmRunner(
            name=vm.removeprefix(SessionConstants.utm_prefix),
            stop_on_close=True,
        )
    if attach:
        return open_attached(settings=settings)
    if not vm:
        return open_vm(settings=settings)
    registry: VmRegistry = VmRegistry(path=settings.registry_path)
    try:
        return registry.use(name=vm)
    except KeyError as error:
        msg: str = f"No VM named {vm!r} in {registry.path}."
        raise ValueError(msg) from error
