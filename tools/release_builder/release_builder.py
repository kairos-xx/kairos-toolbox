"""Build native Python installers for macOS, Linux, and Windows ARM64."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from dataclasses import dataclass
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from platform import machine, system
from shlex import quote
from shutil import copy2, copytree, ignore_patterns, rmtree
from subprocess import CompletedProcess, run
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
)
from zipfile import ZIP_DEFLATED, ZipFile

from tools.release_builder.build_report import (
    BuildReport,
    BuildSource,
    ReportConstants,
)
from tools.release_publish.versioning import read_version

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType
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


class LinuxTable(TypedDict, total=False):
    """Describe accepted Linux configuration keys.

    Attributes:
        docker_platform: Docker ``--platform`` value.
        image: Docker base image.
        maintainer: Debian package maintainer.
        description: Debian package description.

    """

    docker_platform: str
    image: str
    maintainer: str
    description: str


class MacOSTable(TypedDict, total=False):
    """Describe accepted macOS configuration keys.

    Attributes:
        python: Python interpreter used for the build environment.
        signing_identity: Optional Installer signing identity.

    """

    python: str
    signing_identity: str


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
        release_config: ``release_publish`` configuration path.
        dry_run: Plan the release without running it.

    """

    project: Path
    manifest: Path
    output: Path
    platforms: list[str] | None
    output_format: str
    publish: str | None
    release_config: Path
    dry_run: bool


type JsonValue = (
    str | int | float | bool | list[JsonValue] | dict[str, JsonValue]
)


@dataclass(frozen=True, slots=True, kw_only=True)
class BuilderDefaults:
    """Provide defaults for a bare ``python tools/release_builder`` run.

    Attributes:
        project: Python project root to package.
        manifest: TOML build manifest path.
        output: Release output root.
        release_config: ``release_publish`` configuration path.
        platforms: Platform names built by the ``native`` format.
        formats: Output formats to run in order.
        publish: Release project to publish to, or ``None``.
        pypi_upload: Whether to upload the built distributions to PyPI.
        dry_run: Plan the release and upload without running them.

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

    @classmethod
    def load(cls, *, start: Path) -> BuilderDefaults | None:
        """Read ``[tool.release_builder]`` from the nearest pyproject.

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
            raw: object = cast(
                typ="dict[str, object]",
                val=tool,
            ).get("release_builder")
            if raw is None:
                continue
            if not isinstance(raw, dict):
                msg: str = "[tool.release_builder] must be a table"
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
            table: Loaded ``[tool.release_builder]`` table.
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
            },
        )
        unknown: set[str] = set(table) - known
        if unknown:
            msg: str = (
                f"[tool.release_builder] unknown keys: {sorted(unknown)}"
            )
            raise BuildError(msg)
        publish: JsonValue | None = table.get("publish")
        if publish is not None and not isinstance(publish, str):
            msg = "[tool.release_builder] publish must be a string"
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
            msg: str = f"[tool.release_builder] needs a string {key}"
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
                f"[tool.release_builder] {key} must be a list from "
                f"{sorted(allowed)}"
            )
            raise BuildError(msg)
        entries: list[object] = cast(typ="list[object]", val=value)
        items: list[str] = [item for item in entries if isinstance(item, str)]
        if len(items) != len(entries) or not set(items) <= allowed:
            msg = (
                f"[tool.release_builder] {key} must be a list from "
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
            msg: str = f"[tool.release_builder] {key} must be a boolean"
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
        docker_platform: Docker ``--platform`` value.
        image: Docker base image.
        maintainer: Debian package maintainer.
        description: Debian package description.

    """

    docker_platform: str
    image: str
    maintainer: str
    description: str


@dataclass(frozen=True, slots=True, kw_only=True)
class MacOSConfig:
    """Hold native macOS build configuration.

    Attributes:
        python: Python interpreter used for the build environment.
        signing_identity: Optional Installer signing identity.

    """

    python: str
    signing_identity: str | None


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
        installer: Native installer package.
        executable_sha256: SHA-256 digest of the executable.
        installer_sha256: SHA-256 digest of the installer.
        duration_seconds: Wall-clock build duration.

    """

    platform: PlatformName
    architecture: str
    executable: Path
    installer: Path
    executable_sha256: str
    installer_sha256: str
    duration_seconds: float


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildConfiguration:
    """Aggregate validated platform configuration.

    Attributes:
        project: Platform-independent project configuration.
        linux: Linux Docker build configuration.
        macos: macOS build configuration.
        windows: Windows configuration, or ``None`` when not selected.

    """

    project: ProjectConfig
    linux: LinuxConfig
    macos: MacOSConfig
    windows: WindowsConfig | None


class BuildError(RuntimeError):
    """Report a failed external build operation."""


class Runner(Protocol):
    """Command execution boundary consumed by the builders."""

    def execute(
        self,
        *,
        arguments: tuple[str, ...],
        cwd: Path | None = None,
        timeout_seconds: int = 1800,
    ) -> CompletedProcess[str]:
        """Execute one command and return its captured result.

        Args:
            arguments: Command and arguments.
            cwd: Optional working directory.
            timeout_seconds: Maximum command duration.

        Returns:
            Completed command result.

        Raises:
            BuildError: If the command exits unsuccessfully.

        """
        ...


class CommandRunner:
    """Execute subprocesses with deterministic error reporting."""

    @staticmethod
    def execute(
        *,
        arguments: tuple[str, ...],
        cwd: Path | None = None,
        timeout_seconds: int = 1800,
    ) -> CompletedProcess[str]:
        """Run one command and return its captured result.

        Args:
            arguments: Command and arguments without shell interpolation.
            cwd: Optional working directory.
            timeout_seconds: Maximum command duration.

        Returns:
            Completed command result.

        Raises:
            BuildError: If the command exits unsuccessfully.

        """
        result: CompletedProcess[str] = run(
            args=arguments,
            cwd=cwd,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
        if result.returncode == 0:
            return result
        msg: str = (
            "Command failed with exit code "
            f"{result.returncode}: {arguments!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        raise BuildError(
            msg,
        )


class ManifestLoader:
    """Load and validate the release builder manifest."""

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
                    "C:/release-builder",
                ),
                publisher=windows_table.get("publisher", "Unknown"),
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
                docker_platform=linux_table.get(
                    "docker_platform",
                    "linux/arm64",
                ),
                image=linux_table.get("image", "python:3.13-slim-bookworm"),
                maintainer=linux_table.get("maintainer", "Unknown"),
                description=linux_table.get(
                    "description",
                    f"{name} {version}",
                ),
            ),
            macos=MacOSConfig(
                python=macos_table.get("python", executable),
                signing_identity=macos_table.get("signing_identity"),
            ),
            windows=windows,
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


