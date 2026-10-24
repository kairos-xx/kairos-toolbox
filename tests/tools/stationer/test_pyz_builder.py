# Copyright (c) 2026 Project contributors.
"""Tests for the self-contained ``.pyz`` output format."""

from __future__ import annotations

from pathlib import Path
from sys import executable
from zipfile import ZipFile

import pytest

from tools.stationer import (
    BuildError,
    Cli,
    ProjectConfig,
    pyz_builder,
)
from tools.stationer.builder import ArtifactUtilities
from tools.stationer.pyz_builder import (
    PyzBuilder,
    PyzConfig,
    PyzTarget,
    archive_name,
    platform_tag,
)

from .fake_sessions import Hosts
from .helpers import check, expect


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
        actual=archive_name(name="district", version="1.0.0", tag="x-y"),
        expected="district-1.0.0-x-y.pyz",
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
    runner: Hosts = Hosts(handler=handler)
    config: PyzConfig = PyzConfig(
        name="demo-app",
        bootstrap=project.root / "boot.py",
    )
    built: Path = PyzBuilder(open_host=runner).build(
        project=project,
        config=config,
        output=tmp_path / "out",
    )
    expect(
        actual=built.name,
        expected=f"demo-app-1.2.3-{platform_tag()}.pyz",
    )
    expect(
        actual=sorted(p.name for p in built.parent.iterdir()),
        expected=[built.name],
    )
    venv: tuple[str, ...] = runner.commands[0]
    expect(actual=venv[1:4], expected=("-I", "-m", "venv"))
    pip: tuple[str, ...] = runner.commands[1]
    expect(actual=pip[1:4], expected=("-m", "pip", "install"))
    check(
        condition=pip[0] != executable,
        reason="pip runs from the throwaway venv, not the host interpreter",
    )
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
    smoke: tuple[str, ...] = runner.commands[2]
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
    runner: Hosts = Hosts(handler=handler)
    with pytest.raises(expected_exception=BuildError, match="version"):
        _ = PyzBuilder(open_host=runner).build(
            project=project,
            config=PyzConfig(
                name="demo",
                bootstrap=project.root / "boot.py",
            ),
            output=tmp_path / "out",
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
        target: PyzTarget | None = None,
    ) -> Path:
        """Record the build arguments and emit a stub archive.

        Args:
            self: Builder instance being patched.
            project: Project configuration.
            config: Archive configuration.
            output: Output directory.
            target: Matrix cell the archive serves.

        Returns:
            Path to the stub archive.

        """
        _ = (self, target)
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
        expected=[
            (
                "demo-app",
                ArtifactUtilities.pyz_folder(
                    package=ArtifactUtilities.folder(
                        output=tmp_path / "dist",
                        platform_name=cell.platform_name,
                        architecture=cell.architecture,
                        abi=cell.abi,
                        project="demo",
                    ),
                ).resolve(),
            )
            for cell in PyzTarget.cells()
        ],
    )


def test_cells_cover_the_full_matrix() -> None:
    """Every platform/architecture/ABI combination is a cell."""
    cells: tuple[PyzTarget, ...] = PyzTarget.cells()
    expect(actual=len(cells), expected=8)
    check(
        condition=PyzTarget(
            platform_name="linux",
            architecture="amd64",
            abi="musl",
        )
        in cells,
        reason="linux musl cells exist",
    )
    filtered: tuple[PyzTarget, ...] = PyzTarget.cells(
        platforms=("windows",),
        architecture="amd64",
    )
    expect(
        actual=filtered,
        expected=(
            PyzTarget(
                platform_name="windows",
                architecture="amd64",
            ),
        ),
    )


def test_cells_native_architecture_matches_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``native`` selects only this machine's architecture.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setattr(
        target=pyz_builder,
        name="machine",
        value=lambda: "aarch64",
    )
    cells: tuple[PyzTarget, ...] = PyzTarget.cells(architecture="native")
    expect(
        actual=sorted({cell.architecture for cell in cells}),
        expected=["arm64"],
    )
    expect(actual=len(cells), expected=4)


