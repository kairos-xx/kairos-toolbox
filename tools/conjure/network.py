"""Loopback networking helpers for automatic QEMU forwarding."""

from __future__ import annotations

from socket import AF_INET, SOCK_STREAM, socket
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.conjure.config import GuestConfig, QemuConfig


class LoopbackPortAllocator:
    """Allocate operating-system-selected loopback ports."""

    @staticmethod
    def allocate() -> int:
        """Bind port zero on loopback and return the assigned port.

        Returns:
            An ephemeral TCP port currently free on ``127.0.0.1``.

        """
        with socket(family=AF_INET, type=SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])


class QemuNetworkArguments:
    """Render QEMU user-mode networking arguments."""

    @staticmethod
    def build(
        *,
        qemu: QemuConfig,
        guest: GuestConfig,
        host_port: int,
    ) -> tuple[str, ...]:
        """Build NIC and host-forward arguments bound to loopback.

        Args:
            qemu: QEMU configuration supplying the NIC model and network ID.
            guest: Guest configuration supplying the forwarded SSH port.
            host_port: Host loopback port forwarded into the guest.

        Returns:
            QEMU ``-netdev``/``-device`` argument vector.

        """
        return (
            "-netdev",
            f"user,id={qemu.network_id},"
            f"hostfwd=tcp:127.0.0.1:{host_port}-:{guest.ssh_port}",
            "-device",
            f"{qemu.network_device},netdev={qemu.network_id}",
        )
