"""Tests for release-builder configuration and deterministic helpers."""

from __future__ import annotations

from hashlib import sha256
from json import loads
from pathlib import Path
from platform import system
from subprocess import CompletedProcess
from sys import executable
from tempfile import TemporaryDirectory
from time import monotonic
from typing import TYPE_CHECKING, Final, final
from zipfile import ZipFile

import pytest

from tools.release_builder import (
    BuildConfiguration,
    BuildError,
    BuildResult,
    Cli,
    CommandRunner,
    LinuxConfig,
    LinuxDockerBuilder,
    MacOSBuilder,
    MacOSConfig,
    ManifestLoader,
    ProjectConfig,
    ReleaseBuilder,
    WindowsConfig,
    WindowsQemuBuilder,
)
from tools.release_builder.release_builder import (
    ArtifactUtilities,
    SourceArchive,
)

from .helpers import check, expect, present

if TYPE_CHECKING:
    from collections.abc import Callable

    from tools.release_builder.release_builder import PlatformName
type FakeHandler = Callable[[tuple[str, ...]], None]


def test_manifest_loader_reads_all_platforms() -> None:
    """Load a complete manifest into precise platform configuration."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        (root / "app.py").write_text(data="print('ok')\n", encoding="utf-8")
        identity: Path = root / "id_ed25519"
        identity.write_text(data="test-key\n", encoding="utf-8")
        manifest: Path = root / "release.toml"
        manifest.write_text(
            data=(
                "[project]\n"
                'name = "demo"\n'
                'version = "1.2.3"\n'
                'entrypoint = "app.py"\n'
                'identifier = "dev.example.demo"\n'
                "console = true\n\n"
                "[linux]\n"
                'docker_platform = "linux/arm64"\n'
                'image = "python:3.13-slim-bookworm"\n'
                'maintainer = "Example"\n'
                'description = "Demo"\n\n'
                "[macos]\n"
                'python = "/usr/bin/python3"\n\n'
                "[windows]\n"
                'host = "127.0.0.1"\n'
                "port = 2222\n"
                'user = "builder"\n'
                f'identity_file = "{identity}"\n'
                'python = ["py", "-3.13"]\n'
                'remote_root = "C:/release-builder"\n'
                'publisher = "Example"\n'
            ),
            encoding="utf-8",
        )
        configuration: BuildConfiguration = ManifestLoader.load(
            manifest=manifest,
            project_root=root,
        )
        expect(actual=configuration.project.name, expected="demo")
        expect(actual=configuration.project.version, expected="1.2.3")
        expect(
            actual=configuration.linux.docker_platform,
            expected="linux/arm64",
        )
        windows: WindowsConfig = present(value=configuration.windows)
        expect(actual=windows.python, expected=("py", "-3.13"))


def _manifest(root: Path, *, project: str, windows: bool = True) -> Path:
    """Write a manifest with a customised ``[project]`` table.

    Args:
        root: Project root that receives the files.
        project: Body of the ``[project]`` table.
        windows: Whether to include the ``[windows]`` table.

    Returns:
        The manifest path.

    """
    (root / "app.py").write_text(data="print('ok')\n", encoding="utf-8")
    identity: Path = root / "id_ed25519"
    identity.write_text(data="test-key\n", encoding="utf-8")
    manifest: Path = root / "builder.toml"
    manifest.write_text(
        data=(
            f"[project]\n{project}\n"
            + (
                "[windows]\n"
                'host = "127.0.0.1"\nuser = "builder"\n'
                f'identity_file = "{identity}"\n'
                if windows
                else ""
            )
        ),
        encoding="utf-8",
    )
    return manifest


def test_version_can_come_from_a_version_file() -> None:
    """``version_file`` keeps the version in a single place."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        (root / "product.py").write_text(
            data='class P:\n    version: Final[str] = "4.5.6"\n',
            encoding="utf-8",
        )
        manifest: Path = _manifest(
            root=root,
            project=(
                'name = "demo"\nentrypoint = "app.py"\n'
                'version_file = "product.py"\n'
            ),
        )
        configuration: BuildConfiguration = ManifestLoader.load(
            manifest=manifest,
            project_root=root,
        )
        expect(actual=configuration.project.version, expected="4.5.6")