class SourceArchive:
    """Create a bounded source archive for remote builds."""

    _EXCLUDED_PARTS: Final[frozenset[str]] = frozenset(
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
                part in cls._EXCLUDED_PARTS for part in relative.parts
            ):
                selected.append(path)
        return tuple(selected)


class SourceStaging:
    """Copy a project and its local dependencies to a scratch directory.

    ``pip`` builds in-tree and would otherwise leave ``build/`` and
    ``*.egg-info`` directories inside the user's checkout.
    """

    _IGNORED: Final[tuple[str, ...]] = (
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
            *cls._IGNORED,
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
        installer_path: Path,
        started_at: float,
    ) -> BuildResult:
        """Create a verified result record.

        Args:
            platform_name: Target operating system.
            architecture: Verified or requested target architecture.
            executable_path: Frozen application executable.
            installer_path: Native installer package.
            started_at: Monotonic build start timestamp.

        Returns:
            Completed build result.

        Raises:
            BuildError: If an expected artifact is missing.

        """
        if not executable_path.is_file() or not installer_path.is_file():
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
            installer_sha256=cls.digest(path=installer_path),
            duration_seconds=monotonic() - started_at,
        )

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


class MacOSBuilder:
    """Build a native macOS PyInstaller executable and PKG installer."""

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
        config: MacOSConfig,
        output: Path,
    ) -> BuildResult:
        """Build the macOS release artifacts.

        Args:
            project: Project configuration.
            config: macOS configuration.
            output: Platform output directory.

        Returns:
            Verified macOS build result.

        Raises:
            BuildError: If not running on macOS or a build step fails.

        """
        if system() != "Darwin":
            msg: str = "The macOS backend must run on macOS."
            raise BuildError(msg)
        started_at: float = monotonic()
        work: Path = output / ".work"
        venv: Path = work / "venv"
        dist: Path = work / "dist"
        stage: Path = work / "pkg-root" / "usr" / "local" / "bin"
        rmtree(path=work, ignore_errors=True)
        output.mkdir(parents=True, exist_ok=True)
        self._runner.execute(
            arguments=(config.python, "-m", "venv", str(object=venv)),
        )
        python_path: Path = venv / "bin" / "python"
        dependencies, source = SourceStaging.stage(
            project=project,
            directory=work / "staged",
        )
        self._runner.execute(
            arguments=(
                str(object=python_path),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "pyinstaller",
                *project.pip_args,
                *(str(object=path) for path in dependencies),
                str(object=source),
            ),
        )
        self._runner.execute(
            arguments=BuildCommands.pyinstaller(
                python_path=python_path,
                project=project,
                dist=dist,
                work=work / "pyinstaller-work",
                spec=work / "spec",
            ),
            cwd=project.root,
        )
        frozen: Path = dist / project.name
        executable_path: Path = output / (
            f"{project.name}-{project.version}-macos-{machine()}"
        )
        copy2(src=frozen, dst=executable_path)
        stage.mkdir(parents=True, exist_ok=True)
        copy2(src=frozen, dst=stage / project.name)
        installer_path: Path = output / (
            f"{project.name}-{project.version}-macos-{machine()}.pkg"
        )
        package_arguments: tuple[str, ...] = (
            "pkgbuild",
            "--root",
            str(object=work / "pkg-root"),
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
            str(object=installer_path),
        )
        self._runner.execute(arguments=package_arguments)
        return ArtifactUtilities.result(
            platform_name="macos",
            architecture=machine(),
            executable_path=executable_path,
            installer_path=installer_path,
            started_at=started_at,
        )


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


