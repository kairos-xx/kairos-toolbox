"""Tests for stationer configuration and deterministic helpers."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from json import loads
from pathlib import Path
from platform import system
from sys import executable
from tempfile import TemporaryDirectory
from time import monotonic
from typing import TYPE_CHECKING
from zipfile import ZipFile

import pytest

from tools.conjure import (
    CommandResult,
    QemuRunnerError,
    RemoteCommandError,
)
from tools.stationer import (
    BuildConfiguration,
    BuildError,
    BuildResult,
    Cli,
    LinuxConfig,
    LinuxDockerBuilder,
    MacOSBuilder,
    MacOSConfig,
    ManifestLoader,
    PosixBuild,
    PosixGuestConfig,
    PosixQemuBuilder,
    ProjectConfig,
    ReleaseBuilder,
    WindowsConfig,
    WindowsQemuBuilder,
)
from tools.stationer.builder import (
    ArtifactUtilities,
    SourceArchive,
    job_failure,
)
from tools.stationer.source_packs import SourcePacks

from .fake_sessions import (
    FakeSession,
    Hosts,
    Opener,
    RunnerFactory,
    install_fake_runners,
)
from .helpers import check, expect, present

if TYPE_CHECKING:
    from tools.stationer.builder import PlatformName


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
                'remote_root = "C:/stationer"\n'
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


def macos_handler(arguments: tuple[str, ...]) -> str | None:
    """Fabricate PyInstaller, the PKG and the macOS probes.

    The faked CPU follows the command: an ``arch -x86_64`` prefix or an
    ``amd64`` build folder answers as x86_64, anything else as arm64.

    Args:
        arguments: Command arguments being faked.

    Returns:
        Standard output for the command, if any.

    """
    amd64: bool = "-x86_64" in arguments[:2] or any(
        "/amd64/" in argument for argument in arguments
    )
    if "PyInstaller" in arguments:
        dist: Path = Path(arguments[arguments.index("--distpath") + 1])
        dist.mkdir(parents=True, exist_ok=True)
        (dist / "demo").write_text(data="binary\n", encoding="utf-8")
    if "pkgbuild" in arguments:
        Path(arguments[-1]).write_text(data="pkg\n", encoding="utf-8")
    if "lipo" in arguments:
        return "x86_64\n" if amd64 else "arm64\n"
    if "sw_vers" in arguments:
        return "26.3\n"
    if any("platform.machine()" in argument for argument in arguments):
        return "x86_64\n" if amd64 else "arm64\n"
    return None


def test_a_failed_command_keeps_its_output_in_the_build_error() -> None:
    """A runner's failed command becomes a ``BuildError`` with its output."""
    failed: CommandResult = CommandResult(
        command=("tool", "--flag"),
        return_code=3,
        stdout="partial",
        stderr="it broke",
    )
    message: str = str(
        object=job_failure(error=RemoteCommandError(result=failed)),
    )
    check(condition="exit code 3" in message, reason=message)
    check(
        condition="stdout:\npartial" in message
        and "stderr:\nit broke" in message,
        reason=message,
    )
    expect(
        actual=str(
            object=job_failure(error=QemuRunnerError("docker is down")),
        ),
        expected="docker is down",
    )


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


def test_macos_builder_produces_exe_and_pkg(tmp_path: Path) -> None:
    """The macOS backend freezes, packages and verifies the artifacts.

    Args:
        tmp_path: Pytest temporary directory.

    """
    hosts: Hosts = Hosts(handler=macos_handler)
    result: BuildResult = MacOSBuilder(open_host=hosts).build(
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
        condition=present(value=result.installer).suffix == ".pkg",
        reason=str(object=result.installer),
    )
    check(
        condition=any("pkgbuild" in command for command in hosts.commands),
        reason="pkgbuild must run",
    )


def linux_config() -> LinuxConfig:
    """Create a Linux Docker configuration.

    Returns:
        The configuration.

    """
    return LinuxConfig(
        docker_platform="linux/arm64",
        image="python:3.13-slim-bookworm",
        maintainer="Example",
        description="Demo",
    )