def test_missing_version_and_version_file_is_an_error() -> None:
    """A project needs one of the two."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        manifest: Path = _manifest(
            root=root,
            project='name = "demo"\nentrypoint = "app.py"\n',
        )
        with pytest.raises(expected_exception=BuildError, match="version"):
            ManifestLoader.load(manifest=manifest, project_root=root)


def test_windows_is_optional_when_not_building_it() -> None:
    """A macOS or Linux build needs no Windows VM or SSH key."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        manifest: Path = _manifest(
            root=root,
            project=(
                'name = "demo"\nversion = "1.0.0"\nentrypoint = "app.py"\n'
            ),
            windows=False,
        )
        configuration: BuildConfiguration = ManifestLoader.load(
            manifest=manifest,
            project_root=root,
            platforms=("macos", "linux"),
        )
        check(
            condition=configuration.windows is None,
            reason="windows must be absent",
        )
        with pytest.raises(expected_exception=BuildError, match="windows"):
            ManifestLoader.load(
                manifest=manifest,
                project_root=root,
                platforms=("windows",),
            )


@final
class FakeRunner:
    """Fabricate build artifacts instead of running real commands."""

    def __init__(self, *, handler: FakeHandler) -> None:
        """Create a runner that delegates each command to ``handler``.

        Args:
            handler: Callback receiving each command tuple.

        """
        self._handler: Final[FakeHandler] = handler
        self.commands: list[tuple[str, ...]] = []

    def execute(
        self,
        *,
        arguments: tuple[str, ...],
        cwd: Path | None = None,
        timeout_seconds: int = 1800,
    ) -> CompletedProcess[str]:
        """Record the command and let the handler fabricate outputs.

        Args:
            arguments: Command and arguments.
            cwd: Optional working directory.
            timeout_seconds: Maximum command duration.

        Returns:
            A successful canned result.

        """
        _ = (cwd, timeout_seconds)
        self.commands.append(arguments)
        handler: Callable[[tuple[str, ...]], None] = self._handler
        handler(arguments)
        return CompletedProcess(
            args=arguments,
            returncode=0,
            stdout="",
            stderr="",
        )


def project(root: Path) -> ProjectConfig:
    """Create a minimal project configuration in ``root``.

    Args:
        root: Project root that receives the entrypoint file.

    Returns:
        The project configuration.

    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.py").write_text(data="print('ok')\n", encoding="utf-8")
    return ProjectConfig(
        root=root,
        name="demo",
        version="1.2.3",
        entrypoint=root / "app.py",
        identifier="dev.example.demo",
        console=True,
        pyinstaller_args=("--collect-submodules", "demo"),
        pip_args=(),
    )


def macos_handler(arguments: tuple[str, ...]) -> None:
    """Fabricate the PyInstaller output and the PKG installer.

    Args:
        arguments: Command arguments being faked.

    """
    if "PyInstaller" in arguments:
        dist: Path = Path(arguments[arguments.index("--distpath") + 1])
        dist.mkdir(parents=True, exist_ok=True)
        (dist / "demo").write_text(data="binary\n", encoding="utf-8")
    if "pkgbuild" in arguments:
        Path(arguments[-1]).write_text(data="pkg\n", encoding="utf-8")


def linux_handler(*, version: str) -> FakeHandler:
    """Build a handler that fabricates the Linux artifacts for ``version``.

    Args:
        version: Project version embedded in the artifact names.

    Returns:
        A command handler.

    """

    def handle(arguments: tuple[str, ...]) -> None:
        """Fabricate the executable and DEB the Docker worker produces.

        Args:
            arguments: Command arguments being faked.

        """
        if arguments[0] != "docker":
            return
        volumes: tuple[str, ...] = tuple(
            arguments[index + 1]
            for index, value in enumerate(iterable=arguments)
            if value == "--volume"
        )
        out: Path = Path(
            present(
                value=next(
                    (v for v in volumes if v.endswith(":/out")),
                    None,
                ),
            ).removesuffix(":/out"),
        )
        (out / f"demo-{version}-linux-arm64").write_text(
            data="binary\n",
            encoding="utf-8",
        )
        (out / f"demo_{version}_arm64.deb").write_text(
            data="deb\n",
            encoding="utf-8",
        )

    return handle


def windows_handler(arguments: tuple[str, ...]) -> None:
    """Fabricate the artifacts pulled back over ``scp``.

    Args:
        arguments: Command arguments being faked.

    """
    if arguments[0] != "scp":
        return
    destination: str = arguments[-1]
    if ":" not in destination:
        Path(destination).write_text(data="exe\n", encoding="utf-8")


def test_command_runner_reports_failures() -> None:
    """A non-zero exit becomes a ``BuildError`` with captured output."""
    with pytest.raises(expected_exception=BuildError, match="exit code"):
        CommandRunner.execute(arguments=("false",))


def test_manifest_loader_rejects_a_missing_name() -> None:
    """A project without ``name`` cannot be built."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        manifest: Path = _manifest(
            root=root,
            project='version = "1.0.0"\nentrypoint = "app.py"\n',
        )
        with pytest.raises(expected_exception=BuildError, match="name"):
            ManifestLoader.load(manifest=manifest, project_root=root)