class LinuxDockerBuilder:
    """Build a Linux executable and DEB inside Docker."""

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
        config: LinuxConfig,
        output: Path,
    ) -> BuildResult:
        """Build Linux artifacts inside Docker.

        Args:
            project: Project configuration.
            config: Linux Docker configuration.
            output: Platform output directory.

        Returns:
            Verified Linux build result.

        """
        started_at: float = monotonic()
        output.mkdir(parents=True, exist_ok=True)
        script: str = LinuxDockerBuilder._script(
            project=project,
            config=config,
        )
        self._runner.execute(
            arguments=(
                "docker",
                "run",
                "--rm",
                "--platform",
                config.docker_platform,
                "--volume",
                f"{project.root}:/src:ro",
                *(
                    argument
                    for dependency in project.local_dependencies
                    for argument in (
                        "--volume",
                        f"{dependency}:/deps/{dependency.name}:ro",
                    )
                ),
                "--volume",
                f"{output.resolve()}:/out",
                "--workdir",
                "/work",
                config.image,
                "sh",
                "-eu",
                "-c",
                script,
            ),
            timeout_seconds=3600,
        )
        architecture: str = config.docker_platform.removeprefix("linux/")
        executable_path: Path = output / (
            f"{project.name}-{project.version}-linux-{architecture}"
        )
        installer_path: Path = output / (
            f"{project.name}_{project.version}_{architecture}.deb"
        )
        return ArtifactUtilities.result(
            platform_name="linux",
            architecture=architecture,
            executable_path=executable_path,
            installer_path=installer_path,
            started_at=started_at,
        )

    @staticmethod
    def _script(*, project: ProjectConfig, config: LinuxConfig) -> str:
        """Create the fixed Docker worker script.

        Args:
            project: Project configuration.
            config: Linux Docker configuration.

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
        # Mounts are read-only but pip builds in-tree, so work on copies.
        copies: str = "\n".join(
            f"cp -a /deps/{quote(s=dependency.name)} "
            f"/work/deps/{quote(s=dependency.name)}"
            for dependency in project.local_dependencies
        )
        installs: str = " ".join(
            f"/work/deps/{quote(s=dependency.name)}"
            for dependency in project.local_dependencies
        )
        return f"""