def windows_config(*, identity: Path) -> WindowsConfig:
    """Create a Windows guest configuration.

    Args:
        identity: SSH key path.

    Returns:
        The configuration.

    """
    return WindowsConfig(
        host="192.0.2.9",
        port=2222,
        user="builder",
        identity_file=identity,
        python=("py", "-3.13"),
        remote_root="C:/stationer",
        publisher="Example",
    )


def test_linux_builder_produces_exe_and_deb(tmp_path: Path) -> None:
    """The container backend copies sources in and both artifacts out.

    Args:
        tmp_path: Pytest temporary directory.

    """
    opener: Opener = Opener()
    result: BuildResult = LinuxDockerBuilder(open_session=opener).build(
        project=project(root=tmp_path / "src"),
        config=linux_config(),
        output=tmp_path / "out",
    )
    expect(actual=result.platform, expected="linux")
    expect(actual=result.architecture, expected="arm64")
    check(
        condition=result.executable.is_file(),
        reason="executable must exist",
    )
    check(
        condition=present(value=result.installer).suffix == ".deb",
        reason=str(object=result.installer),
    )
    expect(
        actual=(result.abi, result.minimum_glibc),
        expected=("glibc", "2.36"),
    )
    session: FakeSession = opener.created[0]
    expect(actual=session.events[0], expected="start")
    expect(actual=session.events[-1], expected="close")
    expect(
        actual=[source for source, _ in session.downloads],
        expected=[
            "/out/executable",
            "/out/installer",
            "/out/glibc",
        ],
    )
    sources: set[str] = session.trees["/work/src"]
    check(condition="app.py" in sources, reason=str(object=sources))
    check(
        condition=not any("__pycache__" in name for name in sources),
        reason="staged copies exclude caches",
    )
    shell: tuple[str, ...] = session.commands[-1]
    expect(actual=shell[:3], expected=("sh", "-eu", "-c"))
    check(condition="/work/src" in shell[3], reason=shell[3])


def test_linux_failures_keep_the_output_and_release_the_container(
    tmp_path: Path,
) -> None:
    """A failing container command is a ``BuildError`` with its output.

    Args:
        tmp_path: Pytest temporary directory.

    """
    opener: Opener = Opener(fail_on="pyinstaller")
    with pytest.raises(
        expected_exception=BuildError,
        match=r"(?s)exit code 1.*stderr:\nboom",
    ):
        _ = LinuxDockerBuilder(open_session=opener).build(
            project=project(root=tmp_path / "src"),
            config=linux_config(),
            output=tmp_path / "out",
        )
    expect(actual=opener.created[0].events[-1], expected="close")
    expect(actual=opener.created[0].downloads, expected=[])


def test_windows_builder_pushes_sources_and_pulls_artifacts(
    tmp_path: Path,
) -> None:
    """The guest backend archives sources and pulls the artifacts back.

    Args:
        tmp_path: Pytest temporary directory.

    """
    identity: Path = tmp_path / "id_ed25519"
    identity.write_text(data="key\n", encoding="utf-8")
    opener: Opener = Opener()
    result: BuildResult = WindowsQemuBuilder(open_session=opener).build(
        project=project(root=tmp_path / "src"),
        config=windows_config(identity=identity),
        output=tmp_path / "out",
    )
    expect(actual=result.platform, expected="windows")
    expect(actual=result.architecture, expected="arm64")
    check(
        condition=result.executable.suffix == ".exe",
        reason=str(object=result.executable),
    )
    session: FakeSession = opener.created[0]
    uploaded: list[str] = sorted(
        destination.rsplit(sep="/", maxsplit=1)[-1]
        for destination in session.files
    )
    expect(actual=uploaded, expected=["build.ps1", "source.zip"])
    check(
        condition=any("-File" in command for command in session.commands),
        reason="the worker script must run in the guest",
    )
    expect(actual=len(session.downloads), expected=2)
    expect(actual=session.events[-1], expected="close")


