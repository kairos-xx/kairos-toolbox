# Copyright (c) 2026 Project contributors.
"""Tests for the PyPI distribution builder."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.tools.release_builder.helpers import check, expect
from tests.tools.release_builder.test_pyz_builder import RecordingRunner
from tools.release_builder import (
    BuildError,
    ProjectConfig,
    PypiBuilder,
    pypi_builder,
)

from .test_release_builder import Cli, project

if TYPE_CHECKING:
    from subprocess import CompletedProcess


def dist_handler(arguments: tuple[str, ...]) -> str:
    """Fabricate an sdist and wheel for each ``python -m build`` call.

    Args:
        arguments: Command and arguments under test.

    Returns:
        Simulated command output.

    """
    if "build" in arguments and "--outdir" in arguments:
        index: int = arguments.index("--outdir")
        outdir: Path = Path(arguments[index + 1])
        name: str = Path(arguments[-1]).name
        outdir.mkdir(parents=True, exist_ok=True)
        _ = (outdir / f"{name}-1.0.0.tar.gz").write_text(
            data="sdist",
            encoding="utf-8",
        )
        _ = (outdir / f"{name}-1.0.0-py3-none-any.whl").write_text(
            data="wheel",
            encoding="utf-8",
        )
    return ""


def test_builds_dependencies_before_the_project(tmp_path: Path) -> None:
    """Every ``local_dependencies`` entry is built before the project.

    Args:
        tmp_path: Pytest temporary directory.

    """
    dep: Path = tmp_path / "arrow"
    dep.mkdir()
    (dep / "pyproject.toml").write_text(
        data="[project]\nname='dep'\nversion='0.1'\n",
        encoding="utf-8",
    )
    configured: ProjectConfig = replace(
        project(root=tmp_path / "src"),
        local_dependencies=(dep,),
    )
    runner: RecordingRunner = RecordingRunner(handler=dist_handler)
    outdir: Path = tmp_path / "pypi"
    distributions: tuple[Path, ...] = PypiBuilder(runner=runner).build(
        project=configured,
        output=outdir,
    )
    builds: list[tuple[str, ...]] = [
        command
        for command in runner.commands
        if "build" in command and "--outdir" in command
    ]
    expect(actual=len(builds), expected=2)
    check(
        condition=builds[0][-1].endswith("deps/arrow"),
        reason="the sibling distribution must build first",
    )
    check(
        condition=builds[1][-1].endswith("source"),
        reason="the project builds from a staged copy last",
    )
    expect(actual=len(distributions), expected=4)
    check(
        condition=all("staged" in command[-1] for command in builds),
        reason="nothing may build in the real checkout",
    )
    check(
        condition=not (configured.root / "build").exists(),
        reason="the source tree stays clean",
    )


def test_build_reports_missing_artifacts(tmp_path: Path) -> None:
    """A build that writes nothing still returns an empty tuple.

    Args:
        tmp_path: Pytest temporary directory.

    """

    def empty_handler(arguments: tuple[str, ...]) -> str:
        """Produce nothing.

        Args:
            arguments: Command and arguments (unused).

        Returns:
            Empty output.

        """
        _ = arguments
        return ""

    runner: RecordingRunner = RecordingRunner(handler=empty_handler)
    configured: ProjectConfig = project(root=tmp_path / "src")
    distributions: tuple[Path, ...] = PypiBuilder(runner=runner).build(
        project=configured,
        output=tmp_path / "out",
    )
    expect(actual=len(distributions), expected=0)


def test_build_failure_propagates(tmp_path: Path) -> None:
    """A failing ``python -m build`` surfaces as a BuildError.

    Args:
        tmp_path: Pytest temporary directory.

    """

    class FailingRunner:
        """Always fail the build command."""

        @staticmethod
        def execute(
            *,
            arguments: tuple[str, ...],
            cwd: Path | None = None,
            timeout_seconds: int = 1800,
        ) -> CompletedProcess[str]:
            """Raise for every invocation.

            Args:
                arguments: Command and arguments.
                cwd: Optional working directory (unused).
                timeout_seconds: Maximum duration (unused).

            Returns:
                Never returns.

            Raises:
                BuildError: Always.

            """
            _ = (cwd, timeout_seconds)
            msg: str = f"build failed: {' '.join(arguments)}"
            raise BuildError(msg)

    builder: PypiBuilder = PypiBuilder(runner=FailingRunner())
    with pytest.raises(expected_exception=BuildError, match="build failed"):
        _ = builder.build(
            project=project(root=tmp_path / "src"),
            output=tmp_path / "out",
        )


def test_cli_rejects_publish_for_pypi(tmp_path: Path) -> None:
    """``--publish`` with ``--format pypi`` is an argument error.

    Args:
        tmp_path: Pytest temporary directory.

    """
    with pytest.raises(expected_exception=SystemExit):
        _ = Cli.main(
            arguments=(
                "--manifest",
                str(object=tmp_path / "manifest.toml"),
                "--project",
                str(object=tmp_path),
                "--format",
                "pypi",
                "--publish",
                "demo",
            ),
        )


def test_cli_pypi_invokes_the_builder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--format pypi`` routes to ``PypiBuilder.build``.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    manifest: Path = tmp_path / "release-builder.toml"
    project(root=tmp_path / "src")
    _ = manifest.write_text(
        data=(
            '[project]\nname = "demo"\nversion = "1.2.3"\n'
            'entrypoint = "app.py"\nidentifier = "dev.example.demo"\n'
            'console = true\n[macos]\npython = "python3"\n'
        ),
        encoding="utf-8",
    )
    calls: list[Path] = []

    class FakeBuilder:
        """Record the build invocation."""

        def __init__(self, *, runner: object) -> None:
            """Ignore the injected runner.

            Args:
                runner: Command runner (unused).

            """
            _ = runner

        @staticmethod
        def build(
            *,
            project: object,
            output: Path,
        ) -> tuple[Path, ...]:
            """Record the output directory.

            Args:
                project: Project configuration (unused).
                output: Target directory.

            Returns:
                No distributions.

            """
            _ = project
            calls.append(output)
            return ()

    monkeypatch.setattr(
        target=pypi_builder,
        name="PypiBuilder",
        value=FakeBuilder,
    )
    status: int = Cli.main(
        arguments=(
            "--manifest",
            str(object=manifest),
            "--project",
            str(object=tmp_path / "src"),
            "--format",
            "pypi",
            "--output",
            str(object=tmp_path / "dist"),
        ),
    )
    expect(actual=status, expected=0)
    expect(actual=calls, expected=[tmp_path / "dist"])
