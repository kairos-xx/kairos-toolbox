# Copyright (c) 2026 Project contributors.
"""Tests for the self-contained ``.pyz`` output format."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from sys import executable
from typing import Final, Protocol, final
from zipfile import ZipFile

import pytest

from tools.release_builder import BuildError, Cli, ProjectConfig, pyz_builder
from tools.release_builder.pyz_builder import (
    PyzBuilder,
    PyzConfig,
    archive_name,
    platform_tag,
)

from .helpers import check, expect


class Handler(Protocol):
    """Protocol for command-output handlers."""

    def __call__(self, *, arguments: tuple[str, ...]) -> str:
        """Return fabricated output for command arguments.

        Args:
            arguments: Command and arguments.

        Returns:
            Fabricated standard output.

        """
        ...


@final
class RecordingRunner:
    """Record commands and let a handler fabricate their output."""

    def __init__(self, *, handler: Handler) -> None:
        """Create the runner.

        Args:
            handler: Callback returning stdout for each command.

        """
        self._handler: Final[Handler] = handler
        self.commands: list[tuple[str, ...]] = []

    def execute(
        self,
        *,
        arguments: tuple[str, ...],
        cwd: Path | None = None,
        timeout_seconds: int = 1800,
    ) -> CompletedProcess[str]:
        """Record one command.

        Args:
            arguments: Command and arguments.
            cwd: Optional working directory (unused).
            timeout_seconds: Maximum duration (unused).

        Returns:
            A successful result carrying the handler's output.

        """
        _ = (cwd, timeout_seconds)
        self.commands.append(arguments)
        return CompletedProcess(
            args=arguments,
            returncode=0,
            stdout=self._handler(arguments=arguments),
            stderr="",
        )


def _project(*, root: Path) -> ProjectConfig:
    """Create a project with a bootstrap script.

    Args:
        root: Project root.

    Returns:
        The project configuration.

    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.py").write_text(data="print('ok')\n", encoding="utf-8")
    (root / "boot.py").write_text(data="print('boot')\n", encoding="utf-8")
    (root / "build").mkdir()
    (root / "build" / "junk.txt").write_text(data="x", encoding="utf-8")
    return ProjectConfig(
        root=root,
        name="demo",
        version="1.2.3",
        entrypoint=root / "app.py",
        identifier="dev.example.demo",
        console=True,
        pyinstaller_args=(),
        pip_args=("--no-cache-dir",),
    )


def handler(*, arguments: tuple[str, ...]) -> str:
    """Fabricate pip's staging and the smoke run's version output.

    Args:
        arguments: Command and arguments.

    Returns:
        Standard output for the command.

    """
    if "pip" in arguments:
        target: Path = Path(arguments[arguments.index("--target") + 1])
        (target / "demo").mkdir()
        (target / "demo" / "__init__.py").write_text(
            data="",
            encoding="utf-8",
        )
        (target / "bin").mkdir()
        (target / "bin" / "demo").write_text(data="#!", encoding="utf-8")
        return ""
    return "demo 1.2.3\n"


