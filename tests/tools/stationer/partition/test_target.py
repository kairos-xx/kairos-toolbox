# Copyright (c) 2026 Project contributors.
"""Tests for release platform identity and golden tags."""

from __future__ import annotations

import pytest
from stationer.partition.target import (
    Platform,
    host_platform,
    release_tag,
)

from tests.tools.stationer.helpers import expect


@pytest.mark.parametrize(
    ("system", "machine", "libc", "os", "arch", "abi"),
    [
        ("Darwin", "arm64", None, "macos", "arm64", "native"),
        ("Darwin", "x86_64", None, "macos", "amd64", "native"),
        ("Linux", "x86_64", "glibc", "linux", "amd64", "glibc"),
        ("Linux", "x86_64", "musl", "linux", "amd64", "musl"),
        ("Linux", "aarch64", "glibc", "linux", "arm64", "glibc"),
        ("Windows", "AMD64", None, "windows", "amd64", "native"),
        ("Windows", "ARM64", None, "windows", "arm64", "native"),
    ],
)
def test_host_platform(
    system: str,
    machine: str,
    libc: str | None,
    os: str,
    arch: str,
    abi: str,
) -> None:
    """Map injected host values onto platform identity.

    Args:
        system: Injected OS name.
        machine: Injected CPU name.
        libc: Injected libc flavor.
        os: Expected OS.
        arch: Expected architecture.
        abi: Expected ABI.

    """
    platform: Platform = host_platform(
        system=system,
        machine=machine,
        libc=libc,
    )
    expect(actual=(platform.os, platform.arch, platform.abi), expected=(os, arch, abi))


def test_host_platform_rejects_unsupported() -> None:
    """Unknown OS or CPU raises instead of guessing."""
    with pytest.raises(expected_exception=RuntimeError, match="Unsupported OS"):
        host_platform(system="Plan9", machine="x86_64", libc=None)
    with pytest.raises(expected_exception=RuntimeError, match="Unsupported CPU"):
        host_platform(system="Linux", machine="riscv64", libc="glibc")


@pytest.mark.parametrize(
    ("platform", "tag"),
    [
        (Platform(os="macos", arch="arm64", abi="native"), "macos-arm64"),
        (Platform(os="linux", arch="amd64", abi="glibc"), "linux-x86_64"),
        (Platform(os="linux", arch="arm64", abi="musl"), "linux-arm64"),
        (Platform(os="windows", arch="amd64", abi="native"), "windows-amd64"),
    ],
)
def test_release_tag_golden(platform: Platform, tag: str) -> None:
    """Golden release tags stay byte-identical.

    Args:
        platform: Target platform identity.
        tag: Expected golden tag.

    """
    expect(actual=release_tag(platform=platform), expected=tag)
