"""Tests for direct host execution."""

from __future__ import annotations

from subprocess import CompletedProcess
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from tools.conjure.config import LocalConfig
from tools.conjure.errors import RemoteCommandError
from tools.conjure.local import LocalRunner

from .helpers import check, expect

if TYPE_CHECKING:
    from pathlib import Path

    from tools.conjure.models import CommandResult


def test_local_lifecycle_and_execution() -> None:
    """Run commands through the same lifecycle API."""
    runner: LocalRunner = LocalRunner(config=LocalConfig())
    with (
        patch(
            target="tools.conjure.local.run",
            return_value=CompletedProcess(
                args=(),
                returncode=0,
                stdout="ok",
                stderr="",
            ),
        ),
        runner,
    ):
        result: CommandResult = runner.run(command=("python", "--version"))
    expect(actual=result.stdout, expected="ok")
    check(
        condition=not runner.is_running,
        reason="local runner should deactivate on exit",
    )


def test_local_checked_failure() -> None:
    """Normalize checked local failures like other backends."""
    runner: LocalRunner = LocalRunner()
    runner.start()
    with (
        patch(
            target="tools.conjure.local.run",
            return_value=CompletedProcess(
                args=(),
                returncode=2,
                stdout="",
                stderr="failed",
            ),
        ),
        pytest.raises(
            expected_exception=RemoteCommandError,
            match="exited with 2",
        ),
    ):
        runner.run(command=("tool",), check=True)


def test_local_rejects_execution_before_start() -> None:
    """Require explicit lifecycle activation."""
    with pytest.raises(
        expected_exception=RuntimeError,
        match="not been started",
    ):
        LocalRunner().run(command=("tool",))


def test_local_rejects_empty_command() -> None:
    """Reject empty commands."""
    runner: LocalRunner = LocalRunner()
    runner.start()
    with pytest.raises(
        expected_exception=ValueError,
        match="must not be empty",
    ):
        runner.run(command=())


def test_local_upload_and_download(tmp_path: Path) -> None:
    """Copy files and directories through the shared transfer API.

    Args:
        tmp_path: Pytest temporary directory.

    """
    source: Path = tmp_path / "in" / "file.txt"
    source.parent.mkdir(parents=True)
    source.write_text(data="data", encoding="utf-8")
    tree: Path = tmp_path / "tree" / "nested"
    tree.mkdir(parents=True)
    (tree / "child.txt").write_text(data="x", encoding="utf-8")
    runner: LocalRunner = LocalRunner()
    runner.start()
    runner.upload(
        source=source,
        destination=str(object=tmp_path / "out" / "f.txt"),
    )
    runner.upload(
        source=tmp_path / "tree",
        destination=str(object=tmp_path / "copied-tree"),
    )
    runner.download(
        source=str(object=source),
        destination=tmp_path / "down.txt",
    )
    expect(
        actual=(tmp_path / "out" / "f.txt").read_text(encoding="utf-8"),
        expected="data",
    )
    check(
        condition=(tmp_path / "copied-tree" / "nested" / "child.txt").exists(),
        reason="directory uploads should recurse",
    )
    expect(
        actual=(tmp_path / "down.txt").read_text(encoding="utf-8"),
        expected="data",
    )


def test_local_transfer_missing_source(tmp_path: Path) -> None:
    """Missing sources surface as file-not-found errors.

    Args:
        tmp_path: Pytest temporary directory.

    """
    runner: LocalRunner = LocalRunner()
    runner.start()
    with pytest.raises(expected_exception=FileNotFoundError):
        runner.download(
            source=str(object=tmp_path / "missing"),
            destination=tmp_path / "out",
        )