@pytest.mark.parametrize(
    ("system_name", "machine_name", "expected"),
    [
        ("Darwin", "arm64", "macos-arm64"),
        ("Darwin", "x86_64", "macos-x86_64"),
        ("Linux", "x86_64", "linux-x86_64"),
        ("Linux", "aarch64", "linux-aarch64"),
        ("Windows", "AMD64", "windows-amd64"),
        ("Windows", "ARM64", "windows-arm64"),
    ],
)
def test_platform_tag_matrix(
    system_name: str,
    machine_name: str,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Map system and machine names onto asset tags.

    Args:
        system_name: Reported operating system.
        machine_name: Reported architecture.
        expected: Expected tag.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setattr(
        target=pyz_builder,
        name="system",
        value=lambda: system_name,
    )
    monkeypatch.setattr(
        target=pyz_builder,
        name="machine",
        value=lambda: machine_name,
    )
    expect(actual=platform_tag(), expected=expected)


def test_archive_name() -> None:
    """Names follow ``<name>-<version>-<platform>.pyz``."""
    expect(
        actual=archive_name(name="lake-terminal", version="1.0.0", tag="x-y"),
        expected="lake-terminal-1.0.0-x-y.pyz",
    )


def test_config_requires_an_in_project_bootstrap(tmp_path: Path) -> None:
    """The bootstrap must exist inside the project; the name defaults.

    Args:
        tmp_path: Pytest temporary directory.

    """
    root: Path = tmp_path / "proj"
    _ = _project(root=root)
    manifest: Path = tmp_path / "m.toml"
    manifest.write_text(
        data='[pyz]\nbootstrap = "boot.py"\n',
        encoding="utf-8",
    )
    config: PyzConfig = PyzConfig.load(
        manifest=manifest,
        project_root=root,
        default_name="demo",
    )
    expect(actual=config.name, expected="demo")
    expect(actual=config.bootstrap, expected=(root / "boot.py").resolve())
    for body in (
        "[pyz]\n",
        '[pyz]\nbootstrap = "missing.py"\n',
        '[pyz]\nbootstrap = "../outside.py"\n',
    ):
        manifest.write_text(data=body, encoding="utf-8")
        with pytest.raises(expected_exception=BuildError, match="bootstrap"):
            _ = PyzConfig.load(
                manifest=manifest,
                project_root=root,
                default_name="demo",
            )


def test_build_stages_a_copy_and_writes_the_archive(tmp_path: Path) -> None:
    """Install into a staging dir from a copy, bootstrap, zip, smoke-test.

    Args:
        tmp_path: Pytest temporary directory.

    """
    project: ProjectConfig = _project(root=tmp_path / "proj")
    runner: RecordingRunner = RecordingRunner(handler=handler)
    config: PyzConfig = PyzConfig(
        name="demo-app",
        bootstrap=project.root / "boot.py",
    )
    built: Path = PyzBuilder(runner=runner).build(
        project=project,
        config=config,
        output=tmp_path / "out",
        tag="testplat-x1",
    )
    expect(actual=built.name, expected="demo-app-1.2.3-testplat-x1.pyz")
    expect(
        actual=sorted(p.name for p in built.parent.iterdir()),
        expected=[built.name],
    )
    pip: tuple[str, ...] = runner.commands[0]
    expect(actual=pip[:4], expected=(executable, "-m", "pip", "install"))
    for needle in ("--only-binary", ":all:", "--no-cache-dir"):
        check(condition=needle in pip, reason=str(object=pip))
    source: Path = Path(pip[-1])
    check(
        condition=source != project.root,
        reason="pip must build from a throwaway copy",
    )
    check(
        condition=not (project.root / "demo.egg-info").exists(),
        reason="source tree stays clean",
    )
    bundle: ZipFile = ZipFile(file=built)
    with bundle:
        names: set[str] = set(bundle.namelist())
        main_text: str = bundle.read(name="__main__.py").decode()
    check(condition="demo/__init__.py" in names, reason=str(object=names))
    check(condition="bin/demo" not in names, reason="scripts dropped")
    expect(actual=main_text, expected="print('boot')\n")
    smoke: tuple[str, ...] = runner.commands[1]
    check(condition=str(object=built) in smoke, reason=str(object=smoke))
    check(condition=smoke[0] == executable, reason=str(object=smoke))


def test_smoke_failure_is_a_build_error(tmp_path: Path) -> None:
    """An archive that does not report its version is rejected.

    Args:
        tmp_path: Pytest temporary directory.

    """

    def handler(*, arguments: tuple[str, ...]) -> str:
        """Return non-version output for the smoke command.

        Args:
            arguments: Command and arguments being executed.

        Returns:
            Fake command output.

        """
        return "something else\n" if "-c" in arguments else ""

    project: ProjectConfig = _project(root=tmp_path / "proj")
    runner: RecordingRunner = RecordingRunner(handler=handler)
    with pytest.raises(expected_exception=BuildError, match="version"):
        _ = PyzBuilder(runner=runner).build(
            project=project,
            config=PyzConfig(
                name="demo",
                bootstrap=project.root / "boot.py",
            ),
            output=tmp_path / "out",
            tag="t",
        )


def test_cli_pyz_format_skips_native_platforms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--format pyz`` needs no Windows worker and builds one archive.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    root: Path = tmp_path / "proj"
    _ = _project(root=root)
    manifest: Path = tmp_path / "m.toml"
    manifest.write_text(
        data=(
            '[project]\nname = "demo"\nversion = "1.2.3"\n'
            'entrypoint = "app.py"\n'
            '[pyz]\nname = "demo-app"\nbootstrap = "boot.py"\n'
        ),
        encoding="utf-8",
    )
    seen: list[tuple[str, Path]] = []

    def fake_build(
        self: PyzBuilder,
        *,
        project: ProjectConfig,
        config: PyzConfig,
        output: Path,
        tag: str | None = None,
    ) -> Path:
        """Record the build arguments and emit a stub archive.

        Args:
            self: Builder instance being patched.
            project: Project configuration.
            config: Archive configuration.
            output: Output directory.
            tag: Optional platform tag.

        Returns:
            Path to the stub archive.

        """
        _ = (self, tag)
        seen.append((config.name, output))
        return output / f"{project.name}.pyz"

    monkeypatch.setattr(
        target=PyzBuilder,
        name="build",
        value=fake_build,
    )
    status: int = Cli.main(
        arguments=(
            "--project",
            str(object=root),
            "--manifest",
            str(object=manifest),
            "--output",
            str(object=tmp_path / "dist"),
            "--format",
            "pyz",
        ),
    )
    expect(actual=status, expected=0)
    expect(
        actual=seen,
        expected=[("demo-app", (tmp_path / "dist").resolve())],
    )
