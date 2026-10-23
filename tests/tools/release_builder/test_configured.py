# Copyright (c) 2026 Project contributors.
"""Tests for ``[tool.release_builder]``-driven bare invocations."""

from __future__ import annotations

from importlib import import_module
from subprocess import CompletedProcess
from typing import TYPE_CHECKING, ClassVar, final

from tests.tools.release_builder.helpers import check, expect
from tools.release_builder import Cli

from .test_release_builder import project

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    import pytest


def _layout(*, root: Path) -> Path:
    """Create a minimal pyproject, manifest, and project.

    Args:
        root: Directory that acts as the repository root.

    Returns:
        The pyproject path.

    """
    pyproject: Path = root / "pyproject.toml"
    _ = pyproject.write_text(
        data=(
            "[tool.release_builder]\n"
            'project = "src"\n'
            'manifest = "release-builder.toml"\n'
            'output = "out"\n'
            'release_config = "release.toml"\n'
            'platforms = ["linux"]\n'
            'formats = ["pypi", "pyz", "native"]\n'
            'publish = "demo"\n'
            "pypi_upload = true\n"
        ),
        encoding="utf-8",
    )
    _ = (root / "release-builder.toml").write_text(
        data=(
            '[project]\nname = "demo"\nversion = "1.2.3"\n'
            'entrypoint = "app.py"\nidentifier = "dev.example.demo"\n'
            'console = true\n[pyz]\nbootstrap = "app.py"\n'
            '[linux]\ndocker_platform = "linux/amd64"\nimage = "img"\n'
            'maintainer = "m"\ndescription = "d"\n'
        ),
        encoding="utf-8",
    )
    _ = (root / "release.toml").write_text(
        data="[repository]\nowner='o'\nname='r'\n",
        encoding="utf-8",
    )
    project(root=root / "src")
    return pyproject


@final
class Recorders:
    """Fake builders and runner recording the configured pipeline.

    Attributes:
        calls: Ordered (builder, detail) pairs.
        commands: Commands passed to the fake runner.

    """

    calls: ClassVar[list[str]] = []
    commands: ClassVar[list[tuple[str, ...]]] = []

    @classmethod
    def execute(
        cls,
        *,
        arguments: tuple[str, ...],
        cwd: Path | None = None,
        timeout_seconds: int = 1800,
    ) -> CompletedProcess[str]:
        """Record one command as the fake ``CommandRunner``.

        Args:
            arguments: Command and arguments.
            cwd: Optional working directory (unused).
            timeout_seconds: Maximum duration (unused).

        Returns:
            A successful result.

        """
        _ = (cwd, timeout_seconds)
        cls.commands.append(arguments)
        return CompletedProcess(
            args=arguments,
            returncode=0,
            stdout="",
            stderr="",
        )


class _FakeNative:
    """Pretend to build the native installers."""

    def __init__(self, *, runner: object) -> None:
        """Ignore the injected runner.

        Args:
            runner: Command runner (unused).

        """
        _ = runner

    @staticmethod
    def build(
        *,
        configuration: object,
        output: Path,
        platforms: tuple[str, ...],
        invocation: str = "",
    ) -> tuple[()]:
        """Record the native build.

        Args:
            configuration: Build configuration (unused).
            output: Output root.
            platforms: Selected platform names.
            invocation: Command prefix (unused).

        Returns:
            No results.

        """
        _ = (configuration, invocation)
        Recorders.calls.append(f"native:{output.name}:{','.join(platforms)}")
        return ()


class _FakePyz:
    """Pretend to build the ``.pyz`` archive."""

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
        config: object,
        output: Path,
    ) -> Path:
        """Record the archive build.

        Args:
            project: Project configuration (unused).
            config: ``.pyz`` configuration (unused).
            output: Output root.

        Returns:
            A fabricated archive path.

        """
        _ = (project, config)
        Recorders.calls.append(f"pyz:{output.name}")
        return output / "demo.pyz"


class _FakePypi:
    """Pretend to build the PyPI distributions."""

    def __init__(self, *, runner: object) -> None:
        """Ignore the injected runner.

        Args:
            runner: Command runner (unused).

        """
        _ = runner

    @staticmethod
    def build(*, project: object, output: Path) -> tuple[()]:
        """Record the distribution build.

        Args:
            project: Project configuration (unused).
            output: Output root.

        Returns:
            No distributions.

        """
        _ = project
        Recorders.calls.append(f"pypi:{output.name}")
        return ()


