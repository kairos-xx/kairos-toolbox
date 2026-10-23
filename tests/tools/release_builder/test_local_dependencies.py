# Copyright (c) 2026 Project contributors.
"""Tests for installing local sibling projects before the main project."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from sys import executable
from zipfile import ZipFile

import pytest

from tools.release_builder import (
    BuildError,
    LinuxConfig,
    LinuxDockerBuilder,
    MacOSBuilder,
    MacOSConfig,
    ManifestLoader,
    ProjectConfig,
    WindowsConfig,
    WindowsQemuBuilder,
)
from tools.release_builder.pyz_builder import PyzBuilder, PyzConfig
from tools.release_builder.release_builder import (
    BuildConfiguration,
    SourceArchive,
)

from .helpers import check, expect
from .test_pyz_builder import RecordingRunner, handler
from .test_release_builder import (
    FakeRunner,
    linux_handler,
    macos_handler,
    project,
    windows_handler,
)


def _dependency(*, root: Path, name: str = "arrow") -> Path:
    """Create a sibling project that the main project depends on.

    Args:
        root: Directory that receives the dependency.
        name: Dependency directory name.

    Returns:
        The dependency directory.

    """
    dependency: Path = root / name
    (dependency / "pkg").mkdir(parents=True)
    (dependency / "pyproject.toml").write_text(
        data="[project]\nname = 'dep'\n",
        encoding="utf-8",
    )
    (dependency / "pkg" / "__init__.py").write_text(data="", encoding="utf-8")
    (dependency / "pkg" / "__pycache__").mkdir()
    (dependency / "pkg" / "__pycache__" / "x.pyc").write_text(
        data="byte",
        encoding="utf-8",
    )
    return dependency


def _with_dependency(*, tmp_path: Path) -> ProjectConfig:
    """Return a project that depends on a sibling directory.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        The project configuration.

    """
    dependency: Path = _dependency(root=tmp_path)
    _project: ProjectConfig = project(root=tmp_path / "src")
    return replace(_project, local_dependencies=(dependency,))


def test_manifest_resolves_local_dependencies(tmp_path: Path) -> None:
    """``local_dependencies`` resolve relative to the project root.

    Args:
        tmp_path: Pytest temporary directory.

    """
    dependency: Path = _dependency(root=tmp_path)
    root: Path = tmp_path / "proj"
    root.mkdir()
    (root / "app.py").write_text(data="print()\n", encoding="utf-8")
    manifest: Path = tmp_path / "m.toml"
    manifest.write_text(
        data=(
            '[project]\nname = "demo"\nversion = "1.0.0"\n'
            'entrypoint = "app.py"\n'
            'local_dependencies = ["../arrow"]\n'
        ),
        encoding="utf-8",
    )
    configuration: BuildConfiguration = ManifestLoader.load(
        manifest=manifest,
        project_root=root,
        platforms=("linux",),
    )
    expect(
        actual=configuration.project.local_dependencies,
        expected=(dependency.resolve(),),
    )


@pytest.mark.parametrize(
    "entries",
    [
        '["../missing"]',
        '["../arrow", "../arrow"]',
    ],
)
def test_manifest_rejects_bad_local_dependencies(
    tmp_path: Path,
    entries: str,
) -> None:
    """Missing directories and duplicate names fail the build early.

    Args:
        tmp_path: Pytest temporary directory.
        entries: TOML array for ``local_dependencies``.

    """
    _ = _dependency(root=tmp_path)
    root: Path = tmp_path / "proj"
    root.mkdir()
    (root / "app.py").write_text(data="print()\n", encoding="utf-8")
    manifest: Path = tmp_path / "m.toml"
    manifest.write_text(
        data=(
            '[project]\nname = "demo"\nversion = "1.0.0"\n'
            f'entrypoint = "app.py"\nlocal_dependencies = {entries}\n'
        ),
        encoding="utf-8",
    )
    with pytest.raises(expected_exception=BuildError, match="dependenc"):
        _ = ManifestLoader.load(
            manifest=manifest,
            project_root=root,
            platforms=("linux",),
        )


def test_macos_installs_copies_of_dependencies_and_project(
    tmp_path: Path,
) -> None:
    """The venv installs throwaway copies, dependencies first.

    Installing from the source tree would leave ``build/`` and
    ``*.egg-info`` directories in the user's checkout.

    Args:
        tmp_path: Pytest temporary directory.

    """
    project: ProjectConfig = _with_dependency(tmp_path=tmp_path)
    runner: FakeRunner = FakeRunner(handler=macos_handler)
    _ = MacOSBuilder(runner=runner).build(
        project=project,
        config=MacOSConfig(python=executable, signing_identity=None),
        output=tmp_path / "out",
    )
    pip: tuple[str, ...] = next(
        c for c in runner.commands if "pyinstaller" in c and "pip" in c
    )
    check(
        condition=str(object=project.root) not in pip
        and str(object=project.local_dependencies[0]) not in pip,
        reason=f"must not install from the source tree: {pip}",
    )
    names: list[str] = [Path(a).name for a in pip[-2:]]
    expect(actual=names, expected=["arrow", "source"])
    check(
        condition=not (project.root / "build").exists()
        and not list(project.root.glob(pattern="*.egg-info")),
        reason="source tree stays clean",
    )


def test_linux_mounts_and_installs_dependencies(tmp_path: Path) -> None:
    """Docker mounts each dependency and installs it ahead of the project.

    Args:
        tmp_path: Pytest temporary directory.

    """
    project: ProjectConfig = _with_dependency(tmp_path=tmp_path)
    runner: FakeRunner = FakeRunner(handler=linux_handler(version="1.2.3"))
    _ = LinuxDockerBuilder(runner=runner).build(
        project=project,
        config=LinuxConfig(
            docker_platform="linux/arm64",
            image="python:3.13-slim-bookworm",
            maintainer="Example",
            description="Demo",
        ),
        output=tmp_path / "out",
    )
    docker: tuple[str, ...] = runner.commands[0]
    dependency: Path = project.local_dependencies[0]
    check(
        condition=f"{dependency}:/deps/arrow:ro" in docker,
        reason=str(object=docker),
    )
    script: str = docker[-1]
    check(
        condition="/work/deps/arrow /work/src" in script,
        reason=script,
    )
    check(
        condition=script.index("cp -a /deps/arrow")
        < script.index("pip install"),
        reason="mounts are read-only, so they are copied before pip builds",
    )


def test_windows_ships_dependencies_in_the_source_archive(
    tmp_path: Path,
) -> None:
    """The remote worker receives dependencies and installs them first.

    Args:
        tmp_path: Pytest temporary directory.

    """
    project: ProjectConfig = _with_dependency(tmp_path=tmp_path)
    identity: Path = tmp_path / "id_ed25519"
    identity.write_text(data="key\n", encoding="utf-8")
    scripts: list[str] = []
    archives: list[set[str]] = []

    def handler(arguments: tuple[str, ...]) -> None:
        """Capture the uploaded PowerShell script and source archive.

        Args:
            arguments: Command and arguments under test.

        """
        if arguments[0] == "scp" and arguments[-1].endswith("/build.ps1"):
            scripts.append(Path(arguments[-2]).read_text(encoding="utf-8"))
        if arguments[0] == "scp" and arguments[-1].endswith("/source.zip"):
            with ZipFile(file=arguments[-2]) as bundle:
                archives.append(set(bundle.namelist()))
        windows_handler(arguments=arguments)

    _ = WindowsQemuBuilder(runner=FakeRunner(handler=handler)).build(
        project=project,
        config=WindowsConfig(
            host="192.0.2.9",
            port=2222,
            user="builder",
            identity_file=identity,
            python=("py", "-3.13"),
            remote_root="C:/release-builder",
            publisher="Example",
        ),
        output=tmp_path / "out",
    )
    names: set[str] = archives[0]
    check(
        condition=".local-deps/arrow/pyproject.toml" in names,
        reason=str(object=names),
    )
    check(
        condition=not any("__pycache__" in name for name in names),
        reason=str(object=names),
    )
    script: str = scripts[0]
    install: str = next(
        line for line in script.splitlines() if "pip install" in line
    )
    check(
        condition=install.index(".local-deps") < install.rindex(" ."),
        reason=install,
    )


def test_source_archive_without_dependencies_is_unchanged(
    tmp_path: Path,
) -> None:
    """Projects with no local dependencies add nothing extra.

    Args:
        tmp_path: Pytest temporary directory.

    """
    archive: Path = tmp_path / "s.zip"
    SourceArchive.create(
        project=project(root=tmp_path / "src"),
        destination=archive,
    )
    with ZipFile(file=archive) as bundle:
        check(
            condition=not any(
                n.startswith(".local-deps") for n in bundle.namelist()
            ),
            reason=str(object=bundle.namelist()),
        )


def test_pyz_installs_copied_dependencies_before_the_project(
    tmp_path: Path,
) -> None:
    """Pip gets throwaway copies of each dependency, then the project copy.

    Args:
        tmp_path: Pytest temporary directory.

    """
    project: ProjectConfig = _with_dependency(tmp_path=tmp_path)
    (project.root / "boot.py").write_text(data="print()\n", encoding="utf-8")
    runner: RecordingRunner = RecordingRunner(handler=handler)
    _ = PyzBuilder(runner=runner).build(
        project=project,
        config=PyzConfig(name="demo", bootstrap=project.root / "boot.py"),
        output=tmp_path / "out",
        tag="t",
    )
    pip: tuple[str, ...] = runner.commands[0]
    original: str = str(object=project.local_dependencies[0])
    check(condition=original not in pip, reason="installs from a copy")
    paths: list[str] = [a for a in pip if a.endswith(("/arrow", "/source"))]
    expect(actual=[Path(p).name for p in paths], expected=["arrow", "source"])
