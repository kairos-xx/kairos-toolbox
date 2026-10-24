"""Tests for automatic QEMU networking."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.conjure.config import GuestConfig, QemuConfig
from tools.conjure.network import (
    LoopbackPortAllocator,
    QemuNetworkArguments,
)

from .helpers import expect


def test_network_arguments_bind_forwarding_to_loopback() -> None:
    """Generate a localhost-only SSH host forward."""
    config: QemuConfig = QemuConfig(
        executable=Path("qemu"),
        arguments=(),
    )
    expect(
        actual=QemuNetworkArguments.build(
            qemu=config,
            guest=GuestConfig(),
            host_port=49152,
        ),
        expected=(
            "-netdev",
            "user,id=conjure_net,hostfwd=tcp:127.0.0.1:49152-:22",
            "-device",
            "virtio-net-pci,netdev=conjure_net",
        ),
    )


def test_port_allocator_uses_os_ephemeral_port() -> None:
    """Read the OS-selected loopback port."""
    listener: MagicMock = MagicMock()
    listener.__enter__.return_value = listener
    listener.getsockname.return_value = ("127.0.0.1", 54321)
    with patch(
        target="tools.conjure.network.socket",
        return_value=listener,
    ):
        expect(
            actual=LoopbackPortAllocator.allocate(),
            expected=54321,
        )
