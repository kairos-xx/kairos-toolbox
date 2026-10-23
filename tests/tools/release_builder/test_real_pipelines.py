# Copyright (c) 2026 Project contributors.
"""Real-environment pipeline tests.

The unit suite fakes every subprocess. These tests run the actual
pipelines where the host can support them:

- Linux inside Docker (``docker info`` must succeed),
- macOS on a real macOS host,
- Windows on a QEMU/SSH worker described by ``RELEASE_BUILDER_WINDOWS_*``
  environment variables.

Each build installs the project *and* its sibling dependency from
staged copies, so they exercise the copy-based install path for real.
"""

from __future__ import annotations

from os import environ
from pathlib import Path
from platform import machine, system
from shutil import which
from subprocess import run
from sys import executable
from typing import Final, final

import pytest

from tests.tools.release_builder.helpers import check, expect, present
from tools.release_builder import (
    BuildResult,
    CommandRunner,
    LinuxConfig,
    LinuxDockerBuilder,
    MacOSBuilder,
    MacOSConfig,
    ProjectConfig,
    WindowsConfig,
    WindowsQemuBuilder,
)


def _docker_ready() -> bool:
    """Return whether a working Docker daemon is reachable.

    Returns:
        ``True`` only when both the CLI and daemon answer.

    """
    docker: str | None = which(cmd="docker")
    if docker is None:
        return False
    return (
        run(args=(docker, "info"), check=False, capture_output=True).returncode
        == 0
    )


def _windows_host() -> str | None:
    """Return the QEMU/SSH Windows worker host, if configured.

    Returns:
        ``RELEASE_BUILDER_WINDOWS_HOST`` or ``None``.

    """
    return environ.get("RELEASE_BUILDER_WINDOWS_HOST")


@final
class Constants:
    """Group module-level constants.

    Attributes:
        docker: Grouped module constant.
        windows_host: Grouped module constant.

    """

    docker: Final[bool] = _docker_ready()
    windows_host: Final[str | None] = _windows_host()


def _real_project(*, root: Path) -> ProjectConfig:
    """Create a genuinely ``pip install``-able project plus sibling dep.

    Args:
        root: Directory that receives ``src/`` and ``dep/``.

    Returns:
        The project configuration pointing at ``src/``.

    """
    dep: Path = root / "dep"
    (dep / "dep_pkg").mkdir(parents=True)
    _ = (dep / "dep_pkg" / "__init__.py").write_text(
        data="VALUE = 41\n",
        encoding="utf-8",
    )
    _ = (dep / "pyproject.toml").write_text(
        data=(
            "[build-system]\nrequires = ['setuptools>=77']\nbuild-backend = "
            "'setuptools.build_meta'\n[project]\nname = 'dep-pkg'\nversion = "
            "'0.1.0'\n[tool.setuptools]\npackages = ['dep_pkg']\n"
        ),
        encoding="utf-8",
    )
    source: Path = root / "src"
    source.mkdir(parents=True)
    _ = (source / "app.py").write_text(
        data="from dep_pkg import VALUE\nprint(f'ok {VALUE}')\n",
        encoding="utf-8",
    )
    _ = (source / "pyproject.toml").write_text(
        data=(
            "[build-system]\nrequires = ['setuptools>=77']\nbuild-backend = "
            "'setuptools.build_meta'\n[project]\nname = 'demo'\nversion = "
            "'1.2.3'\ndependencies = ['dep-pkg']\n[tool.setuptools]\npy"
            "-modules = ['app']\n"
        ),
        encoding="utf-8",
    )
    return ProjectConfig(
        root=source,
        name="demo",
        version="1.2.3",
        entrypoint=source / "app.py",
        identifier="dev.example.demo",
        console=True,
        pyinstaller_args=(),
        pip_args=(),
        local_dependencies=(dep,),
    )


@pytest.mark.skipif(
    not Constants.docker,
    reason="requires a running Docker daemon",
)
def test_linux_pipeline_builds_a_real_deb(tmp_path: Path) -> None:
    """The Linux pipeline installs from copies inside Docker.

    Args:
        tmp_path: Pytest temporary directory.

    """
    architecture: str = (
        "linux/arm64" if machine() in {"arm64", "aarch64"} else "linux/amd64"
    )
    result: BuildResult = LinuxDockerBuilder(runner=CommandRunner()).build(
        project=_real_project(root=tmp_path),
        config=LinuxConfig(
            docker_platform=architecture,
            image="python:3.13-slim-bookworm",
            maintainer="Tests <tests@example.com>",
            description="Real Docker pipeline test",
        ),
        output=tmp_path / "linux",
    )
    check(condition=result.installer.is_file(), reason="the .deb must exist")
    with result.installer.open(mode="rb") as stream:
        magic: bytes = stream.read(8)
    check(
        condition=magic == b"!<arch>\n",
        reason="the artifact must be a real ar archive",
    )
    check(
        condition=result.executable.is_file(),
        reason="the executable must exist",
    )
    present(value=result.installer_sha256)


@pytest.mark.skipif(system() != "Darwin", reason="requires macOS")
def test_macos_pipeline_builds_a_real_pkg(tmp_path: Path) -> None:
    """The macOS pipeline installs from staged copies on the host.

    Args:
        tmp_path: Pytest temporary directory.

    """
    result: BuildResult = MacOSBuilder(runner=CommandRunner()).build(
        project=_real_project(root=tmp_path),
        config=MacOSConfig(python=executable, signing_identity=None),
        output=tmp_path / "macos",
    )
    check(condition=result.installer.is_file(), reason="the .pkg must exist")
    with result.installer.open(mode="rb") as stream:
        magic: bytes = stream.read(4)
    check(
        condition=magic == b"xar!",
        reason="the artifact must be a real xar package",
    )


@pytest.mark.skipif(
    Constants.windows_host is None,
    reason=(
        "requires a QEMU/SSH Windows worker configured through RELEASE"
        "_BUILDER_WINDOWS_HOST"
    ),
)
def test_windows_pipeline_via_qemu_worker(tmp_path: Path) -> None:
    """The Windows pipeline builds on a real Windows VM over SSH.

    Args:
        tmp_path: Pytest temporary directory.

    """
    host: str = present(value=Constants.windows_host)
    result: BuildResult = WindowsQemuBuilder(runner=CommandRunner()).build(
        project=_real_project(root=tmp_path),
        config=WindowsConfig(
            host=host,
            port=int(environ.get("RELEASE_BUILDER_WINDOWS_PORT", "22")),
            user=environ.get("RELEASE_BUILDER_WINDOWS_USER", "builder"),
            identity_file=Path(
                environ.get(
                    "RELEASE_BUILDER_WINDOWS_KEY",
                    str(object=Path.home() / ".ssh" / "id_ed25519"),
                ),
            ),
            python=(environ.get("RELEASE_BUILDER_WINDOWS_PYTHON", "python"),),
            remote_root=environ.get(
                "RELEASE_BUILDER_WINDOWS_ROOT",
                "C:\\release-builder",
            ),
            publisher="Tests",
        ),
        output=tmp_path / "windows",
    )
    expect(actual=result.installer.suffix, expected=".msi")
