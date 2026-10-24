# Copyright (c) 2026 Project contributors.
"""Build a self-contained, platform-specific ``.pyz`` archive."""

from __future__ import annotations

from dataclasses import dataclass
from os import name as os_name
from pathlib import Path
from platform import libc_ver, machine, system
from shutil import copy2, rmtree
from sys import executable, version_info
from tempfile import TemporaryDirectory
from tomllib import load
from typing import TYPE_CHECKING, BinaryIO, Final, cast
from zipapp import create_archive

from tools.conjure import QemuRunnerError
from tools.stationer.builder import (
    BuildError,
    SourceStaging,
    job_failure,
)
from tools.stationer.naming import asset_name
from tools.stationer.sessions import HostConstants, open_host

if TYPE_CHECKING:
    from tools.conjure import CommandResult
    from tools.stationer.builder import ProjectConfig
    from tools.stationer.sessions import HostOpener, JobSession


class PyzConstants:
    """Group ``.pyz`` build constants.

    Attributes:
        interpreter: Shebang written into the archive.
        smoke: Program that runs ``--version`` with an isolated home.
        wheel_platforms: pip ``--platform`` values per
            ``(platform, architecture, abi)`` cell, used to resolve target
            wheels when cross-building an archive for another machine.

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
    wheel_platforms: Final[dict[tuple[str, str, str], tuple[str, ...]]] = {
        ("windows", "amd64", "native"): ("win_amd64",),
        ("windows", "arm64", "native"): ("win_arm64",),
        ("macos", "amd64", "native"): ("macosx_13_0_x86_64",),
        ("macos", "arm64", "native"): ("macosx_13_0_arm64",),
        ("linux", "amd64", "glibc"): (
            "manylinux_2_17_x86_64",
            "manylinux2014_x86_64",
        ),
        ("linux", "amd64", "musl"): (
            "musllinux_1_2_x86_64",
            "musllinux_1_1_x86_64",
        ),
        ("linux", "arm64", "glibc"): (
            "manylinux_2_17_aarch64",
            "manylinux2014_aarch64",
        ),
        ("linux", "arm64", "musl"): (
            "musllinux_1_2_aarch64",
            "musllinux_1_1_aarch64",
        ),
    }


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
    return asset_name(project=name, version=version, tag=tag)


@dataclass(frozen=True, slots=True, kw_only=True)
class PyzTarget:
    """Describe one platform/architecture/ABI cell an archive serves.

    Attributes:
        platform_name: ``windows``, ``macos`` or ``linux``.
        architecture: ``arm64`` or ``amd64``.
        abi: ``native``, or the C library family on Linux.

    """

    platform_name: str
    architecture: str
    abi: str = "native"

    @classmethod
    def cells(
        cls,
        *,
        platforms: tuple[str, ...] = (),
        architecture: str | None = None,
    ) -> tuple[PyzTarget, ...]:
        """List the matrix cells selected platforms and CPU cover.

        Args:
            platforms: Platforms to cover; empty means all three.
            architecture: ``None``/``all`` for both CPUs, ``native`` for
                this machine's, or one explicit architecture.

        Returns:
            One target per platform/architecture/ABI combination.

        """
        names: tuple[str, ...] = platforms or ("macos", "linux", "windows")
        if architecture in (None, "all"):
            architectures: tuple[str, ...] = ("arm64", "amd64")
        elif architecture == "native":
            architectures = (
                "arm64"
                if machine().lower() in {"arm64", "aarch64"}
                else "amd64",
            )
        else:
            architectures = (architecture,)
        cells: list[PyzTarget] = []
        for platform_name in names:
            abis: tuple[str, ...] = (
                ("glibc", "musl") if platform_name == "linux" else ("native",)
            )
            cells.extend(
                cls(
                    platform_name=platform_name,
                    architecture=arch,
                    abi=abi,
                )
                for arch in architectures
                for abi in abis
            )
        return tuple(cells)

    @property
    def tag(self) -> str:
        """Return the release-asset platform tag.

        Returns:
            ``<os>-<arch>``, with the ABI appended on Linux.

        """
        tag: str = f"{self.platform_name}-{self.architecture}"
        if self.platform_name == "linux":
            tag = f"{tag}-{self.abi}"
        return tag

    @property
    def wheel_platforms(self) -> tuple[str, ...]:
        """Return the pip ``--platform`` values that select this cell.

        Returns:
            Wheel platform tags, most preferred first.

        Raises:
            BuildError: If the cell has no known wheel platform.

        """
        key: tuple[str, str, str] = (
            self.platform_name,
            self.architecture,
            self.abi,
        )
        try:
            return PyzConstants.wheel_platforms[key]
        except KeyError:
            msg: str = f"No wheel platform known for {self.tag}."
            raise BuildError(msg) from None

    @property
    def host(self) -> bool:
        """Return whether this cell matches the machine building it.

        Returns:
            ``True`` when the archive can be smoke-tested locally.

        """
        host_os: str = {
            "Darwin": "macos",
            "Linux": "linux",
            "Windows": "windows",
        }.get(system(), system().lower())
        host_cpu: str = machine().lower()
        host_cpu = "arm64" if host_cpu in {"arm64", "aarch64"} else "amd64"
        if (self.platform_name, self.architecture) != (host_os, host_cpu):
            return False
        if self.platform_name != "linux":
            return True
        musl: bool = Path("/etc/alpine-release").exists() or (
            "musl" in (libc_ver()[0] or "")
        )
        return self.abi == ("musl" if musl else "glibc")


class PyzBuilder:
    """Build one ``.pyz`` for the current platform.

    Compiled dependencies make the archive platform-specific, so one is
    built per target machine rather than cross-compiled.
    """

    def __init__(self, *, open_host: HostOpener = open_host) -> None:
        """Initialize the builder.

        Args:
            open_host: Creates the host session commands run in.

        """
        self._open_host: Final[HostOpener] = open_host

    def build(
        self,
        *,
        project: ProjectConfig,
        config: PyzConfig,
        output: Path,
        target: PyzTarget | None = None,
    ) -> Path:
        """Stage the project and wheels, zip them, and smoke-test the result.

        A ``target`` naming another platform builds a cross archive: the
        project's pure wheels are built locally, then pip resolves the
        remaining dependencies for the target's wheel platform. Foreign
        archives cannot be smoke-tested on this machine.

        Args:
            project: Project configuration.
            config: ``[pyz]`` configuration.
            output: Directory that receives the archive.
            target: Cell to build for; the host's own cell when omitted.

        Returns:
            The archive path.

        Raises:
            BuildError: If staging, archiving or the smoke test fails.
            job_failure: When a host command fails.

        """
        output.mkdir(parents=True, exist_ok=True)
        tag: str = target.tag if target is not None else platform_tag()
        cross: bool = target is not None and not target.host
        artifact: Path = output / archive_name(
            name=config.name,
            version=project.version,
            tag=tag,
        )
        host: JobSession = self._open_host()
        try:
            host.start()
            with TemporaryDirectory() as temporary:
                stage: Path = Path(temporary) / "stage"
                stage.mkdir()
                venv: Path = Path(temporary) / "venv"
                _ = host.run(
                    command=(executable, "-I", "-m", "venv", str(object=venv)),
                    check=True,
                    timeout_seconds=HostConstants.timeout_seconds,
                )
                if cross:
                    self._install_cross(
                        host=host,
                        temporary=Path(temporary),
                        project=project,
                        target=cast(typ="PyzTarget", val=target),
                    )
                else:
                    dependencies, source = SourceStaging.stage(
                        project=project,
                        directory=Path(temporary),
                    )
                    pip: tuple[str, ...] = (
                        str(
                            object=venv
                            / (
                                "Scripts/python.exe"
                                if os_name == "nt"
                                else "bin/python"
                            ),
                        ),
                        "-m",
                        "pip",
                    )
                    _ = host.run(
                        command=(
                            *pip,
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
                        check=True,
                        timeout_seconds=HostConstants.timeout_seconds,
                    )
                rmtree(path=stage / "bin", ignore_errors=True)
                for cache in stage.rglob(pattern="__pycache__"):
                    rmtree(path=cache, ignore_errors=True)
                _ = copy2(src=config.bootstrap, dst=stage / "__main__.py")
                create_archive(
                    source=stage,
                    target=artifact,
                    interpreter=PyzConstants.interpreter,
                    compressed=True,
                )
            if cross:
                return artifact
            probe: CommandResult = host.run(
                command=(
                    executable,
                    "-c",
                    PyzConstants.smoke,
                    str(object=artifact),
                ),
                check=True,
                timeout_seconds=HostConstants.timeout_seconds,
            )
        except QemuRunnerError as error:
            raise job_failure(error=error) from error
        finally:
            host.close()
        if project.version not in probe.stdout:
            msg: str = (
                f"smoke test failed: expected version {project.version} "
                f"in {probe.stdout!r}"
            )
            raise BuildError(msg)
        return artifact

    @staticmethod
    def _install_cross(
        *,
        host: JobSession,
        temporary: Path,
        project: ProjectConfig,
        target: PyzTarget,
    ) -> None:
        """Install target-platform wheels into the staging directory.

        pip cannot resolve foreign platforms from source directories, so
        the pure project wheels are built first and installed alongside
        their resolved dependencies.

        Args:
            host: Host session commands run in.
            temporary: Build scratch root holding ``venv/`` and ``stage/``.
            project: Project configuration.
            target: The foreign cell.

        Raises:
            job_failure: When a host command fails.

        """
        stage: Path = temporary / "stage"
        wheels: Path = temporary / "wheels"
        wheels.mkdir()
        dependencies, source = SourceStaging.stage(
            project=project,
            directory=temporary / "src",
        )
        pip: tuple[str, ...] = (
            str(
                object=temporary
                / "venv"
                / ("Scripts/python.exe" if os_name == "nt" else "bin/python"),
            ),
            "-m",
            "pip",
        )
        _ = host.run(
            command=(
                *pip,
                "wheel",
                "--no-deps",
                "--quiet",
                "--wheel-dir",
                str(object=wheels),
                *(str(object=path) for path in dependencies),
                str(object=source),
            ),
            check=True,
            timeout_seconds=HostConstants.timeout_seconds,
        )
        major, minor, _ = version_info[:3]
        _ = host.run(
            command=(
                *pip,
                "install",
                "--quiet",
                "--target",
                str(object=stage),
                "--only-binary",
                ":all:",
                *(
                    flag
                    for platform_tag_value in target.wheel_platforms
                    for flag in ("--platform", platform_tag_value)
                ),
                "--python-version",
                f"{major}.{minor}",
                "--implementation",
                "cp",
                "--abi",
                f"cp{major}{minor}",
                "--abi",
                "abi3",
                "--abi",
                "none",
                *project.pip_args,
                *(
                    str(object=path)
                    for path in sorted(wheels.glob(pattern="*.whl"))
                ),
            ),
            check=True,
            timeout_seconds=HostConstants.timeout_seconds,
        )
