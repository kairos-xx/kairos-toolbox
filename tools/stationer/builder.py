"""Build native Python installers for macOS, Linux, and Windows ARM64."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from contextlib import suppress
from dataclasses import dataclass, replace
from hashlib import sha256
from importlib import import_module
from pathlib import Path, PurePosixPath
from platform import machine, system
from shlex import quote
from shutil import copy2, copytree, ignore_patterns, rmtree
from sys import argv as sys_argv
from sys import executable
from sys import stderr as sys_stderr
from sys import stdout as sys_stdout
from tempfile import TemporaryDirectory
from time import monotonic
from tomllib import load
from typing import (
    TYPE_CHECKING,
    BinaryIO,
    Final,
    Literal,
    Protocol,
    TypedDict,
    cast,
    final,
)
from zipfile import ZIP_DEFLATED, ZipFile

from tools.conjure import (
    QemuRunnerError,
    RemoteCommandError,
    SettingsLoader,
)
from tools.stationer.build_report import (
    BuildReport,
    BuildSource,
    ReportConstants,
)
from tools.stationer.progress import (
    ProgressDisplay,
    ProgressSink,
    ProgressTracker,
    StepMarker,
)
from tools.stationer.remote_exposure import ExposureConstants
from tools.stationer.sessions import (
    ContainerOpener,
    HostConstants,
    HostOpener,
    JobSession,
    SshOpener,
    StreamingSession,
    open_container,
    open_host,
    open_ssh,
)
from tools.stationer.versioning import read_version

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from tools.conjure import CommandResult as JobResult
    from tools.conjure import Settings as JobSettings
    from tools.stationer.installer_support import PyzPayload
    from tools.stationer.pyz_builder import PyzBuilder, PyzConfig
    from tools.stationer.remote_builder import (
        RemoteBuilder,
        RemoteSettings,
    )
type PlatformName = Literal["macos", "linux", "windows"]


class DigestProtocol(Protocol):
    """Describe the hash operations consumed by artifact hashing."""

    def update(self, data: bytes, /) -> None:
        """Add bytes to the digest.

        Args:
            data: Bytes to hash.

        """

    def hexdigest(self) -> str:
        """Return the lowercase hexadecimal digest.

        Returns:
            Hexadecimal digest text.

        """
        ...


class ProjectTable(TypedDict, total=False):
    """Describe accepted project configuration keys.

    Attributes:
        name: Project name.
        version: Explicit semantic version.
        version_file: Project file carrying a ``version`` assignment.
        entrypoint: Python entrypoint relative to the project root.
        identifier: Reverse-DNS bundle identifier.
        console: Whether the frozen executable keeps a console.
        pyinstaller_args: Extra PyInstaller arguments.
        pip_args: Extra pip install arguments.
        local_dependencies: Sibling project directories installed first.

    """

    name: str
    version: str
    version_file: str
    entrypoint: str
    identifier: str
    console: bool
    pyinstaller_args: list[str]
    pip_args: list[str]
    local_dependencies: list[str]


class GuestTable(TypedDict, total=False):
    """Describe an SSH-reachable POSIX QEMU guest's keys.

    Attributes:
        host: SSH host of the guest.
        port: SSH port of the guest.
        user: SSH user on the guest.
        identity_file: SSH private key path.
        python: Python launcher command on the guest.
        remote_root: Remote build directory root.

    """

    host: str
    port: int
    user: str
    identity_file: str
    python: list[str]
    remote_root: str


class LinuxTable(TypedDict, total=False):
    """Describe accepted Linux configuration keys.

    Attributes:
        docker_platform: Docker ``--platform`` value.
        docker_platforms: Every Docker ``--platform`` value to build.
        image: Docker base image for glibc builds.
        musl_image: Docker base image for musl builds, such as
            ``python:3.13-alpine``; musl cells are skipped when omitted.
        maintainer: Debian package maintainer.
        description: Debian package description.
        guest: ``[linux.guest]`` table; when present Linux builds run on
            the SSH-reachable guest it describes instead of in Docker.

    """

    docker_platform: str
    docker_platforms: list[str]
    image: str
    musl_image: str
    maintainer: str
    description: str
    guest: GuestTable


class MacOSTable(TypedDict, total=False):
    """Describe accepted macOS configuration keys.

    Attributes:
        python: Python interpreter used for the build environment.
        python_amd64: x86_64 Python for amd64 builds on Apple Silicon.
        signing_identity: Optional Installer signing identity.
        minimum_macos: Override of the minimum macOS version recorded.
        guest: ``[macos.guest]`` table; when present macOS builds run on
            the SSH-reachable guest it describes instead of natively.

    """

    python: str
    python_amd64: str
    signing_identity: str
    minimum_macos: str
    guest: GuestTable


class WindowsTable(TypedDict, total=False):
    """Describe accepted Windows configuration keys.

    Attributes:
        host: SSH host of the Windows ARM64 guest.
        port: SSH port of the guest.
        user: SSH user on the guest.
        identity_file: SSH private key path.
        python: Python launcher command on the guest.
        remote_root: Remote build directory root.
        publisher: Installer publisher name.

    """

    host: str
    port: int
    user: str
    identity_file: str
    python: list[str]
    remote_root: str
    publisher: str


class ManifestDocument(TypedDict, total=False):
    """Describe the complete TOML manifest shape.

    Attributes:
        project: ``[project]`` table.
        linux: ``[linux]`` table.
        macos: ``[macos]`` table.
        windows: ``[windows]`` table.

    """

    project: ProjectTable
    linux: LinuxTable
    macos: MacOSTable
    windows: WindowsTable


class CliNamespace(Namespace):
    """Provide precise types for parsed command-line arguments.

    Attributes:
        project: Python project root to package.
        manifest: TOML manifest path.
        output: Release output root.
        platforms: Selected platform names, or ``None`` for all.
        output_format: ``native`` or ``pyz``.
        publish: Release project to publish to, if any.
        release_config: ``stationer`` configuration path.
        dry_run: Plan the release without running it.
        remote: Operating system of a remote pull worker, if any.
        bind: Interface the remote job server listens on.
        port: Port the remote job server listens on.
        worker_host: Address the remote worker uses to reach the host.
        expose: Charter backend that makes the job server public.
        short: Short-URL alias (empty for a random one).
        arch: CPU the remote Windows executable is built for.
        vm: Registered VM to run the remote worker in; empty for the VM that
            ``[tool.conjure]`` defines.
        attach: Use the already-running guest ``[tool.conjure]`` describes.
        vm_user: SSH user in the VM.
        vm_identity: SSH private key for the VM.
        vm_port: Host loopback port of the VM's SSH server.
        local: Run the remote worker on this machine instead (host OS must
            match ``--remote``).

    """

    project: Path
    manifest: Path
    output: Path
    platforms: list[str] | None
    output_format: str
    publish: str | None
    release_config: Path
    dry_run: bool
    remote: str | None
    bind: str | None
    port: int | None
    worker_host: str | None
    expose: str | None
    short: str | None
    arch: str | None
    vm: str | None
    attach: bool
    vm_user: str | None
    vm_identity: Path | None
    vm_port: int | None
    local: bool


type JsonValue = (
    str | int | float | bool | list[JsonValue] | dict[str, JsonValue]
)


@dataclass(frozen=True, slots=True, kw_only=True)
class BuilderDefaults:
    """Provide defaults for a bare ``python tools/stationer`` run.

    Attributes:
        project: Python project root to package.
        manifest: TOML build manifest path.
        output: Release output root.
        release_config: ``stationer`` configuration path.
        platforms: Platform names built by the ``native`` format.
        formats: Output formats to run in order.
        publish: Release project to publish to, or ``None``.
        pypi_upload: Whether to upload the built distributions to PyPI.
        dry_run: Plan the release and upload without running them.
        remote: Operating systems built on a remote pull worker after the
            formats finish.
        vm: Registered VM that runs the remote workers; empty for the VM that
            ``[tool.conjure]`` defines.
        attach: Use the already-running guest ``[tool.conjure]`` describes.
        arch: ``--arch`` value applied to native formats.

    """

    project: Path
    manifest: Path
    output: Path
    release_config: Path
    platforms: tuple[PlatformName, ...]
    formats: tuple[str, ...]
    publish: str | None
    pypi_upload: bool
    dry_run: bool
    remote: tuple[str, ...] = ()
    vm: str | None = None
    attach: bool = False
    arch: str | None = None

    @classmethod
    def load(cls, *, start: Path) -> BuilderDefaults | None:
        """Read ``[tool.stationer.builder]`` from the nearest pyproject.

        Args:
            start: Directory the ``pyproject.toml`` search starts at.

        Returns:
            Parsed defaults, or ``None`` when no table is found.

        Raises:
            BuildError: If the table holds an invalid key or value.

        """
        for directory in (start, *start.parents):
            pyproject: Path = directory / "pyproject.toml"
            if not pyproject.is_file():
                continue
            with pyproject.open(mode="rb") as stream:
                data: dict[str, object] = cast(
                    typ="dict[str, object]",
                    val=load(stream),
                )
            tool: object = data.get("tool", {})
            if not isinstance(tool, dict):
                continue
            stationer: object = cast(
                typ="dict[str, object]",
                val=tool,
            ).get("stationer")
            if not isinstance(stationer, dict):
                continue
            raw: object = cast(
                typ="dict[str, object]",
                val=stationer,
            ).get("builder")
            if raw is None:
                continue
            if not isinstance(raw, dict):
                msg: str = "[tool.stationer.builder] must be a table"
                raise BuildError(msg)
            return cls._parse(
                table=cast(
                    typ="dict[str, JsonValue]",
                    val=raw,
                ),
                root=directory,
            )
        return None

    @classmethod
    def _parse(
        cls,
        *,
        table: dict[str, JsonValue],
        root: Path,
    ) -> BuilderDefaults:
        """Validate the table into defaults.

        Args:
            table: Loaded ``[tool.stationer.builder]`` table.
            root: Directory containing ``pyproject.toml``.

        Returns:
            The resolved defaults.

        Raises:
            BuildError: If a value has the wrong type or a key is unknown.

        """
        known: frozenset[str] = frozenset(
            {
                "project",
                "manifest",
                "output",
                "release_config",
                "platforms",
                "formats",
                "publish",
                "pypi_upload",
                "dry_run",
                "remote",
                "vm",
                "attach",
                "arch",
            },
        )
        unknown: set[str] = set(table) - known
        if unknown:
            msg: str = (
                f"[tool.stationer.builder] unknown keys: {sorted(unknown)}"
            )
            raise BuildError(msg)
        publish: JsonValue | None = table.get("publish")
        if publish is not None and not isinstance(publish, str):
            msg = "[tool.stationer.builder] publish must be a string"
            raise BuildError(msg)
        vm: JsonValue | None = table.get("vm")
        if vm is not None and not isinstance(vm, str):
            msg = "[tool.stationer.builder] vm must be a string"
            raise BuildError(msg)
        arch: JsonValue | None = table.get("arch")
        if arch is not None and arch not in (
            "native",
            "arm64",
            "amd64",
            "all",
        ):
            msg = (
                "[tool.stationer.builder] arch must be native, arm64, "
                "amd64 or all"
            )
            raise BuildError(msg)
        return cls(
            project=cls._path(table=table, key="project", root=root),
            manifest=cls._path(table=table, key="manifest", root=root),
            output=cls._path(
                table=table,
                key="output",
                root=root,
                default="build",
            ),
            release_config=cls._path(
                table=table,
                key="release_config",
                root=root,
                default="release.toml",
            ),
            platforms=cast(
                typ="tuple[PlatformName, ...]",
                val=cls._names(
                    table=table,
                    key="platforms",
                    allowed=frozenset({"macos", "linux", "windows"}),
                    default=("macos", "linux", "windows"),
                ),
            ),
            formats=cls._names(
                table=table,
                key="formats",
                allowed=frozenset({"native", "pyz", "pypi"}),
                default=("native",),
            ),
            publish=publish,
            pypi_upload=cls._flag(
                table=table,
                key="pypi_upload",
                default=False,
            ),
            dry_run=cls._flag(table=table, key="dry_run", default=False),
            remote=cls._names(
                table=table,
                key="remote",
                allowed=frozenset({"macos", "linux", "windows"}),
                default=(),
            ),
            vm=vm,
            attach=cls._flag(table=table, key="attach", default=False),
            arch=cast(typ="str | None", val=arch),
        )

    @staticmethod
    def _path(
        *,
        table: dict[str, JsonValue],
        key: str,
        root: Path,
        default: str | None = None,
    ) -> Path:
        """Resolve one required path key relative to the pyproject root.

        Args:
            table: Loaded table.
            key: Key whose value must be a non-empty string.
            root: Directory containing ``pyproject.toml``.
            default: Relative path used when ``key`` is absent.

        Returns:
            The resolved path.

        Raises:
            BuildError: If the key is missing or not a string.

        """
        value: object = table.get(key, default)
        if not isinstance(value, str) or not value:
            msg: str = f"[tool.stationer.builder] needs a string {key}"
            raise BuildError(msg)
        return (root / value).resolve()

    @staticmethod
    def _names(
        *,
        table: dict[str, JsonValue],
        key: str,
        allowed: frozenset[str],
        default: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Return a validated list-of-strings key.

        Args:
            table: Loaded table.
            key: List key.
            allowed: Accepted values.
            default: Value used when the key is absent.

        Returns:
            The validated names.

        Raises:
            BuildError: If a value is not a string list in ``allowed``.

        """
        value: object = table.get(key, list(default))
        if not isinstance(value, list):
            msg: str = (
                f"[tool.stationer.builder] {key} must be a list from "
                f"{sorted(allowed)}"
            )
            raise BuildError(msg)
        entries: list[object] = cast(typ="list[object]", val=value)
        items: list[str] = [item for item in entries if isinstance(item, str)]
        if len(items) != len(entries) or not set(items) <= allowed:
            msg = (
                f"[tool.stationer.builder] {key} must be a list from "
                f"{sorted(allowed)}"
            )
            raise BuildError(msg)
        return tuple(items)

    @staticmethod
    def _flag(
        *,
        table: dict[str, JsonValue],
        key: str,
        default: bool,
    ) -> bool:
        """Return a validated boolean key.

        Args:
            table: Loaded table.
            key: Boolean key.
            default: Value used when the key is absent.

        Returns:
            The boolean value.

        Raises:
            BuildError: If the value is not a boolean.

        """
        value: object = table.get(key, default)
        if not isinstance(value, bool):
            msg: str = f"[tool.stationer.builder] {key} must be a boolean"
            raise BuildError(msg)
        return value


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectConfig:
    """Hold platform-independent build configuration.

    Attributes:
        root: Python project root.
        name: Project name.
        version: Semantic version text.
        entrypoint: Resolved Python entrypoint.
        identifier: Reverse-DNS bundle identifier.
        console: Whether the frozen executable keeps a console.
        pyinstaller_args: Extra PyInstaller arguments.
        pip_args: Extra pip install arguments.
        local_dependencies: Resolved sibling projects installed first.

    """

    root: Path
    name: str
    version: str
    entrypoint: Path
    identifier: str
    console: bool
    pyinstaller_args: tuple[str, ...]
    pip_args: tuple[str, ...]
    local_dependencies: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class LinuxConfig:
    """Hold Linux Docker build configuration.

    Attributes:
        docker_platforms: Docker ``--platform`` values, one build each.
        docker_platform: The cell one build call targets; the matrix loop
            overrides it per platform.
        image: Docker base image for glibc builds.
        musl_image: Docker base image for musl (Alpine) builds, or ``None``
            when no musl cells are built.
        maintainer: Debian package maintainer.
        description: Debian package description.
        guest: Optional SSH-reachable Linux build guest.

    """

    image: str
    maintainer: str
    description: str
    docker_platforms: tuple[str, ...] = ()
    musl_image: str | None = None
    docker_platform: str = "linux/arm64"
    guest: PosixGuestConfig | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MacOSConfig:
    """Hold native macOS build configuration.

    Attributes:
        python: Python interpreter used for the arm64 build environment.
        signing_identity: Optional Installer signing identity.
        python_amd64: Path of an x86_64 Python. It runs under Rosetta so pip
            resolves x86_64 wheels; required for amd64 builds.
        minimum_macos: Minimum macOS version to record; the build host's
            version is used when omitted.
        guest: Optional SSH-reachable macOS build guest.

    """

    python: str
    signing_identity: str | None
    python_amd64: str | None = None
    minimum_macos: str | None = None
    guest: PosixGuestConfig | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PosixGuestConfig:
    """Hold an SSH-reachable POSIX QEMU guest's configuration.

    Attributes:
        host: SSH host of the guest.
        port: SSH port of the guest.
        user: SSH user on the guest.
        identity_file: SSH private key path.
        python: Python launcher command on the guest.
        remote_root: Remote build directory root.

    """

    host: str
    port: int
    user: str
    identity_file: Path
    python: tuple[str, ...]
    remote_root: str