def test_windows_failures_keep_the_output_and_release_the_guest(
    tmp_path: Path,
) -> None:
    """A failing guest command is a ``BuildError`` with its output.

    Args:
        tmp_path: Pytest temporary directory.

    """
    identity: Path = tmp_path / "id_ed25519"
    identity.write_text(data="key\n", encoding="utf-8")
    opener: Opener = Opener(fail_on="build.ps1")
    with pytest.raises(expected_exception=BuildError, match="boom"):
        _ = WindowsQemuBuilder(open_session=opener).build(
            project=project(root=tmp_path / "src"),
            config=windows_config(identity=identity),
            output=tmp_path / "out",
        )
    expect(actual=opener.created[0].events[-1], expected="close")
    expect(actual=opener.created[0].downloads, expected=[])


def test_builder_runs_each_selected_platform(
    tmp_path: Path,
) -> None:
    """The orchestrator builds every platform and writes a manifest.

    Args:
        tmp_path: Pytest temporary directory.

    """
    root: Path = tmp_path / "src"
    identity: Path = tmp_path / "id_ed25519"
    identity.write_text(data="key\n", encoding="utf-8")
    configuration: BuildConfiguration = BuildConfiguration(
        project=project(root=root),
        linux=linux_config(),
        macos=MacOSConfig(python=executable, signing_identity=None),
        windows=windows_config(identity=identity),
    )
    containers: Opener = Opener()
    guests: Opener = Opener()
    output: Path = tmp_path / "out"
    platforms: tuple[PlatformName, ...] = (
        ("macos", "linux", "windows")
        if system() == "Darwin"
        else ("linux", "windows")
    )
    results: tuple[BuildResult, ...] = ReleaseBuilder(
        open_host=Hosts(handler=macos_handler),
        containers=containers,
        guests=guests,
    ).build(
        configuration=configuration,
        output=output,
        platforms=platforms,
    )
    expect(actual=len(results), expected=len(platforms))
    expect(actual=len(containers.created), expected=1)
    expect(actual=len(guests.created), expected=1)
    manifest: Path = output / "artifacts.json"
    check(condition=manifest.is_file(), reason="manifest written")
    document: list[dict[str, object]] = loads(
        s=manifest.read_text(encoding="utf-8"),
    )
    expect(actual=len(document), expected=len(platforms))


