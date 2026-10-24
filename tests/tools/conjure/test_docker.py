"""Tests for the Docker execution backend."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from tools.conjure.config import DockerConfig
from tools.conjure.docker import DockerRunner
from tools.conjure.errors import (
    QemuRunnerError,
    RemoteCommandError,
    TransferError,
)

from .helpers import check, expect

if TYPE_CHECKING:
    from tools.conjure.models import CommandResult


def _runner() -> DockerRunner:
    """Return a deterministic Docker runner.

    Returns:
        Runner bound to a disposable container configuration.

    """
    return DockerRunner(
        config=DockerConfig(
            image="python:3.13",
            working_directory="/work",
        ),
    )


def _attached() -> DockerRunner:
    """Return a runner bound to an existing container.

    Returns:
        Runner already associated with container ``abc``.

    """
    return DockerRunner(
        config=DockerConfig(
            image="python:3.13",
            working_directory="/work",
        ),
        container_id="abc",
    )


def test_start_create_and_start_container() -> None:
    """Create then start the disposable container."""
    runner: DockerRunner = _runner()
    with patch(
        target="tools.conjure.docker.run",
        side_effect=(
            CompletedProcess(
                args=(),
                returncode=0,
                stdout="abc\n",
                stderr="",
            ),
            CompletedProcess(
                args=(),
                returncode=0,
                stdout="abc\n",
                stderr="",
            ),
        ),
    ) as docker:
        runner.start()
    check(
        condition=runner.is_running,
        reason="container should be associated after start",
    )
    expect(actual=docker.call_count, expected=2)


def test_run_captures_result_and_checked_failure() -> None:
    """Expose Docker exec output using the shared command result."""
    runner: DockerRunner = _attached()
    with patch(
        target="tools.conjure.docker.run",
        return_value=CompletedProcess(
            args=(),
            returncode=7,
            stdout="out",
            stderr="err",
        ),
    ):
        result: CommandResult = runner.run(command=("python", "--version"))
        expect(actual=result.return_code, expected=7)
        with pytest.raises(
            expected_exception=RemoteCommandError,
            match="exited with 7",
        ):
            runner.run(command=("python", "--version"), check=True)


def _fake_docker(*, directory: Path, body: str) -> DockerRunner:
    """Return a runner whose Docker client is a shell script.

    Args:
        directory: Folder that receives the script.
        body: Shell commands the fake ``docker exec`` runs.

    Returns:
        Runner attached to container ``abc`` using the script.

    """
    script: Path = directory / "docker"
    script.write_text(data=f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(mode=0o755)
    return DockerRunner(
        config=DockerConfig(
            image="python:3.13",
            working_directory="/work",
            docker_executable=script,
        ),
        container_id="abc",
    )


def test_run_streams_merged_output_line_by_line(tmp_path: Path) -> None:
    """``on_output`` sees stdout and stderr in order, as they are printed.

    Args:
        tmp_path: Pytest temporary directory.

    """
    runner: DockerRunner = _fake_docker(
        directory=tmp_path,
        body="echo one; echo two >&2; echo three; exit 3",
    )
    seen: list[str] = []
    result: CommandResult = runner.run(
        command=("build",),
        on_output=seen.append,
    )
    expect(actual=seen, expected=["one\n", "two\n", "three\n"])
    expect(actual=result.stdout, expected="one\ntwo\nthree\n")
    expect(actual=result.return_code, expected=3)
    with pytest.raises(
        expected_exception=RemoteCommandError,
        match="exited with 3",
    ):
        runner.run(command=("build",), check=True, on_output=seen.append)


def test_streamed_run_kills_a_command_that_outlives_its_timeout(
    tmp_path: Path,
) -> None:
    """A hung streamed command is killed and reported like a capture.

    Args:
        tmp_path: Pytest temporary directory.

    """
    runner: DockerRunner = _fake_docker(
        directory=tmp_path,
        body="echo started; exec sleep 30",
    )
    seen: list[str] = []
    with pytest.raises(expected_exception=TimeoutExpired):
        runner.run(
            command=("build",),
            timeout_seconds=0.5,
            on_output=seen.append,
        )


def test_upload_and_download_use_docker_cp() -> None:
    """Transfer artifacts through Docker's native copy operation."""
    runner: DockerRunner = _attached()
    source: MagicMock = MagicMock(spec=Path)
    source.exists.return_value = True
    with patch(
        target="tools.conjure.docker.run",
        return_value=CompletedProcess(
            args=(),
            returncode=0,
            stdout="",
            stderr="",
        ),
    ) as docker:
        runner.upload(source=source, destination="/work/input")
        runner.download(
            source="/work/output",
            destination=Path("output"),
        )
    expect(actual=docker.call_count, expected=2)


def test_copy_failure_is_transfer_error() -> None:
    """Normalize docker cp failures."""
    runner: DockerRunner = _attached()
    with (
        patch(
            target="tools.conjure.docker.run",
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
        runner.download(
            source="/missing",
            destination=Path("output"),
        )


def test_close_stops_and_removes_container() -> None:
    """Closing owns both stop and removal."""
    runner: DockerRunner = _attached()
    with patch(
        target="tools.conjure.docker.run",
        return_value=CompletedProcess(
            args=(),
            returncode=0,
            stdout="",
            stderr="",
        ),
    ) as docker:
        runner.close()
    check(
        condition=not runner.is_running,
        reason="container should detach after close",
    )
    expect(actual=docker.call_count, expected=2)


def test_operations_require_started_container() -> None:
    """Reject execution before container startup."""
    runner: DockerRunner = _runner()
    with pytest.raises(
        expected_exception=QemuRunnerError,
        match="not been started",
    ):
        runner.run(command=("true",))
