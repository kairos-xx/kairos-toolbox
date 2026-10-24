# Copyright (c) 2026 Project contributors.
"""Tests for the ``<os>/<arch>/<abi>/<package>/`` output layout."""

from __future__ import annotations

from json import dumps, loads
from pathlib import Path

import pytest

from tools.stationer import (
    BuildConfiguration,
    BuildError,
    Cli,
    LinuxDockerBuilder,
    MacOSBuilder,
    MacOSConfig,
    ReleaseBuilder,
    ReleasePublisher,
    WindowsQemuBuilder,
)
from tools.stationer.builder import (
    ArtifactUtilities,
    BuildResult,
)

from .fake_sessions import Hosts, Opener
from .helpers import check, expect
from .test_builder import (
    linux_config,
    macos_handler,
    project,
    windows_config,
)


def _configuration(*, root: Path, macos: MacOSConfig) -> BuildConfiguration:
    """Create a configuration for all platforms.

    Args:
        root: Project root.
        macos: macOS configuration under test.

    Returns:
        The configuration.

    """
    identity: Path = root.parent / "id"
    identity.write_text(data="key\n", encoding="utf-8")
    return BuildConfiguration(
        project=project(root=root),
        linux=linux_config(),
        macos=macos,
        windows=windows_config(identity=identity),
    )


def _macos(*, python_amd64: str | None = "x86-python") -> MacOSConfig:
    """Create a macOS configuration.

    Args:
        python_amd64: The x86_64 interpreter, if any.

    Returns:
        The configuration.

    """
    return MacOSConfig(
        python="arm-python",
        signing_identity=None,
        python_amd64=python_amd64,
    )


def test_helpers_name_the_three_subfolders() -> None:
    """The helper functions are the only source of the folder names."""
    package: Path = Path("build/linux/arm64/glibc/demo")
    expect(
        actual=ArtifactUtilities.pyinstaller_folder(package=package),
        expected=package / "pyinstaller",
    )
    expect(
        actual=ArtifactUtilities.installer_folder(package=package),
        expected=package / "installer",
    )
    expect(
        actual=ArtifactUtilities.pyz_folder(package=package),
        expected=package / "pyz",
    )


def test_both_macos_architectures_get_their_own_folders(
    tmp_path: Path,
) -> None:
    """arm64 and amd64 coexist, with installers under ``installer/``.

    Args:
        tmp_path: Pytest temporary directory.

    """
    hosts: Hosts = Hosts(handler=macos_handler)
    output: Path = tmp_path / "build"
    results: tuple[BuildResult, ...] = ReleaseBuilder(
        open_host=hosts,
        containers=Opener(),
        guests=Opener(),
    ).build(
        configuration=_configuration(root=tmp_path / "src", macos=_macos()),
        output=output,
        platforms=("macos",),
        macos_architectures=("arm64", "amd64"),
    )
    for arch in ("arm64", "amd64"):
        package: Path = output / "macos" / arch / "native" / "demo"
        check(
            condition=(
                package / "pyinstaller" / f"demo-1.2.3-macos-{arch}"
            ).is_file(),
            reason=f"{arch} executable",
        )
        check(
            condition=(
                package / "installer" / f"demo-1.2.3-macos-{arch}.pkg"
            ).is_file(),
            reason=f"{arch} installer",
        )
        check(
            condition=not (package / ".work").exists()
            and not (package / "pyinstaller" / "dist").exists(),
            reason="scratch folders are removed",
        )
    expect(
        actual=[r.architecture for r in results],
        expected=["arm64", "amd64"],
    )
    entries: list[dict[str, str]] = loads(
        s=(output / "artifacts.json").read_text(encoding="utf-8"),
    )
    expect(
        actual=sorted(e["architecture"] for e in entries),
        expected=["amd64", "arm64"],
    )
    check(
        condition=all(e["minimum_macos"] == "26.3" for e in entries),
        reason=str(object=entries),
    )
    check(
        condition=any(
            command[:2] == ("arch", "-x86_64") for command in hosts.commands
        ),
        reason="amd64 runs under Rosetta",
    )
    text: str = (output / "BUILD.md").read_text(encoding="utf-8")
    check(
        condition="macos/amd64/native/demo/installer/" in text,
        reason=text,
    )


def test_minimum_macos_can_be_overridden(tmp_path: Path) -> None:
    """``[macos] minimum_macos`` wins over the host's version.

    Args:
        tmp_path: Pytest temporary directory.

    """
    result: BuildResult = MacOSBuilder(
        open_host=Hosts(handler=macos_handler),
    ).build(
        project=project(root=tmp_path / "src"),
        config=MacOSConfig(
            python="arm-python",
            signing_identity=None,
            minimum_macos="12.0",
        ),
        output=tmp_path / "out",
        architecture="arm64",
    )
    expect(actual=result.minimum_macos, expected="12.0")


def test_lipo_must_report_exactly_the_target_architecture(
    tmp_path: Path,
) -> None:
    """A fat or wrong-architecture result fails the build.

    Args:
        tmp_path: Pytest temporary directory.

    """

    def fat(*, arguments: tuple[str, ...]) -> str | None:
        """Report a universal binary for ``lipo``.

        Args:
            arguments: Command being faked.

        Returns:
            ``lipo`` output, or the shared handler's answer.

        """
        if "lipo" in arguments:
            return "x86_64 arm64\n"
        return macos_handler(arguments=arguments)

    with pytest.raises(expected_exception=BuildError, match="exactly arm64"):
        _ = MacOSBuilder(open_host=Hosts(handler=fat)).build(
            project=project(root=tmp_path / "src"),
            config=_macos(),
            output=tmp_path / "out",
            architecture="arm64",
        )
    check(
        condition=not (tmp_path / "out" / ".work").exists(),
        reason="scratch folder removed after a failure",
    )