def test_manifest_loader_rejects_an_outside_entrypoint() -> None:
    """The entrypoint must stay inside the project root."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        manifest: Path = _manifest(
            root=root,
            project=(
                'name = "demo"\nversion = "1.0.0"\nentrypoint = "../app.py"\n'
            ),
        )
        with pytest.raises(
            expected_exception=BuildError,
            match="entrypoint",
        ):
            ManifestLoader.load(manifest=manifest, project_root=root)


def test_manifest_loader_requires_windows_fields() -> None:
    """Selecting Windows without its table or keys is an error."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        (root / "app.py").write_text(data="print('ok')\n", encoding="utf-8")
        manifest: Path = root / "builder.toml"
        manifest.write_text(
            data=(
                '[project]\nname = "demo"\nversion = "1.0.0"\n'
                'entrypoint = "app.py"\n\n[windows]\nhost = "w"\n'
            ),
            encoding="utf-8",
        )
        with pytest.raises(
            expected_exception=BuildError,
            match=r"windows\.user",
        ):
            ManifestLoader.load(
                manifest=manifest,
                project_root=root,
                platforms=("windows",),
            )


def test_manifest_loader_requires_a_real_identity_file() -> None:
    """A Windows build needs an SSH identity that exists."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        (root / "app.py").write_text(data="print('ok')\n", encoding="utf-8")
        manifest: Path = root / "builder.toml"
        manifest.write_text(
            data=(
                '[project]\nname = "demo"\nversion = "1.0.0"\n'
                'entrypoint = "app.py"\n\n[windows]\nhost = "w"\n'
                'user = "u"\nidentity_file = "/nonexistent/key"\n'
            ),
            encoding="utf-8",
        )
        with pytest.raises(
            expected_exception=BuildError,
            match="identity",
        ):
            ManifestLoader.load(
                manifest=manifest,
                project_root=root,
                platforms=("windows",),
            )


def test_source_archive_excludes_transient_directories(
    tmp_path: Path,
) -> None:
    """Caches, virtualenvs and VCS data never reach the worker.

    Args:
        tmp_path: Pytest temporary directory.

    """
    (tmp_path / "pkg" / "__pycache__").mkdir(parents=True)
    (tmp_path / "pkg" / "__pycache__" / "x.pyc").write_text(
        data="byte",
        encoding="utf-8",
    )
    (tmp_path / "pkg" / "mod.py").write_text(data="x = 1\n", encoding="utf-8")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "junk.whl").write_text(
        data="wheel",
        encoding="utf-8",
    )
    archive: Path = tmp_path / "source.zip"
    SourceArchive.create(project=project(root=tmp_path), destination=archive)
    with ZipFile(file=archive) as bundle:
        names: set[str] = set(bundle.namelist())
    check(condition="pkg/mod.py" in names, reason=str(object=names))
    check(condition="app.py" in names, reason=str(object=names))
    check(
        condition=not any("__pycache__" in name for name in names),
        reason=str(object=names),
    )
    check(
        condition=not any(name.startswith("dist/") for name in names),
        reason=str(object=names),
    )


def test_artifact_result_requires_the_outputs(tmp_path: Path) -> None:
    """Missing executables or installers fail the result.

    Args:
        tmp_path: Pytest temporary directory.

    """
    with pytest.raises(expected_exception=BuildError, match="missing"):
        ArtifactUtilities.result(
            platform_name="macos",
            architecture="arm64",
            executable_path=tmp_path / "nope",
            installer_path=tmp_path / "nope.pkg",
            started_at=monotonic(),
        )


def test_artifact_result_reports_real_digests(tmp_path: Path) -> None:
    """A verified result carries the artifacts' SHA-256 digests.

    Args:
        tmp_path: Pytest temporary directory.

    """
    binary: Path = tmp_path / "demo"
    binary.write_bytes(data=b"binary")
    pkg: Path = tmp_path / "demo.pkg"
    pkg.write_bytes(data=b"pkg")
    result: BuildResult = ArtifactUtilities.result(
        platform_name="linux",
        architecture="arm64",
        executable_path=binary,
        installer_path=pkg,
        started_at=monotonic(),
    )
    expect(
        actual=result.executable_sha256,
        expected=sha256(data=b"binary").hexdigest(),
    )
    expect(
        actual=result.installer_sha256,
        expected=sha256(data=b"pkg").hexdigest(),
    )


@pytest.mark.skipif(system() != "Darwin", reason="macOS only")
def test_macos_builder_produces_exe_and_pkg(tmp_path: Path) -> None:
    """The macOS backend freezes, packages and verifies the artifacts.

    Args:
        tmp_path: Pytest temporary directory.

    """
    runner: FakeRunner = FakeRunner(handler=macos_handler)
    result: BuildResult = MacOSBuilder(runner=runner).build(
        project=project(root=tmp_path / "src"),
        config=MacOSConfig(python=executable, signing_identity=None),
        output=tmp_path / "out",
    )
    expect(actual=result.platform, expected="macos")
    check(
        condition=result.executable.is_file(),
        reason="executable must exist",
    )
    check(
        condition=result.installer.suffix == ".pkg",
        reason=str(object=result.installer),
    )
    check(
        condition=any("pkgbuild" in command for command in runner.commands),
        reason="pkgbuild must run",
    )


def test_linux_builder_produces_exe_and_deb(tmp_path: Path) -> None:
    """The Docker backend produces and verifies both Linux artifacts.

    Args:
        tmp_path: Pytest temporary directory.

    """
    runner: FakeRunner = FakeRunner(handler=linux_handler(version="1.2.3"))
    result: BuildResult = LinuxDockerBuilder(runner=runner).build(
        project=project(root=tmp_path / "src"),
        config=LinuxConfig(
            docker_platform="linux/arm64",
            image="python:3.13-slim-bookworm",
            maintainer="Example",
            description="Demo",
        ),
        output=tmp_path / "out",
    )
    expect(actual=result.platform, expected="linux")
    expect(actual=result.architecture, expected="arm64")
    check(
        condition=result.executable.is_file(),
        reason="executable must exist",
    )
    check(
        condition=result.installer.suffix == ".deb",
        reason=str(object=result.installer),
    )


def test_windows_builder_pushes_sources_and_pulls_artifacts(
    tmp_path: Path,
) -> None:
    """The QEMU backend archives sources and verifies pulled artifacts.

    Args:
        tmp_path: Pytest temporary directory.

    """
    identity: Path = tmp_path / "id_ed25519"
    identity.write_text(data="key\n", encoding="utf-8")
    runner: FakeRunner = FakeRunner(handler=windows_handler)
    result: BuildResult = WindowsQemuBuilder(runner=runner).build(
        project=project(root=tmp_path / "src"),
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
    expect(actual=result.platform, expected="windows")
    expect(actual=result.architecture, expected="arm64")
    check(
        condition=result.executable.suffix == ".exe",
        reason=str(object=result.executable),
    )
    check(
        condition=any(command[0] == "ssh" for command in runner.commands),
        reason="ssh must drive the guest",
    )


def test_release_builder_runs_each_selected_platform(
    tmp_path: Path,
) -> None:
    """The orchestrator builds every platform and writes a manifest.

    Args:
        tmp_path: Pytest temporary directory.

    """

    def handler(arguments: tuple[str, ...]) -> None:
        """Dispatch fabrication by tool.

        Args:
            arguments: Command arguments being faked.

        """
        macos_handler(arguments=arguments)
        linux: Callable[[tuple[str, ...]], None] = linux_handler(
            version="1.2.3",
        )
        linux(arguments)
        windows_handler(arguments=arguments)

    root: Path = tmp_path / "src"
    identity: Path = tmp_path / "id_ed25519"
    identity.write_text(data="key\n", encoding="utf-8")
    configuration: BuildConfiguration = BuildConfiguration(
        project=project(root=root),
        linux=LinuxConfig(
            docker_platform="linux/arm64",
            image="img",
            maintainer="M",
            description="D",
        ),
        macos=MacOSConfig(python=executable, signing_identity=None),
        windows=WindowsConfig(
            host="w",
            port=22,
            user="u",
            identity_file=identity,
            python=("py", "-3.13"),
            remote_root="C:/rb",
            publisher="P",
        ),
    )
    runner: FakeRunner = FakeRunner(handler=handler)
    output: Path = tmp_path / "out"
    platforms: tuple[PlatformName, ...] = (
        ("macos", "linux", "windows")
        if system() == "Darwin"
        else ("linux", "windows")
    )
    results: tuple[BuildResult, ...] = ReleaseBuilder(
        runner=runner,
    ).build(
        configuration=configuration,
        output=output,
        platforms=platforms,
    )
    expect(actual=len(results), expected=len(platforms))
    manifest: Path = output / "artifacts.json"
    check(condition=manifest.is_file(), reason="manifest written")
    document: list[dict[str, object]] = loads(
        s=manifest.read_text(encoding="utf-8"),
    )
    expect(actual=len(document), expected=len(platforms))


def test_release_builder_rejects_windows_without_config(
    tmp_path: Path,
) -> None:
    """A Windows build without a ``[windows]`` table fails cleanly.

    Args:
        tmp_path: Pytest temporary directory.

    """
    configuration: BuildConfiguration = BuildConfiguration(
        project=project(root=tmp_path / "src"),
        linux=LinuxConfig(
            docker_platform="linux/arm64",
            image="img",
            maintainer="M",
            description="D",
        ),
        macos=MacOSConfig(python=executable, signing_identity=None),
        windows=None,
    )
    with pytest.raises(expected_exception=BuildError, match="windows"):
        ReleaseBuilder(
            runner=FakeRunner(handler=linux_handler(version="1.0.0")),
        ).build(
            configuration=configuration,
            output=tmp_path / "out",
            platforms=("windows",),
        )


def test_cli_builds_the_selected_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI parses arguments and runs the selected builders.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    root: Path = tmp_path / "src"
    root.mkdir()
    manifest: Path = _manifest(
        root=root,
        project='name = "demo"\nversion = "1.0.0"\nentrypoint = "app.py"\n',
        windows=False,
    )

    def fake_execute(
        *,
        arguments: tuple[str, ...],
        cwd: Path | None = None,
        timeout_seconds: int = 1800,
    ) -> CompletedProcess[str]:
        """Delegate the CLI's commands to the Linux fabricator.

        Args:
            arguments: Command and arguments.
            cwd: Optional working directory.
            timeout_seconds: Maximum command duration.

        Returns:
            A successful canned result.

        """
        _ = (cwd, timeout_seconds)
        handler: Callable[[tuple[str, ...]], None] = linux_handler(
            version="1.0.0",
        )
        handler(arguments)
        return CompletedProcess(
            args=arguments,
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(
        target=CommandRunner,
        name="execute",
        value=staticmethod(fake_execute),
    )
    status: int = Cli.main(
        arguments=(
            "--project",
            str(object=root),
            "--manifest",
            str(object=manifest),
            "--output",
            str(object=tmp_path / "out"),
            "--platform",
            "linux",
        ),
    )
    expect(actual=status, expected=0)
    check(
        condition=(tmp_path / "out" / "artifacts.json").is_file(),
        reason="manifest written",
    )