def test_builder_rejects_windows_without_config(
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
            open_host=Hosts(handler=macos_handler),
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
    runners: RunnerFactory = install_fake_runners(monkeypatch=monkeypatch)
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
    expect(actual=len(runners.docker), expected=1)
    expect(actual=runners.docker[0].command, expected=("sleep", "infinity"))


def guest_config(*, identity: Path) -> PosixGuestConfig:
    """Create a POSIX QEMU guest configuration.

    Args:
        identity: SSH key path.

    Returns:
        The configuration.

    """
    return PosixGuestConfig(
        host="192.0.2.10",
        port=2222,
        user="builder",
        identity_file=identity,
        python=("python3",),
        remote_root="/home/builder/stationer",
    )


def glibc_probe(*, arguments: tuple[str, ...]) -> str | None:
    """Answer the guest probe as an x86_64 glibc machine.

    Args:
        arguments: Command arguments being faked.

    Returns:
        Probe output, or ``None`` for other commands.

    """
    if "uname" in arguments[-1]:
        return "x86_64\n"
    return None


def musl_probe(*, arguments: tuple[str, ...]) -> str | None:
    """Answer the guest probe as an aarch64 musl machine.

    Args:
        arguments: Command arguments being faked.

    Returns:
        Probe output, or ``None`` for other commands.

    """
    if "uname" in arguments[-1]:
        return "aarch64\nmusl\n"
    return None


def test_linux_guest_builder_probes_and_pulls_artifacts(
    tmp_path: Path,
) -> None:
    """A glibc guest yields a ``.deb`` named from its own probe.

    Args:
        tmp_path: Pytest temporary directory.

    """
    identity: Path = tmp_path / "id_ed25519"
    identity.write_text(data="key\n", encoding="utf-8")
    opener: Opener = Opener(handler=glibc_probe)
    result: BuildResult = PosixQemuBuilder(open_session=opener).build(
        build=PosixBuild(
            project=project(root=tmp_path / "src"),
            config=guest_config(identity=identity),
            platform_name="linux",
            linux=linux_config(),
        ),
        output=tmp_path / "out",
    )
    expect(actual=result.platform, expected="linux")
    expect(actual=result.architecture, expected="amd64")
    expect(actual=result.abi, expected="glibc")
    expect(actual=result.minimum_glibc, expected="2.36")
    check(
        condition=present(value=result.installer).suffix == ".deb",
        reason=str(object=result.installer),
    )
    session: FakeSession = opener.created[0]
    uploaded: list[str] = sorted(
        destination.rsplit(sep="/", maxsplit=1)[-1]
        for destination in session.files
    )
    check(
        condition="source.zip" not in uploaded,
        reason="source travels as content-addressed packs",
    )
    expect(
        actual={"build.sh", "catalog.json", "partition-runtime.zip"}
        <= set(uploaded),
        expected=True,
    )
    check(
        condition=any(name.endswith(".zst") for name in uploaded),
        reason="at least one content pack must reach the guest",
    )
    check(
        condition=any(
            command[-1].endswith("build.sh") for command in session.commands
        ),
        reason="the worker script must run in the guest",
    )
    expect(actual=session.events[-1], expected="close")


def test_guest_build_uploads_only_missing_packs(tmp_path: Path) -> None:
    """A store the guest already holds transfers only new metadata.

    Args:
        tmp_path: Pytest temporary directory.

    """
    identity: Path = tmp_path / "id_ed25519"
    identity.write_text(data="key\n", encoding="utf-8")
    output: Path = tmp_path / "out"
    first: Opener = Opener(handler=glibc_probe)
    _ = PosixQemuBuilder(open_session=first).build(
        build=PosixBuild(
            project=project(root=tmp_path / "src"),
            config=guest_config(identity=identity),
            platform_name="linux",
            linux=linux_config(),
        ),
        output=output,
    )

    def warm_probe(*, arguments: tuple[str, ...]) -> str | None:
        """Answer probes as a guest holding every pack and runtime.

        Args:
            arguments: Command arguments being faked.

        Returns:
            Probe or store listing output for matching commands.

        """
        if "uname" in arguments[-1]:
            return "x86_64\n"
        if "find" in arguments[-1]:
            packs: dict[str, Path] = SourcePacks.packs(
                store=output / ".partition",
            )
            names: str = "".join(f"./{name}\n" for name in sorted(packs))
            return names + "./partition-runtime.zip\n"
        return None

    second: Opener = Opener(handler=warm_probe)
    _ = PosixQemuBuilder(open_session=second).build(
        build=PosixBuild(
            project=project(root=tmp_path / "src"),
            config=guest_config(identity=identity),
            platform_name="linux",
            linux=linux_config(),
        ),
        output=output,
    )
    uploaded: list[str] = sorted(
        destination.rsplit(sep="/", maxsplit=1)[-1]
        for destination in second.created[0].files
    )
    expect(actual=uploaded, expected=["build.sh", "catalog.json"])


def test_linux_guest_musl_probe_names_the_apk(
    tmp_path: Path,
) -> None:
    """A musl guest reports ``musl`` and yields an ``.apk``.

    Args:
        tmp_path: Pytest temporary directory.

    """
    identity: Path = tmp_path / "id_ed25519"
    identity.write_text(data="key\n", encoding="utf-8")
    opener: Opener = Opener(handler=musl_probe)
    result: BuildResult = PosixQemuBuilder(open_session=opener).build(
        build=PosixBuild(
            project=project(root=tmp_path / "src"),
            config=guest_config(identity=identity),
            platform_name="linux",
            linux=linux_config(),
        ),
        output=tmp_path / "out",
    )
    expect(actual=result.architecture, expected="arm64")
    expect(actual=result.abi, expected="musl")
    expect(actual=result.minimum_glibc, expected=None)
    check(
        condition=present(value=result.installer).suffix == ".apk",
        reason=str(object=result.installer),
    )


def test_macos_guest_builder_produces_a_pkg(tmp_path: Path) -> None:
    """A macOS guest freezes the executable and packages a ``.pkg``.

    Args:
        tmp_path: Pytest temporary directory.

    """
    identity: Path = tmp_path / "id_ed25519"
    identity.write_text(data="key\n", encoding="utf-8")
    opener: Opener = Opener(handler=glibc_probe)
    result: BuildResult = PosixQemuBuilder(open_session=opener).build(
        build=PosixBuild(
            project=project(root=tmp_path / "src"),
            config=guest_config(identity=identity),
            platform_name="macos",
            macos=MacOSConfig(python=executable, signing_identity=None),
        ),
        output=tmp_path / "out",
    )
    expect(actual=result.platform, expected="macos")
    expect(actual=result.architecture, expected="amd64")
    check(
        condition=present(value=result.installer).suffix == ".pkg",
        reason=str(object=result.installer),
    )
    check(
        condition=result.minimum_macos is not None,
        reason="the guest's sw_vers must be recorded",
    )
    expect(actual=opener.created[0].events[-1], expected="close")


def test_guest_failures_keep_the_output_and_release_the_guest(
    tmp_path: Path,
) -> None:
    """A failing guest command is a ``BuildError`` with its output.

    Args:
        tmp_path: Pytest temporary directory.

    """
    identity: Path = tmp_path / "id_ed25519"
    identity.write_text(data="key\n", encoding="utf-8")
    opener: Opener = Opener(fail_on="build.sh", handler=glibc_probe)
    with pytest.raises(expected_exception=BuildError, match="boom"):
        _ = PosixQemuBuilder(open_session=opener).build(
            build=PosixBuild(
                project=project(root=tmp_path / "src"),
                config=guest_config(identity=identity),
                platform_name="linux",
                linux=linux_config(),
            ),
            output=tmp_path / "out",
        )
    expect(actual=opener.created[0].events[-1], expected="close")
    expect(actual=opener.created[0].downloads, expected=[])


def test_manifest_loader_reads_guest_tables() -> None:
    """``[linux.guest]`` and ``[macos.guest]`` parse into guest configs."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        (root / "app.py").write_text(data="print('ok')\n", encoding="utf-8")
        identity: Path = root / "id_ed25519"
        identity.write_text(data="test-key\n", encoding="utf-8")
        manifest: Path = root / "builder.toml"
        manifest.write_text(
            data=(
                '[project]\nname = "demo"\nversion = "1.0.0"\n'
                'entrypoint = "app.py"\n\n'
                "[linux.guest]\n"
                'host = "192.0.2.10"\nport = 2223\nuser = "builder"\n'
                f'identity_file = "{identity}"\n'
                'python = ["python3"]\nremote_root = "/tmp/stationer"\n\n'
                "[macos.guest]\n"
                'host = "192.0.2.11"\nuser = "builder"\n'
                f'identity_file = "{identity}"\n'
            ),
            encoding="utf-8",
        )
        configuration: BuildConfiguration = ManifestLoader.load(
            manifest=manifest,
            project_root=root,
            platforms=("linux", "macos"),
        )
        linux_guest: PosixGuestConfig = present(
            value=configuration.linux.guest,
        )
        expect(actual=linux_guest.host, expected="192.0.2.10")
        expect(actual=linux_guest.port, expected=2223)
        expect(actual=linux_guest.python, expected=("python3",))
        macos_guest: PosixGuestConfig = present(
            value=configuration.macos.guest,
        )
        expect(actual=macos_guest.port, expected=2222)


def test_builder_runs_linux_in_its_guest(tmp_path: Path) -> None:
    """A configured ``[linux.guest]`` replaces the Docker cells.

    Args:
        tmp_path: Pytest temporary directory.

    """
    identity: Path = tmp_path / "id_ed25519"
    identity.write_text(data="key\n", encoding="utf-8")
    containers: Opener = Opener()
    guests: Opener = Opener(handler=glibc_probe)
    results: tuple[BuildResult, ...] = ReleaseBuilder(
        open_host=Hosts(handler=macos_handler),
        containers=containers,
        guests=guests,
    ).build(
        configuration=BuildConfiguration(
            project=project(root=tmp_path / "src"),
            linux=replace(
                linux_config(),
                guest=guest_config(identity=identity),
            ),
            macos=MacOSConfig(python=executable, signing_identity=None),
            windows=None,
        ),
        output=tmp_path / "out",
        platforms=("linux",),
    )
    expect(actual=len(results), expected=1)
    expect(actual=results[0].architecture, expected="amd64")
    expect(actual=len(guests.created), expected=1)
    expect(actual=len(containers.created), expected=0)
