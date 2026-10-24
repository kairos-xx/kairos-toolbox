# Copyright (c) 2026 Project contributors.
"""Tests for the ``conjure`` session openers the builders inject."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from typing import TYPE_CHECKING, Final, cast, final
from unittest.mock import patch

import pytest

from tools.conjure import (
    AttachedVm,
    LocalRunner,
    QemuConfig,
    QemuVm,
    Settings,
    SshConfig,
    VmDefinition,
    VmRegistry,
)
from tools.stationer.builder import LinuxConfig, WindowsConfig
from tools.stationer.sessions import (
    JobSession,
    open_attached,
    open_container,
    open_host,
    open_remote_vm,
    open_ssh,
    open_vm,
)

from .fake_sessions import install_fake_runners
from .helpers import check, expect

if TYPE_CHECKING:
    from tests.tools.stationer.fake_sessions import RunnerFactory
    from tools.conjure import DockerConfig


def _linux_config() -> LinuxConfig:
    """Create a Linux Docker configuration.

    Returns:
        The configuration.

    """
    return LinuxConfig(
        docker_platform="linux/arm64",
        image="python:3.13-slim-bookworm",
        maintainer="Example",
        description="Demo",
    )


def _windows_config(*, identity: Path) -> WindowsConfig:
    """Create a Windows guest configuration.

    Args:
        identity: SSH key path.

    Returns:
        The configuration.

    """
    return WindowsConfig(
        host="192.0.2.9",
        port=2222,
        user="builder",
        identity_file=identity,
        python=("py", "-3.13"),
        remote_root="C:/builds",
        publisher="Demo",
    )


def _job_settings(*, tmp_path: Path) -> Settings:
    """Create fully-populated ``conjure`` settings.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        The settings.

    """
    return Settings(
        registry_path=tmp_path / "vms.json",
        qemu_executable=Path("/qemu/qemu-system-aarch64"),
        qemu_arguments=("-machine", "virt"),
        qemu_network_device="e1000",
        qemu_network_id="build-net",
        qemu_automatic_host_forward=False,
        qemu_startup_timeout_seconds=240.0,
        qemu_shutdown_timeout_seconds=45.0,
        qemu_terminate_timeout_seconds=8.0,
        ssh_username="builder",
        ssh_identity_file=tmp_path / "id_ed25519",
        ssh_host="192.0.2.9",
        ssh_port=2299,
        ssh_executable=Path("/tools/ssh"),
        scp_executable=Path("/tools/scp"),
        ssh_known_hosts_file=tmp_path / "known_hosts",
        ssh_connect_timeout_seconds=9,
        ssh_poll_interval_seconds=1.5,
        guest_shutdown_command=("shutdown", "/s", "/t", "0"),
        guest_ssh_port=2222,
    )


def test_open_host_returns_a_local_runner(tmp_path: Path) -> None:
    """The host session is the local runner pointed at ``cwd``.

    Args:
        tmp_path: Pytest temporary directory.

    """
    check(
        condition=isinstance(open_host(cwd=tmp_path), LocalRunner),
        reason="open_host must build a LocalRunner",
    )


def test_open_container_maps_the_docker_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Linux configuration becomes a disposable container config.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    factory: RunnerFactory = install_fake_runners(monkeypatch=monkeypatch)
    _ = open_container(config=_linux_config())
    docker: DockerConfig = factory.docker[0]
    expect(
        actual=(
            docker.image,
            docker.create_arguments,
            docker.command,
            docker.working_directory,
        ),
        expected=(
            "python:3.13-slim-bookworm",
            ("--platform", "linux/arm64"),
            ("sleep", "infinity"),
            "/work",
        ),
    )


def test_open_ssh_maps_the_guest_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Windows configuration becomes the SSH endpoint config.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    factory: RunnerFactory = install_fake_runners(monkeypatch=monkeypatch)
    identity: Path = tmp_path / "id_ed25519"
    config: WindowsConfig = _windows_config(identity=identity)
    _ = open_ssh(config=config)
    ssh: SshConfig = factory.ssh[0]
    expect(
        actual=(ssh.username, ssh.identity_file, ssh.host, ssh.port),
        expected=(
            config.user,
            config.identity_file,
            config.host,
            config.port,
        ),
    )


@pytest.mark.parametrize(
    "settings",
    [
        Settings(),
        Settings(qemu_executable=Path("qemu"), ssh_username="builder"),
        Settings(
            qemu_executable=Path("qemu"),
            ssh_identity_file=Path("key"),
        ),
    ],
)
def test_open_vm_requires_qemu_and_ssh_settings(settings: Settings) -> None:
    """A VM needs the QEMU executable, SSH user and SSH key.

    Args:
        settings: Incomplete settings under test.

    """
    with pytest.raises(
        expected_exception=ValueError,
        match=r"qemu\.executable",
    ):
        _ = open_vm(settings=settings)


def test_open_vm_maps_the_conjure_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every ``conjure`` setting lands in the VM configuration.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    factory: RunnerFactory = install_fake_runners(monkeypatch=monkeypatch)
    _ = open_vm(settings=_job_settings(tmp_path=tmp_path))
    qemu: QemuConfig = factory.qemu[0]
    expect(
        actual=(qemu.executable, qemu.arguments, qemu.network_device),
        expected=(
            Path("/qemu/qemu-system-aarch64"),
            ("-machine", "virt"),
            "e1000",
        ),
    )
    expect(
        actual=(qemu.network_id, qemu.automatic_host_forward),
        expected=("build-net", False),
    )
    expect(
        actual=(
            qemu.startup_timeout_seconds,
            qemu.shutdown_timeout_seconds,
            qemu.terminate_timeout_seconds,
        ),
        expected=(240.0, 45.0, 8.0),
    )
    ssh: SshConfig = factory.ssh[0]
    expect(
        actual=(ssh.username, ssh.identity_file, ssh.host, ssh.port),
        expected=("builder", tmp_path / "id_ed25519", "192.0.2.9", 2299),
    )
    expect(
        actual=(
            ssh.ssh_executable,
            ssh.scp_executable,
            ssh.known_hosts_file,
        ),
        expected=(
            Path("/tools/ssh"),
            Path("/tools/scp"),
            tmp_path / "known_hosts",
        ),
    )
    expect(
        actual=(ssh.connect_timeout_seconds, ssh.poll_interval_seconds),
        expected=(9, 1.5),
    )
    expect(
        actual=factory.guest[0].shutdown_command,
        expected=("shutdown", "/s", "/t", "0"),
    )
    expect(actual=factory.guest[0].ssh_port, expected=2222)


@pytest.mark.parametrize(
    "settings",
    [
        Settings(),
        Settings(ssh_username="builder"),
        Settings(ssh_username="builder", ssh_identity_file=Path("key")),
    ],
)
def test_open_attached_requires_ssh_settings(settings: Settings) -> None:
    """Attaching needs the SSH user, key and forwarded port.

    Args:
        settings: Incomplete settings under test.

    """
    with pytest.raises(expected_exception=ValueError, match=r"ssh\.port"):
        _ = open_attached(settings=settings)


def test_open_attached_maps_the_ssh_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SSH settings become the attached endpoint's config.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    factory: RunnerFactory = install_fake_runners(monkeypatch=monkeypatch)
    _ = open_attached(settings=_job_settings(tmp_path=tmp_path))
    ssh: SshConfig = factory.ssh[0]
    expect(
        actual=(ssh.username, ssh.identity_file, ssh.host, ssh.port),
        expected=("builder", tmp_path / "id_ed25519", "192.0.2.9", 2299),
    )
    expect(
        actual=(ssh.ssh_executable, ssh.scp_executable),
        expected=(Path("/tools/ssh"), Path("/tools/scp")),
    )
    expect(
        actual=(ssh.connect_timeout_seconds, ssh.poll_interval_seconds),
        expected=(9, 1.5),
    )


def test_open_remote_vm_attaches_instead_of_booting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``attach`` uses the running guest, whatever the VM name says.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    factory: RunnerFactory = install_fake_runners(monkeypatch=monkeypatch)
    _ = open_remote_vm(
        vm="ignored",
        attach=True,
        settings=_job_settings(tmp_path=tmp_path),
    )
    expect(actual=factory.qemu, expected=[])
    expect(actual=len(factory.ssh), expected=1)


@final
class FakeUtmctl:
    """Stand in for ``utmctl`` and remember which verbs were issued.

    Attributes:
        verbs: Every ``utmctl`` verb in the order it was run.

    """

    def __init__(self, *, running: bool) -> None:
        """Create the fake.

        Args:
            running: Whether the guest is already started.

        """
        self.verbs: Final[list[str]] = []
        self._running: bool = running

    def __call__(
        self,
        *,
        args: tuple[str, ...],
        **ignored: object,
    ) -> CompletedProcess[str]:
        """Answer one ``utmctl`` invocation.

        Args:
            args: Complete argument vector; the verb is the second item.
            **ignored: Subprocess options the fake does not need.

        Returns:
            A successful result carrying the verb's canned output.

        """
        _ = ignored
        verb: str = args[1]
        self.verbs.append(verb)
        output: str = {
            "status": "started\n" if self._running else "stopped\n",
            "ip-address": "192.168.64.9\n",
        }.get(verb, "")
        if verb == "start":
            self._running = True
        return CompletedProcess(args=args, returncode=0, stdout=output)


@pytest.mark.parametrize(
    ("was_running", "stops"),
    [(False, True), (True, False)],
)
def test_a_utm_guest_is_powered_off_only_if_the_build_booted_it(
    *,
    tmp_path: Path,
    was_running: bool,
    stops: bool,
) -> None:
    """A guest that was already on is left on; one that was off is stopped.

    Args:
        tmp_path: Pytest temporary directory.
        was_running: Whether the guest is on when the build begins.
        stops: Whether the build should power it off afterwards.

    """
    utmctl: FakeUtmctl = FakeUtmctl(running=was_running)
    with patch(target="tools.conjure.utm_runner.run", new=utmctl):
        session: JobSession = open_remote_vm(
            vm="utm:Windows 2",
            attach=False,
            settings=_job_settings(tmp_path=tmp_path),
        )
        session.start()
        session.close()
    expect(actual="stop" in utmctl.verbs, expected=stops)
    expect(actual="start" in utmctl.verbs, expected=not was_running)


def test_open_remote_vm_uses_the_configured_vm_when_unnamed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty name boots the VM ``[tool.conjure]`` defines.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    factory: RunnerFactory = install_fake_runners(monkeypatch=monkeypatch)
    _ = open_remote_vm(
        vm="",
        attach=False,
        settings=_job_settings(tmp_path=tmp_path),
    )
    expect(actual=len(factory.qemu), expected=1)


def test_open_remote_vm_materializes_a_registered_vm(tmp_path: Path) -> None:
    """A registered name is read back from the registry file.

    Args:
        tmp_path: Pytest temporary directory.

    """
    registry_path: Path = tmp_path / "vms.json"
    registry: VmRegistry = VmRegistry(path=registry_path)
    registry.register(
        definition=VmDefinition(
            name="builder-vm",
            qemu=QemuConfig(
                executable=Path("/qemu/qemu-system-aarch64"),
                arguments=("-machine", "virt"),
            ),
            ssh=SshConfig(
                username="builder",
                identity_file=tmp_path / "id_ed25519",
            ),
        ),
    )
    session: JobSession = open_remote_vm(
        vm="builder-vm",
        attach=False,
        settings=Settings(registry_path=registry_path),
    )
    check(condition=isinstance(session, QemuVm), reason="a QEMU VM boots")
    vm: QemuVm = cast(typ="QemuVm", val=session)
    check(
        condition=not vm.is_running,
        reason="the returned session is not started",
    )


def test_open_remote_vm_rejects_an_unknown_name(tmp_path: Path) -> None:
    """An unregistered name is a clear error, not a ``KeyError``.

    Args:
        tmp_path: Pytest temporary directory.

    """
    with pytest.raises(expected_exception=ValueError, match="No VM named"):
        _ = open_remote_vm(
            vm="ghost-vm",
            attach=False,
            settings=Settings(registry_path=tmp_path / "vms.json"),
        )


def test_open_remote_vm_attach_returns_the_endpoint(tmp_path: Path) -> None:
    """The real attached endpoint is returned un-started.

    Args:
        tmp_path: Pytest temporary directory.

    """
    session: JobSession = open_remote_vm(
        vm="",
        attach=True,
        settings=_job_settings(tmp_path=tmp_path),
    )
    check(
        condition=isinstance(session, AttachedVm),
        reason="attach builds an AttachedVm",
    )