@dataclass(frozen=True, slots=True, kw_only=True)
class WindowsConfig:
    """Hold Windows ARM64 QEMU worker configuration.

    Attributes:
        host: SSH host of the Windows ARM64 guest.
        port: SSH port of the guest.
        user: SSH user on the guest.
        identity_file: SSH private key path.
        python: Python launcher command on the guest.
        remote_root: Remote build directory root.
        publisher: Installer publisher name.

    """

    host: str
    port: int
    user: str
    identity_file: Path
    python: tuple[str, ...]
    remote_root: str
    publisher: str


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildResult:
    """Describe one completed platform build.

    Attributes:
        platform: Target operating system.
        architecture: Verified or requested target architecture.
        executable: Frozen application executable.
        installer: Native installer package, or ``None`` when the build
            produces only a frozen executable.
        executable_sha256: SHA-256 digest of the executable.
        installer_sha256: SHA-256 digest of the installer, or empty.
        duration_seconds: Wall-clock build duration.
        abi: ``native``, or the C library family on Linux (``glibc``).
        minimum_macos: Oldest macOS the executable runs on, for macOS builds.
        minimum_glibc: glibc of the build image: the oldest glibc the
            executable is guaranteed to run on (everything bundled in it was
            built against that version), for glibc builds.

    """

    platform: PlatformName
    architecture: str
    executable: Path
    installer: Path | None
    executable_sha256: str
    installer_sha256: str
    duration_seconds: float
    abi: str = "native"
    minimum_glibc: str | None = None
    minimum_macos: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildConfiguration:
    """Aggregate validated platform configuration.

    Attributes:
        project: Platform-independent project configuration.
        linux: Linux Docker build configuration.
        macos: macOS build configuration.
        windows: Windows configuration, or ``None`` when not selected.
        pyz: ``(name, bootstrap)`` from ``[pyz]`` — bootstrap relative to
            the project root — or ``None`` when the table is absent.

    """

    project: ProjectConfig
    linux: LinuxConfig
    macos: MacOSConfig
    windows: WindowsConfig | None
    pyz: tuple[str, str] | None = None


class BuildError(RuntimeError):
    """Report a failed external build operation."""


class ManifestLoader:
    """Load and validate the stationer manifest."""

    @classmethod
    def load(
        cls,
        *,
        manifest: Path,
        project_root: Path,
        platforms: tuple[PlatformName, ...] | None = None,
    ) -> BuildConfiguration:
        """Load build configuration from TOML.

        Args:
            manifest: TOML manifest path.
            project_root: Python project root to package.
            platforms: Platforms to build, or ``None`` for all of them.

        Returns:
            Fully validated build configuration.

        Raises:
            BuildError: If required configuration is absent or invalid.

        """
        stream: BinaryIO
        with manifest.open(mode="rb") as stream:
            document: ManifestDocument = cast(
                typ="ManifestDocument",
                val=load(stream),
            )
        selected: tuple[PlatformName, ...] = platforms or (
            "macos",
            "linux",
            "windows",
        )
        project_table: ProjectTable = document.get("project", ProjectTable())
        linux_table: LinuxTable = document.get("linux", LinuxTable())
        macos_table: MacOSTable = document.get("macos", MacOSTable())
        windows_table: WindowsTable = document.get(
            "windows",
            WindowsTable(),
        )
        name: str = cls._required_string(
            value=project_table.get("name"),
            key="name",
        )
        version: str = project_table.get("version", "")
        version_file: str = project_table.get("version_file", "")
        if not version and version_file:
            version_path: Path = project_root / version_file
            if version_path.is_file():
                version = (
                    read_version(
                        text=version_path.read_text(encoding="utf-8"),
                    )
                    or ""
                )
        version = cls._required_string(value=version, key="version")
        entrypoint_text: str = cls._required_string(
            value=project_table.get("entrypoint"),
            key="entrypoint",
        )
        identifier: str = project_table.get(
            "identifier",
            f"com.local.{name.lower().replace('_', '-')}",
        )
        entrypoint: Path = (project_root / entrypoint_text).resolve()
        root: Path = project_root.resolve()
        if not entrypoint.is_relative_to(root) or not entrypoint.is_file():
            msg: str = "Project entrypoint must be a file inside project root."
            raise BuildError(msg)
        windows: WindowsConfig | None = None
        if "windows" in selected:
            if not windows_table:
                msg = "Missing [windows] table for a windows platform build."
                raise BuildError(msg)
            host: str = cls._required_string(
                value=windows_table.get("host"),
                key="windows.host",
            )
            user: str = cls._required_string(
                value=windows_table.get("user"),
                key="windows.user",
            )
            identity_text: str = cls._required_string(
                value=windows_table.get("identity_file"),
                key="windows.identity_file",
            )
            identity_file: Path = Path(identity_text).expanduser().resolve()
            if not identity_file.is_file():
                msg = f"SSH identity file does not exist: {identity_file}"
                raise BuildError(msg)
            windows = WindowsConfig(
                host=host,
                port=windows_table.get("port", 2222),
                user=user,
                identity_file=identity_file,
                python=tuple(
                    windows_table.get("python", ["py", "-3.13"]),
                ),
                remote_root=windows_table.get(
                    "remote_root",
                    "C:/stationer",
                ),
                publisher=windows_table.get("publisher", "Unknown"),
            )
        pyz: tuple[str, str] | None = None
        if document.get("pyz"):
            # pyz_builder imports this module, so it is loaded lazily.
            pyz_module: ModuleType = import_module(
                name="tools.stationer.pyz_builder",
            )
            pyz_config: PyzConfig = pyz_module.PyzConfig.load(
                manifest=manifest,
                project_root=root,
                default_name=name,
            )
            pyz = (
                pyz_config.name,
                pyz_config.bootstrap.relative_to(root).as_posix(),
            )
        return BuildConfiguration(
            project=ProjectConfig(
                root=root,
                name=name,
                version=version,
                entrypoint=entrypoint,
                identifier=identifier,
                console=project_table.get("console", True),
                pyinstaller_args=tuple(
                    project_table.get("pyinstaller_args", []),
                ),
                pip_args=tuple(project_table.get("pip_args", [])),
                local_dependencies=cls._local_dependencies(
                    root=root,
                    entries=project_table.get("local_dependencies", []),
                ),
            ),
            linux=LinuxConfig(
                docker_platforms=tuple(
                    linux_table.get(
                        "docker_platforms",
                        [
                            linux_table.get(
                                "docker_platform",
                                "linux/arm64",
                            ),
                        ],
                    ),
                ),
                docker_platform=linux_table.get(
                    "docker_platform",
                    "linux/arm64",
                ),
                image=linux_table.get("image", "python:3.13-slim-bookworm"),
                musl_image=linux_table.get("musl_image"),
                maintainer=linux_table.get("maintainer", "Unknown"),
                description=linux_table.get(
                    "description",
                    f"{name} {version}",
                ),
                guest=cls._guest(
                    table=linux_table.get("guest"),
                    key="linux.guest",
                ),
            ),
            macos=MacOSConfig(
                python=macos_table.get("python", executable),
                signing_identity=macos_table.get("signing_identity"),
                python_amd64=macos_table.get("python_amd64"),
                minimum_macos=macos_table.get("minimum_macos"),
                guest=cls._guest(
                    table=macos_table.get("guest"),
                    key="macos.guest",
                ),
            ),
            windows=windows,
            pyz=pyz,
        )

    @classmethod
    def _guest(
        cls,
        *,
        table: GuestTable | None,
        key: str,
    ) -> PosixGuestConfig | None:
        """Validate a ``[<platform>.guest]`` SSH guest table.

        Args:
            table: The nested guest table, when present.
            key: Dotted setting prefix used in error messages.

        Returns:
            The guest configuration, or ``None`` when absent.

        Raises:
            BuildError: If a required key is missing or the identity file
                does not exist.

        """
        if not table:
            return None
        host: str = cls._required_string(
            value=table.get("host"),
            key=f"{key}.host",
        )
        user: str = cls._required_string(
            value=table.get("user"),
            key=f"{key}.user",
        )
        identity_text: str = cls._required_string(
            value=table.get("identity_file"),
            key=f"{key}.identity_file",
        )
        identity_file: Path = Path(identity_text).expanduser().resolve()
        if not identity_file.is_file():
            msg: str = f"SSH identity file does not exist: {identity_file}"
            raise BuildError(msg)
        return PosixGuestConfig(
            host=host,
            port=table.get("port", 2222),
            user=user,
            identity_file=identity_file,
            python=tuple(table.get("python", ["python3"])),
            remote_root=table.get("remote_root", ".stationer"),
        )

    @staticmethod
    def _local_dependencies(
        *,
        root: Path,
        entries: list[str],
    ) -> tuple[Path, ...]:
        """Resolve sibling projects that must be installed first.

        Args:
            root: Python project root; entries are relative to it.
            entries: Manifest ``local_dependencies`` values.

        Returns:
            Absolute dependency directories.

        Raises:
            BuildError: If an entry is missing, not a project, or repeated.

        """
        resolved: list[Path] = []
        for entry in entries:
            path: Path = (root / entry).resolve()
            if not (path / "pyproject.toml").is_file():
                msg: str = (
                    f"Local dependency {entry!r} is not a project "
                    "directory (no pyproject.toml)."
                )
                raise BuildError(msg)
            if any(other.name == path.name for other in resolved):
                msg = f"Duplicate local dependency name: {path.name}"
                raise BuildError(msg)
            resolved.append(path)
        return tuple(resolved)

    @staticmethod
    def _required_string(*, value: str | None, key: str) -> str:
        """Return a required non-empty string setting.

        Args:
            value: Optional manifest value.
            key: Setting name.

        Returns:
            Validated string value.

        Raises:
            BuildError: If the value is absent or empty.

        """
        if value is not None and value.strip():
            return value
        msg: str = f"Missing required non-empty setting: {key}"
        raise BuildError(msg)