def test_amd64_needs_a_configured_x86_64_python(tmp_path: Path) -> None:
    """Missing, wrong-architecture or unrunnable interpreters are refused.

    Args:
        tmp_path: Pytest temporary directory.

    """
    source: Path = tmp_path / "src"
    with pytest.raises(expected_exception=BuildError, match="python_amd64"):
        _ = MacOSBuilder(open_host=Hosts(handler=macos_handler)).build(
            project=project(root=source),
            config=_macos(python_amd64=None),
            output=tmp_path / "a",
            architecture="amd64",
        )

    def arm_only(*, arguments: tuple[str, ...]) -> str | None:
        """Report an arm64 interpreter even under ``arch -x86_64``.

        Args:
            arguments: Command being faked.

        Returns:
            The probe's answer, or the shared handler's.

        """
        if any("platform.machine()" in item for item in arguments):
            return "arm64\n"
        return macos_handler(arguments=arguments)

    with pytest.raises(expected_exception=BuildError, match="x86_64 Python"):
        _ = MacOSBuilder(open_host=Hosts(handler=arm_only)).build(
            project=project(root=source),
            config=_macos(),
            output=tmp_path / "b",
            architecture="amd64",
        )

    def missing(*, arguments: tuple[str, ...]) -> str | None:
        """Raise like a missing interpreter.

        Args:
            arguments: Command being faked.

        Returns:
            Standard output for the command, if any.

        Raises:
            FileNotFoundError: For the interpreter probe.

        """
        if any("platform.machine()" in item for item in arguments):
            raise FileNotFoundError(arguments[0])
        return macos_handler(arguments=arguments)

    with pytest.raises(expected_exception=BuildError, match="Cannot run"):
        _ = MacOSBuilder(open_host=Hosts(handler=missing)).build(
            project=project(root=source),
            config=_macos(),
            output=tmp_path / "c",
            architecture="amd64",
        )


@pytest.mark.parametrize(
    ("argument", "expected"),
    [
        (None, ("native",)),
        ("native", ("native",)),
        ("all", ("arm64", "amd64")),
        ("amd64", ("amd64",)),
    ],
)
def test_arch_selects_the_macos_builds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argument: str | None,
    expected: tuple[str, ...],
) -> None:
    """``--arch`` expands to the architectures handed to the builder.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
        argument: ``--arch`` value, if any.
        expected: Architectures the builder must receive.

    """
    seen: list[tuple[str, ...]] = []

    def fake_build(
        self: ReleaseBuilder,
        *,
        macos_architectures: tuple[str, ...] = ("native",),
        **ignored: object,
    ) -> tuple[()]:
        """Record the requested architectures.

        Args:
            self: Ignored builder.
            macos_architectures: Architectures requested.
            **ignored: Other build arguments.

        Returns:
            No results.

        """
        _ = (self, ignored)
        seen.append(macos_architectures)
        return ()

    monkeypatch.setattr(target=ReleaseBuilder, name="build", value=fake_build)
    root: Path = tmp_path / "src"
    root.mkdir()
    manifest: Path = tmp_path / "m.toml"
    manifest.write_text(
        data=(
            '[project]\nname = "demo"\nversion = "1"\nentrypoint = "app.py"\n'
        ),
        encoding="utf-8",
    )
    (root / "app.py").write_text(data="print()\n", encoding="utf-8")
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
            *(("--arch", argument) if argument is not None else ()),
        ),
    )
    expect(actual=(status, seen), expected=(0, [expected]))


def test_linux_and_windows_put_installers_under_installer(
    tmp_path: Path,
) -> None:
    """The ``.deb`` and ``setup.exe`` live in ``installer/``.

    Args:
        tmp_path: Pytest temporary directory.

    """
    linux: Path = tmp_path / "linux"
    result: BuildResult = LinuxDockerBuilder(open_session=Opener()).build(
        project=project(root=tmp_path / "src"),
        config=linux_config(),
        output=linux,
    )
    expect(
        actual=result.installer.parent.name if result.installer else "",
        expected="installer",
    )
    expect(actual=result.executable.parent.name, expected="pyinstaller")
    identity: Path = tmp_path / "id"
    identity.write_text(data="k\n", encoding="utf-8")
    windows: Path = tmp_path / "windows"
    outcome: BuildResult = WindowsQemuBuilder(open_session=Opener()).build(
        project=project(root=tmp_path / "src"),
        config=windows_config(identity=identity),
        output=windows,
    )
    expect(
        actual=outcome.installer.parent.name if outcome.installer else "",
        expected="installer",
    )
    expect(actual=outcome.executable.parent.name, expected="pyinstaller")


def test_publisher_finds_installers_in_the_new_location(
    tmp_path: Path,
) -> None:
    """``ReleasePublisher.files`` follows the manifest into ``installer/``.

    Args:
        tmp_path: Pytest temporary directory.

    """
    package: Path = tmp_path / "linux" / "arm64" / "glibc" / "demo"
    for relative in ("pyinstaller/demo", "installer/demo.deb"):
        target: Path = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data="x", encoding="utf-8")
    (tmp_path / "artifacts.json").write_text(
        data=dumps(
            obj=[
                {
                    "executable": "linux/arm64/glibc/demo/pyinstaller/demo",
                    "installer": "linux/arm64/glibc/demo/installer/demo.deb",
                },
            ],
        ),
        encoding="utf-8",
    )
    names: list[str] = [
        path.name for path in ReleasePublisher.files(output=tmp_path)
    ]
    expect(actual=names, expected=["demo.deb", "demo"])
