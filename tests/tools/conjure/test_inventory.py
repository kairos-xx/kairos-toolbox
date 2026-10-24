"""Tests for resource discovery and attachment."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from tools.conjure.docker_inventory import DockerInventory
from tools.conjure.errors import QemuRunnerError
from tools.conjure.qemu_inventory import QemuInventory

from .helpers import check, expect, present

if TYPE_CHECKING:
    from tools.conjure.inventory import (
        DockerContainerInfo,
        DockerImageInfo,
        QemuImageInfo,
        QemuProcessInfo,
    )


def test_docker_lists_images() -> None:
    """Parse Docker image inventory."""
    with patch(
        target="tools.conjure.docker_inventory.run",
        return_value=CompletedProcess(
            args=(),
            returncode=0,
            stdout=(
                "sha256:abc\tpython\t3.13\t"
                "2026-01-01 00:00:00 +0000 UTC\t1GB\n"
            ),
            stderr="",
        ),
    ):
        images: tuple[DockerImageInfo, ...] = DockerInventory().images()
    expect(
        actual=present(value=images[0] if images else None).reference,
        expected="python:3.13",
    )


def test_docker_lists_containers() -> None:
    """Parse running and stopped Docker containers."""
    with patch(
        target="tools.conjure.docker_inventory.run",
        return_value=CompletedProcess(
            args=(),
            returncode=0,
            stdout="abc\tpython:3.13\tbuilder\trunning\tUp 2 hours\n",
            stderr="",
        ),
    ):
        containers: tuple[DockerContainerInfo, ...] = (
            DockerInventory().containers()
        )
    check(
        condition=present(
            value=containers[0] if containers else None,
        ).is_running,
        reason="container state should parse as running",
    )


def test_qemu_reads_image_details() -> None:
    """Parse qemu-img JSON metadata."""
    with patch(
        target="tools.conjure.qemu_inventory.run",
        return_value=CompletedProcess(
            args=(),
            returncode=0,
            stdout=(
                '{"format":"qcow2","virtual-size":1073741824,'
                '"actual-size":12345}'
            ),
            stderr="",
        ),
    ):
        info: QemuImageInfo = QemuInventory().image_details(
            path=Path("guest.qcow2"),
        )
    expect(actual=info.format, expected="qcow2")
    expect(actual=info.virtual_size_bytes, expected=1073741824)


def test_qemu_lists_running_processes() -> None:
    """Parse QEMU processes from the process inventory."""
    with patch(
        target="tools.conjure.qemu_inventory.run",
        return_value=CompletedProcess(
            args=(),
            returncode=0,
            stdout=("123 /usr/bin/qemu-system-aarch64 -machine virt -m 4G\n"),
            stderr="",
        ),
    ):
        processes: tuple[QemuProcessInfo, ...] = QemuInventory().processes()
    expect(
        actual=present(
            value=processes[0] if processes else None,
        ).process_id,
        expected=123,
    )


def test_qemu_images_discovers_supported_suffixes(
    tmp_path: Path,
) -> None:
    """Find only QEMU disk-image files below existing roots.

    Args:
        tmp_path: Pytest temporary directory.

    """
    image: Path = tmp_path / "a.qcow2"
    image.write_bytes(data=b"")
    raw: Path = tmp_path / "nested" / "b.raw"
    raw.parent.mkdir()
    raw.write_bytes(data=b"")
    other: Path = tmp_path / "c.txt"
    other.write_bytes(data=b"")
    with patch(
        target="tools.conjure.qemu_inventory.run",
        return_value=CompletedProcess(
            args=(),
            returncode=0,
            stdout=('{"format":"qcow2","virtual-size":1,"actual-size":1}'),
            stderr="",
        ),
    ):
        found: tuple[QemuImageInfo, ...] = QemuInventory().images(
            roots=(tmp_path, tmp_path / "missing"),
        )
    expect(
        actual=tuple(sorted(str(object=item.path) for item in found)),
        expected=(str(object=image), str(object=raw)),
    )


def test_qemu_image_details_normalizes_failures() -> None:
    """qemu-img failures become runner errors."""
    with (
        patch(
            target="tools.conjure.qemu_inventory.run",
            return_value=CompletedProcess(
                args=(),
                returncode=1,
                stdout="",
                stderr="bad image",
            ),
        ),
        pytest.raises(
            expected_exception=QemuRunnerError,
            match="bad image",
        ),
    ):
        QemuInventory().image_details(path=Path("x.qcow2"))


def test_qemu_image_details_rejects_malformed_json() -> None:
    """Non-object qemu-img output is rejected."""
    with (
        patch(
            target="tools.conjure.qemu_inventory.run",
            return_value=CompletedProcess(
                args=(),
                returncode=0,
                stdout="[]",
                stderr="",
            ),
        ),
        pytest.raises(
            expected_exception=QemuRunnerError,
            match="invalid metadata",
        ),
    ):
        QemuInventory().image_details(path=Path("x.qcow2"))


def test_qemu_processes_normalizes_failures() -> None:
    """Process-listing failures become runner errors."""
    with (
        patch(
            target="tools.conjure.qemu_inventory.run",
            return_value=CompletedProcess(
                args=(),
                returncode=1,
                stdout="",
                stderr="ps failed",
            ),
        ),
        pytest.raises(
            expected_exception=QemuRunnerError,
            match="ps failed",
        ),
    ):
        QemuInventory().processes()


def test_qemu_processes_skips_unrelated_lines() -> None:
    """Blank and non-QEMU process rows are ignored."""
    with patch(
        target="tools.conjure.qemu_inventory.run",
        return_value=CompletedProcess(
            args=(),
            returncode=0,
            stdout=(
                "\n"
                "  \n"
                "42 /usr/bin/python -m http.server\n"
                "9 qemu-bridge-helper\n"
            ),
            stderr="",
        ),
    ):
        expect(actual=QemuInventory().processes(), expected=())