apt-get update
apt-get install -y --no-install-recommends binutils dpkg-dev
mkdir -p /work/deps
cp -a /src /work/src
{copies}
python -m venv /work/venv
/work/venv/bin/python -m pip install \
  --disable-pip-version-check pyinstaller {pip_extra} {installs} /work/src
/work/venv/bin/python -m PyInstaller \
  --noconfirm --clean --onefile {console} --name {name} \
  --distpath /work/dist --workpath /work/build --specpath /work/spec \
  {extra} /work/src/{entrypoint}
cp /work/dist/{name} \
  /out/{name}-{version}-linux-{architecture}
mkdir -p /work/pkg/DEBIAN /work/pkg/usr/local/bin
cp /work/dist/{name} /work/pkg/usr/local/bin/{name}
printf 'Package: %s\\nVersion: %s\\nArchitecture: %s\\n' \
  {name} {version} {architecture} > /work/pkg/DEBIAN/control
printf 'Maintainer: %s\\nDescription: %s\\n' \
  {maintainer} {description} >> /work/pkg/DEBIAN/control
dpkg-deb --build --root-owner-group /work/pkg \
  /out/{name}_{version}_{architecture}.deb
""".strip()


class WindowsQemuBuilder:
    """Build Windows ARM64 artifacts through an SSH-accessible QEMU guest."""

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
        config: WindowsConfig,
        output: Path,
    ) -> BuildResult:
        """Build Windows ARM64 executable and setup executable.

        Args:
            project: Project configuration.
            config: Windows guest configuration.
            output: Platform output directory.

        Returns:
            Verified Windows build result.

        """
        started_at: float = monotonic()
        output.mkdir(parents=True, exist_ok=True)
        temporary_text: str
        with TemporaryDirectory(prefix="release-builder-") as temporary_text:
            temporary: Path = Path(temporary_text)
            source_zip: Path = temporary / "source.zip"
            worker: Path = temporary / "build.ps1"
            SourceArchive.create(project=project, destination=source_zip)
            build_id: str = ArtifactUtilities.digest(path=source_zip)[:16]
            remote: str = f"{config.remote_root.rstrip('/')}/{build_id}"
            worker.write_text(
                data=WindowsQemuBuilder._worker_script(
                    project=project,
                    config=config,
                    remote=remote,
                ),
                encoding="utf-8",
            )
            ssh: tuple[str, ...] = self._ssh_base(config=config)
            target: str = f"{config.user}@{config.host}"
            self._runner.execute(
                arguments=(
                    *ssh,
                    target,
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    (
                        "Remove-Item -LiteralPath "
                        f"'{remote}' -Recurse -Force "
                        "-ErrorAction SilentlyContinue; "
                        "New-Item -ItemType Directory -Force -Path "
                        f"'{remote}' | Out-Null"
                    ),
                ),
            )
            scp_base: tuple[str, ...] = (
                "scp",
                "-P",
                str(object=config.port),
                "-i",
                str(object=config.identity_file),
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
            )
            self._runner.execute(
                arguments=(
                    *scp_base,
                    str(object=source_zip),
                    f"{target}:{remote}/source.zip",
                ),
            )
            self._runner.execute(
                arguments=(
                    *scp_base,
                    str(object=worker),
                    f"{target}:{remote}/build.ps1",
                ),
            )
            self._runner.execute(
                arguments=(
                    *ssh,
                    target,
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    f"{remote}/build.ps1",
                ),
                timeout_seconds=3600,
            )
            executable_path: Path = output / (
                f"{project.name}-{project.version}-windows-arm64.exe"
            )
            installer_path: Path = output / (
                f"{project.name}-{project.version}-windows-arm64-setup.exe"
            )
            self._runner.execute(
                arguments=(
                    *scp_base,
                    f"{target}:{remote}/out/{project.name}.exe",
                    str(object=executable_path),
                ),
            )
            self._runner.execute(
                arguments=(
                    *scp_base,
                    f"{target}:{remote}/out/setup.exe",
                    str(object=installer_path),
                ),
            )
            self._runner.execute(
                arguments=(
                    *ssh,
                    target,
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    f"Remove-Item -LiteralPath '{remote}' -Recurse -Force",
                ),
            )
        return ArtifactUtilities.result(
            platform_name="windows",
            architecture="arm64",
            executable_path=executable_path,
            installer_path=installer_path,
            started_at=started_at,
        )

    @classmethod
    def _worker_script(
        cls,
        *,
        project: ProjectConfig,
        config: WindowsConfig,
        remote: str,
    ) -> str:
        """Generate the controlled Windows build worker.

        Args:
            project: Project configuration.
            config: Windows configuration.
            remote: Unique remote build directory.

        Returns:
            PowerShell worker source.

        """
        entrypoint: str = str(
            object=project.entrypoint.relative_to(project.root),
        ).replace("/", "\\")
        quoted_pyinstaller: list[str] = [
            cls._ps_quote(value=value) for value in project.pyinstaller_args
        ]
        quoted_pip: list[str] = [
            cls._ps_quote(value=value) for value in project.pip_args
        ]
        quoted_pip.extend(
            cls._ps_quote(value=f".local-deps\\{dependency.name}")
            for dependency in project.local_dependencies
        )
        pyinstaller_extra: str = " ".join(quoted_pyinstaller)
        pip_extra: str = " ".join(quoted_pip)
        console: str = "" if project.console else "--windowed"
        quoted_python: list[str] = [
            cls._ps_quote(value=value) for value in config.python
        ]
        python_command: str = " ".join(quoted_python)
        remote_executable: str = f"{remote}\\out\\{project.name}.exe"
        app_executable: str = f"{{app}}\\{project.name}.exe"
        run_line: str = (
            f'Filename: "{app_executable}"; '
            f'Description: "Launch {project.name}"; '
            "Flags: nowait postinstall skipifsilent"
        )
        return f"""$ErrorActionPreference = 'Stop'