def test_tag_wheel_platforms_and_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cell tags, wheel platforms and host detection agree.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    cell: PyzTarget = PyzTarget(
        platform_name="linux",
        architecture="arm64",
        abi="musl",
    )
    expect(actual=cell.tag, expected="linux-arm64-musl")
    expect(
        actual=cell.wheel_platforms,
        expected=("musllinux_1_2_aarch64", "musllinux_1_1_aarch64"),
    )
    expect(
        actual=PyzTarget(platform_name="windows", architecture="amd64").tag,
        expected="windows-amd64",
    )
    with pytest.raises(expected_exception=BuildError, match="wheel platform"):
        _ = PyzTarget(
            platform_name="amiga",
            architecture="m68k",
        ).wheel_platforms
    monkeypatch.setattr(
        target=pyz_builder,
        name="system",
        value=lambda: "Darwin",
    )
    monkeypatch.setattr(
        target=pyz_builder,
        name="machine",
        value=lambda: "arm64",
    )
    check(
        condition=PyzTarget(
            platform_name="macos",
            architecture="arm64",
        ).host,
        reason="the host cell detects itself",
    )
    check(
        condition=not PyzTarget(
            platform_name="macos",
            architecture="amd64",
        ).host,
        reason="a foreign cell is not the host",
    )
    check(
        condition=not cell.host,
        reason="a foreign platform cell is not the host",
    )


def test_cross_build_resolves_target_wheels_without_smoke(
    tmp_path: Path,
) -> None:
    """A foreign cell resolves its wheels and skips the smoke test.

    Args:
        tmp_path: Pytest temporary directory.

    """

    def cross_handler(*, arguments: tuple[str, ...]) -> str:
        """Fabricate the wheel build and the target install.

        Args:
            arguments: Command and arguments.

        Returns:
            Standard output for the command.

        """
        if "wheel" in arguments:
            wheels: Path = Path(
                arguments[arguments.index("--wheel-dir") + 1],
            )
            (wheels / "demo-1.2.3-py3-none-any.whl").write_bytes(data=b"whl")
        if "--target" in arguments:
            stage: Path = Path(arguments[arguments.index("--target") + 1])
            (stage / "demo").mkdir(exist_ok=True)
            (stage / "demo" / "__init__.py").write_text(
                data="",
                encoding="utf-8",
            )
        return ""

    project: ProjectConfig = _project(root=tmp_path / "proj")
    runner: Hosts = Hosts(handler=cross_handler)
    built: Path = PyzBuilder(open_host=runner).build(
        project=project,
        config=PyzConfig(
            name="demo-app",
            bootstrap=project.root / "boot.py",
        ),
        output=tmp_path / "out",
        target=PyzTarget(platform_name="windows", architecture="amd64"),
    )
    expect(actual=built.name, expected="demo-app-1.2.3-windows-amd64.pyz")
    install: tuple[str, ...] = runner.commands[-1]
    check(condition="--platform" in install, reason=str(object=install))
    check(condition="win_amd64" in install, reason=str(object=install))
    check(
        condition="--python-version" in install,
        reason=str(object=install),
    )
    check(
        condition=not any("-c" in command for command in runner.commands),
        reason="foreign archives are not smoke-tested",
    )
    bundle: ZipFile = ZipFile(file=built)
    with bundle:
        names: set[str] = set(bundle.namelist())
    check(condition="__main__.py" in names, reason=str(object=names))
    check(condition="demo/__init__.py" in names, reason=str(object=names))


def test_cross_build_without_wheels_fails(tmp_path: Path) -> None:
    """A cell with no known wheel platform fails before resolving.

    Args:
        tmp_path: Pytest temporary directory.

    """
    project: ProjectConfig = _project(root=tmp_path / "proj")
    runner: Hosts = Hosts()
    with pytest.raises(expected_exception=BuildError, match="wheel platform"):
        _ = PyzBuilder(open_host=runner).build(
            project=project,
            config=PyzConfig(
                name="demo",
                bootstrap=project.root / "boot.py",
            ),
            output=tmp_path / "out",
            target=PyzTarget(platform_name="amiga", architecture="m68k"),
        )
