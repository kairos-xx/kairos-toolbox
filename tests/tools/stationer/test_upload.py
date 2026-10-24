"""Tests for the safe registry upload helper."""

from __future__ import annotations

from subprocess import CompletedProcess
from typing import TYPE_CHECKING

import pytest

from tools.stationer import upload

from .helpers import check, expect

if TYPE_CHECKING:
    from pathlib import Path


def test_upload_rejects_an_empty_directory(tmp_path: Path) -> None:
    """No distributions means nothing can be uploaded.

    Args:
        tmp_path: Pytest temporary directory.

    """
    with pytest.raises(
        expected_exception=FileNotFoundError,
        match="No distributions",
    ):
        upload.main(arguments=(str(object=tmp_path),))


def test_upload_sends_sorted_distributions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wheels, archives and zips upload in sorted order.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    (tmp_path / "b-2.0-py3-none-any.whl").write_text(
        data="wheel",
        encoding="utf-8",
    )
    (tmp_path / "a-1.0.tar.gz").write_text(data="sdist", encoding="utf-8")
    (tmp_path / "notes.txt").write_text(data="skip me", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(*, args: list[str], check: bool) -> CompletedProcess[bytes]:
        """Record the upload command.

        Args:
            args: Command arguments.
            check: Whether the caller wants failures raised.

        Returns:
            A successful canned result.

        """
        _ = check
        commands.append(args)
        return CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(target=upload, name="run", value=fake_run)
    status: int = upload.main(arguments=(str(object=tmp_path),))
    expect(actual=status, expected=0)
    expect(actual=len(commands), expected=1)
    command: list[str] = commands[0]
    expect(
        actual=command[:4],
        expected=["python", "-m", "twine", "upload"],
    )
    artifacts: list[str] = command[4:]
    expect(
        actual=artifacts,
        expected=[
            str(object=tmp_path / "a-1.0.tar.gz"),
            str(object=tmp_path / "b-2.0-py3-none-any.whl"),
        ],
    )


def test_upload_supports_a_custom_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--repository-url`` is forwarded to twine unchanged.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    (tmp_path / "pkg-1.0.zip").write_text(data="zip", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(*, args: list[str], check: bool) -> CompletedProcess[bytes]:
        """Record the upload command.

        Args:
            args: Command arguments.
            check: Whether the caller wants failures raised.

        Returns:
            A successful canned result.

        """
        _ = check
        commands.append(args)
        return CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(target=upload, name="run", value=fake_run)
    status: int = upload.main(
        arguments=(
            str(object=tmp_path),
            "--repository-url",
            "https://example.test/pypi",
        ),
    )
    expect(actual=status, expected=0)
    check(
        condition="--repository-url" in commands[0],
        reason=str(object=commands),
    )
    check(
        condition="https://example.test/pypi" in commands[0],
        reason=str(object=commands),
    )


def test_upload_finds_distributions_in_nested_folders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distributions under ``pypi/<package>/`` are found recursively.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    for name in ("bowman/a-1.0.tar.gz", "district/l-1.0-py3-none-any.whl"):
        target: Path = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data="dist", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(*, args: list[str], check: bool) -> CompletedProcess[bytes]:
        """Record the upload command.

        Args:
            args: Command arguments.
            check: Ignored.

        Returns:
            A successful result.

        """
        _ = check
        commands.append(args)
        return CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(target=upload, name="run", value=fake_run)
    expect(actual=upload.main(arguments=(str(object=tmp_path),)), expected=0)
    uploaded: list[str] = [
        item.rsplit(sep="/", maxsplit=1)[-1] for item in commands[0][-2:]
    ]
    expect(
        actual=uploaded,
        expected=["a-1.0.tar.gz", "l-1.0-py3-none-any.whl"],
    )
