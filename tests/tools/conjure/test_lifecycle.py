"""Lifecycle tests for the high-level QEMU controller."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.conjure.config import GuestConfig, QemuConfig, SshConfig
from tools.conjure.models import CommandResult
from tools.conjure.process import QemuProcess
from tools.conjure.transport import SshTransport
from tools.conjure.vm import QemuVm

from .helpers import expect


def _vm() -> QemuVm:
    """Return a VM with deterministic configuration.

    Returns:
        VM wired with a graceful shutdown command.

    """
    return QemuVm(
        guest=GuestConfig(
            shutdown_command=("poweroff",),
        ),
        qemu=QemuConfig(
            executable=Path("qemu"),
            arguments=("-m", "4G"),
        ),
        ssh=SshConfig(
            username="builder",
            identity_file=Path("key"),
        ),
    )


def test_start_allocates_port_and_injects_hostfwd() -> None:
    """Automatically configure the forwarded SSH endpoint."""
    vm: QemuVm = _vm()
    with (
        patch(
            target="tools.conjure.vm.LoopbackPortAllocator.allocate",
            return_value=45678,
        ),
        patch.object(target=QemuProcess, attribute="start") as start,
        patch.object(
            target=SshTransport,
            attribute="wait_until_ready",
        ),
    ):
        vm.start()
    expect(actual=vm.host_port, expected=45678)
    start.assert_called_once_with(
        extra_arguments=(
            "-netdev",
            "user,id=conjure_net,hostfwd=tcp:127.0.0.1:45678-:22",
            "-device",
            "virtio-net-pci,netdev=conjure_net",
        ),
    )


def test_explicit_port_is_preserved() -> None:
    """Allow deterministic port selection when explicitly requested."""
    vm: QemuVm = QemuVm(
        qemu=QemuConfig(
            executable=Path("qemu"),
            arguments=(),
        ),
        ssh=SshConfig(
            username="builder",
            identity_file=Path("key"),
            port=2222,
        ),
    )
    with (
        patch.object(target=QemuProcess, attribute="start"),
        patch.object(target=SshTransport, attribute="wait_until_ready"),
    ):
        vm.start()
    expect(actual=vm.host_port, expected=2222)


def test_run_uses_resolved_transport() -> None:
    """Guest execution delegates through the active SSH transport."""
    vm: QemuVm = _vm()
    transport: MagicMock = MagicMock(spec=SshTransport)
    expected: CommandResult = CommandResult(
        command=("cmd.exe",),
        return_code=0,
        stdout="ok",
        stderr="",
    )
    transport.execute.return_value = expected
    with (
        patch.object(target=QemuProcess, attribute="start"),
        patch(
            target="tools.conjure.vm.SshTransport",
            return_value=transport,
        ),
        patch.object(target=SshTransport, attribute="wait_until_ready"),
    ):
        vm.start()
    expect(actual=vm.run(command=("cmd.exe",)), expected=expected)
