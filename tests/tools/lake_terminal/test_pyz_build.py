# Copyright (c) 2026 Project contributors.
"""Tests for the single-source version and the ``.pyz`` build."""

from __future__ import annotations

from dataclasses import replace
from os import environ
from pathlib import Path
from shutil import copytree
from subprocess import CompletedProcess, run
from sys import executable
from tomllib import loads
from types import SimpleNamespace
from typing import ClassVar, Final, cast, final
from zipfile import ZipFile, is_zipfile

import pytest

from tests.conftest import sibling_tree
from tools.lake_terminal import pyz_main
from tools.lake_terminal.lake_terminal import __version__ as package_version
from tools.lake_terminal.lake_terminal import installer as installer_module
from tools.lake_terminal.lake_terminal import update
from tools.lake_terminal.lake_terminal.installer import running_archive
from tools.lake_terminal.lake_terminal.product import Product
from tools.lake_terminal.lake_terminal.update import (
    Asset,
    Release,
    UpdateConstants,
    platform_tag,
    select_asset,
)
from tools.release_builder import (
    BuildConfiguration,
    CommandRunner,
    ManifestLoader,
    pyz_builder,
)
from tools.release_builder.pyz_builder import (
    PyzBuilder,
    PyzConfig,
    archive_name,
)

from .helpers import check, expect, section


@final
class Constants:
    """Group module-level constants.

    Attributes:
        project: Grouped module constant.

    """

    project: Final[Path] = (
        sibling_tree().project.parent.parent / "tools" / "lake_terminal"
    )


def test_pyproject_version_comes_from_product() -> None:
    """There is one version literal, in ``product.py``."""
    config: dict[str, dict[str, dict[str, dict[str, object]]]] = loads(
        (Constants.project / "pyproject.toml").read_text(encoding="utf-8"),
    )
    expect(actual=config["project"]["dynamic"], expected=["version"])
    check(
        condition="version" not in config["project"],
        reason="static version",
    )
    expect(
        actual=config["tool"]["setuptools"]["dynamic"]["version"],
        expected={"attr": "lake_terminal.__version__"},
    )
    expect(actual=package_version, expected=Product.version)