class _FakeReport:
    """Skip writing ``BUILD.md``/``artifacts.json``."""

    @staticmethod
    def write(*, output: Path, source: object, runner: object) -> Path:
        """Pretend the report was written.

        Args:
            output: Output root.
            source: Report source metadata (unused).
            runner: Command runner (unused).

        Returns:
            A fabricated report path.

        """
        _ = (source, runner)
        return output / "BUILD.md"


class _FakePublisher:
    """Record the GitHub release publish."""

    def __init__(self, *, config_path: Path) -> None:
        """Record the configuration path.

        Args:
            config_path: ``release.toml`` path.

        """
        Recorders.calls.append(f"config:{config_path.name}")

    @staticmethod
    def publish(
        *,
        output: Path,
        project: str,
        version: str,
        dry_run: bool,
    ) -> object:
        """Record the publish call.

        Args:
            output: Build output root.
            project: Release project name.
            version: Built version.
            dry_run: Whether this is a plan-only run.

        Returns:
            A fabricated plan token.

        """
        Recorders.calls.append(
            f"publish:{project}:{version}:{dry_run}:{output.name}",
        )
        return object()


def test_bare_invocation_runs_the_configured_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare run builds every format, publishes, and uploads to PyPI.

    Args:
        tmp_path: Pytest temporary directory acting as repository root.
        monkeypatch: Pytest monkeypatch fixture.

    """
    _ = _layout(root=tmp_path)
    Recorders.calls.clear()
    Recorders.commands.clear()
    builder_module: ModuleType = import_module(
        name="tools.release_builder.release_builder",
    )
    publisher_module: ModuleType = import_module(
        name="tools.release_builder.publisher",
    )
    pyz_module: ModuleType = import_module(
        name="tools.release_builder.pyz_builder",
    )
    pypi_module: ModuleType = import_module(
        name="tools.release_builder.pypi_builder",
    )
    monkeypatch.setattr(
        target=builder_module,
        name="ReleaseBuilder",
        value=_FakeNative,
    )
    monkeypatch.setattr(
        target=builder_module,
        name="CommandRunner",
        value=Recorders,
    )
    monkeypatch.setattr(
        target=builder_module,
        name="BuildReport",
        value=_FakeReport,
    )
    monkeypatch.setattr(
        target=pyz_module,
        name="PyzBuilder",
        value=_FakePyz,
    )
    monkeypatch.setattr(
        target=pypi_module,
        name="PypiBuilder",
        value=_FakePypi,
    )
    monkeypatch.setattr(
        target=publisher_module,
        name="ReleasePublisher",
        value=_FakePublisher,
    )
    monkeypatch.chdir(path=tmp_path)
    status: int = Cli.main(arguments=())
    expect(actual=status, expected=0)
    expect(
        actual=Recorders.calls,
        expected=[
            "pypi:out",
            "pyz:out",
            "native:out:linux",
            "config:release.toml",
            "publish:demo:1.2.3:False:out",
        ],
    )
    uploads: list[tuple[str, ...]] = [
        command
        for command in Recorders.commands
        if "tools.release_publish.upload" in command
    ]
    expect(actual=len(uploads), expected=1)
    check(
        condition=str(object=tmp_path / "out") in uploads[0][-1],
        reason="the upload must target the output directory",
    )


def test_bare_invocation_without_a_table_returns_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No arguments and no ``[tool.release_builder]`` table is an error.

    Args:
        tmp_path: Pytest temporary directory without a pyproject.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.chdir(path=tmp_path)
    expect(actual=Cli.main(arguments=()), expected=2)


def test_defaults_reject_unknown_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown ``[tool.release_builder]`` keys fail loudly.

    Args:
        tmp_path: Pytest temporary directory acting as repository root.
        monkeypatch: Pytest monkeypatch fixture.

    """
    _ = (tmp_path / "pyproject.toml").write_text(
        data=(
            "[tool.release_builder]\n"
            'project = "src"\nmanifest = "m.toml"\nsurprise = 1\n'
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(path=tmp_path)
    expect(actual=Cli.main(arguments=()), expected=2)
