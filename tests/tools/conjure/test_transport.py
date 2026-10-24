"""Tests for SSH command construction and result handling."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from tools.conjure.config import SshConfig
from tools.conjure.errors import RemoteCommandError, TransferError
from tools.conjure.transport import SshTransport

from .helpers import expect

if TYPE_CHECKING:
    from tools.conjure.models import CommandResult


def _transport() -> SshTransport:
    """Return a deterministic transport fixture.

    Returns:
        Transport bound to an explicit forwarded port.

    """
    return SshTransport(
        config=SshConfig(
            username="builder",
            identity_file=Path("/key"),
            port=2222,
        ),
    )


def test_execute_returns_captured_result() -> None:
    """Return stdout, stderr, and exit status."""
    with patch(
        target="tools.conjure.transport.run",
        return_value=CompletedProcess(
            args=(),
            returncode=0,
            stdout="hello\n",
            stderr="",
        ),
    ):
        result: CommandResult = _transport().execute(
            command=("cmd.exe", "/c", "echo hello"),
        )
    expect(actual=result.return_code, expected=0)
    expect(actual=result.stdout, expected="hello\n")


def test_checked_execute_raises() -> None:
    """Raise the typed remote-command error when requested."""
    with (
        patch(
            target="tools.conjure.transport.run",
            return_value=CompletedProcess(
                args=(),
                returncode=7,
                stdout="",
                stderr="failure",
            ),
        ),
        pytest.raises(
            expected_exception=RemoteCommandError,
            match="exited with 7",
        ) as captured,
    ):
        _transport().execute(
            command=("cmd.exe", "/c", "exit 7"),
            check=True,
        )
    expect(actual=captured.value.result.return_code, expected=7)


def test_upload_rejects_missing_source() -> None:
    """Reject a missing host source before invoking SCP."""
    with pytest.raises(expected_exception=FileNotFoundError):
        _transport().upload(
            source=Path("/definitely/missing"),
            destination="C:/build/file",
        )


def test_download_raises_for_scp_failure() -> None:
    """Normalize SCP failure into TransferError."""
    with (
        patch(
            target="tools.conjure.transport.run",
            return_value=CompletedProcess(
                args=(),
                returncode=1,
                stdout="",
                stderr="missing",
            ),
        ),
        pytest.raises(
            expected_exception=TransferError,
            match="missing",
        ),
    ):
        _transport().download(
            source="C:/missing",
            destination=Path("artifact"),
        )