@final
class SourceArchive:
    """Create a bounded source archive for remote builds."""

    _excluded_parts: Final[frozenset[str]] = frozenset(
        {
            ".git",
            ".venv",
            "__pycache__",
            "build",
            "dist",
            "node_modules",
        },
    )

    @classmethod
    def create(cls, *, project: ProjectConfig, destination: Path) -> None:
        """Create a ZIP containing the owned project source.

        Local dependencies are stored under ``.local-deps/<name>/`` so a
        remote worker can install them before the project itself.

        Args:
            project: Project configuration.
            destination: ZIP path to create.

        """
        archive: ZipFile
        with ZipFile(
            file=destination,
            mode="w",
            compression=ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for path in cls._files(root=project.root):
                archive.write(
                    filename=path,
                    arcname=path.relative_to(project.root),
                )
            for dependency in project.local_dependencies:
                for path in cls._files(root=dependency):
                    archive.write(
                        filename=path,
                        arcname=Path(".local-deps")
                        / dependency.name
                        / path.relative_to(dependency),
                    )

    @classmethod
    def _files(cls, *, root: Path) -> tuple[Path, ...]:
        """List the files of a tree that belong in a source archive.

        Args:
            root: Directory to scan.

        Returns:
            Included files in a stable order.

        """
        selected: list[Path] = []
        for path in sorted(root.rglob(pattern="*")):
            relative: Path = path.relative_to(root)
            if path.is_file() and not any(
                part in cls._excluded_parts for part in relative.parts
            ):
                selected.append(path)
        return tuple(selected)


@final
class SourceStaging:
    """Copy a project and its local dependencies to a scratch directory.

    ``pip`` builds in-tree and would otherwise leave ``build/`` and
    ``*.egg-info`` directories inside the user's checkout.
    """

    _ignored: Final[tuple[str, ...]] = (
        "build",
        "dist",
        "*.egg-info",
        "__pycache__",
        ".venv",
        ".git",
        "node_modules",
        ".DS_Store",
    )

    @classmethod
    def stage(
        cls,
        *,
        project: ProjectConfig,
        directory: Path,
    ) -> tuple[tuple[Path, ...], Path]:
        """Copy the dependencies and the project under ``directory``.

        Args:
            project: Project configuration.
            directory: Scratch directory that receives the copies.

        Returns:
            The dependency copies (install first) and the project copy.

        """
        ignore: Callable[[str, list[str]], set[str]] = ignore_patterns(
            *cls._ignored,
        )
        dependencies: list[Path] = []
        for dependency in project.local_dependencies:
            copied: Path = directory / "deps" / dependency.name
            _ = copytree(src=dependency, dst=copied, ignore=ignore)
            dependencies.append(copied)
        source: Path = directory / "source"
        _ = copytree(src=project.root, dst=source, ignore=ignore)
        return tuple(dependencies), source


class ArtifactUtilities:
    """Provide deterministic artifact operations."""

    @classmethod
    def result(
        cls,
        *,
        platform_name: PlatformName,
        architecture: str,
        executable_path: Path,
        installer_path: Path | None,
        started_at: float,
    ) -> BuildResult:
        """Create a verified result record.

        Args:
            platform_name: Target operating system.
            architecture: Verified or requested target architecture.
            executable_path: Frozen application executable.
            installer_path: Native installer package, if the build makes one.
            started_at: Monotonic build start timestamp.

        Returns:
            Completed build result.

        Raises:
            BuildError: If an expected artifact is missing.

        """
        if not executable_path.is_file() or (
            installer_path is not None and not installer_path.is_file()
        ):
            msg: str = (
                f"Expected build artifacts are missing for {platform_name}."
            )
            raise BuildError(
                msg,
            )
        return BuildResult(
            platform=platform_name,
            architecture=architecture,
            executable=executable_path,
            installer=installer_path,
            executable_sha256=cls.digest(path=executable_path),
            installer_sha256=(
                cls.digest(path=installer_path)
                if installer_path is not None
                else ""
            ),
            duration_seconds=monotonic() - started_at,
        )

    @classmethod
    def folder(
        cls,
        *,
        output: Path,
        platform_name: str,
        architecture: str,
        project: str,
        abi: str = "",
    ) -> Path:
        """Compose ``<output>/<os>/<arch>/<abi>/<project>``.

        Args:
            output: Build output root.
            platform_name: ``macos``, ``linux`` or ``windows``.
            architecture: CPU architecture in any common spelling.
            project: Project (package) name.
            abi: ABI folder name; guessed as ``native`` or ``glibc`` when
                omitted.

        Returns:
            The folder holding the project's artifacts for that build.

        """
        return (
            output
            / platform_name
            / cls.canonical(architecture=architecture)
            / (abi or cls.abi(platform_name=platform_name))
            / project
        )

    @staticmethod
    def pyinstaller_folder(*, package: Path) -> Path:
        """Name the folder of a package's frozen executables.

        Args:
            package: Package folder from :meth:`folder`.

        Returns:
            ``<package>/pyinstaller``.

        """
        return package / "pyinstaller"

    @staticmethod
    def installer_folder(*, package: Path) -> Path:
        """Name the folder of a package's installers.

        Args:
            package: Package folder from :meth:`folder`.

        Returns:
            ``<package>/installer``.

        """
        return package / "installer"

    @staticmethod
    def pyz_folder(*, package: Path) -> Path:
        """Name the folder of a package's ``.pyz`` archives.

        Args:
            package: Package folder from :meth:`folder`.

        Returns:
            ``<package>/pyz``.

        """
        return package / "pyz"

    @staticmethod
    def canonical(*, architecture: str) -> str:
        """Name a CPU architecture the way the output folders do.

        Args:
            architecture: ``arm64``, ``aarch64``, ``amd64``, ``x86_64``, etc.

        Returns:
            ``arm64`` for ARM CPUs, ``amd64`` for x86-64 CPUs, otherwise the
            lower-cased architecture.

        """
        name: str = architecture.lower()
        if name.startswith(("arm", "aarch")):
            return "arm64"
        if name in {"amd64", "x86_64", "x64"}:
            return "amd64"
        return name

    @staticmethod
    def abi(*, platform_name: str, image: str = "") -> str:
        """Name the ABI folder of a build.

        Args:
            platform_name: ``macos``, ``linux`` or ``windows``.
            image: Linux build image, used to tell musl (Alpine) from glibc.

        Returns:
            ``native`` outside Linux, otherwise ``musl`` or ``glibc``.

        """
        if platform_name != "linux":
            return "native"
        return "musl" if "alpine" in image.lower() else "glibc"

    @staticmethod
    def digest(*, path: Path) -> str:
        """Return the SHA-256 digest for an artifact.

        Args:
            path: Artifact to hash.

        Returns:
            Lowercase hexadecimal SHA-256 digest.

        """
        digest: DigestProtocol = sha256()
        stream: BinaryIO
        chunk: bytes
        with path.open(mode="rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class MacOSContext:
    """Bundle the shared context a macOS build step needs.

    Attributes:
        host: Host session commands run in.
        prefix: Interpreter command prefix (Rosetta for amd64 builds).
        venv_python: Build virtual-environment interpreter.
        dependencies: Staged local-dependency directories.
        source: Staged project directory.
        work: Scratch root deleted when the build ends.
        output: Package output directory for this architecture.

    """

    host: JobSession
    prefix: tuple[str, ...]
    venv_python: Path
    dependencies: tuple[Path, ...]
    source: Path
    work: Path
    output: Path


class MacOSBuilder:
    """Build thin macOS PyInstaller executables and PKG installers.

    Each architecture gets its own virtual environment: arm64 runs the
    configured Python natively, amd64 runs an x86_64 Python under Rosetta
    (``arch -x86_64``) so pip resolves x86_64 wheels. Nothing is combined
    with ``lipo``; every result is checked with ``lipo -archs`` instead.
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
        config: MacOSConfig,
        output: Path,
        architecture: str = "native",
        pyz: PyzPayload | None = None,
    ) -> BuildResult:
        """Build the macOS release artifacts for one architecture.

        Args:
            project: Project configuration.
            config: macOS configuration.
            output: Package output directory for this architecture.
            architecture: ``arm64``, ``amd64`` or ``native`` (this Mac's).
            pyz: ``.pyz`` payload built on the host and bundled into the
                installer when present.

        Returns:
            Verified macOS build result.

        Raises:
            BuildError: If not running on macOS, the interpreter or the
                frozen executable has the wrong architecture, or a build
                step fails.
            job_failure: When a session command or transfer fails.

        """
        if system() != "Darwin":
            msg: str = "The macOS backend must run on macOS."
            raise BuildError(msg)
        host_cpu: str = ArtifactUtilities.canonical(architecture=machine())
        target: str = (
            host_cpu
            if architecture == "native"
            else ArtifactUtilities.canonical(architecture=architecture)
        )
        python, prefix = self._interpreter(
            config=config,
            target=target,
            host_cpu=host_cpu,
        )
        started_at: float = monotonic()
        work: Path = output / ".work"
        venv: Path = work / "venv"
        frozen_dir: Path = ArtifactUtilities.pyinstaller_folder(package=output)
        dist: Path = frozen_dir / "dist"
        stage: Path = work / "pkg-root" / "usr" / "local" / "bin"
        packaged: Path = work / "installer.pkg"
        output.mkdir(parents=True, exist_ok=True)
        venv_python: Path = venv / "bin" / "python"
        executable_path: Path = frozen_dir / (
            f"{project.name}-{project.version}-macos-{target}"
        )
        installer_path: Path = ArtifactUtilities.installer_folder(
            package=output,
        ) / (f"{project.name}-{project.version}-macos-{target}.pkg")
        host: JobSession = self._open_host()
        rooted: JobSession = self._open_host(cwd=project.root)
        minimum: str
        try:
            host.start()
            rooted.start()
            self._require_python(
                host=host,
                prefix=prefix,
                python=python,
                target=target,
            )
            _ = host.run(
                command=("rm", "-rf", str(object=work)),
                check=True,
                timeout_seconds=HostConstants.timeout_seconds,
            )
            _ = host.run(
                command=(*prefix, python, "-m", "venv", str(object=venv)),
                check=True,
                timeout_seconds=HostConstants.timeout_seconds,
            )
            dependencies, source = SourceStaging.stage(
                project=project,
                directory=work / "staged",
            )
            _ = host.run(
                command=(
                    *prefix,
                    str(object=venv_python),
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "pyinstaller",
                    *project.pip_args,
                    *(str(object=path) for path in dependencies),
                    str(object=source),
                ),
                check=True,
                timeout_seconds=HostConstants.timeout_seconds,
            )
            _ = rooted.run(
                command=(
                    *prefix,
                    *BuildCommands.pyinstaller(
                        python_path=venv_python,
                        project=project,
                        dist=dist,
                        work=work / "pyinstaller-work",
                        spec=work / "spec",
                    ),
                ),
                check=True,
                timeout_seconds=HostConstants.timeout_seconds,
            )
            frozen: Path = dist / project.name
            self._require_thin(host=host, executable=frozen, target=target)
            host.upload(
                source=frozen,
                destination=str(object=stage / project.name),
            )
            scripts: Path = work / "scripts"
            if pyz is not None:
                _ = self._stage_pyz(
                    context=MacOSContext(
                        host=host,
                        prefix=prefix,
                        venv_python=venv_python,
                        dependencies=dependencies,
                        source=source,
                        work=work,
                        output=output,
                    ),
                    project=project,
                    pyz=pyz,
                )
            _ = host.run(
                command=(
                    "pkgbuild",
                    "--root",
                    str(object=work / "pkg-root"),
                    *(("--scripts", str(object=scripts)) if pyz else ()),
                    "--identifier",
                    project.identifier,
                    "--version",
                    project.version,
                    "--install-location",
                    "/",
                    *(
                        ("--sign", config.signing_identity)
                        if config.signing_identity
                        else ()
                    ),
                    str(object=packaged),
                ),
                check=True,
                timeout_seconds=HostConstants.timeout_seconds,
            )
            minimum = (
                config.minimum_macos
                or host.run(
                    command=("sw_vers", "-productVersion"),
                    check=True,
                ).stdout.strip()
            )
            host.download(
                source=str(object=frozen),
                destination=executable_path,
            )
            host.download(
                source=str(object=packaged),
                destination=installer_path,
            )
        except QemuRunnerError as error:
            raise job_failure(error=error) from error
        finally:
            rooted.close()
            host.close()
            rmtree(path=work, ignore_errors=True)
            rmtree(path=dist, ignore_errors=True)
        return replace(
            ArtifactUtilities.result(
                platform_name="macos",
                architecture=target,
                executable_path=executable_path,
                installer_path=installer_path,
                started_at=started_at,
            ),
            minimum_macos=minimum,
        )

    @staticmethod
    def _stage_pyz(
        *,
        context: MacOSContext,
        project: ProjectConfig,
        pyz: PyzPayload,
    ) -> Path:
        """Build the ``.pyz`` locally and stage the installer payload.

        The archive is written both to the cell's ``pyz/`` folder and into
        the package root, alongside the ``<name>-pyz`` shim, the
        ensure-python helper and the ``postinstall`` script pkgbuild runs.

        Args:
            context: Shared macOS build context.
            project: Project configuration.
            pyz: ``.pyz`` payload description.

        Returns:
            The ``scripts/`` directory to pass to ``pkgbuild --scripts``.

        """
        support: ModuleType = import_module(
            name="tools.stationer.installer_support",
        )
        libdir: Path = (
            context.work / "pkg-root" / "usr" / "local" / "lib" / project.name
        )
        bindir: Path = context.work / "pkg-root" / "usr" / "local" / "bin"
        libdir.mkdir(parents=True, exist_ok=True)
        maker: Path = context.work / "make_pyz.py"
        maker.write_text(
            data=support.make_pyz_source(),
            encoding="utf-8",
        )
        _ = context.host.run(
            command=(
                *context.prefix,
                str(object=context.venv_python),
                str(object=maker),
                "--python",
                str(object=context.venv_python),
                "--source",
                str(object=context.source),
                "--bootstrap",
                str(object=context.source / pyz.bootstrap),
                "--output",
                str(object=libdir / pyz.bundled),
                "--",
                *project.pip_args,
                *(str(object=path) for path in context.dependencies),
            ),
            check=True,
            timeout_seconds=HostConstants.timeout_seconds,
        )
        (libdir / "ensure-python.sh").write_text(
            data=support.ensure_python_sh(),
            encoding="utf-8",
        )
        shim: Path = bindir / f"{project.name}-pyz"
        shim.write_text(
            data=support.shim_sh(name=project.name, pyz=pyz),
            encoding="utf-8",
        )
        shim.chmod(mode=0o755)
        (libdir / "ensure-python.sh").chmod(mode=0o755)
        scripts: Path = context.work / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        postinstall: Path = scripts / "postinstall"
        postinstall.write_text(
            data=support.pkg_postinstall(name=project.name),
            encoding="utf-8",
        )
        postinstall.chmod(mode=0o755)
        pyz_folder: Path = ArtifactUtilities.pyz_folder(
            package=context.output,
        )
        pyz_folder.mkdir(parents=True, exist_ok=True)
        _ = copy2(
            src=libdir / pyz.bundled,
            dst=pyz_folder / pyz.archive,
        )
        return scripts

    @staticmethod
    def _interpreter(
        *,
        config: MacOSConfig,
        target: str,
        host_cpu: str,
    ) -> tuple[str, tuple[str, ...]]:
        """Choose the Python and the command prefix for an architecture.

        Args:
            config: macOS configuration.
            target: ``arm64`` or ``amd64``.
            host_cpu: This Mac's architecture, ``arm64`` or ``amd64``.

        Returns:
            The interpreter path and the prefix (``arch -x86_64`` when an
            x86_64 build runs under Rosetta, else empty).

        Raises:
            BuildError: If the architecture is unsupported here or the
                x86_64 Python is not configured.

        """
        if target == "arm64":
            if host_cpu != "arm64":
                msg: str = "An arm64 macOS build needs an Apple Silicon Mac."
                raise BuildError(msg)
            return config.python, ()
        if target != "amd64":
            msg = f"Unsupported macOS architecture: {target}"
            raise BuildError(msg)
        if config.python_amd64 is None:
            msg = (
                "An amd64 macOS build needs python_amd64 in [macos]: the "
                "path of an x86_64 Python (it runs under Rosetta)."
            )
            raise BuildError(msg)
        prefix: tuple[str, ...] = (
            ("arch", "-x86_64") if host_cpu == "arm64" else ()
        )
        return config.python_amd64, prefix

    @staticmethod
    def _require_python(
        *,
        host: JobSession,
        prefix: tuple[str, ...],
        python: str,
        target: str,
    ) -> None:
        """Fail unless the interpreter really runs as the target CPU.

        Args:
            host: Host session to run the probe in.
            prefix: Command prefix, such as ``arch -x86_64``.
            python: Interpreter path.
            target: ``arm64`` or ``amd64``.

        Raises:
            BuildError: If the interpreter is missing, cannot run as the
                target architecture, or reports a different one.

        """
        expected: str = "x86_64" if target == "amd64" else "arm64"
        try:
            probe: JobResult = host.run(
                command=(
                    *prefix,
                    python,
                    "-c",
                    "import platform; print(platform.machine())",
                ),
            )
        except OSError as error:
            msg: str = f"Cannot run the {expected} Python {python}: {error}"
            raise BuildError(msg) from error
        reported: str = probe.stdout.strip()
        if probe.return_code != 0 or reported != expected:
            msg = (
                f"{python} must be a {expected} Python for a {target} "
                f"build, but it reported {reported or 'nothing'} (exit "
                f"status {probe.return_code}). {probe.stderr.strip()}"
            ).strip()
            raise BuildError(msg)

    @staticmethod
    def _require_thin(
        *,
        host: JobSession,
        executable: Path,
        target: str,
    ) -> None:
        """Fail unless the executable holds exactly the target slice.

        Args:
            host: Host session to run ``lipo`` in.
            executable: The frozen executable.
            target: ``arm64`` or ``amd64``.

        Raises:
            BuildError: If ``lipo -archs`` reports anything but the one
                expected architecture.

        """
        expected: str = "x86_64" if target == "amd64" else "arm64"
        archs: list[str] = host.run(
            command=("lipo", "-archs", str(object=executable)),
            check=True,
        ).stdout.split()
        if archs != [expected]:
            msg: str = (
                f"{executable.name} contains {' '.join(archs) or 'nothing'} "
                f"but must be exactly {expected}."
            )
            raise BuildError(msg)


class BuildCommands:
    """Construct shared build commands."""

    @staticmethod
    def pyinstaller(
        *,
        python_path: Path,
        project: ProjectConfig,
        dist: Path,
        work: Path,
        spec: Path,
    ) -> tuple[str, ...]:
        """Construct a PyInstaller command.

        Args:
            python_path: Interpreter inside the build environment.
            project: Project configuration.
            dist: PyInstaller distribution directory.
            work: PyInstaller work directory.
            spec: PyInstaller spec directory.

        Returns:
            Shell-free command tuple.

        """
        return (
            str(object=python_path),
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            *(() if project.console else ("--windowed",)),
            "--name",
            project.name,
            "--distpath",
            str(object=dist),
            "--workpath",
            str(object=work),
            "--specpath",
            str(object=spec),
            *project.pyinstaller_args,
            str(object=project.entrypoint),
        )


def job_failure(*, error: QemuRunnerError) -> BuildError:
    """Turn a runner failure into a build error that keeps the output.

    Args:
        error: Failure raised by a ``conjure`` runner.

    Returns:
        A ``BuildError``; failed commands include their stdout and stderr.

    """
    if isinstance(error, RemoteCommandError):
        result: JobResult = error.result
        return BuildError(
            "Command failed with exit code "
            f"{result.return_code}: {result.command!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
    return BuildError(str(object=error))


class DockerSteps:
    """Plan the phases of the container script, for progress reporting.

    Attributes:
        plan: ``(message, weight)`` per phase, in the order the script runs
            them; the weight sizes the phase's slice of the progress bar.

    """

    plan: Final[tuple[tuple[str, int], ...]] = (
        ("installing system tools", 1),
        ("preparing Python", 1),
        ("creating an isolated virtual environment", 1),
        ("upgrading pip", 1),
        ("installing PyInstaller, dependencies and the project", 5),
        ("building the native executable", 4),
        ("packaging the installer", 1),
    )

    @classmethod
    def mark(cls, *, index: int) -> str:
        """Compose the shell line that announces a phase.

        Args:
            index: One-based phase index.

        Returns:
            A command the container prints a step marker with.

        """
        return StepMarker.shell(
            index=index,
            message=cls.plan[index - 1][0],
        )


class LinuxDockerBuilder:
    """Build a Linux executable and DEB inside a Docker container."""

    def __init__(
        self,
        *,
        open_session: ContainerOpener = open_container,
        progress: ProgressSink | None = None,
    ) -> None:
        """Initialize the builder.

        Args:
            open_session: Creates the container session of a build.
            progress: Receives structured progress updates parsed from the
                container's streamed output.

        """
        self._open_session: Final[ContainerOpener] = open_session
        self._progress: Final[ProgressSink | None] = progress

    @staticmethod
    def worker_script(
        *,
        project: ProjectConfig,
        config: LinuxConfig,
        pyz: PyzPayload | None = None,
    ) -> str:
        """Create the fixed container worker script.

        The project is expected at ``/work/src``, each local dependency at
        ``/work/deps/<name>`` and the installer-support files under
        ``/work/support``; artifacts are written to ``/out``.

        Args:
            project: Project configuration.
            config: Linux Docker configuration.
            pyz: ``.pyz`` payload description, if bundled.

        Returns:
            POSIX shell script with validated values quoted.

        """
        name: str = quote(s=project.name)
        version: str = quote(s=project.version)
        entrypoint: str = quote(
            s=str(object=project.entrypoint.relative_to(project.root)),
        )
        architecture: str = quote(
            s=config.docker_platform.removeprefix("linux/"),
        )
        maintainer: str = quote(s=config.maintainer)
        description: str = quote(s=config.description)
        quoted_pyinstaller: list[str] = [
            quote(s=value) for value in project.pyinstaller_args
        ]
        quoted_pip: list[str] = [quote(s=value) for value in project.pip_args]
        extra: str = " ".join(quoted_pyinstaller)
        pip_extra: str = " ".join(quoted_pip)
        console: str = "" if project.console else "--windowed"
        installs: str = " ".join(
            f"/work/deps/{quote(s=dependency.name)}"
            for dependency in project.local_dependencies
        )
        musl: bool = (
            ArtifactUtilities.abi(
                platform_name="linux",
                image=config.image,
            )
            == "musl"
        )
        apk_architecture: str = quote(
            s={
                "amd64": "x86_64",
                "arm64": "aarch64",
            }.get(
                config.docker_platform.removeprefix("linux/"),
                config.docker_platform.removeprefix("linux/"),
            ),
        )
        pyz_steps: str = ""
        deb_payload: str = ""
        apk_payload: str = ""
        deb_depends: str = ""
        apk_depends: str = ""
        if pyz is not None:
            bundled: str = quote(s=pyz.bundled)
            bootstrap: str = quote(s=pyz.bootstrap)
            pyz_steps = f"""
/work/venv/bin/python /work/support/make_pyz.py \
  --python /work/venv/bin/python --source /work/src \
  --bootstrap /work/src/{bootstrap} --output /out/pyz \
  -- {pip_extra} {installs}
mkdir -p /work/pkg/usr/local/lib/{name}
cp /out/pyz /work/pkg/usr/local/lib/{name}/{bundled}
cp /work/support/ensure-python.sh /work/pkg/usr/local/lib/{name}/
cp /work/support/{name}-pyz /work/pkg/usr/local/bin/{name}-pyz
chmod 755 /work/pkg/usr/local/bin/{name}-pyz \
  /work/pkg/usr/local/lib/{name}/ensure-python.sh
"""
            deb_payload = """
cp /work/support/postinst /work/pkg/DEBIAN/postinst
chmod 755 /work/pkg/DEBIAN/postinst
"""
            deb_depends = "Depends: python3, curl, ca-certificates\\n"
            apk_depends = '-I "depends:python3 curl ca-certificates" '
            apk_payload = (
                "cp /work/support/post-install /work/pkg-post-install\n"
            )
        packaging: str = (
            f"""
{apk_payload}apk mkpkg -I name:{name} -I version:{version} \
  -I arch:{apk_architecture} {apk_depends}-I description:{description} \
  -s post-install:/work/pkg-post-install -o /out/installer /work/pkg
""".strip()
            if musl
            else f"""
getconf GNU_LIBC_VERSION | sed 's/^glibc //' > /out/glibc
mkdir -p /work/pkg/DEBIAN
printf 'Package: %s\\nVersion: %s\\nArchitecture: %s\\n{deb_depends}' \
  {name} {version} {architecture} > /work/pkg/DEBIAN/control
printf 'Maintainer: %s\\nDescription: %s\\n' \
  {maintainer} {description} >> /work/pkg/DEBIAN/control
{deb_payload}dpkg-deb --build --root-owner-group /work/pkg /out/installer
""".strip()
        )
        marks: tuple[str, ...] = tuple(
            DockerSteps.mark(index=index)
            for index in range(1, len(DockerSteps.plan) + 1)
        )
        return f"""
{marks[0]}
if command -v apk >/dev/null 2>&1; then
  apk add --no-cache binutils
else
  apt-get update
  apt-get install -y --no-install-recommends binutils dpkg-dev
fi
{marks[1]}
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
elif command -v apk >/dev/null 2>&1; then
  apk add --no-cache python3
  PY=python3
else
  apt-get install -y --no-install-recommends python3 python3-venv
  PY=python3
fi
{marks[2]}
$PY -m venv /work/venv
{marks[3]}
/work/venv/bin/python -m pip install --disable-pip-version-check --upgrade pip
{marks[4]}
/work/venv/bin/python -m pip install --progress-bar raw \
  --disable-pip-version-check pyinstaller {pip_extra} {installs} /work/src
{marks[5]}
/work/venv/bin/python -m PyInstaller \
  --noconfirm --clean --onefile {console} --name {name} \
  --distpath /work/dist --workpath /work/build --specpath /work/spec \
  {extra} /work/src/{entrypoint}
cp /work/dist/{name} /out/executable
{marks[6]}
mkdir -p /work/pkg/usr/local/bin
cp /work/dist/{name} /work/pkg/usr/local/bin/{name}
{pyz_steps}{packaging}
""".strip()

    def build(
        self,
        *,
        project: ProjectConfig,
        config: LinuxConfig,
        output: Path,
        pyz: PyzPayload | None = None,
    ) -> BuildResult:
        """Build Linux artifacts inside Docker.

        The staged project, its dependencies and the installer support
        scripts are copied into a fresh container, built there, and only
        the artifacts are copied back.

        Args:
            project: Project configuration.
            config: Linux Docker configuration.
            output: Platform output directory.
            pyz: ``.pyz`` payload built in the container and bundled into
                the installer when present.

        Returns:
            Verified Linux build result.

        Raises:
            BuildError: If the container, a command or a transfer fails.
            job_failure: When a container command or transfer fails.

        """
        support: ModuleType = import_module(
            name="tools.stationer.installer_support",
        )
        started_at: float = monotonic()
        output.mkdir(parents=True, exist_ok=True)
        abi: str = ArtifactUtilities.abi(
            platform_name="linux",
            image=config.image,
        )
        architecture: str = config.docker_platform.removeprefix("linux/")
        executable_name: str = (
            f"{project.name}-{project.version}-linux-{architecture}"
            + ("-musl" if abi == "musl" else "")
        )
        installer_name: str = (
            f"{project.name}-{project.version}-linux-{architecture}-musl.apk"
            if abi == "musl"
            else f"{project.name}_{project.version}_{architecture}.deb"
        )
        script: str = LinuxDockerBuilder.worker_script(
            project=project,
            config=config,
            pyz=pyz,
        )
        session: StreamingSession = self._open_session(config=config)
        try:
            with TemporaryDirectory(prefix="stationer-") as temporary:
                dependencies, source = SourceStaging.stage(
                    project=project,
                    directory=Path(temporary),
                )
                session.start()
                _ = session.run(
                    command=(
                        "mkdir",
                        "-p",
                        "/work/deps",
                        "/work/support",
                        "/out",
                    ),
                    check=True,
                )
                session.upload(source=source, destination="/work/src")
                for dependency in dependencies:
                    session.upload(
                        source=dependency,
                        destination=f"/work/deps/{dependency.name}",
                    )
                for file_name, content in self._support_files(
                    project=project,
                    abi=abi,
                    pyz=pyz,
                    support=support,
                ).items():
                    staged: Path = Path(temporary) / file_name
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    staged.write_text(data=content, encoding="utf-8")
                    session.upload(
                        source=staged,
                        destination=f"/work/support/{file_name}",
                    )
                tracker: ProgressTracker | None = (
                    ProgressTracker(
                        weights=tuple(
                            weight for _, weight in DockerSteps.plan
                        ),
                        sink=self._progress,
                    )
                    if self._progress is not None
                    else None
                )
                _ = session.run(
                    command=("sh", "-eu", "-c", script),
                    check=True,
                    timeout_seconds=3600,
                    on_output=(
                        None
                        if tracker is None
                        else lambda text: tracker.output(text=text)
                    ),
                )
                if tracker is not None:
                    tracker.finish()
                frozen_folder: Path = ArtifactUtilities.pyinstaller_folder(
                    package=output,
                )
                installer_folder: Path = ArtifactUtilities.installer_folder(
                    package=output,
                )
                frozen_folder.mkdir(parents=True, exist_ok=True)
                installer_folder.mkdir(parents=True, exist_ok=True)
                session.download(
                    source="/out/executable",
                    destination=frozen_folder / executable_name,
                )
                session.download(
                    source="/out/installer",
                    destination=installer_folder / installer_name,
                )
                if pyz is not None:
                    pyz_folder: Path = ArtifactUtilities.pyz_folder(
                        package=output,
                    )
                    pyz_folder.mkdir(parents=True, exist_ok=True)
                    session.download(
                        source="/out/pyz",
                        destination=pyz_folder / pyz.archive,
                    )
                minimum_glibc: str = ""
                if abi == "glibc":
                    floor: Path = frozen_folder / f"{executable_name}.glibc"
                    session.download(
                        source="/out/glibc",
                        destination=floor,
                    )
                    minimum_glibc = floor.read_text(
                        encoding="utf-8",
                    ).strip()
                    floor.unlink()
        except QemuRunnerError as error:
            raise job_failure(error=error) from error
        finally:
            session.close()
        return replace(
            ArtifactUtilities.result(
                platform_name="linux",
                architecture=architecture,
                executable_path=ArtifactUtilities.pyinstaller_folder(
                    package=output,
                )
                / executable_name,
                installer_path=ArtifactUtilities.installer_folder(
                    package=output,
                )
                / installer_name,
                started_at=started_at,
            ),
            abi=abi,
            minimum_glibc=minimum_glibc or None,
        )

    @staticmethod
    def _support_files(
        *,
        project: ProjectConfig,
        abi: str,
        pyz: PyzPayload | None,
        support: ModuleType,
    ) -> dict[str, str]:
        """Render the installer-support files uploaded to the container.

        Args:
            project: Project configuration.
            abi: ``glibc`` or ``musl``.
            pyz: ``.pyz`` payload description, if bundled.
            support: The ``installer_support`` module.

        Returns:
            ``/work/support``-relative file names mapped to their content.

        """
        files: dict[str, str] = {}
        if pyz is None:
            return files
        files["make_pyz.py"] = support.make_pyz_source()
        files["ensure-python.sh"] = support.ensure_python_sh()
        files[f"{project.name}-pyz"] = support.shim_sh(
            name=project.name,
            pyz=pyz,
        )
        if abi == "musl":
            files["post-install"] = support.apk_post_install(
                name=project.name,
            )
        else:
            files["postinst"] = support.deb_postinst(name=project.name)
        return files


class WindowsBuildConstants:
    """Group Windows-build constants.

    Attributes:
        worker_template: PowerShell worker-template file name.

    """

    worker_template: Final[str] = "windows_build.ps1.tmpl"


class WindowsQemuBuilder:
    """Build Windows ARM64 artifacts through an SSH-accessible QEMU guest."""

    def __init__(self, *, open_session: SshOpener = open_ssh) -> None:
        """Initialize the builder.

        Args:
            open_session: Creates the guest session of a build.

        """
        self._open_session: Final[SshOpener] = open_session

    @classmethod
    def worker_script(
        cls,
        *,
        project: ProjectConfig,
        config: WindowsConfig,
        remote: str,
        pyz: PyzPayload | None = None,
    ) -> str:
        """Generate the controlled Windows build worker.

        Args:
            project: Project configuration.
            config: Windows configuration.
            remote: Unique remote build directory.
            pyz: ``.pyz`` payload built in the guest and bundled into the
                installer when present.

        Returns:
            PowerShell worker source.

        """
        entrypoint: str = str(
            object=project.entrypoint.relative_to(project.root),
        ).replace("/", "\\")
        quoted_pyinstaller: list[str] = [
            cls.ps_quote(value=value) for value in project.pyinstaller_args
        ]
        quoted_pip: list[str] = [
            cls.ps_quote(value=value) for value in project.pip_args
        ]
        quoted_pip.extend(
            cls.ps_quote(value=f".local-deps\\{dependency.name}")
            for dependency in project.local_dependencies
        )
        pyinstaller_extra: str = " ".join(quoted_pyinstaller)
        pip_extra: str = " ".join(quoted_pip)
        console: str = "" if project.console else "--windowed"
        quoted_python: list[str] = [
            cls.ps_quote(value=value) for value in config.python
        ]
        python_command: str = " ".join(quoted_python)
        remote_executable: str = f"{remote}\\out\\{project.name}.exe"
        app_executable: str = f"{{app}}\\{project.name}.exe"
        run_line: str = (
            f'Filename: "{app_executable}"; '
            f'Description: "Launch {project.name}"; '
            "Flags: nowait postinstall skipifsilent"
        )
        pyz_build: str = ""
        iss_pyz_files: str = ""
        run_ensure: str = ""
        if pyz is not None:
            pyz_build = (
                "$PyzOut = Join-Path $Out 'pyz'\n"
                f"& $Python (Join-Path $Root 'support\\make_pyz.py') "
                f"--python $Python --source $Source "
                f"--bootstrap (Join-Path $Source "
                f"'{pyz.bootstrap.replace('/', chr(92))}') "
                f"--output (Join-Path $PyzOut '{pyz.archive}') "
                f"-- {pip_extra}\n"
            )
            iss_pyz_files = (
                f'Source: "{remote}\\out\\pyz\\{pyz.archive}"; '
                f'DestDir: "{{app}}"; DestName: "{pyz.bundled}"; '
                "Flags: ignoreversion\n"
                f'Source: "{remote}\\support\\{project.name}-pyz.cmd"; '
                'DestDir: "{app}"\n'
                f'Source: "{remote}\\support\\ensure-python.ps1"; '
                'DestDir: "{app}"'
            )
            run_ensure = (
                'Filename: "powershell.exe"; Parameters: "-NoProfile '
                "-ExecutionPolicy Bypass -File "
                '""{app}\\ensure-python.ps1"" ""{app}"""; '
                "Flags: runhidden waituntilterminated; "
                'StatusMsg: "Checking for a Python interpreter..."'
            )
        support: ModuleType = import_module(
            name="tools.stationer.installer_support",
        )
        return support.render(
            source=support.template(
                name=WindowsBuildConstants.worker_template,
            ),
            values={
                "ROOT": cls.ps_quote(value=remote),
                "PYTHON": python_command,
                "PIP_EXTRA": pip_extra,
                "CONSOLE": console,
                "PYINSTALLER_EXTRA": pyinstaller_extra,
                "NAME": cls.ps_quote(value=project.name),
                "ENTRYPOINT": cls.ps_quote(value=entrypoint),
                "EXE_NAME": cls.ps_quote(value=f"{project.name}.exe"),
                "ISS_ID": project.identifier,
                "ISS_NAME": project.name,
                "ISS_VERSION": project.version,
                "ISS_PUBLISHER": config.publisher,
                "ISS_REMOTE": remote,
                "ISS_EXE": remote_executable,
                "RUN_LINE": run_line,
                "PYZ_BUILD": pyz_build,
                "ISS_PYZ_FILES": iss_pyz_files,
                "RUN_ENSURE": run_ensure,
            },
        )

    @staticmethod
    def ps_quote(*, value: str) -> str:
        """Quote one PowerShell literal string.

        Args:
            value: Untrusted literal value.

        Returns:
            Single-quoted PowerShell literal.

        """
        return "'" + value.replace("'", "''") + "'"

    def build(
        self,
        *,
        project: ProjectConfig,
        config: WindowsConfig,
        output: Path,
        pyz: PyzPayload | None = None,
    ) -> BuildResult:
        """Build Windows ARM64 executable and setup executable.

        Args:
            project: Project configuration.
            config: Windows guest configuration.
            output: Platform output directory.
            pyz: ``.pyz`` payload built in the guest and bundled into the
                installer when present.

        Returns:
            Verified Windows build result.

        Raises:
            job_failure: When a guest command or transfer fails.

        """
        started_at: float = monotonic()
        output.mkdir(parents=True, exist_ok=True)
        executable_path: Path = ArtifactUtilities.pyinstaller_folder(
            package=output,
        ) / (f"{project.name}-{project.version}-windows-arm64.exe")
        installer_path: Path = ArtifactUtilities.installer_folder(
            package=output,
        ) / (f"{project.name}-{project.version}-windows-arm64-setup.exe")
        shell: tuple[str, ...] = (
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
        )
        temporary_text: str
        with TemporaryDirectory(prefix="stationer-") as temporary_text:
            temporary: Path = Path(temporary_text)
            source_zip: Path = temporary / "source.zip"
            worker: Path = temporary / "build.ps1"
            SourceArchive.create(project=project, destination=source_zip)
            build_id: str = ArtifactUtilities.digest(path=source_zip)[:16]
            remote: str = f"{config.remote_root.rstrip('/')}/{build_id}"
            worker.write_text(
                data=WindowsQemuBuilder.worker_script(
                    project=project,
                    config=config,
                    remote=remote,
                    pyz=pyz,
                ),
                encoding="utf-8",
            )
            session: JobSession = self._open_session(config=config)
            try:
                session.start()
                _ = session.run(
                    command=(
                        *shell,
                        "-Command",
                        (
                            "Remove-Item -LiteralPath "
                            f"'{remote}' -Recurse -Force "
                            "-ErrorAction SilentlyContinue; "
                            "New-Item -ItemType Directory -Force -Path "
                            f"'{remote}' | Out-Null"
                        ),
                    ),
                    check=True,
                )
                session.upload(
                    source=source_zip,
                    destination=f"{remote}/source.zip",
                )
                session.upload(
                    source=worker,
                    destination=f"{remote}/build.ps1",
                )
                if pyz is not None:
                    support: ModuleType = import_module(
                        name="tools.stationer.installer_support",
                    )
                    for file_name, content in (
                        ("make_pyz.py", support.make_pyz_source()),
                        ("ensure-python.ps1", support.ensure_python_ps1()),
                        (
                            f"{project.name}-pyz.cmd",
                            support.shim_cmd(name=project.name, pyz=pyz),
                        ),
                    ):
                        staged_file: Path = temporary / file_name
                        staged_file.write_text(
                            data=content,
                            encoding="utf-8",
                        )
                        session.upload(
                            source=staged_file,
                            destination=f"{remote}/support/{file_name}",
                        )
                _ = session.run(
                    command=(
                        *shell,
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        f"{remote}/build.ps1",
                    ),
                    check=True,
                    timeout_seconds=3600,
                )
                executable_path.parent.mkdir(parents=True, exist_ok=True)
                session.download(
                    source=f"{remote}/out/{project.name}.exe",
                    destination=executable_path,
                )
                installer_path.parent.mkdir(parents=True, exist_ok=True)
                session.download(
                    source=f"{remote}/out/setup.exe",
                    destination=installer_path,
                )
                if pyz is not None:
                    pyz_folder: Path = ArtifactUtilities.pyz_folder(
                        package=output,
                    )
                    pyz_folder.mkdir(parents=True, exist_ok=True)
                    session.download(
                        source=f"{remote}/out/pyz/{pyz.archive}",
                        destination=pyz_folder / pyz.archive,
                    )
            except QemuRunnerError as error:
                raise job_failure(error=error) from error
            finally:
                with suppress(QemuRunnerError):
                    _ = session.run(
                        command=(
                            *shell,
                            "-Command",
                            "Remove-Item -LiteralPath "
                            f"'{remote}' -Recurse -Force "
                            "-ErrorAction SilentlyContinue",
                        ),
                        check=False,
                        timeout_seconds=60,
                    )
                session.close()
        return ArtifactUtilities.result(
            platform_name="windows",
            architecture="arm64",
            executable_path=executable_path,
            installer_path=installer_path,
            started_at=started_at,
        )


class PosixBuildConstants:
    """Group POSIX-guest build constants.

    Attributes:
        linux_template: Linux guest worker-template file name.
        macos_template: macOS guest worker-template file name.
        probe: Shell probe reporting ``uname -m`` and, on musl guests
            (Alpine), an extra ``musl`` line.
        linux_support: Installer-support files a Linux guest needs for a
            bundled ``.pyz``; both maintainer scripts ship because the
            packager is detected inside the guest.
        macos_support: Installer-support files a macOS guest needs for a
            bundled ``.pyz``.

    """

    linux_template: Final[str] = "linux_build.sh.tmpl"
    macos_template: Final[str] = "macos_build.sh.tmpl"
    probe: Final[str] = (
        "uname -m; command -v apk >/dev/null 2>&1 && echo musl || true"
    )
    linux_support: Final[tuple[str, ...]] = (
        "make_pyz.py",
        "ensure-python.sh",
        "postinst",
        "post-install",
    )
    macos_support: Final[tuple[str, ...]] = (
        "make_pyz.py",
        "ensure-python.sh",
        "postinstall",
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class PosixBuild:
    """Inputs shared by one POSIX guest build request.

    Attributes:
        project: Project configuration.
        config: POSIX guest configuration.
        platform_name: ``linux`` or ``macos``.
        linux: Linux packaging metadata, required for Linux guests.
        macos: macOS configuration for signing and the recorded
            minimum version on macOS guests.
        pyz: ``(name, bootstrap)`` from ``[pyz]``, when the installer
            bundles a ``.pyz``.

    """

    project: ProjectConfig
    config: PosixGuestConfig
    platform_name: Literal["linux", "macos"]
    linux: LinuxConfig | None = None
    macos: MacOSConfig | None = None
    pyz: tuple[str, str] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class _PosixLayout:
    """Probed guest facts and the artifact layout they imply.

    Attributes:
        architecture: Guest-reported canonical architecture.
        abi: ``glibc``, ``musl`` or ``native``.
        package: Release package folder for the probe result.
        executable_name: Final executable file name.
        installer_name: Final installer file name.
        payload: ``.pyz`` payload built in the guest, when bundled.

    """

    architecture: str
    abi: str
    package: Path
    executable_name: str
    installer_name: str
    payload: PyzPayload | None


class PosixQemuBuilder:
    """Build on an SSH-reachable POSIX QEMU guest (Linux or macOS).

    Unlike the Windows guest, a POSIX guest reports its own architecture
    through ``uname -m`` and, for Linux, its C library through the presence
    of ``apk``: the package folder, the ``.pyz`` release tag and the
    artifact names all derive from the probe, so any guest the manifest
    points at describes itself.
    """

    def __init__(self, *, open_session: SshOpener = open_ssh) -> None:
        """Initialize the builder.

        Args:
            open_session: Creates the guest session of a build.

        """
        self._open_session: Final[SshOpener] = open_session

    @classmethod
    def worker_script(
        cls,
        *,
        build: PosixBuild,
        remote: str,
        store: str,
        payload: PyzPayload | None = None,
    ) -> str:
        """Generate the controlled POSIX guest build worker.

        Args:
            build: Shared guest build request.
            remote: Unique remote build directory.
            store: Persistent remote partition store; the worker
                materializes the source tree from its packs.
            payload: ``.pyz`` payload built in the guest and bundled into
                the installer when present.

        Returns:
            POSIX shell worker source.

        """
        support: ModuleType = import_module(
            name="tools.stationer.installer_support",
        )
        project: ProjectConfig = build.project
        entrypoint: str = project.entrypoint.relative_to(
            project.root,
        ).as_posix()
        pip_extra: str = " ".join(
            quote(s=value)
            for value in (
                *project.pip_args,
                *(
                    f".local-deps/{dependency.name}"
                    for dependency in project.local_dependencies
                ),
            )
        )
        pyinstaller_extra: str = " ".join(
            quote(s=value) for value in project.pyinstaller_args
        )
        values: dict[str, str] = {
            "ROOT": quote(s=remote),
            "STORE": quote(s=store),
            "PYTHON": " ".join(
                quote(s=value) for value in build.config.python
            ),
            "NAME": quote(s=project.name),
            "VERSION": quote(s=project.version),
            "ENTRYPOINT": quote(s=entrypoint),
            "PIP_EXTRA": pip_extra,
            "PYINSTALLER_EXTRA": pyinstaller_extra,
            "CONSOLE": "" if project.console else "--windowed",
        }
        if build.platform_name == "linux":
            linux_config: LinuxConfig = (
                build.linux
                if build.linux is not None
                else LinuxConfig(
                    image="",
                    maintainer="Unknown",
                    description=project.name,
                )
            )
            values |= cls._linux_values(
                linux=linux_config,
                pyz=payload,
                pip_extra=pip_extra,
            )
        else:
            values |= cls._macos_values(
                project=project,
                macos=build.macos,
                pyz=payload,
                pip_extra=pip_extra,
            )
        return support.render(
            source=support.template(
                name=(
                    PosixBuildConstants.linux_template
                    if build.platform_name == "linux"
                    else PosixBuildConstants.macos_template
                ),
            ),
            values=values,
        )

    def build(self, *, build: PosixBuild, output: Path) -> BuildResult:
        """Build POSIX guest artifacts over SSH.

        The release output root is passed in ``output``; the package
        folder, release tag and artifact names are derived from the guest's
        own ``uname -m`` (and, on Linux, its C library) so the guest
        describes what it built.

        Args:
            build: Shared guest build request.
            output: Release output root.

        Returns:
            Verified POSIX build result.

        Raises:
            BuildError: If the guest, a command or a transfer fails.
            job_failure: When a guest command or transfer fails.

        """
        packs_mod: ModuleType = import_module(
            name="tools.stationer.source_packs",
        )
        started_at: float = monotonic()
        temporary_text: str
        with TemporaryDirectory(prefix="stationer-") as temporary_text:
            temporary: Path = Path(temporary_text)
            worker: Path = temporary / "build.sh"
            runtime_zip: Path = temporary / "partition-runtime.zip"
            store: Path = output / ".partition"
            catalog: Path = packs_mod.SourcePacks.create(
                project=build.project,
                store=store,
            )
            packs_mod.SourcePacks.bootstrap(destination=runtime_zip)
            build_id: str = ArtifactUtilities.digest(path=catalog)[:16]
            remote: str = f"{build.config.remote_root.rstrip('/')}/{build_id}"
            remote_store: str = (
                f"{build.config.remote_root.rstrip('/')}/.partition"
            )
            session: JobSession = self._open_session(config=build.config)
            layout: _PosixLayout
            executable_path: Path
            installer_path: Path
            minimum_glibc: str
            minimum_macos: str
            try:
                session.start()
                layout = self._layout(
                    session=session,
                    build=build,
                    output=output,
                )
                worker.write_text(
                    data=PosixQemuBuilder.worker_script(
                        build=build,
                        remote=remote,
                        store=remote_store,
                        payload=layout.payload,
                    ),
                    encoding="utf-8",
                )
                _ = session.run(command=("rm", "-rf", remote), check=False)
                _ = session.run(
                    command=("mkdir", "-p", remote, remote_store),
                    check=True,
                )
                self._sync_remote_store(
                    session=session,
                    remote_store=remote_store,
                    store=store,
                    runtime_zip=runtime_zip,
                )
                session.upload(
                    source=catalog,
                    destination=f"{remote}/catalog.json",
                )
                session.upload(
                    source=worker,
                    destination=f"{remote}/build.sh",
                )
                if layout.payload is not None:
                    self._stage_payload(
                        session=session,
                        temporary=temporary,
                        remote=remote,
                        build=build,
                        payload=layout.payload,
                    )
                _ = session.run(
                    command=("sh", f"{remote}/build.sh"),
                    check=True,
                    timeout_seconds=3600,
                )
                (
                    executable_path,
                    installer_path,
                    minimum_glibc,
                    minimum_macos,
                ) = self._collect(
                    session=session,
                    temporary=temporary,
                    remote=remote,
                    build=build,
                    layout=layout,
                )
            except QemuRunnerError as error:
                raise job_failure(error=error) from error
            finally:
                with suppress(QemuRunnerError):
                    _ = session.run(
                        command=("rm", "-rf", remote),
                        check=False,
                        timeout_seconds=60,
                    )
                session.close()
        return replace(
            ArtifactUtilities.result(
                platform_name=build.platform_name,
                architecture=layout.architecture,
                executable_path=executable_path,
                installer_path=installer_path,
                started_at=started_at,
            ),
            abi=layout.abi,
            minimum_glibc=minimum_glibc or None,
            minimum_macos=minimum_macos or None,
        )

    @staticmethod
    def _linux_values(
        *,
        linux: LinuxConfig,
        pyz: PyzPayload | None,
        pip_extra: str,
    ) -> dict[str, str]:
        """Collect the Linux-only placeholders of the guest worker.

        Args:
            linux: Linux packaging metadata.
            pyz: ``.pyz`` payload, if bundled.
            pip_extra: Rendered pip argument text for ``make_pyz.py``.

        Returns:
            Replacement text per placeholder name.

        """
        pyz_build: str = ""
        apk_depends: str = ""
        apk_scripts: str = ""
        deb_depends: str = ""
        deb_prep: str = ""
        if pyz is not None:
            bundled: str = quote(s=pyz.bundled)
            bootstrap: str = quote(s=pyz.bootstrap)
            pyz_build = (
                '"$ROOT/venv/bin/python" "$ROOT/support/make_pyz.py" '
                '--python "$ROOT/venv/bin/python" --source "$ROOT/src" '
                f'--bootstrap "$ROOT/src/{bootstrap}" '
                f'--output "$ROOT/out/pyz" -- {pip_extra}\n'
                'mkdir -p "$ROOT/pkg/usr/local/lib/$NAME"\n'
                f'cp "$ROOT/out/pyz" '
                f'"$ROOT/pkg/usr/local/lib/$NAME/{bundled}"\n'
                'cp "$ROOT/support/ensure-python.sh" '
                '"$ROOT/pkg/usr/local/lib/$NAME/"\n'
                'cp "$ROOT/support/$NAME-pyz" '
                '"$ROOT/pkg/usr/local/bin/$NAME-pyz"\n'
                'chmod 755 "$ROOT/pkg/usr/local/bin/$NAME-pyz" '
                '"$ROOT/pkg/usr/local/lib/$NAME/ensure-python.sh"\n'
            )
            apk_depends = '-I "depends:python3 curl ca-certificates" '
            apk_scripts = '-s "post-install:$ROOT/support/post-install" '
            deb_depends = "Depends: python3, curl, ca-certificates\\n"
            deb_prep = (
                'cp "$ROOT/support/postinst" "$ROOT/pkg/DEBIAN/postinst"\n'
                'chmod 755 "$ROOT/pkg/DEBIAN/postinst"\n'
            )
        return {
            "MAINTAINER": quote(s=linux.maintainer),
            "DESCRIPTION": quote(s=linux.description),
            "PYZ_BUILD": pyz_build,
            "APK_DEPENDS": apk_depends,
            "APK_SCRIPTS": apk_scripts,
            "DEB_DEPENDS": deb_depends,
            "DEB_PREP": deb_prep,
        }

    @staticmethod
    def _macos_values(
        *,
        project: ProjectConfig,
        macos: MacOSConfig | None,
        pyz: PyzPayload | None,
        pip_extra: str,
    ) -> dict[str, str]:
        """Collect the macOS-only placeholders of the guest worker.

        Args:
            project: Project configuration.
            macos: macOS configuration, for signing and the recorded
                minimum version.
            pyz: ``.pyz`` payload, if bundled.
            pip_extra: Rendered pip argument text for ``make_pyz.py``.

        Returns:
            Replacement text per placeholder name.

        """
        pyz_build: str = ""
        pkg_scripts: str = ""
        if pyz is not None:
            bundled: str = quote(s=pyz.bundled)
            bootstrap: str = quote(s=pyz.bootstrap)
            pyz_build = (
                '"$ROOT/venv/bin/python" "$ROOT/support/make_pyz.py" '
                '--python "$ROOT/venv/bin/python" --source "$ROOT/src" '
                f'--bootstrap "$ROOT/src/{bootstrap}" '
                f'--output "$ROOT/out/pyz" -- {pip_extra}\n'
                'mkdir -p "$ROOT/scripts" '
                '"$ROOT/pkg-root/usr/local/lib/$NAME"\n'
                f'cp "$ROOT/out/pyz" '
                f'"$ROOT/pkg-root/usr/local/lib/$NAME/{bundled}"\n'
                'cp "$ROOT/support/ensure-python.sh" '
                '"$ROOT/pkg-root/usr/local/lib/$NAME/"\n'
                'cp "$ROOT/support/$NAME-pyz" '
                '"$ROOT/pkg-root/usr/local/bin/$NAME-pyz"\n'
                'cp "$ROOT/support/postinstall" '
                '"$ROOT/scripts/postinstall"\n'
                'chmod 755 "$ROOT/pkg-root/usr/local/bin/$NAME-pyz" '
                '"$ROOT/pkg-root/usr/local/lib/$NAME/ensure-python.sh" '
                '"$ROOT/scripts/postinstall"\n'
            )
            pkg_scripts = '--scripts "$ROOT/scripts" '
        sign: str = ""
        minimum: str = "sw_vers -productVersion"
        if macos is not None:
            if macos.signing_identity:
                sign = f"--sign {quote(s=macos.signing_identity)} "
            if macos.minimum_macos:
                minimum = f"echo {quote(s=macos.minimum_macos)}"
        return {
            "IDENTIFIER": quote(s=project.identifier),
            "PYZ_BUILD": pyz_build,
            "PKG_SCRIPTS": pkg_scripts,
            "SIGN": sign,
            "MINIMUM": minimum,
        }

    @staticmethod
    def _layout(
        *,
        session: JobSession,
        build: PosixBuild,
        output: Path,
    ) -> _PosixLayout:
        """Probe the guest and derive the artifact layout from it.

        Args:
            session: Active guest session.
            build: Shared guest build request.
            output: Release output root.

        Returns:
            Layout for every artifact the guest will produce.

        Raises:
            BuildError: If the guest reports no architecture.

        """
        support: ModuleType = import_module(
            name="tools.stationer.installer_support",
        )
        probe: JobResult = session.run(
            command=("sh", "-c", PosixBuildConstants.probe),
            check=True,
        )
        parts: list[str] = probe.stdout.split()
        if not parts:
            msg: str = "The guest did not report its architecture."
            raise BuildError(msg)
        architecture: str = ArtifactUtilities.canonical(
            architecture=parts[0],
        )
        abi: str = (
            "musl"
            if "musl" in parts
            else "glibc"
            if build.platform_name == "linux"
            else "native"
        )
        project: ProjectConfig = build.project
        tag: str = f"{build.platform_name}-{architecture}" + (
            f"-{abi}" if build.platform_name == "linux" else ""
        )
        return _PosixLayout(
            architecture=architecture,
            abi=abi,
            package=ArtifactUtilities.folder(
                output=output,
                platform_name=build.platform_name,
                architecture=architecture,
                project=project.name,
                abi=abi,
            ),
            executable_name=f"{project.name}-{project.version}-{tag}",
            installer_name=(
                f"{project.name}-{project.version}-linux-"
                f"{architecture}-musl.apk"
                if build.platform_name == "linux" and abi == "musl"
                else f"{project.name}_{project.version}_{architecture}.deb"
                if build.platform_name == "linux"
                else f"{project.name}-{project.version}-"
                f"{build.platform_name}-{architecture}.pkg"
            ),
            payload=(
                support.pyz_payload(
                    pyz_name=build.pyz[0],
                    version=project.version,
                    tag=tag,
                    bootstrap=build.pyz[1],
                )
                if build.pyz is not None
                else None
            ),
        )


    @staticmethod
    def _sync_remote_store(
        *,
        session: JobSession,
        remote_store: str,
        store: Path,
        runtime_zip: Path,
    ) -> None:
        """Upload the runtime zip and every source pack the guest lacks.

        Args:
            session: Active guest session.
            remote_store: Persistent remote content store directory.
            store: Local partition store holding the packs.
            runtime_zip: Launcher runtime archive for the guest.

        """
        packs_mod: ModuleType = import_module(
            name="tools.stationer.source_packs",
        )
        listing: JobResult = session.run(
            command=(
                "sh",
                "-c",
                f"cd {quote(s=remote_store)} 2>/dev/null"
                " && find . -type f || true",
            ),
            check=True,
        )
        have: frozenset[str] = frozenset(listing.stdout.split())
        if "./partition-runtime.zip" not in have:
            session.upload(
                source=runtime_zip,
                destination=f"{remote_store}/partition-runtime.zip",
            )
        pack_files: dict[str, Path] = packs_mod.SourcePacks.packs(
            store=store,
        )
        made: set[str] = set()
        for relative, source in pack_files.items():
            if f"./{relative}" in have:
                continue
            parent: str = PurePosixPath(relative).parent.as_posix()
            if parent not in made:
                _ = session.run(
                    command=(
                        "mkdir",
                        "-p",
                        f"{remote_store}/{parent}",
                    ),
                    check=True,
                )
                made.add(parent)
            session.upload(
                source=source,
                destination=f"{remote_store}/{relative}",
            )

    @staticmethod
    def _stage_payload(
        *,
        session: JobSession,
        temporary: Path,
        remote: str,
        build: PosixBuild,
        payload: PyzPayload,
    ) -> None:
        """Upload the files a bundled ``.pyz`` build needs.

        Args:
            session: Active guest session.
            temporary: Local staging directory.
            remote: Unique remote build directory.
            build: Shared guest build request.
            payload: ``.pyz`` payload descriptor.

        """
        support: ModuleType = import_module(
            name="tools.stationer.installer_support",
        )
        name: str = build.project.name
        extra: tuple[tuple[str, str], ...] = (
            (
                ("postinst", support.deb_postinst(name=name)),
                ("post-install", support.apk_post_install(name=name)),
            )
            if build.platform_name == "linux"
            else (("postinstall", support.pkg_postinstall(name=name)),)
        )
        for file_name, content in (
            ("make_pyz.py", support.make_pyz_source()),
            ("ensure-python.sh", support.ensure_python_sh()),
            (f"{name}-pyz", support.shim_sh(name=name, pyz=payload)),
            *extra,
        ):
            staged_file: Path = temporary / file_name
            staged_file.write_text(data=content, encoding="utf-8")
            session.upload(
                source=staged_file,
                destination=f"{remote}/support/{file_name}",
            )

    @staticmethod
    def _collect(
        *,
        session: JobSession,
        temporary: Path,
        remote: str,
        build: PosixBuild,
        layout: _PosixLayout,
    ) -> tuple[Path, Path, str, str]:
        """Download the artifacts and version floors the guest wrote.

        Args:
            session: Active guest session.
            temporary: Local staging directory.
            remote: Unique remote build directory.
            build: Shared guest build request.
            layout: Artifact layout derived from the probe.

        Returns:
            Executable path, installer path, minimum glibc and minimum
            macOS version strings.

        """
        executable_path: Path = (
            ArtifactUtilities.pyinstaller_folder(package=layout.package)
            / layout.executable_name
        )
        installer_path: Path = (
            ArtifactUtilities.installer_folder(package=layout.package)
            / layout.installer_name
        )
        executable_path.parent.mkdir(parents=True, exist_ok=True)
        installer_path.parent.mkdir(parents=True, exist_ok=True)
        session.download(
            source=f"{remote}/out/executable",
            destination=executable_path,
        )
        session.download(
            source=f"{remote}/out/installer",
            destination=installer_path,
        )
        minimum_glibc: str = ""
        minimum_macos: str = ""
        floor: Path
        if build.platform_name == "linux" and layout.abi == "glibc":
            floor = temporary / "glibc"
            session.download(
                source=f"{remote}/out/glibc",
                destination=floor,
            )
            minimum_glibc = floor.read_text(encoding="utf-8").strip()
        if build.platform_name == "macos":
            floor = temporary / "minimum"
            session.download(
                source=f"{remote}/out/minimum",
                destination=floor,
            )
            minimum_macos = floor.read_text(encoding="utf-8").strip()
        if layout.payload is not None:
            pyz_folder: Path = ArtifactUtilities.pyz_folder(
                package=layout.package,
            )
            pyz_folder.mkdir(parents=True, exist_ok=True)
            session.download(
                source=f"{remote}/out/pyz",
                destination=pyz_folder / layout.payload.archive,
            )
        return (
            executable_path,
            installer_path,
            minimum_glibc,
            minimum_macos,
        )


class ReleaseBuilder:
    """Orchestrate selected platform builders."""

    def __init__(
        self,
        *,
        open_host: HostOpener = open_host,
        containers: ContainerOpener = open_container,
        guests: SshOpener = open_ssh,
        progress: ProgressSink | None = None,
    ) -> None:
        """Initialize the release orchestrator.

        Args:
            open_host: Creates the host session of macOS and report commands.
            containers: Creates the container session of a Linux build.
            guests: Creates the guest session of a Windows build.
            progress: Receives progress parsed from container output.

        """
        self._open_host: Final[HostOpener] = open_host
        self._containers: Final[ContainerOpener] = containers
        self._guests: Final[SshOpener] = guests
        self._progress: Final[ProgressSink | None] = progress

    def build(
        self,
        *,
        configuration: BuildConfiguration,
        output: Path,
        platforms: tuple[PlatformName, ...],
        invocation: str = "",
        macos_architectures: tuple[str, ...] = ("native",),
    ) -> tuple[BuildResult, ...]:
        """Build every selected platform sequentially.

        Args:
            configuration: Validated release configuration.
            output: Release output root.
            platforms: Platforms to build.
            invocation: Command prefix shown in ``BUILD.md`` rebuild steps.
            macos_architectures: ``native``, ``arm64`` or ``amd64`` builds
                to make for macOS, one thin executable each.

        Returns:
            Build results in requested order.

        """
        results: list[BuildResult] = []
        for platform_name in platforms:
            results.extend(
                self._platform(
                    platform_name=platform_name,
                    configuration=configuration,
                    output=output,
                    macos_architectures=macos_architectures,
                ),
            )
        _ = BuildReport.write(
            output=output,
            source=BuildSource(
                name=configuration.project.name,
                version=configuration.project.version,
                root=configuration.project.root,
                invocation=invocation or ReportConstants.invocation,
            ),
            open_host=self._open_host,
            results=tuple(results),
        )
        return tuple(results)

    def _platform(
        self,
        *,
        platform_name: PlatformName,
        configuration: BuildConfiguration,
        output: Path,
        macos_architectures: tuple[str, ...],
    ) -> tuple[BuildResult, ...]:
        """Build one platform.

        Args:
            platform_name: Platform to build.
            configuration: Validated release configuration.
            output: Release output root.
            macos_architectures: Architectures to build on macOS.

        Returns:
            One result per architecture built.

        Raises:
            BuildError: If a platform build fails or is not configured.

        """
        project: ProjectConfig = configuration.project
        support: ModuleType = import_module(
            name="tools.stationer.installer_support",
        )

        def payload(*, tag: str) -> PyzPayload | None:
            """Describe the ``.pyz`` bundled into one cell's installer.

            Args:
                tag: Release platform tag.

            Returns:
                The payload, or ``None`` without a ``[pyz]`` table.

            """
            if configuration.pyz is None:
                return None
            pyz_name, bootstrap = configuration.pyz
            return support.pyz_payload(
                pyz_name=pyz_name,
                version=project.version,
                tag=tag,
                bootstrap=bootstrap,
            )

        match platform_name:
            case "macos":
                if configuration.macos.guest is not None:
                    return (
                        PosixQemuBuilder(
                            open_session=self._guests,
                        ).build(
                            build=PosixBuild(
                                project=project,
                                config=configuration.macos.guest,
                                platform_name="macos",
                                macos=configuration.macos,
                                pyz=configuration.pyz,
                            ),
                            output=output,
                        ),
                    )
                return tuple(
                    MacOSBuilder(open_host=self._open_host).build(
                        project=project,
                        config=configuration.macos,
                        output=ArtifactUtilities.folder(
                            output=output,
                            platform_name="macos",
                            architecture=(
                                machine() if arch == "native" else arch
                            ),
                            project=project.name,
                        ),
                        architecture=arch,
                        pyz=payload(
                            tag="macos-"
                            + (
                                ArtifactUtilities.canonical(
                                    architecture=machine()
                                    if arch == "native"
                                    else arch,
                                )
                            ),
                        ),
                    )
                    for arch in macos_architectures
                )
            case "linux":
                linux: LinuxConfig = configuration.linux
                if linux.guest is not None:
                    return (
                        PosixQemuBuilder(
                            open_session=self._guests,
                        ).build(
                            build=PosixBuild(
                                project=project,
                                config=linux.guest,
                                platform_name="linux",
                                linux=linux,
                                pyz=configuration.pyz,
                            ),
                            output=output,
                        ),
                    )
                builder: LinuxDockerBuilder = LinuxDockerBuilder(
                    open_session=self._containers,
                    progress=self._progress,
                )
                images: tuple[str, ...] = tuple(
                    image for image in (linux.image, linux.musl_image) if image
                )
                docker_platforms: tuple[str, ...] = linux.docker_platforms or (
                    linux.docker_platform,
                )
                if "native" not in macos_architectures:
                    wanted: frozenset[str] = frozenset(
                        ArtifactUtilities.canonical(architecture=arch)
                        for arch in macos_architectures
                    )
                    docker_platforms = tuple(
                        platform
                        for platform in docker_platforms
                        if ArtifactUtilities.canonical(
                            architecture=platform.removeprefix("linux/"),
                        )
                        in wanted
                    )
                return tuple(
                    builder.build(
                        project=project,
                        config=replace(
                            linux,
                            image=image,
                            docker_platform=docker_platform,
                        ),
                        output=ArtifactUtilities.folder(
                            output=output,
                            platform_name="linux",
                            architecture=docker_platform.removeprefix(
                                "linux/",
                            ),
                            project=project.name,
                            abi=ArtifactUtilities.abi(
                                platform_name="linux",
                                image=image,
                            ),
                        ),
                        pyz=payload(
                            tag="linux-"
                            + docker_platform.removeprefix("linux/")
                            + "-"
                            + ArtifactUtilities.abi(
                                platform_name="linux",
                                image=image,
                            ),
                        ),
                    )
                    for image in images
                    for docker_platform in docker_platforms
                )
            case "windows":
                windows: WindowsConfig | None = configuration.windows
                if windows is None:
                    msg: str = (
                        "Windows build requested without a [windows] "
                        "configuration."
                    )
                    raise BuildError(msg)
                return (
                    WindowsQemuBuilder(open_session=self._guests).build(
                        project=project,
                        config=windows,
                        output=ArtifactUtilities.folder(
                            output=output,
                            platform_name="windows",
                            architecture="arm64",
                            project=project.name,
                        ),
                        pyz=payload(tag="windows-arm64"),
                    ),
                )


class Cli:
    """Parse command-line arguments and run the stationer."""

    @classmethod
    def main(cls, *, arguments: tuple[str, ...] | None = None) -> int:
        """Run the stationer CLI.

        Args:
            arguments: Optional explicit CLI arguments.

        Returns:
            Process exit status.

        """
        argv: tuple[str, ...] = (
            tuple(sys_argv[1:]) if arguments is None else arguments
        )
        if not argv:
            return cls._configured()
        parser: ArgumentParser = cls._parser()
        namespace: CliNamespace = cast(
            typ="CliNamespace",
            val=parser.parse_args(args=argv),
        )
        if namespace.publish and namespace.output_format == "pyz":
            parser.error(
                message=(
                    "--publish does not apply to --format pyz: archives "
                    "are built per platform and checksummed by CI."
                ),
            )
        if namespace.publish and namespace.output_format == "pypi":
            parser.error(
                message=(
                    "--publish does not apply to --format pypi: upload "
                    "with `python -m tools.stationer.upload`."
                ),
            )
        cls._validate_remote(namespace=namespace, parser=parser)
        project_root: Path = namespace.project
        manifest: Path = namespace.manifest
        output: Path = namespace.output.resolve()
        invocation: str = cls._invocation(namespace=namespace)
        if namespace.remote:
            return cls._remote(
                namespace=namespace,
                output=output,
                invocation=invocation,
            )
        if namespace.output_format == "pyz":
            return cls._pyz(
                namespace=namespace,
                output=output,
                invocation=invocation,
            )
        if namespace.output_format == "pypi":
            return cls._pypi(
                manifest=manifest,
                project_root=project_root,
                output=output,
            )
        selected: list[str] | None = namespace.platforms
        platforms: tuple[PlatformName, ...] = cast(
            typ="tuple[PlatformName, ...]",
            val=tuple(selected or ("macos", "linux", "windows")),
        )
        configuration: BuildConfiguration = ManifestLoader.load(
            manifest=manifest,
            project_root=project_root,
            platforms=platforms,
        )
        display: ProgressDisplay = ProgressDisplay()
        _ = ReleaseBuilder(progress=display.update).build(
            configuration=configuration,
            output=output,
            platforms=platforms,
            invocation=invocation,
            macos_architectures=cls._macos_architectures(
                selection=namespace.arch,
            ),
        )
        if namespace.publish:
            return cls._publish(
                namespace=namespace,
                output=output,
                version=configuration.project.version,
            )
        return 0

    @classmethod
    def _configured(cls) -> int:
        """Run the full pipeline described by ``[tool.stationer.builder]``.

        A bare ``python tools/stationer`` reads the table from the
        nearest ``pyproject.toml`` and runs every listed format, then
        publishes the GitHub release (synchronizing ``wiki/`` when present)
        and uploads the distributions to PyPI when configured.

        Returns:
            Process exit status.

        """
        try:
            defaults: BuilderDefaults | None = BuilderDefaults.load(
                start=Path.cwd(),
            )
        except BuildError as error:
            sys_stderr.write(f"error: {error}\n")
            return 2
        if defaults is None:
            sys_stderr.write(
                "error: no arguments given and no [tool.stationer.builder] "
                "table found in pyproject.toml\n",
            )
            return 2
        try:
            configuration: BuildConfiguration = ManifestLoader.load(
                manifest=defaults.manifest,
                project_root=defaults.project,
                platforms=(
                    defaults.platforms
                    if "native" in defaults.formats
                    else ("linux",)
                ),
            )
        except BuildError as error:
            sys_stderr.write(f"error: {error}\n")
            return 2
        status: int = cls._run_configured(defaults=defaults)
        if status != 0:
            return status
        if defaults.publish is not None:
            return cls._publish_configured(
                defaults=defaults,
                version=configuration.project.version,
            )
        return 0

    @classmethod
    def _run_configured(cls, *, defaults: BuilderDefaults) -> int:
        """Run every configured format, then every remote build.

        Args:
            defaults: Parsed ``[tool.stationer.builder]`` table.

        Returns:
            Zero, or the first failing build's exit status.

        """
        for output_format in defaults.formats:
            status: int = cls.main(
                arguments=cls._format_argv(
                    defaults=defaults,
                    output_format=output_format,
                ),
            )
            if status != 0:
                return status
        for target in defaults.remote:
            if defaults.dry_run:
                cls._print_line(
                    message=f"dry run: skipping the remote {target} build",
                )
                continue
            architectures: tuple[str, ...] = (
                ("arm64", "amd64") if target == "windows" else ("native",)
            )
            for architecture in architectures:
                status = cls.main(
                    arguments=cls._remote_argv(
                        defaults=defaults,
                        target=target,
                        arch=architecture,
                    ),
                )
                if status != 0:
                    return status
        return 0

    @classmethod
    def _remote(
        cls,
        *,
        namespace: CliNamespace,
        output: Path,
        invocation: str,
    ) -> int:
        """Build on a remote pull worker and record the result.

        Args:
            namespace: Parsed arguments.
            output: Build output directory.
            invocation: Command prefix shown in the rebuild section.

        Returns:
            Process exit status.

        """
        if namespace.remote is None:
            sys_stderr.write("error: --remote requires a target platform\n")
            return 2
        target: str = namespace.remote
        # remote_builder imports this module, so it is loaded lazily.
        module: ModuleType = import_module(
            name="tools.stationer.remote_builder",
        )
        support: ModuleType = import_module(
            name="tools.stationer.installer_support",
        )
        architectures: tuple[str, ...] = (
            ("arm64", "amd64")
            if namespace.arch == "all"
            else (namespace.arch or "native",)
        )
        try:
            job_settings: JobSettings | None = cls._job_settings(
                namespace=namespace,
            )
            configuration: BuildConfiguration = ManifestLoader.load(
                manifest=namespace.manifest,
                project_root=namespace.project,
                platforms=("linux",),
            )
            base: RemoteSettings = cls._overlay(
                base=module.RemoteSettings.load(manifest=namespace.manifest),
                namespace=namespace,
            )
            results: list[BuildResult] = []
            display: ProgressDisplay = ProgressDisplay()
            builder: RemoteBuilder = module.RemoteBuilder(
                say=display.line,
                job_settings=job_settings,
                progress=display.update,
            )
            for architecture in architectures:
                settings: RemoteSettings = replace(
                    base,
                    architecture=architecture,
                )
                settings.validate()
                pyz: PyzPayload | None = cls._pyz_payload(
                    namespace=namespace,
                    configuration=configuration,
                    target=target,
                    architecture=architecture,
                    support=support,
                )
                results.append(
                    builder.build(
                        project=configuration.project,
                        settings=settings,
                        target=target,
                        output=output,
                        packaging=module.RemotePackaging(
                            maintainer=configuration.linux.maintainer,
                            description=configuration.linux.description,
                            publisher=(
                                configuration.windows.publisher
                                if configuration.windows is not None
                                else configuration.project.name
                            ),
                            pyz=pyz,
                        ),
                    ),
                )
        except (BuildError, ValueError) as error:
            sys_stderr.write(f"error: {error}\n")
            return 1
        _ = BuildReport.write(
            output=output,
            source=BuildSource(
                name=configuration.project.name,
                version=configuration.project.version,
                root=configuration.project.root,
                invocation=invocation,
            ),
            results=tuple(results),
        )
        if namespace.publish:
            return cls._publish(
                namespace=namespace,
                output=output,
                version=configuration.project.version,
            )
        return 0

    @staticmethod
    def _validate_remote(
        *,
        namespace: CliNamespace,
        parser: ArgumentParser,
    ) -> None:
        """Check that the remote-mode flags can be combined.

        Args:
            namespace: Parsed arguments.
            parser: Parser used to report errors.

        """
        if namespace.remote and (
            namespace.output_format != "native" or namespace.platforms
        ):
            parser.error(
                message="--remote is its own mode; drop --format/--platform.",
            )
        vm_options: bool = (
            namespace.vm is not None
            or namespace.attach
            or namespace.vm_user is not None
            or namespace.vm_identity is not None
            or namespace.vm_port is not None
        )
        if vm_options and namespace.remote != "windows":
            parser.error(message="--vm options need --remote windows.")
        if namespace.local and namespace.remote is None:
            parser.error(message="--local needs --remote TARGET.")
        if namespace.local and vm_options:
            parser.error(message="--local does not use --vm options.")

    @staticmethod
    def _macos_architectures(*, selection: str | None) -> tuple[str, ...]:
        """Expand ``--arch`` into the macOS builds to make.

        Args:
            selection: The ``--arch`` value, or ``None`` when omitted.

        Returns:
            ``native`` by default, both architectures for ``all``, or the
            one requested.

        """
        if selection is None or selection == "native":
            return ("native",)
        if selection == "all":
            return ("arm64", "amd64")
        return (selection,)

    @staticmethod
    def _overlay(
        *,
        base: RemoteSettings,
        namespace: CliNamespace,
    ) -> RemoteSettings:
        """Apply the command-line overrides to ``[remote]`` settings.

        Args:
            base: Settings loaded from the manifest.
            namespace: Parsed arguments.

        Returns:
            Settings overlaid with every option the command line set.

        """
        if namespace.bind is not None:
            base = replace(base, bind=namespace.bind)
        if namespace.port is not None:
            base = replace(base, port=namespace.port)
        if namespace.worker_host is not None:
            base = replace(base, worker_host=namespace.worker_host)
        if namespace.expose is not None:
            base = replace(base, expose=namespace.expose)
        if namespace.short is not None:
            base = replace(base, short=namespace.short)
        if namespace.vm is not None:
            base = replace(base, vm=namespace.vm)
        if namespace.attach:
            base = replace(base, vm_attach=True)
        if namespace.local:
            base = replace(
                base,
                local=True,
                bind="127.0.0.1",
                worker_host="127.0.0.1",
                expose="direct",
                short=None,
                port=namespace.port if namespace.port is not None else 0,
            )
        return base

    @staticmethod
    def _job_settings(*, namespace: CliNamespace) -> JobSettings | None:
        """Resolve the ``conjure`` settings when a VM is involved.

        Layered as ``[tool.conjure]`` in ``pyproject.toml``, then the SSH
        options given on the command line.

        Args:
            namespace: Parsed arguments.

        Returns:
            The settings, or ``None`` when no VM option was given (the
            builder then loads them itself if the manifest selects a VM).

        """
        overrides: dict[str, str | int | Path] = {}
        if namespace.vm_user is not None:
            overrides["ssh_username"] = namespace.vm_user
        if namespace.vm_identity is not None:
            overrides["ssh_identity_file"] = (
                namespace.vm_identity.expanduser().resolve()
            )
        if namespace.vm_port is not None:
            overrides["ssh_port"] = namespace.vm_port
        if not overrides and namespace.vm is None and not namespace.attach:
            return None
        return SettingsLoader.load(overrides=overrides)

    @staticmethod
    def _parser() -> ArgumentParser:
        """Build the argument parser.

        Returns:
            The configured parser.

        """
        parser: ArgumentParser = ArgumentParser(
            description=(
                "Build macOS PKG, Linux DEB, and Windows ARM64 setup EXE "
                "artifacts for a Python project, report them in "
                "BUILD.md, and optionally attach them to a release."
            ),
        )
        parser.add_argument("--project", required=True, type=Path)
        parser.add_argument("--manifest", required=True, type=Path)
        parser.add_argument(
            "--output",
            type=Path,
            default=Path("build"),
            help="Output directory (default: ./build).",
        )
        parser.add_argument(
            "--platform",
            action="append",
            choices=("macos", "linux", "windows"),
            dest="platforms",
        )
        parser.add_argument(
            "--format",
            choices=("native", "pyz", "pypi"),
            default="native",
            dest="output_format",
        )
        parser.add_argument(
            "--publish",
            metavar="PROJECT",
            help=(
                "After building, create the GitHub release for PROJECT "
                "(a release.toml project) or add the builds to it; also "
                "syncs wiki/ to the GitHub Wiki when the folder exists."
            ),
        )
        parser.add_argument(
            "--release-config",
            type=Path,
            default=Path("release.toml"),
            help="stationer configuration (default: ./release.toml).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="With --publish, plan the release without running it.",
        )
        parser.add_argument(
            "--remote",
            choices=("windows", "linux", "macos"),
            help=(
                "Build on a remote native worker that pulls the job: prints "
                "one command to run on a machine of that operating system."
            ),
        )
        parser.add_argument("--bind", help="Remote: interface to listen on.")
        parser.add_argument(
            "--port",
            type=int,
            help="Remote: port to listen on (0 picks a free one).",
        )
        parser.add_argument(
            "--worker-host",
            help="Remote: address the worker uses to reach this host.",
        )
        parser.add_argument(
            "--expose",
            choices=tuple(sorted(ExposureConstants.backends)),
            help="Remote: charter that makes the job server public.",
        )
        parser.add_argument(
            "--short",
            nargs="?",
            const="",
            metavar="ALIAS",
            help="Remote: shorten the bootstrap URL (optional alias).",
        )
        parser.add_argument(
            "--arch",
            choices=("native", "arm64", "amd64", "all"),
            help=(
                "CPU to build for (default: native). macOS: arm64 and/or "
                "amd64 thin builds, amd64 under Rosetta with python_amd64 "
                "from [macos]; all builds both. Remote windows: an ARM64 "
                "guest can build amd64 through x64 emulation."
            ),
        )
        parser.add_argument(
            "--vm",
            nargs="?",
            const="",
            metavar="NAME",
            help=(
                "Remote windows: run the worker in a VM through conjure "
                "and start it over SSH. NAME is a registered VM; without a "
                "name, the VM defined in [tool.conjure] is used."
            ),
        )
        parser.add_argument(
            "--attach",
            action="store_true",
            help=(
                "Remote windows: use the already-running guest that "
                "[tool.conjure] describes (ssh user, key and port)."
            ),
        )
        parser.add_argument(
            "--local",
            action="store_true",
            help=(
                "Remote: run the worker on this machine over the loopback "
                "job server (the target must match the host OS)."
            ),
        )
        parser.add_argument("--vm-user", help="Remote VM: SSH user.")
        parser.add_argument(
            "--vm-identity",
            type=Path,
            help="Remote VM: SSH private key.",
        )
        parser.add_argument(
            "--vm-port",
            type=int,
            help="Remote VM: host loopback port of its SSH server.",
        )
        return parser

    @staticmethod
    def _invocation(*, namespace: CliNamespace) -> str:
        """Compose the rebuild command prefix for the report.

        Args:
            namespace: Parsed arguments.

        Returns:
            Command prefix, including remote options when they were used.

        """
        invocation: str = (
            f"python -m tools.stationer --project {namespace.project} "
            f"--manifest {namespace.manifest}"
        )
        if namespace.remote:
            invocation += (
                f" --remote {namespace.remote}"
                + (f" --vm {namespace.vm}" if namespace.vm else "")
                + (" --attach" if namespace.attach else "")
                + (" --local" if namespace.local else "")
                + (
                    f" --arch {namespace.arch}"
                    if namespace.arch is not None
                    else ""
                )
            )
        return invocation

    @staticmethod
    def _print_line(message: str) -> None:
        """Print one progress line to standard output.

        Args:
            message: Text to print.

        """
        _ = sys_stdout.write(f"{message}\n")

    @staticmethod
    def _publish(
        *,
        namespace: CliNamespace,
        output: Path,
        version: str,
    ) -> int:
        """Attach the build to a GitHub release through ``stationer``.

        Args:
            namespace: Parsed arguments.
            output: Build output directory.
            version: Version that was built.

        Returns:
            Process exit status.

        """
        # publisher imports this module, so it is loaded lazily.
        module: ModuleType = import_module(
            name="tools.stationer.publisher",
        )
        try:
            _ = module.ReleasePublisher(
                config_path=namespace.release_config,
            ).publish(
                output=output,
                project=namespace.publish,
                version=version,
                dry_run=namespace.dry_run,
            )
        except BuildError as error:
            sys_stderr.write(f"error: {error}\n")
            return 1
        return 0

    @staticmethod
    def _pyz(
        *,
        namespace: CliNamespace,
        output: Path,
        invocation: str,
    ) -> int:
        """Build a self-contained ``.pyz`` for every selected matrix cell.

        Args:
            namespace: Parsed arguments; ``--platform``/``--arch`` narrow
                the covered cells.
            output: Directory that receives the archive.
            invocation: Command prefix shown in the rebuild section.

        Returns:
            Process exit status.

        """
        # pyz_builder imports this module, so it is loaded lazily.
        module: ModuleType = import_module(
            name="tools.stationer.pyz_builder",
        )
        configuration: BuildConfiguration = ManifestLoader.load(
            manifest=namespace.manifest,
            project_root=namespace.project,
            platforms=("linux",),
        )
        config: PyzConfig = module.PyzConfig.load(
            manifest=namespace.manifest,
            project_root=namespace.project,
            default_name=configuration.project.name,
        )
        builder: PyzBuilder = module.PyzBuilder()
        for cell in module.PyzTarget.cells(
            platforms=tuple(namespace.platforms or ()),
            architecture=namespace.arch,
        ):
            archive: Path = builder.build(
                project=configuration.project,
                config=config,
                target=cell,
                output=ArtifactUtilities.pyz_folder(
                    package=ArtifactUtilities.folder(
                        output=output,
                        platform_name=cell.platform_name,
                        architecture=cell.architecture,
                        abi=cell.abi,
                        project=configuration.project.name,
                    ),
                ),
            )
            sys_stdout.write(f"{archive}\n")
        _ = BuildReport.write(
            output=output,
            source=BuildSource(
                name=configuration.project.name,
                version=configuration.project.version,
                root=configuration.project.root,
                invocation=invocation,
            ),
        )
        return 0

    @staticmethod
    def _pyz_payload(
        *,
        namespace: CliNamespace,
        configuration: BuildConfiguration,
        target: str,
        architecture: str,
        support: ModuleType,
    ) -> PyzPayload | None:
        """Describe the ``.pyz`` a remote worker builds for one cell.

        Args:
            namespace: Parsed arguments.
            configuration: Validated release configuration.
            target: Operating system the worker builds for.
            architecture: CPU architecture of the cell.
            support: The lazily-imported ``installer_support`` module.

        Returns:
            The payload description, or ``None`` when the manifest has no
            usable ``[pyz]`` table.

        """
        module: ModuleType = import_module(
            name="tools.stationer.pyz_builder",
        )
        try:
            config: PyzConfig = module.PyzConfig.load(
                manifest=namespace.manifest,
                project_root=namespace.project,
                default_name=configuration.project.name,
            )
        except BuildError:
            return None
        return support.pyz_payload(
            pyz_name=config.name,
            version=configuration.project.version,
            tag=(
                f"{target}-{architecture}"
                + ("-glibc" if target == "linux" else "")
            ),
            bootstrap=config.bootstrap.relative_to(
                configuration.project.root.resolve(),
            ).as_posix(),
        )

    @staticmethod
    def _pypi(
        *,
        manifest: Path,
        project_root: Path,
        output: Path,
    ) -> int:
        """Build PyPI distributions for the project and its dependencies.

        Args:
            manifest: TOML manifest path.
            project_root: Python project root.
            output: Directory that receives the sdist/wheel files.

        Returns:
            Process exit status.

        """
        # pypi_builder imports this module, so it is loaded lazily.
        module: ModuleType = import_module(
            name="tools.stationer.pypi_builder",
        )
        configuration: BuildConfiguration = ManifestLoader.load(
            manifest=manifest,
            project_root=project_root,
            platforms=("linux",),
        )
        _ = module.PypiBuilder().build(
            project=configuration.project,
            output=output / "pypi",
        )
        return 0

    @staticmethod
    def _format_argv(
        *,
        defaults: BuilderDefaults,
        output_format: str,
    ) -> tuple[str, ...]:
        """Build the command line for one configured format.

        Args:
            defaults: Parsed ``[tool.stationer.builder]`` table.
            output_format: Format to build.

        Returns:
            Arguments for :meth:`Cli.main`.

        """
        argv: list[str] = [
            "--project",
            str(object=defaults.project),
            "--manifest",
            str(object=defaults.manifest),
            "--output",
            str(object=defaults.output),
            "--format",
            output_format,
        ]
        if output_format == "native":
            for platform in defaults.platforms:
                argv.extend(("--platform", platform))
            if defaults.arch is not None:
                argv.extend(("--arch", defaults.arch))
        if defaults.dry_run:
            argv.append("--dry-run")
        return tuple(argv)

    @staticmethod
    def _remote_argv(
        *,
        defaults: BuilderDefaults,
        target: str,
        arch: str,
    ) -> tuple[str, ...]:
        """Build the command line for one configured remote build.

        Args:
            defaults: Parsed ``[tool.stationer.builder]`` table.
            target: Operating system the remote worker builds for.
            arch: CPU the worker builds for; ``native`` asks the worker.

        Returns:
            Arguments for :meth:`Cli.main`.

        """
        argv: list[str] = [
            "--project",
            str(object=defaults.project),
            "--manifest",
            str(object=defaults.manifest),
            "--output",
            str(object=defaults.output),
            "--remote",
            target,
        ]
        if arch != "native":
            argv.extend(("--arch", arch))
        if defaults.vm is not None:
            argv.append(f"--vm={defaults.vm}")
        if defaults.attach:
            argv.append("--attach")
        return tuple(argv)

    @staticmethod
    def _publish_configured(
        *,
        defaults: BuilderDefaults,
        version: str,
    ) -> int:
        """Publish the GitHub release, wiki, and PyPI distributions.

        Args:
            defaults: Parsed ``[tool.stationer.builder]`` table.
            version: Version that was built.

        Returns:
            Process exit status.

        """
        # publisher imports this module, so it is loaded lazily.
        module: ModuleType = import_module(
            name="tools.stationer.publisher",
        )
        try:
            _ = module.ReleasePublisher(
                config_path=defaults.release_config,
            ).publish(
                output=defaults.output,
                project=cast(typ="str", val=defaults.publish),
                version=version,
                dry_run=defaults.dry_run,
            )
        except BuildError as error:
            sys_stderr.write(f"error: {error}\n")
            return 1
        distributions: Path = defaults.output / "pypi"
        if defaults.pypi_upload and distributions.is_dir():
            host: JobSession = open_host()
            try:
                host.start()
                _ = host.run(
                    command=(
                        executable,
                        "-m",
                        "tools.stationer.upload",
                        str(object=distributions),
                    ),
                    check=True,
                    timeout_seconds=HostConstants.timeout_seconds,
                )
            except QemuRunnerError as error:
                sys_stderr.write(f"error: {job_failure(error=error)}\n")
                return 1
            finally:
                host.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(Cli.main())
