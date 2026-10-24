# Copyright (c) 2026 Project contributors.
"""Tests for ``[tool.stationer.builder]``-driven bare invocations."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, ClassVar, final

from tests.tools.stationer.helpers import check, expect
from tools.stationer import Cli

from .fake_sessions import Hosts
from .test_builder import project

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    import pytest


def layout(*, root: Path) -> Path:
    """Create a minimal pyproject, manifest, and project.

    Args:
        root: Directory that acts as the repository root.

    Returns:
        The pyproject path.

    """
    pyproject: Path = root / "pyproject.toml"
    _ = pyproject.write_text(
        data=(
            "[tool.stationer.builder]\n"
            'project = "src"\n'
            'manifest = "stationer.toml"\n'
            'output = "out"\n'
            'release_config = "release.toml"\n'
            'platforms = ["linux"]\n'
            'formats = ["pypi", "pyz", "native"]\n'
            'publish = "demo"\n'
            "pypi_upload = true\n"
        ),
        encoding="utf-8",
    )
    _ = (root / "stationer.toml").write_text(
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

    """

    calls: ClassVar[list[str]] = []


class _FakeNative:
    """Pretend to build the native installers."""

    def __init__(self, **_kwargs: object) -> None:
        """Accept the CLI's progress and reporter construction.

        Args:
            **_kwargs: Ignored progress and reporter sinks.

        """

    @staticmethod
    def build(
        *,
        configuration: object,
        output: Path,
        platforms: tuple[str, ...],
        invocation: str = "",
        macos_architectures: tuple[str, ...] = ("native",),
    ) -> tuple[()]:
        """Record the native build.

        Args:
            configuration: Build configuration (unused).
            output: Output root.
            platforms: Selected platform names.
            invocation: Command prefix (unused).
            macos_architectures: Architectures requested (unused).

        Returns:
            No results.

        """
        _ = (configuration, invocation, macos_architectures)
        Recorders.calls.append(f"native:{output.name}:{','.join(platforms)}")
        return ()


class _FakePyz:
    """Pretend to build the ``.pyz`` archive."""

    def __init__(self) -> None:
        """Accept the CLI's default construction."""

    @staticmethod
    def build(
        *,
        project: object,
        config: object,
        output: Path,
        target: object = None,
    ) -> Path:
        """Record the archive build.

        Args:
            project: Project configuration (unused).
            config: ``.pyz`` configuration (unused).
            output: Output root.
            target: Matrix cell the archive serves (unused).

        Returns:
            A fabricated archive path.

        """
        _ = (project, config, target)
        Recorders.calls.append(f"pyz:{output.name}")
        return output / "demo.pyz"


class _FakePypi:
    """Pretend to build the PyPI distributions."""

    def __init__(self) -> None:
        """Accept the CLI's default construction."""

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
        output.mkdir(parents=True, exist_ok=True)
        Recorders.calls.append(f"pypi:{output.name}")
        return ()


class _FakeReport:
    """Skip writing ``BUILD.md``/``artifacts.json``."""

    @staticmethod
    def write(*, output: Path, source: object) -> Path:
        """Pretend the report was written.

        Args:
            output: Output root.
            source: Report source metadata (unused).

        Returns:
            A fabricated report path.

        """
        _ = source
        return output / "BUILD.md"


class _FakePublisher:
    """Record the GitHub stationer."""

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
    _ = layout(root=tmp_path)
    Recorders.calls.clear()
    builder_module: ModuleType = import_module(
        name="tools.stationer.builder",
    )
    publisher_module: ModuleType = import_module(
        name="tools.stationer.publisher",
    )
    pyz_module: ModuleType = import_module(
        name="tools.stationer.pyz_builder",
    )
    pypi_module: ModuleType = import_module(
        name="tools.stationer.pypi_builder",
    )
    monkeypatch.setattr(
        target=builder_module,
        name="ReleaseBuilder",
        value=_FakeNative,
    )
    hosts: Hosts = Hosts()
    monkeypatch.setattr(target=builder_module, name="open_host", value=hosts)
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
            "pypi:pypi",
            *(["pyz:pyz"] * 8),
            "native:out:linux",
            "config:release.toml",
            "publish:demo:1.2.3:False:out",
        ],
    )
    uploads: list[tuple[str, ...]] = [
        command
        for command in hosts.commands
        if "tools.stationer.upload" in command
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
    """No arguments and no ``[tool.stationer.builder]`` table is an error.

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
    """Unknown ``[tool.stationer.builder]`` keys fail loudly.

    Args:
        tmp_path: Pytest temporary directory acting as repository root.
        monkeypatch: Pytest monkeypatch fixture.

    """
    _ = (tmp_path / "pyproject.toml").write_text(
        data=(
            "[tool.stationer.builder]\n"
            'project = "src"\nmanifest = "m.toml"\nsurprise = 1\n'
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(path=tmp_path)
    expect(actual=Cli.main(arguments=()), expected=2)
