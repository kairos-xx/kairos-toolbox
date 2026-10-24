"""Target remaining backend lifecycle branches for coverage gates."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from tools.conjure.config import (
    DockerConfig,
    GuestConfig,
    QemuConfig,
    SshConfig,
)
from tools.conjure.docker import DockerRunner
from tools.conjure.docker_inventory import DockerInventory
from tools.conjure.errors import QemuRunnerError
from tools.conjure.models import CommandResult
from tools.conjure.process import QemuProcess
from tools.conjure.transport import SshTransport
from tools.conjure.vm import QemuVm

from .helpers import check, expect


def _completed(
    *,
    code: int = 0,
    out: str = "",
) -> CompletedProcess[str]:
    """Return a deterministic completed process.

    Args:
        code: Exit status.
        out: Standard output.

    Returns:
        Captured process result.

    """
    return CompletedProcess(
        args=(),
        returncode=code,
        stdout=out,
        stderr="err" if code else "",
    )


def test_docker_attach_start_and_context() -> None:
    """Attach to a stopped container and detach cleanly."""
    runner: DockerRunner = DockerRunner(
        config=DockerConfig(image="x"),
        container_id="c",
        owns_container=False,
    )
    with (
        patch(
            target="tools.conjure.docker.run",
            side_effect=(_completed(out="false\n"), _completed()),
        ),
        runner,
    ):
        check(
            condition=runner.is_running,
            reason="attached container should report running",
        )
    check(
        condition=not runner.is_running,
        reason="detached container should report stopped",
    )


def test_docker_create_failures_and_keep() -> None:
    """Normalize create failure and honor the keep policy."""
    with (
        patch(
            target="tools.conjure.docker.run",
            return_value=_completed(code=1),
        ),
        pytest.raises(
            expected_exception=QemuRunnerError,
            match="err",
        ),
    ):
        DockerRunner(config=DockerConfig(image="x")).start()
    runner: DockerRunner = DockerRunner(
        config=DockerConfig(image="x", remove_on_close=False),
    )
    with patch(
        target="tools.conjure.docker.run",
        side_effect=(
            _completed(out="c\n"),
            _completed(),
            _completed(),
        ),
    ):
        runner.start()
        runner.close()
    check(
        condition=not runner.is_running,
        reason="container should detach after close",
    )


def test_docker_inventory_inspect_and_errors() -> None:
    """Inspect resources and normalize Docker failure."""
    inventory: DockerInventory = DockerInventory()
    with patch(
        target="tools.conjure.docker_inventory.run",
        return_value=_completed(out='[{"Id":"x"}]'),
    ):
        expect(
            actual=inventory.inspect(resource="x")["Id"],
            expected="x",
        )
    with (
        patch(
            target="tools.conjure.docker_inventory.run",
            return_value=_completed(code=1),
        ),
        pytest.raises(
            expected_exception=QemuRunnerError,
            match="err",
        ),
    ):
        inventory.inspect(resource="x")


def _vm(
    *,
    shutdown: tuple[str, ...] | None = ("poweroff",),
) -> QemuVm:
    """Return a VM with deterministic configuration.

    Args:
        shutdown: Graceful shutdown command or ``None``.

    Returns:
        Configured QEMU VM controller.

    """
    return QemuVm(
        qemu=QemuConfig(executable=Path("qemu"), arguments=()),
        ssh=SshConfig(username="u", identity_file=Path("key")),
        guest=GuestConfig(shutdown_command=shutdown),
    )


def test_vm_context_run_transfer_and_graceful_close(
    tmp_path: Path,
) -> None:
    """Exercise run, transfers, and a graceful shutdown.

    Args:
        tmp_path: Pytest temporary directory.

    """
    vm: QemuVm = _vm()
    transport: MagicMock = MagicMock(spec=SshTransport)
    transport.execute.return_value = CommandResult(
        command=("x",),
        return_code=0,
        stdout="",
        stderr="",
    )
    with (
        patch.object(target=QemuProcess, attribute="start"),
        patch(
            target="tools.conjure.vm.SshTransport",
            return_value=transport,
        ),
        patch(
            target="tools.conjure.vm.LoopbackPortAllocator.allocate",
            return_value=3333,
        ),
        patch.object(target=QemuProcess, attribute="wait", return_value=True),
        patch.object(target=QemuProcess, attribute="reap"),
    ):
        with vm:
            vm.run(command=("x",))
            vm.upload(source=tmp_path, destination="/x")
            vm.download(source="/x", destination=tmp_path)
        expect(actual=vm.host_port, expected=None)


def test_vm_force_close_without_shutdown() -> None:
    """Terminate and kill QEMU when no shutdown command exists."""
    vm: QemuVm = _vm(shutdown=None)
    transport: MagicMock = MagicMock(spec=SshTransport)
    with (
        patch.object(target=QemuProcess, attribute="start"),
        patch(
            target="tools.conjure.vm.SshTransport",
            return_value=transport,
        ),
        patch(
            target="tools.conjure.vm.LoopbackPortAllocator.allocate",
            return_value=3333,
        ),
        patch.object(
            target=QemuProcess,
            attribute="is_running",
            new_callable=PropertyMock,
            return_value=True,
        ),
        patch.object(target=QemuProcess, attribute="terminate") as terminate,
        patch.object(target=QemuProcess, attribute="kill") as kill,
        patch.object(
            target=QemuProcess,
            attribute="wait",
            side_effect=(False, True),
        ),
        patch.object(target=QemuProcess, attribute="reap"),
    ):
        vm.start()
        vm.close()
    terminate.assert_called_once()
    kill.assert_called_once()


def test_vm_requires_transport() -> None:
    """Reject guest execution before startup."""
    with pytest.raises(
        expected_exception=RuntimeError,
        match="not been started",
    ):
        _vm().run(command=("x",))