def invoke(*arguments: str, env: dict[str, str]) -> CompletedProcess[str]:
    """Run the current interpreter with ``arguments`` and capture output.

    Args:
        *arguments: Interpreter arguments.
        env: Child environment.
        arguments: Command-line arguments.

    Returns:
        The completed process.

    """
    command: tuple[str, ...] = (executable, *arguments)
    return run(
        args=command,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def make_archive(*, path: Path, marker: str) -> Path:
    """Write a tiny zip standing in for an archive.

    Args:
        path: Destination.
        marker: File content, so builds differ.

    Returns:
        The archive path.

    """
    with ZipFile(file=path, mode="w") as bundle:
        bundle.writestr(zinfo_or_arcname="pkg/value.txt", data=marker)
    return path


def test_extract_is_idempotent_and_prunes(tmp_path: Path) -> None:
    """Extract once per archive build; drop caches of replaced builds.

    Args:
        tmp_path: Pytest temporary directory.

    """
    archive: Path = make_archive(
        path=tmp_path / "lake-terminal.pyz",
        marker="one",
    )
    root: Path = tmp_path / "cache"
    first: Path = pyz_main.extract(archive=archive, root=root)
    expect(actual=(first / "pkg" / "value.txt").read_text(), expected="one")
    marker: Path = first / "pkg" / "value.txt"
    _ = marker.write_text(data="touched")
    again: Path = pyz_main.extract(archive=archive, root=root)
    expect(actual=again, expected=first)
    expect(actual=marker.read_text(), expected="touched")
    _ = make_archive(path=archive, marker="two-longer")
    second: Path = pyz_main.extract(archive=archive, root=root)
    check(condition=second != first, reason="new build, new cache")
    expect(
        actual=(second / "pkg" / "value.txt").read_text(),
        expected="two-longer",
    )
    check(condition=not first.exists(), reason="stale cache pruned")
    expect(actual=[p.name for p in root.iterdir()], expected=[second.name])


def test_running_archive_uses_argv_when_extracted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After extraction ``__file__`` is not in the zip; argv still is.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    archive: Path = make_archive(path=tmp_path / "lake.pyz", marker="x")
    monkeypatch.setattr(
        target=installer_module,
        name="argv",
        value=[str(object=archive), "--version"],
    )
    expect(actual=running_archive(), expected=archive)
    monkeypatch.setattr(
        target=installer_module,
        name="argv",
        value=["/usr/bin/pytest"],
    )
    expect(actual=running_archive(), expected=None)


@final
class PackageRunner(CommandRunner):
    """Run real commands, but stage the package instead of calling pip.

    Attributes:
        project: Project whose ``lake_terminal`` package is staged.

    """

    project: ClassVar[Path]

    def __init__(self, *, project: Path) -> None:
        """Remember the project to stage.

        Args:
            project: Project directory.

        """
        PackageRunner.project = project

    @staticmethod
    def execute(
        *,
        arguments: tuple[str, ...],
        cwd: Path | None = None,
        timeout_seconds: int = 1800,
    ) -> CompletedProcess[str]:
        """Copy the package for ``pip``; run everything else for real.

        Args:
            arguments: Command and arguments.
            cwd: Optional working directory.
            timeout_seconds: Maximum command duration.

        Returns:
            The completed process.

        """
        if "pip" in arguments:
            target: Path = Path(arguments[arguments.index("--target") + 1])
            _ = copytree(
                src=PackageRunner.project / "lake_terminal",
                dst=target / "lake_terminal",
            )
            return CompletedProcess(
                args=arguments,
                returncode=0,
                stdout="",
                stderr="",
            )
        return CommandRunner.execute(
            arguments=arguments,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
        )


def test_archive_names_match_what_the_updater_selects() -> None:
    """release_builder names the file ``select_asset`` looks for."""
    release: Release = Release(
        tag=f"lake-terminal/v{Product.version}",
        version=Product.version,
        url="",
        prerelease=False,
        assets=(),
    )
    expected: str = archive_name(
        name="lake-terminal",
        version=Product.version,
        tag=platform_tag(),
    )
    asset: Asset = Asset(name=expected, url="https://example.test/a")
    found: Asset | None = select_asset(
        release=replace(release, assets=(asset,)),
    )
    expect(actual=found, expected=asset)


@pytest.mark.parametrize(
    ("system_name", "machine_name"),
    [
        ("Darwin", "arm64"),
        ("Darwin", "x86_64"),
        ("Linux", "x86_64"),
        ("Linux", "aarch64"),
        ("Windows", "AMD64"),
        ("Windows", "ARM64"),
    ],
)
def test_builder_and_updater_agree_on_platform_tags(
    system_name: str,
    machine_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tag in the built file name is the tag the updater computes.

    Args:
        system_name: Reported operating system.
        machine_name: Reported architecture.
        monkeypatch: Pytest monkeypatch fixture.

    """
    for module in (update, pyz_builder):
        monkeypatch.setattr(
            target=module,
            name="system",
            value=lambda: system_name,
        )
        monkeypatch.setattr(
            target=module,
            name="machine",
            value=lambda: machine_name,
        )
    expect(actual=pyz_builder.platform_tag(), expected=update.platform_tag())


def test_manifest_builds_the_archive_the_updater_expects() -> None:
    """The shipped ``[pyz]`` table names the asset and the bootstrap."""
    config: PyzConfig = PyzConfig.load(
        manifest=Constants.project / "release-builder.toml",
        project_root=Constants.project,
        default_name="unused",
    )
    expect(actual=config.name, expected="lake-terminal")
    expect(
        actual=config.bootstrap,
        expected=(Constants.project / "pyz_main.py").resolve(),
    )


def test_built_archive_runs_and_propagates_exit_codes(tmp_path: Path) -> None:
    """Build a real archive with release_builder and run it as a user would.

    Args:
        tmp_path: Pytest temporary directory.

    """
    configuration: BuildConfiguration = ManifestLoader.load(
        manifest=Constants.project / "release-builder.toml",
        project_root=Constants.project,
        platforms=("linux",),
    )
    built: Path = PyzBuilder(
        runner=PackageRunner(project=Constants.project),
    ).build(
        project=configuration.project,
        config=PyzConfig.load(
            manifest=Constants.project / "release-builder.toml",
            project_root=Constants.project,
            default_name=configuration.project.name,
        ),
        output=tmp_path / "dist",
        tag="testplat-x1",
    )
    expect(
        actual=built.name,
        expected=f"lake-terminal-{Product.version}-testplat-x1.pyz",
    )
    check(condition=is_zipfile(filename=built), reason="zip archive")
    expect(
        actual=sorted(p.name for p in built.parent.iterdir()),
        expected=[built.name],
    )
    with ZipFile(file=built) as bundle:
        names: set[str] = set(bundle.namelist())
    check(condition="__main__.py" in names, reason=str(object=names))
    check(condition="lake_terminal/cli.py" in names, reason="package bundled")
    env: dict[str, str] = {
        **environ,
        "HOME": str(object=tmp_path / "home"),
        "USERPROFILE": str(object=tmp_path / "home"),
    }
    (tmp_path / "home").mkdir()
    version: CompletedProcess[str] = invoke(
        str(object=built),
        "--version",
        env=env,
    )
    expect(actual=version.returncode, expected=0)
    expect(
        actual=version.stdout,
        expected=f"lake-terminal {Product.version}\n",
    )
    cached: list[Path] = list(
        (tmp_path / "home" / ".lake_terminal" / "pyz-cache").iterdir(),
    )
    expect(actual=len(cached), expected=1)
    usage_error: CompletedProcess[str] = invoke(
        str(object=built),
        "--update",
        "--bogus",
        env=env,
    )
    expect(actual=usage_error.returncode, expected=2)
    settings: CompletedProcess[str] = invoke(
        str(object=built),
        "--settings",
        env=env,
    )
    expect(actual=settings.returncode, expected=0)
    check(
        condition=f'"version": "{Product.version}"' in settings.stdout,
        reason=settings.stdout,
    )


def _workflow() -> str:
    """Return the release workflow text.

    Returns:
        The contents of ``lake-release.yml``.

    """
    return (
        Constants.project.parent.parent
        / ".github"
        / "workflows"
        / "lake-release.yml"
    ).read_text(encoding="utf-8")


def test_release_workflow_matches_python_contract() -> None:
    """The workflow and the Python side agree on tags and runners."""
    workflow: str = _workflow()
    check(
        condition=f'tags: ["{UpdateConstants.default_tag_prefix}*"]'
        in workflow,
        reason="tag trigger must use the default tag prefix",
    )
    for runner in (
        "macos-14",
        "macos-15-intel",
        "ubuntu-latest",
        "ubuntu-24.04-arm",
        "windows-latest",
    ):
        check(condition=runner in workflow, reason=runner)
    check(condition="workflow_dispatch" in workflow, reason="manual trigger")


def test_release_workflow_only_uses_the_release_tools() -> None:
    """Building and publishing go through release_builder/release_publish."""
    workflow: str = _workflow()
    check(
        condition="tools.release_builder" in workflow
        and "--format pyz" in workflow
        and "release-builder.toml" in workflow,
        reason="archives are built by release_builder",
    )
    check(
        condition=(
            "tools.release_publish.artifacts dist --name "
            f"{UpdateConstants.checksum_asset}"
        )
        in workflow,
        reason="the updater's checksum asset is written by release_publish",
    )
    check(
        condition="tools.release_publish --config release.toml show"
        in workflow,
        reason="project directory and version come from release.toml",
    )
    check(
        condition="tools.release_publish --config release.toml publish"
        in workflow,
        reason="the GitHub release is created by release_publish",
    )
    for forbidden in (
        "build_pyz",
        "gh release create",
        "cat *.sha256",
        "re.search",
    ):
        check(
            condition=forbidden not in workflow,
            reason=f"{forbidden!r} duplicates a release tool",
        )


def test_cache_root_points_at_the_lake_state_directory() -> None:
    """Archive builds unpack beside the rest of Lake Terminal's state."""
    root: Path = pyz_main.cache_root()
    expect(actual=root.name, expected="pyz-cache")
    expect(actual=root.parent.name, expected=".lake_terminal")


def test_main_dispatches_the_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-``run`` invocations go to the Lake Terminal CLI.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    archive: Path = make_archive(path=tmp_path / "lake.pyz", marker="cli")
    calls: list[str] = []

    def fake_cli(*, arguments: tuple[str, ...]) -> int:
        """Record the CLI invocation.

        Args:
            arguments: Command-line arguments.

        Returns:
            A sentinel status.

        """
        del arguments
        calls.append("cli")
        return 7

    def fake_import(name: str) -> SimpleNamespace:
        """Return a module stub with the CLI entry point.

        Args:
            name: Module name being imported.

        Returns:
            The stub module.

        """
        del name
        return SimpleNamespace(main=fake_cli)

    monkeypatch.setattr(
        target=pyz_main,
        name="import_module",
        value=fake_import,
    )
    monkeypatch.setattr(
        target=pyz_main,
        name="argv",
        value=[str(object=archive), "--version"],
    )
    monkeypatch.setattr(target=pyz_main, name="path", value=[])
    monkeypatch.setattr(
        target=pyz_main,
        name="cache_root",
        value=lambda: tmp_path / "cache",
    )
    expect(actual=pyz_main.main(), expected=7)
    expect(actual=calls, expected=["cli"])


def test_main_runs_the_installer_for_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run ...`` is dispatched to the installer entry point.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    archive: Path = make_archive(path=tmp_path / "lake.pyz", marker="run")
    seen: list[tuple[str, ...]] = []

    def fake_install(*, arguments: tuple[str, ...]) -> int:
        """Record the installer invocation.

        Args:
            arguments: Command-line arguments after ``run``.

        Returns:
            A sentinel status.

        """
        seen.append(arguments)
        return 9

    def fake_import(name: str) -> SimpleNamespace:
        """Return a module stub with the installer entry point.

        Args:
            name: Module name being imported.

        Returns:
            The stub module.

        """
        del name
        return SimpleNamespace(main=fake_install)

    monkeypatch.setattr(
        target=pyz_main,
        name="import_module",
        value=fake_import,
    )
    monkeypatch.setattr(
        target=pyz_main,
        name="argv",
        value=[str(object=archive), "run", "--list"],
    )
    monkeypatch.setattr(target=pyz_main, name="path", value=[])
    monkeypatch.setattr(
        target=pyz_main,
        name="cache_root",
        value=lambda: tmp_path / "cache",
    )
    expect(actual=pyz_main.main(), expected=9)
    expect(actual=seen, expected=[("--list",)])


def test_main_falls_back_to_a_temp_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unwritable home cache falls back to the temp directory.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    archive: Path = make_archive(path=tmp_path / "lake.pyz", marker="x")
    blocked: Path = tmp_path / "blocked"
    blocked.write_text(data="not a directory", encoding="utf-8")

    def fake_cli(*, arguments: tuple[str, ...]) -> int:
        """Return a sentinel status.

        Args:
            arguments: Command-line arguments.

        Returns:
            A sentinel status.

        """
        _ = arguments
        return 3

    def fake_import(name: str) -> SimpleNamespace:
        """Return a module stub with the CLI entry point.

        Args:
            name: Module name being imported.

        Returns:
            The stub module.

        """
        del name
        return SimpleNamespace(main=fake_cli)

    monkeypatch.setattr(
        target=pyz_main,
        name="import_module",
        value=fake_import,
    )
    monkeypatch.setattr(
        target=pyz_main,
        name="argv",
        value=[str(object=archive)],
    )
    monkeypatch.setattr(target=pyz_main, name="path", value=[])
    monkeypatch.setattr(
        target=pyz_main,
        name="cache_root",
        value=lambda: blocked,
    )
    monkeypatch.setattr(
        target=pyz_main,
        name="gettempdir",
        value=lambda: str(object=tmp_path / "temp"),
    )
    expect(actual=pyz_main.main(), expected=3)


def _project_table(*, path: Path) -> dict[str, object]:
    """Return the ``[project]`` table of a TOML file.

    Args:
        path: TOML file.

    Returns:
        The table as a concrete mapping.

    """
    document: dict[str, object] = section(
        value=loads(path.read_text(encoding="utf-8")),
    )
    return section(value=document["project"])


def test_arrow_is_a_declared_dependency_installed_with_lake() -> None:
    """Lake imports ``arrow_uri``, so it must be declared and installable."""
    lake: dict[str, object] = _project_table(
        path=Constants.project / "pyproject.toml",
    )
    arrow: dict[str, object] = _project_table(
        path=Constants.project.parent / "arrow" / "pyproject.toml",
    )
    name: str = str(object=arrow["name"])
    dependencies: list[str] = cast(typ="list[str]", val=lake["dependencies"])
    check(
        condition=any(
            dependency.startswith(name) for dependency in dependencies
        ),
        reason=f"{name} missing from lake-terminal dependencies",
    )
    manifest: dict[str, object] = _project_table(
        path=Constants.project / "release-builder.toml",
    )
    expect(actual=manifest["local_dependencies"], expected=["../arrow"])
    root: Path = Constants.project.parent.parent
    for workflow in ("ci.yml", "lake-release.yml"):
        text: str = (root / ".github" / "workflows" / workflow).read_text(
            encoding="utf-8",
        )
        check(
            condition="-e tools/arrow -e tools/lake_terminal" in text,
            reason=f"{workflow} must install Arrow with Lake Terminal",
        )
