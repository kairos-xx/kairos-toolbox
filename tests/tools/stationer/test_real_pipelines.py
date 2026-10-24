# Copyright (c) 2026 Project contributors.
"""Real-environment pipeline tests.

The unit suite fakes every subprocess. These tests run the actual
pipelines where the host can support them:

- Linux inside Docker (``docker info`` must succeed),
- macOS on a real macOS host,
- Windows through the UTM guest agent (no SSH): the VM named in
  ``[tool.stationer.tests]``, driven by ``utmctl``,
- PyPI distributions, which need network access to fetch ``build``,
- a Windows build through ``--remote windows`` inside that same VM, where
  the guest pulls the job from this host.

No test is skipped for a missing environment: Docker, macOS, network and a
Windows VM are required, and a machine without them fails loudly.

Each build installs the project *and* its sibling dependency from
staged copies, so they exercise the copy-based install path for real.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from os import devnull, environ
from pathlib import Path
from platform import machine
from sys import executable
from tomllib import load
from typing import TYPE_CHECKING, Final, cast, final

from tests.tools.stationer.helpers import check, expect, present
from tools.conjure import SettingsLoader, UtmRunner
from tools.stationer import (
    BuildResult,
    LinuxConfig,
    LinuxDockerBuilder,
    MacOSBuilder,
    MacOSConfig,
    PosixGuestConfig,
    ProjectConfig,
    PypiBuilder,
    RemoteBuilder,
    RemoteSettings,
    WindowsConfig,
    WindowsQemuBuilder,
)

if TYPE_CHECKING:
    from collections.abc import Generator

    import pytest


@final
class Constants:
    """Group module-level constants.

    Attributes:
        pyproject: Grouped module constant.
        utm_prefix: Grouped module constant.
        python_root: Grouped module constant.
        python_url: Grouped module constant.

    """

    pyproject: Final[Path] = (
        Path(__file__).resolve().parents[3] / "pyproject.toml"
    )
    utm_prefix: Final[str] = "utm:"
    python_root: Final[str] = "C:\\stationer-test-python"
    python_url: Final[str] = (
        "https://api.nuget.org/v3-flatcontainer/pythonarm64/3.13.9/"
        "pythonarm64.3.13.9.nupkg"
    )


def _section(*, parent: dict[str, object], key: str) -> dict[str, object]:
    """Return one nested TOML table, empty when it is absent.

    Args:
        parent: Enclosing table.
        key: Name of the nested table.

    Returns:
        The nested table.

    """
    value: object = parent.get(key)
    if isinstance(value, dict):
        return cast(typ="dict[str, object]", val=value)
    return {}


def _configured(*, table: str, key: str) -> str | None:
    """Read one string from a ``[tool.<table>]`` table of the repository.

    Args:
        table: Dotted table path below ``[tool]``.
        key: Key inside the table.

    Returns:
        The configured text, or ``None`` when it is not set.

    """
    with Constants.pyproject.open(mode="rb") as stream:
        document: dict[str, object] = load(stream)
    section: dict[str, object] = _section(parent=document, key="tool")
    for part in table.split(sep="."):
        section = _section(parent=section, key=part)
    value: object = section.get(key)
    return value if isinstance(value, str) else None


def _required(*, value: str | None, reason: str) -> str:
    """Fail the test with ``reason`` when a setting is missing.

    Args:
        value: The discovered setting.
        reason: What to configure when it is missing.

    Returns:
        The setting.

    """
    check(condition=bool(value), reason=reason)
    return value or ""


def _windows_vm() -> str:
    """Return the VM the Windows tests build in.

    Returns:
        ``STATIONER_WINDOWS_VM``, else ``windows_vm`` of
        ``[tool.stationer.tests]``, else the VM of
        ``[tool.stationer.builder]``.

    """
    return _required(
        value=environ.get("STATIONER_WINDOWS_VM")
        or _configured(table="stationer.tests", key="windows_vm")
        or _configured(table="stationer.builder", key="vm"),
        reason=(
            "No Windows VM is configured: set STATIONER_WINDOWS"
            "_VM or [tool.stationer.tests] windows_vm in "
            "pyproject.toml."
        ),
    )


@contextmanager
def _windows_guest() -> Generator[UtmRunner]:
    """Provide a running Windows guest driven through the UTM guest agent.

    A guest that was off is booted for the duration and powered off
    afterwards; one that was already running is left running.

    Yields:
        The started guest session.

    """
    vm: str = _windows_vm()
    check(
        condition=vm.startswith(Constants.utm_prefix),
        reason=f"the guest-agent test needs a UTM guest, got {vm!r}",
    )
    with UtmRunner(
        name=vm.removeprefix(Constants.utm_prefix),
        stop_on_close=True,
    ) as guest:
        yield guest


@contextmanager
def _guest_python(*, guest: UtmRunner) -> Generator[str]:
    """Install a throw-away Python in the guest and remove it afterwards.

    The guest ships without Python and the VM must stay as it was found, so
    the interpreter lives in a temporary folder for the length of the build.

    Args:
        guest: The started guest session.

    Yields:
        Path of ``python.exe`` inside the guest.

    """
    root: str = Constants.python_root
    shell: tuple[str, ...] = (
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
    )
    _ = guest.run(
        command=(
            *shell,
            "$ErrorActionPreference='Stop'; "
            f"Remove-Item -LiteralPath '{root}' -Recurse -Force "
            "-ErrorAction SilentlyContinue; "
            f"New-Item -ItemType Directory -Force -Path '{root}' | Out-Null; "
            "Invoke-WebRequest -UseBasicParsing "
            f"-Uri '{Constants.python_url}' "
            f"-OutFile '{root}\\python.zip'; "
            f"Expand-Archive -Path '{root}\\python.zip' "
            f"-DestinationPath '{root}\\py' -Force",
        ),
        check=True,
        timeout_seconds=900,
    )
    try:
        yield f"{root}\\py\\tools\\python.exe"
    finally:
        _ = guest.run(
            command=(
                *shell,
                f"Remove-Item -LiteralPath '{root}' -Recurse -Force "
                "-ErrorAction SilentlyContinue",
            ),
            check=False,
            timeout_seconds=120,
        )


def _agent_config(*, python: str) -> WindowsConfig:
    """Describe the guest for the builder; no SSH endpoint is involved.

    The builder talks to the guest through the session it is handed, so the
    SSH fields only satisfy the configuration type.

    Args:
        python: Path of ``python.exe`` inside the guest.

    Returns:
        The Windows worker configuration.

    """
    return WindowsConfig(
        host="guest-agent",
        port=0,
        user="guest-agent",
        identity_file=Path(devnull),
        python=(python,),
        remote_root="C:\\stationer",
        publisher="Tests",
    )


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


def test_linux_pipeline_builds_a_real_deb(tmp_path: Path) -> None:
    """The Linux pipeline installs from copies inside Docker.

    Args:
        tmp_path: Pytest temporary directory.

    """
    architecture: str = (
        "linux/arm64" if machine() in {"arm64", "aarch64"} else "linux/amd64"
    )
    result: BuildResult = LinuxDockerBuilder().build(
        project=_real_project(root=tmp_path),
        config=LinuxConfig(
            docker_platform=architecture,
            image="python:3.13-slim-bookworm",
            maintainer="Tests <tests@example.com>",
            description="Real Docker pipeline test",
        ),
        output=tmp_path / "linux",
    )
    check(
        condition=present(value=result.installer).is_file(),
        reason="the .deb must exist",
    )
    with present(value=result.installer).open(mode="rb") as stream:
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


def test_macos_pipeline_builds_a_real_pkg(tmp_path: Path) -> None:
    """The macOS pipeline installs from staged copies on the host.

    Args:
        tmp_path: Pytest temporary directory.

    """
    result: BuildResult = MacOSBuilder().build(
        project=_real_project(root=tmp_path),
        config=MacOSConfig(python=executable, signing_identity=None),
        output=tmp_path / "macos",
    )
    check(
        condition=present(value=result.installer).is_file(),
        reason="the .pkg must exist",
    )
    with present(value=result.installer).open(mode="rb") as stream:
        magic: bytes = stream.read(4)
    check(
        condition=magic == b"xar!",
        reason="the artifact must be a real xar package",
    )


def test_pypi_pipeline_builds_real_distributions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The PyPI pipeline builds dependency and project distributions.

    The working directory holds a ``build`` folder, like the repository
    root does, to prove it cannot shadow the PyPA ``build`` tool.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    work: Path = tmp_path / "work"
    (work / "build").mkdir(parents=True)
    monkeypatch.chdir(path=work)
    distributions: tuple[Path, ...] = PypiBuilder().build(
        project=_real_project(root=tmp_path),
        output=tmp_path / "pypi",
    )
    names: list[str] = [path.name for path in distributions]
    expect(
        actual=names,
        expected=[
            "dep_pkg-0.1.0-py3-none-any.whl",
            "dep_pkg-0.1.0.tar.gz",
            "demo-1.2.3-py3-none-any.whl",
            "demo-1.2.3.tar.gz",
        ],
    )


def test_windows_pipeline_via_qemu_worker(tmp_path: Path) -> None:
    """The Windows pipeline builds in a real Windows VM through the agent.

    Args:
        tmp_path: Pytest temporary directory.

    """

    def open_agent(
        *,
        config: WindowsConfig | PosixGuestConfig,
    ) -> UtmRunner:
        """Hand the builder a non-owning session on the running guest.

        Args:
            config: Ignored; the guest is already chosen.

        Returns:
            A session that never powers the guest off.

        """
        _ = config
        return UtmRunner(name=_windows_vm().removeprefix(Constants.utm_prefix))

    with _windows_guest() as guest, _guest_python(guest=guest) as python:
        result: BuildResult = WindowsQemuBuilder(
            open_session=open_agent,
        ).build(
            project=_real_project(root=tmp_path),
            config=_agent_config(python=python),
            output=tmp_path / "windows",
        )
    _check_windows_artifacts(result=result)


def _check_windows_artifacts(*, result: BuildResult) -> None:
    """Check that a Windows build produced a real executable and installer.

    Args:
        result: The finished build.

    """
    for artifact in (result.executable, present(value=result.installer)):
        check(condition=artifact.is_file(), reason=f"{artifact} must exist")
        with artifact.open(mode="rb") as stream:
            magic: bytes = stream.read(2)
        check(
            condition=magic == b"MZ",
            reason=f"{artifact.name} is not a Windows PE file",
        )
    expect(actual=result.executable.suffix, expected=".exe")
    check(
        condition=present(value=result.installer).name.endswith("setup.exe"),
        reason=str(object=result.installer),
    )


def test_windows_pipeline_inside_an_existing_vm(tmp_path: Path) -> None:
    """The pull worker builds real artifacts inside a booted VM.

    The guest reaches this host at its address on the VM network, runs the
    worker from there and uploads the verified result.

    Args:
        tmp_path: Pytest temporary directory.

    """
    messages: list[str] = []
    result: BuildResult = RemoteBuilder(
        say=messages.append,
        job_settings=replace(
            SettingsLoader.load(),
            qemu_startup_timeout_seconds=2400,
        ),
    ).build(
        project=_real_project(root=tmp_path),
        settings=RemoteSettings(vm=_windows_vm(), timeout_seconds=1800),
        target="windows",
        output=tmp_path / "windows",
    )
    check(condition=bool(messages), reason="the worker reports progress")
    _check_windows_artifacts(result=result)