$Root = {cls._ps_quote(value=remote)}
$Source = Join-Path $Root 'source'
$Out = Join-Path $Root 'out'
New-Item -ItemType Directory -Force -Path $Source, $Out | Out-Null
Expand-Archive -LiteralPath (Join-Path $Root 'source.zip') `
  -DestinationPath $Source -Force
$Runtime = [System.Runtime.InteropServices.RuntimeInformation]
$Architecture = $Runtime::OSArchitecture.ToString()
if (
  $Architecture -ne 'Arm64'
) {{
  throw "Windows guest is not ARM64: $Architecture"
}}
Set-Location $Source
& {python_command} -m venv (Join-Path $Root 'venv')
$Python = Join-Path $Root 'venv\\Scripts\\python.exe'
& $Python -m pip install --disable-pip-version-check pyinstaller {pip_extra} .
& $Python -m PyInstaller `
  --noconfirm --clean --onefile {console} `
  --name {cls._ps_quote(value=project.name)} `
  --distpath $Out --workpath (Join-Path $Root 'pyinstaller-work') `
  --specpath (Join-Path $Root 'spec') {pyinstaller_extra} `
  {cls._ps_quote(value=entrypoint)}
$Iscc = @(
  "$env:LOCALAPPDATA\\Programs\\Inno Setup 7\\ISCC.exe",
  "$env:ProgramFiles\\Inno Setup 7\\ISCC.exe",
  "${{env:ProgramFiles(x86)}}\\Inno Setup 6\\ISCC.exe"
) | Where-Object {{ Test-Path -LiteralPath $_ }} | Select-Object -First 1
if (-not $Iscc) {{
  $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
  if (-not $Winget) {{
    throw 'Inno Setup is absent and winget is unavailable.'
  }}
  & $Winget.Source install --id JRSoftware.InnoSetup --exact `
    --silent --accept-package-agreements --accept-source-agreements
  $Iscc = @(
    "$env:LOCALAPPDATA\\Programs\\Inno Setup 7\\ISCC.exe",
    "$env:ProgramFiles\\Inno Setup 7\\ISCC.exe",
    "${{env:ProgramFiles(x86)}}\\Inno Setup 6\\ISCC.exe"
  ) | Where-Object {{ Test-Path -LiteralPath $_ }} | Select-Object -First 1
}}
if (-not $Iscc) {{
  throw 'Inno Setup compiler was not found after installation.'
}}
$Iss = Join-Path $Root 'installer.iss'
@'
[Setup]
AppId={{{project.identifier}}}
AppName={project.name}
AppVersion={project.version}
AppPublisher={config.publisher}
DefaultDirName={{autopf}}\\{project.name}
DefaultGroupName={project.name}
OutputDir={remote}\\out
OutputBaseFilename=setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=arm64
ArchitecturesInstallIn64BitMode=arm64
PrivilegesRequired=lowest

[Files]
Source: "{remote_executable}"; DestDir: "{{app}}"; Flags: ignoreversion

[Icons]
Name: "{{group}}\\{project.name}"; Filename: "{{app}}\\{project.name}.exe"

[Run]
{run_line}
'@ | Set-Content -LiteralPath $Iss -Encoding utf8
& $Iscc /Qp $Iss
if (-not (
  Test-Path -LiteralPath (Join-Path $Out '{project.name}.exe')
)) {{
  throw 'PyInstaller output is missing.'
}}
if (-not (
  Test-Path -LiteralPath (Join-Path $Out 'setup.exe')
)) {{
  throw 'Installer output is missing.'
}}
"""

    @staticmethod
    def _ssh_base(*, config: WindowsConfig) -> tuple[str, ...]:
        """Return hardened SSH client arguments.

        Args:
            config: Windows SSH configuration.

        Returns:
            SSH command prefix.

        """
        return (
            "ssh",
            "-p",
            str(object=config.port),
            "-i",
            str(object=config.identity_file),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
        )

    @staticmethod
    def _ps_quote(*, value: str) -> str:
        """Quote one PowerShell literal string.

        Args:
            value: Untrusted literal value.

        Returns:
            Single-quoted PowerShell literal.

        """
        return "'" + value.replace("'", "''") + "'"


class ReleaseBuilder:
    """Orchestrate selected platform builders."""

    def __init__(self, *, runner: Runner) -> None:
        """Initialize the release orchestrator.

        Args:
            runner: Shared command executor.

        """
        self._runner: Final[Runner] = runner

    def build(
        self,
        *,
        configuration: BuildConfiguration,
        output: Path,
        platforms: tuple[PlatformName, ...],
        invocation: str = "",
    ) -> tuple[BuildResult, ...]:
        """Build every selected platform sequentially.

        Args:
            configuration: Validated release configuration.
            output: Release output root.
            platforms: Platforms to build.
            invocation: Command prefix shown in ``BUILD.md`` rebuild steps.

        Returns:
            Build results in requested order.

        Raises:
            BuildError: If a platform build fails.

        """
        results: list[BuildResult] = []
        platform_name: PlatformName
        result: BuildResult
        for platform_name in platforms:
            platform_output: Path = output / platform_name
            match platform_name:
                case "macos":
                    result = MacOSBuilder(
                        runner=self._runner,
                    ).build(
                        project=configuration.project,
                        config=configuration.macos,
                        output=platform_output,
                    )
                case "linux":
                    result = LinuxDockerBuilder(runner=self._runner).build(
                        project=configuration.project,
                        config=configuration.linux,
                        output=platform_output,
                    )
                case "windows":
                    windows: WindowsConfig | None = configuration.windows
                    if windows is None:
                        msg: str = (
                            "Windows build requested without a "
                            "[windows] configuration."
                        )
                        raise BuildError(msg)
                    result = WindowsQemuBuilder(runner=self._runner).build(
                        project=configuration.project,
                        config=windows,
                        output=platform_output,
                    )
            results.append(result)
        _ = BuildReport.write(
            output=output,
            source=BuildSource(
                name=configuration.project.name,
                version=configuration.project.version,
                root=configuration.project.root,
                invocation=invocation or ReportConstants.invocation,
            ),
            runner=self._runner,
            results=tuple(results),
        )
        return tuple(results)


class Cli:
    """Parse command-line arguments and run the release builder."""

    @classmethod
    def main(cls, *, arguments: tuple[str, ...] | None = None) -> int:
        """Run the release builder CLI.

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
                    "with `python -m tools.release_publish.upload`."
                ),
            )
        project_root: Path = namespace.project
        manifest: Path = namespace.manifest
        output: Path = namespace.output.resolve()
        invocation: str = (
            f"python -m tools.release_builder --project {namespace.project} "
            f"--manifest {namespace.manifest}"
        )
        if namespace.output_format == "pyz":
            return cls._pyz(
                manifest=manifest,
                project_root=project_root,
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
        _ = ReleaseBuilder(runner=CommandRunner()).build(
            configuration=configuration,
            output=output,
            platforms=platforms,
            invocation=invocation,
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
        """Run the full pipeline described by ``[tool.release_builder]``.

        A bare ``python tools/release_builder`` reads the table from the
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
                "error: no arguments given and no [tool.release_builder] "
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
        for output_format in defaults.formats:
            status: int = cls.main(
                arguments=cls._format_argv(
                    defaults=defaults,
                    output_format=output_format,
                ),
            )
            if status != 0:
                return status
        if defaults.publish is not None:
            return cls._publish_configured(
                defaults=defaults,
                version=configuration.project.version,
            )
        return 0

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
            help="release_publish configuration (default: ./release.toml).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="With --publish, plan the release without running it.",
        )
        return parser

    @staticmethod
    def _publish(
        *,
        namespace: CliNamespace,
        output: Path,
        version: str,
    ) -> int:
        """Attach the build to a GitHub release through ``release_publish``.

        Args:
            namespace: Parsed arguments.
            output: Build output directory.
            version: Version that was built.

        Returns:
            Process exit status.

        """
        # publisher imports this module, so it is loaded lazily.
        module: ModuleType = import_module(
            name="tools.release_builder.publisher",
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
        manifest: Path,
        project_root: Path,
        output: Path,
        invocation: str,
    ) -> int:
        """Build the self-contained ``.pyz`` for the current platform.

        Args:
            manifest: TOML manifest path.
            project_root: Python project root.
            output: Directory that receives the archive.
            invocation: Command prefix shown in the rebuild section.

        Returns:
            Process exit status.

        """
        # pyz_builder imports this module, so it is loaded lazily.
        module: ModuleType = import_module(
            name="tools.release_builder.pyz_builder",
        )
        runner: CommandRunner = CommandRunner()
        configuration: BuildConfiguration = ManifestLoader.load(
            manifest=manifest,
            project_root=project_root,
            platforms=("linux",),
        )
        archive: Path = module.PyzBuilder(runner=runner).build(
            project=configuration.project,
            config=module.PyzConfig.load(
                manifest=manifest,
                project_root=project_root,
                default_name=configuration.project.name,
            ),
            output=output,
        )
        _ = BuildReport.write(
            output=output,
            source=BuildSource(
                name=configuration.project.name,
                version=configuration.project.version,
                root=configuration.project.root,
                invocation=invocation,
            ),
            runner=runner,
        )
        sys_stdout.write(f"{archive}\n")
        return 0

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
            name="tools.release_builder.pypi_builder",
        )
        configuration: BuildConfiguration = ManifestLoader.load(
            manifest=manifest,
            project_root=project_root,
            platforms=("linux",),
        )
        _ = module.PypiBuilder(runner=CommandRunner()).build(
            project=configuration.project,
            output=output,
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
            defaults: Parsed ``[tool.release_builder]`` table.
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
        if defaults.dry_run:
            argv.append("--dry-run")
        return tuple(argv)

    @staticmethod
    def _publish_configured(
        *,
        defaults: BuilderDefaults,
        version: str,
    ) -> int:
        """Publish the GitHub release, wiki, and PyPI distributions.

        Args:
            defaults: Parsed ``[tool.release_builder]`` table.
            version: Version that was built.

        Returns:
            Process exit status.

        """
        # publisher imports this module, so it is loaded lazily.
        module: ModuleType = import_module(
            name="tools.release_builder.publisher",
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
        if defaults.pypi_upload and "pypi" in defaults.formats:
            _ = CommandRunner().execute(
                arguments=(
                    executable,
                    "-m",
                    "tools.release_publish.upload",
                    str(object=defaults.output),
                ),
            )
        return 0


if __name__ == "__main__":
    raise SystemExit(Cli.main())
