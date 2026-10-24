"""CLI coverage for conjure."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.conjure.cli import command_vector, main
from tools.conjure.inventory import (
    DockerContainerInfo,
    DockerImageInfo,
    QemuImageInfo,
    QemuProcessInfo,
)
from tools.conjure.models import CommandResult

from .helpers import expect


def _result() -> CommandResult:
    """Return a deterministic captured result.

    Returns:
        Successful command result fixture.

    """
    return CommandResult(
        command=("tool",),
        return_code=0,
        stdout="ok",
        stderr="",
    )


def test_command_validation() -> None:
    """Reject an empty command remainder."""
    with pytest.raises(
        expected_exception=ValueError,
        match="command",
    ):
        command_vector(values=["--"])
    expect(
        actual=command_vector(values=["--", "tool"]),
        expected=("tool",),
    )


def test_local_cli() -> None:
    """Dispatch ``local run`` through the runner."""
    with patch(target="tools.conjure.cli.LocalRunner") as cls:
        runner: MagicMock = cls.return_value.__enter__.return_value
        runner.run.return_value = _result()
        expect(
            actual=main(arguments=("local", "run", "--", "tool")),
            expected=0,
        )


def test_docker_inventory_cli() -> None:
    """Dispatch the Docker inventory subcommands."""
    image: DockerImageInfo = DockerImageInfo(
        image_id="id",
        repository="repo",
        tag="tag",
        created_at="now",
        size="1MB",
    )
    container: DockerContainerInfo = DockerContainerInfo(
        container_id="cid",
        image="repo:tag",
        name="c",
        state="running",
        status="Up",
    )
    with patch(target="tools.conjure.cli.DockerInventory") as cls:
        inventory: MagicMock = cls.return_value
        inventory.images.return_value = (image,)
        inventory.containers.return_value = (container,)
        inventory.inspect.return_value = {"Id": "cid"}
        expect(
            actual=main(arguments=("docker", "images")),
            expected=0,
        )
        expect(
            actual=main(
                arguments=("docker", "containers", "--running"),
            ),
            expected=0,
        )
        expect(
            actual=main(arguments=("docker", "inspect", "cid")),
            expected=0,
        )


def test_docker_run_exec_and_copy_cli(tmp_path: Path) -> None:
    """Dispatch Docker run, exec, and copy subcommands.

    Args:
        tmp_path: Pytest temporary directory.

    """
    source: Path = tmp_path / "a"
    source.write_text(data="x", encoding="utf-8")
    with patch(target="tools.conjure.cli.DockerRunner") as cls:
        runner: MagicMock = cls.return_value
        runner.run.return_value = _result()
        expect(
            actual=main(
                arguments=(
                    "docker",
                    "run",
                    "--image",
                    "img",
                    "--",
                    "tool",
                ),
            ),
            expected=0,
        )
        expect(
            actual=main(
                arguments=("docker", "exec", "cid", "--", "tool"),
            ),
            expected=0,
        )
        expect(
            actual=main(
                arguments=(
                    "docker",
                    "copy",
                    "cid",
                    "upload",
                    str(object=source),
                    "/a",
                ),
            ),
            expected=0,
        )
        expect(
            actual=main(
                arguments=(
                    "docker",
                    "copy",
                    "cid",
                    "download",
                    "/a",
                    str(object=tmp_path / "b"),
                ),
            ),
            expected=0,
        )


def test_qemu_inventory_attach_and_copy_cli(tmp_path: Path) -> None:
    """Dispatch QEMU discovery, attach, and copy subcommands.

    Args:
        tmp_path: Pytest temporary directory.

    """
    image: QemuImageInfo = QemuImageInfo(
        path=Path("vm.qcow2"),
        format="qcow2",
        virtual_size_bytes=1,
        actual_size_bytes=1,
    )
    process: QemuProcessInfo = QemuProcessInfo(
        process_id=1,
        executable=Path("qemu"),
        arguments=(),
    )
    ssh: tuple[str, ...] = (
        "--user",
        "u",
        "--identity",
        "key",
        "--port",
        "2222",
    )
    with (
        patch(target="tools.conjure.cli.QemuInventory") as inv_cls,
        patch(target="tools.conjure.cli.AttachedVm") as vm_cls,
    ):
        inventory: MagicMock = inv_cls.return_value
        inventory.images.return_value = (image,)
        inventory.image_details.return_value = image
        inventory.processes.return_value = (process,)
        vm: MagicMock = vm_cls.return_value.__enter__.return_value
        vm.run.return_value = _result()
        expect(
            actual=main(arguments=("qemu", "images", ".")),
            expected=0,
        )
        expect(
            actual=main(arguments=("qemu", "image", "vm.qcow2")),
            expected=0,
        )
        expect(
            actual=main(arguments=("qemu", "processes")),
            expected=0,
        )
        expect(
            actual=main(
                arguments=("qemu", "attach", *ssh, "--", "tool"),
            ),
            expected=0,
        )
        expect(
            actual=main(
                arguments=(
                    "qemu",
                    "copy",
                    *ssh,
                    "upload",
                    str(object=tmp_path),
                    "/conjure/x",
                ),
            ),
            expected=0,
        )
        expect(
            actual=main(
                arguments=(
                    "qemu",
                    "copy",
                    *ssh,
                    "download",
                    "/conjure/x",
                    str(object=tmp_path),
                ),
            ),
            expected=0,
        )


def test_registry_cli(tmp_path: Path) -> None:
    """Dispatch the persistent-registry subcommands.

    Args:
        tmp_path: Pytest temporary directory.

    """
    registry_path: str = str(object=tmp_path / "vms.json")
    common: tuple[str, ...] = ("registry", "--registry", registry_path)
    register: tuple[str, ...] = (
        *common,
        "register",
        "vm",
        "--qemu",
        "qemu-system-aarch64",
        "--user",
        "u",
        "--identity",
        "key",
        "--qemu-arg=-m",
        "--qemu-arg=4G",
    )
    expect(actual=main(arguments=register), expected=0)
    expect(actual=main(arguments=(*common, "list")), expected=0)
    expect(
        actual=main(arguments=(*common, "details", "vm")),
        expected=0,
    )
    with patch(target="tools.conjure.registry.QemuVm") as vm_cls:
        runner: MagicMock = vm_cls.return_value.__enter__.return_value
        runner.run.return_value = _result()
        expect(
            actual=main(arguments=(*common, "run", "vm", "--", "tool")),
            expected=0,
        )
        expect(
            actual=main(
                arguments=(
                    *common,
                    "copy",
                    "vm",
                    "upload",
                    str(object=tmp_path),
                    "/conjure/x",
                ),
            ),
            expected=0,
        )
        expect(
            actual=main(
                arguments=(
                    *common,
                    "copy",
                    "vm",
                    "download",
                    "/conjure/x",
                    str(object=tmp_path),
                ),
            ),
            expected=0,
        )
    expect(
        actual=main(arguments=(*common, "remove", "vm")),
        expected=0,
    )


def test_cli_error_is_exit_one() -> None:
    """Normalize handler failure into exit code one."""
    with patch(
        target="tools.conjure.cli.LocalRunner",
        side_effect=ValueError("bad"),
    ):
        expect(
            actual=main(
                arguments=("--json", "local", "run", "--", "tool"),
            ),
            expected=1,
        )
