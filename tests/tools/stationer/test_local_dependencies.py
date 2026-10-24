# Copyright (c) 2026 Project contributors.
"""Tests for installing local sibling projects before the main project."""

from __future__ import annotations

from dataclasses import replace
from io import BytesIO
from pathlib import Path
from sys import executable
from zipfile import ZipFile

import pytest

from tools.stationer import (
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
from tools.stationer.builder import (
    BuildConfiguration,
    SourceArchive,
)
from tools.stationer.pyz_builder import PyzBuilder, PyzConfig

from .fake_sessions import FakeSession, Hosts, Opener
from .helpers import check, expect
from .test_builder import (
    macos_handler,
    project,
)
from .test_pyz_builder import handler


def _dependency(*, root: Path, name: str = "bowman") -> Path:
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
            'local_dependencies = ["../bowman"]\n'
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
        '["../bowman", "../bowman"]',
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
    runner: Hosts = Hosts(handler=macos_handler)
    _ = MacOSBuilder(open_host=runner).build(
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
    expect(actual=names, expected=["bowman", "source"])
    check(
        condition=not (project.root / "build").exists()
        and not list(project.root.glob(pattern="*.egg-info")),
        reason="source tree stays clean",
    )


def test_linux_copies_and_installs_dependencies(tmp_path: Path) -> None:
    """Each dependency is copied into the container and installed first.

    Args:
        tmp_path: Pytest temporary directory.

    """
    project: ProjectConfig = _with_dependency(tmp_path=tmp_path)
    opener: Opener = Opener()
    _ = LinuxDockerBuilder(open_session=opener).build(
        project=project,
        config=LinuxConfig(
            docker_platform="linux/arm64",
            image="python:3.13-slim-bookworm",
            maintainer="Example",
            description="Demo",
        ),
        output=tmp_path / "out",
    )
    session: FakeSession = opener.created[0]
    check(
        condition="/work/deps/bowman" in session.trees,
        reason=str(object=sorted(session.trees)),
    )
    check(
        condition="pkg/__init__.py" in session.trees["/work/deps/bowman"],
        reason="the dependency's files are copied",
    )
    check(
        condition=not any(
            "__pycache__" in name
            for name in session.trees["/work/deps/bowman"]
        ),
        reason="staged copies exclude caches",
    )
    check(
        condition=("mkdir", "-p", "/work/deps", "/work/support", "/out")
        in session.commands,
        reason="the dependency directory exists before the copy",
    )
    script: str = session.commands[-1][3]
    check(
        condition="/work/deps/bowman /work/src" in script,
        reason=script,
    )
    check(
        condition="cp -a /deps" not in script and ":ro" not in script,
        reason="nothing is bind-mounted any more",
    )
    check(
        condition=not (project.root / "build").exists(),
        reason="the source tree stays clean",
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
    opener: Opener = Opener()
    _ = WindowsQemuBuilder(open_session=opener).build(
        project=project,
        config=WindowsConfig(
            host="192.0.2.9",
            port=2222,
            user="builder",
            identity_file=identity,
            python=("py", "-3.13"),
            remote_root="C:/stationer",
            publisher="Example",
        ),
        output=tmp_path / "out",
    )
    uploads: dict[str, bytes] = opener.created[0].files
    archive: bytes = next(
        data for name, data in uploads.items() if name.endswith("/source.zip")
    )
    with ZipFile(file=BytesIO(initial_bytes=archive)) as bundle:
        names: set[str] = set(bundle.namelist())
    check(
        condition=".local-deps/bowman/pyproject.toml" in names,
        reason=str(object=names),
    )
    check(
        condition=not any("__pycache__" in name for name in names),
        reason=str(object=names),
    )
    script: str = next(
        data.decode(encoding="utf-8")
        for name, data in uploads.items()
        if name.endswith("/build.ps1")
    )
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
    runner: Hosts = Hosts(handler=handler)
    _ = PyzBuilder(open_host=runner).build(
        project=project,
        config=PyzConfig(name="demo", bootstrap=project.root / "boot.py"),
        output=tmp_path / "out",
    )
    pip: tuple[str, ...] = runner.commands[1]
    original: str = str(object=project.local_dependencies[0])
    check(condition=original not in pip, reason="installs from a copy")
    paths: list[str] = [a for a in pip if a.endswith(("/bowman", "/source"))]
    expect(actual=[Path(p).name for p in paths], expected=["bowman", "source"])
