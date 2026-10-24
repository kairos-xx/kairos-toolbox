"""Additional branch tests for process and transport behavior."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import MagicMock, patch

import pytest

from tools.conjure.config import QemuConfig, SshConfig
from tools.conjure.errors import QemuStartError, SshTimeoutError
from tools.conjure.process import QemuProcess
from tools.conjure.transport import SshTransport

from .helpers import check


def _transport() -> SshTransport:
    """Return a transport with a dedicated known-hosts file.

    Returns:
        Transport configured for readiness polling tests.

    """
    return SshTransport(
        config=SshConfig(
            username="builder",
            identity_file=Path("key"),
            known_hosts_file=Path("known_hosts"),
            port=2222,
            poll_interval_seconds=0.01,
        ),
    )


def test_config_rejects_remaining_invalid_values() -> None:
    """Validate SSH and QEMU constructor invariants."""
    with pytest.raises(expected_exception=ValueError, match="username"):
        SshConfig(username="", identity_file=Path("key"))
    with pytest.raises(
        expected_exception=ValueError,
        match="connect timeout",
    ):
        SshConfig(
            username="x",
            identity_file=Path("key"),
            connect_timeout_seconds=0,
        )
    with pytest.raises(
        expected_exception=ValueError,
        match="poll interval",
    ):
        SshConfig(
            username="x",
            identity_file=Path("key"),
            poll_interval_seconds=0.0,
        )
    with pytest.raises(
        expected_exception=ValueError,
        match="greater than zero",
    ):
        QemuConfig(
            executable=Path("qemu"),
            arguments=(),
            shutdown_timeout_seconds=-1.0,
        )


def test_wait_until_ready_succeeds_after_retry() -> None:
    """Readiness retries failed SSH probes."""
    failed: CompletedProcess[str] = CompletedProcess(
        args=(),
        returncode=255,
        stdout="",
        stderr="late",
    )
    ready: CompletedProcess[str] = CompletedProcess(
        args=(),
        returncode=0,
        stdout="",
        stderr="",
    )
    with (
        patch(
            target="tools.conjure.transport.run",
            side_effect=(failed, ready),
        ),
        patch(target="tools.conjure.transport.sleep"),
    ):
        _transport().wait_until_ready(timeout_seconds=2.0)


def test_wait_until_ready_times_out() -> None:
    """Readiness has a bounded overall deadline."""
    with (
        patch(
            target="tools.conjure.transport.monotonic",
            side_effect=(0.0, 0.5, 2.0),
        ),
        patch(
            target="tools.conjure.transport.run",
            return_value=CompletedProcess(
                args=(),
                returncode=255,
                stdout="",
                stderr="late",
            ),
        ),
        patch(target="tools.conjure.transport.sleep"),
        pytest.raises(
            expected_exception=SshTimeoutError,
            match="did not become ready",
        ),
    ):
        _transport().wait_until_ready(timeout_seconds=1.0)


def test_execute_rejects_empty_command() -> None:
    """Empty guest commands are invalid."""
    with pytest.raises(
        expected_exception=ValueError,
        match="must not be empty",
    ):
        _transport().execute(command=())


def test_upload_success_uses_scp() -> None:
    """Existing host paths are transferred successfully."""
    source: MagicMock = MagicMock(spec=Path)
    source.exists.return_value = True
    with patch(
        target="tools.conjure.transport.run",
        return_value=CompletedProcess(
            args=(),
            returncode=0,
            stdout="",
            stderr="",
        ),
    ) as runner:
        _transport().upload(
            source=source,
            destination="C:/build",
            recursive=True,
        )
    check(condition=runner.called, reason="SCP should run")


def test_process_start_os_error_is_normalized() -> None:
    """OS launch failures become QemuStartError."""
    owner: QemuProcess = QemuProcess(
        config=QemuConfig(
            executable=Path("missing"),
            arguments=(),
        ),
    )
    with (
        patch(
            target="tools.conjure.process.Popen",
            side_effect=OSError("missing"),
        ),
        pytest.raises(
            expected_exception=QemuStartError,
            match="missing",
        ),
    ):
        owner.start(extra_arguments=())


def test_process_wait_timeout_and_escalation_methods() -> None:
    """Process timeout, terminate, kill, and reap are deterministic."""
    process: MagicMock = MagicMock()
    process.poll.return_value = None
    process.wait.side_effect = (
        TimeoutExpired(cmd="qemu", timeout=1.0),
        0,
    )
    owner: QemuProcess = QemuProcess(
        config=QemuConfig(
            executable=Path("qemu"),
            arguments=(),
        ),
    )
    with patch(
        target="tools.conjure.process.Popen",
        return_value=process,
    ):
        owner.start()
    check(
        condition=not owner.wait(timeout_seconds=1.0),
        reason="timeout should report not-exited",
    )
    owner.terminate()
    owner.kill()
    owner.reap()
    process.terminate.assert_called_once_with()
    process.kill.assert_called_once_with()
    check(
        condition=not owner.is_running,
        reason="reaped process should no longer run",
    )


def test_start_rejects_second_running_process() -> None:
    """Starting an already-running owner is rejected."""
    process: MagicMock = MagicMock()
    process.poll.return_value = None
    owner: QemuProcess = QemuProcess(
        config=QemuConfig(
            executable=Path("qemu"),
            arguments=(),
        ),
    )
    with patch(
        target="tools.conjure.process.Popen",
        return_value=process,
    ):
        owner.start(extra_arguments=())
        with pytest.raises(
            expected_exception=QemuStartError,
            match="already",
        ):
            owner.start(extra_arguments=())
