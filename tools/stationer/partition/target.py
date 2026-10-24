# Copyright (c) 2026 Project contributors.
"""Build-target identity for partitioned releases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, final

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildTarget:
    """Target platform description for one build.

    Attributes:
        os: Operating system family.
        arch: CPU architecture.
        abi: ABI or libc flavor (``glibc``, ``musl``, ``native``).

    """

    os: str
    arch: str
    abi: str


@final
class Constants:
    """Group module-level constants.

    Attributes:
        os_names: Grouped module constant.
        arch_names: Grouped module constant.

    """

    os_names: Final[Mapping[str, str]] = {
        "Darwin": "macos",
        "Linux": "linux",
        "Windows": "windows",
    }
    arch_names: Final[Mapping[str, str]] = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86_64": "x86_64",
        "amd64": "amd64",
        "x86": "x86",
        "i386": "x86",
        "i686": "x86",
    }


@dataclass(frozen=True, slots=True, kw_only=True)
class Platform:
    """Release-platform identity for one target.

    Attributes:
        os: Operating system family.
        arch: Canonical CPU architecture.
        abi: ABI or libc flavor.

    """

    os: Literal["linux", "macos", "windows"]
    arch: Literal["amd64", "arm64", "x86"]
    abi: Literal["glibc", "musl", "native"]


def host_platform(*, system: str, machine: str, libc: str | None) -> Platform:
    """Map host identity values onto a release platform.

    Args:
        system: ``platform.system()``-style OS name.
        machine: ``platform.machine()``-style CPU name.
        libc: C library flavor, or ``None`` off Linux.

    Returns:
        The matching release platform.

    Raises:
        RuntimeError: When the OS or architecture is unsupported.

    """
    os_name: str = Constants.os_names.get(system, system.lower())
    arch_name: str = Constants.arch_names.get(machine.lower(), "")
    if os_name == "macos":
        return Platform(
            os="macos",
            arch="arm64" if arch_name == "arm64" else "amd64",
            abi="native",
        )
    if os_name == "windows":
        return Platform(
            os="windows",
            arch="arm64" if arch_name == "arm64" else "amd64",
            abi="native",
        )
    if os_name != "linux":
        msg: str = f"Unsupported OS: {system}"
        raise RuntimeError(msg)
    if arch_name not in {"arm64", "x86_64"}:
        msg = f"Unsupported CPU: {machine}"
        raise RuntimeError(msg)
    return Platform(
        os="linux",
        arch="arm64" if arch_name == "arm64" else "amd64",
        abi="musl" if libc == "musl" else "glibc",
    )


def release_tag(*, platform: Platform) -> str:
    """Return the release-asset platform tag.

    Args:
        platform: Target platform identity.

    Returns:
        A tag such as ``macos-arm64``, ``linux-x86_64`` or
        ``windows-amd64``.

    """
    arch: str = (
        "arm64"
        if platform.arch == "arm64"
        else "x86_64"
        if platform.os in {"macos", "linux"}
        else platform.arch
    )
    return f"{platform.os}-{arch}"
