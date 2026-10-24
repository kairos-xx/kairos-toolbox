"""Tests for QEMU subprocess ownership."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.conjure.config import QemuConfig
from tools.conjure.errors import QemuStartError
from tools.conjure.process import QemuProcess

from .helpers import check


def _config() -> QemuConfig:
    """Return deterministic QEMU configuration.

    Returns:
        Minimal QEMU process configuration.

    """
    return QemuConfig(
        executable=Path("qemu-system-aarch64"),
        arguments=("-m", "4G"),
    )


def test_start_builds_shell_free_process() -> None:
    """Launch QEMU without a shell."""
    process: MagicMock = MagicMock()
    process.poll.return_value = None
    with patch(
        target="tools.conjure.process.Popen",
        return_value=process,
    ) as popen:
        owner: QemuProcess = QemuProcess(config=_config())
        owner.start(extra_arguments=())
    check(
        condition=owner.is_running,
        reason="QEMU should be running after start",
    )
    check(
        condition=popen.call_args.kwargs["shell"] is False,
        reason="QEMU must launch without a shell",
    )


def test_immediate_exit_is_start_error() -> None:
    """Reject a QEMU process that exits immediately."""
    process: MagicMock = MagicMock()
    process.poll.return_value = 9
    process.returncode = 9
    with (
        patch(
            target="tools.conjure.process.Popen",
            return_value=process,
        ),
        pytest.raises(
            expected_exception=QemuStartError,
            match="9",
        ),
    ):
        QemuProcess(config=_config()).start()
