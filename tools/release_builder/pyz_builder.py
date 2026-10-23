# Copyright (c) 2026 Project contributors.
"""Build a self-contained, platform-specific ``.pyz`` archive."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from platform import machine, system
from shutil import copy2, rmtree
from sys import executable
from tempfile import TemporaryDirectory
from tomllib import load
from typing import TYPE_CHECKING, BinaryIO, Final, cast
from zipapp import create_archive

from tools.release_builder.release_builder import BuildError, SourceStaging

if TYPE_CHECKING:
    from subprocess import CompletedProcess

    from tools.release_builder.release_builder import ProjectConfig, Runner


class PyzConstants:
    """Group ``.pyz`` build constants.

    Attributes:
        interpreter: Shebang written into the archive.
        smoke: Program that runs ``--version`` with an isolated home.

    """

    interpreter: Final[str] = "/usr/bin/env python3"
    smoke: Final[str] = (
        "import os, runpy, sys, tempfile\n"
        "with tempfile.TemporaryDirectory() as home:\n"
        "    os.environ['HOME'] = os.environ['USERPROFILE'] = home\n"
        "    archive = sys.argv[1]\n"
        "    sys.argv = [archive, '--version']\n"
        "    runpy.run_path(archive, run_name='__main__')\n"
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class PyzConfig:
    """Hold the ``[pyz]`` manifest table.

    Attributes:
        name: Archive file name prefix.
        bootstrap: Script shipped as the archive's ``__main__.py``.

    """

    name: str
    bootstrap: Path

    @classmethod
    def load(
        cls,
        *,
        manifest: Path,
        project_root: Path,
        default_name: str,
    ) -> PyzConfig:
        """Read and validate the ``[pyz]`` table.

        Args:
            manifest: TOML manifest path.
            project_root: Python project root.
            default_name: Name used when the table omits one.

        Returns:
            The validated configuration.

        Raises:
            BuildError: If the bootstrap is missing or outside the project.

        """
        stream: BinaryIO
        with manifest.open(mode="rb") as stream:
            document: dict[str, object] = load(stream)
        table: dict[str, str] = cast(
            typ="dict[str, str]",
            val=document.get("pyz", {}),
        )
        name: str = table.get("name", default_name)
        text: str = table.get("bootstrap", "")
        root: Path = project_root.resolve()
        bootstrap: Path = (root / text).resolve()
        if (
            not text
            or not bootstrap.is_relative_to(root)
            or not bootstrap.is_file()
        ):
            msg: str = "pyz.bootstrap must be a file inside the project root."
            raise BuildError(msg)
        return cls(name=name, bootstrap=bootstrap)


def platform_tag() -> str:
    """Return the release-asset platform tag for this machine.

    Returns:
        A tag such as ``macos-arm64`` or ``linux-x86_64``.

    """
    os_name: str = {
        "Darwin": "macos",
        "Linux": "linux",
        "Windows": "windows",
    }.get(system(), system().lower())
    arch: str = machine().lower()
    if arch in {"arm64", "aarch64"}:
        arch = "arm64" if os_name in {"macos", "windows"} else "aarch64"
    elif arch in {"x86_64", "amd64"}:
        arch = "amd64" if os_name == "windows" else "x86_64"
    return f"{os_name}-{arch}"


def archive_name(*, name: str, version: str, tag: str) -> str:
    """Return the archive file name.

    Args:
        name: Archive name prefix.
        version: Project version.
        tag: Platform tag.

    Returns:
        ``<name>-<version>-<tag>.pyz``.

    """
    return f"{name}-{version}-{tag}.pyz"


class PyzBuilder:
    """Build one ``.pyz`` for the current platform.

    Compiled dependencies make the archive platform-specific, so one is
    built per target machine rather than cross-compiled.
    """

    def __init__(self, *, runner: Runner) -> None:
        """Initialize the builder.

        Args:
            runner: External command executor.

        """
        self._runner: Final[Runner] = runner

    def build(
        self,
        *,
        project: ProjectConfig,
        config: PyzConfig,
        output: Path,
        tag: str | None = None,
    ) -> Path:
        """Stage the project and wheels, zip them, and smoke-test the result.

        Args:
            project: Project configuration.
            config: ``[pyz]`` configuration.
            output: Directory that receives the archive.
            tag: Platform tag; detected when omitted.

        Returns:
            The archive path.

        Raises:
            BuildError: If staging, archiving or the smoke test fails.

        """
        output.mkdir(parents=True, exist_ok=True)
        target: Path = output / archive_name(
            name=config.name,
            version=project.version,
            tag=tag or platform_tag(),
        )
        with TemporaryDirectory() as temporary:
            stage: Path = Path(temporary) / "stage"
            stage.mkdir()
            dependencies, source = SourceStaging.stage(
                project=project,
                directory=Path(temporary),
            )
            _ = self._runner.execute(
                arguments=(
                    executable,
                    "-m",
                    "pip",
                    "install",
                    "--quiet",
                    "--target",
                    str(object=stage),
                    "--only-binary",
                    ":all:",
                    *project.pip_args,
                    *(str(object=path) for path in dependencies),
                    str(object=source),
                ),
            )
            rmtree(path=stage / "bin", ignore_errors=True)
            for cache in stage.rglob(pattern="__pycache__"):
                rmtree(path=cache, ignore_errors=True)
            _ = copy2(src=config.bootstrap, dst=stage / "__main__.py")
            create_archive(
                source=stage,
                target=target,
                interpreter=PyzConstants.interpreter,
                compressed=True,
            )
        probe: CompletedProcess[str] = self._runner.execute(
            arguments=(
                executable,
                "-c",
                PyzConstants.smoke,
                str(object=target),
            ),
        )
        if project.version not in probe.stdout:
            msg: str = (
                f"smoke test failed: expected version {project.version} "
                f"in {probe.stdout!r}"
            )
            raise BuildError(msg)
        return target
