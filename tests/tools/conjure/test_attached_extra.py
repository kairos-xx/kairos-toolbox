"""Additional AttachedVm coverage."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tools.conjure.attached import AttachedVm
from tools.conjure.config import SshConfig
from tools.conjure.models import CommandResult
from tools.conjure.transport import SshTransport

from .helpers import check


def _config() -> SshConfig:
    """Return an attached endpoint configuration.

    Returns:
        SSH configuration with an explicit port.

    """
    return SshConfig(username="u", identity_file=Path("key"), port=2222)


def test_attached_requires_port() -> None:
    """Reject attachments without a resolved SSH port."""
    with pytest.raises(
        expected_exception=ValueError,
        match="explicit SSH port",
    ):
        AttachedVm(ssh=SshConfig(username="u", identity_file=Path("key")))


def test_attached_full_lifecycle(tmp_path: Path) -> None:
    """Exercise attach, execute, transfer, and detach.

    Args:
        tmp_path: Pytest temporary directory.

    """
    with (
        patch.object(target=SshTransport, attribute="wait_until_ready"),
        patch.object(target=SshTransport, attribute="execute") as execute,
        patch.object(target=SshTransport, attribute="upload") as upload,
        patch.object(target=SshTransport, attribute="download") as download,
    ):
        execute.return_value = CommandResult(
            command=("x",),
            return_code=0,
            stdout="",
            stderr="",
        )
        vm: AttachedVm = AttachedVm(ssh=_config())
        with vm:
            check(
                condition=vm.is_running,
                reason="attachment should be active inside the context",
            )
            vm.run(command=("x",))
            vm.upload(source=tmp_path, destination="/x")
            vm.download(source="/x", destination=tmp_path)
        check(
            condition=not vm.is_running,
            reason="attachment should deactivate on exit",
        )
        upload.assert_called_once()
        download.assert_called_once()


def test_attached_requires_start() -> None:
    """Reject execution before the attachment is activated."""
    vm: AttachedVm = AttachedVm(ssh=_config())
    with pytest.raises(
        expected_exception=RuntimeError,
        match="not been started",
    ):
        vm.run(command=("x",))
