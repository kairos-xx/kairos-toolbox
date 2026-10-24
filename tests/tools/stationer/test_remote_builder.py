# Copyright (c) 2026 Project contributors.
"""Tests for the remote pull-worker build backend."""

from __future__ import annotations

from http.client import HTTPConnection
from os import name as os_name
from platform import machine
from re import Match, search
from socket import AF_INET, SOCK_STREAM, socket
from subprocess import run
from sys import platform as sys_platform
from threading import Thread
from time import monotonic, sleep
from typing import TYPE_CHECKING, Final, NoReturn, cast, final
from urllib.parse import SplitResult, urlsplit

import pytest

from tools.stationer import remote_builder
from tools.stationer.builder import (
    ArtifactUtilities,
    BuildError,
    BuildResult,
    ProjectConfig,
)
from tools.stationer.conjure import RemoteStep, ResultBundle
from tools.stationer.installer_support import PyzPayload
from tools.stationer.remote_builder import (
    RemoteBuilder,
    RemoteConstants,
    RemotePackaging,
    RemoteSettings,
    remote_steps,
)
from tools.stationer.remote_exposure import Charter, ExposureError

from .helpers import check, expect, present

if TYPE_CHECKING:
    from pathlib import Path
    from subprocess import Popen


def project(
    *,
    root: Path,
    dependencies: tuple[Path, ...] = (),
) -> ProjectConfig:
    """Create a small project.

    Args:
        root: Project root.
        dependencies: Local dependency directories.

    Returns:
        The project configuration.

    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.py").write_text(data="print('hello')\n", encoding="utf-8")
    return ProjectConfig(
        root=root,
        name="demo",
        version="1.2.3",
        entrypoint=root / "app.py",
        identifier="dev.example.demo",
        console=True,
        pyinstaller_args=("--collect-submodules", "demo"),
        pip_args=("--no-cache-dir",),
        local_dependencies=dependencies,
    )


def manifest(*, tmp_path: Path, body: str) -> Path:
    """Write a manifest holding only the given TOML.

    Args:
        tmp_path: Pytest temporary directory.
        body: TOML text.

    Returns:
        The manifest path.

    """
    path: Path = tmp_path / "m.toml"
    path.write_text(data=body, encoding="utf-8")
    return path


def test_settings_default_to_a_loopback_only_server(tmp_path: Path) -> None:
    """Without a ``[remote]`` table the server is not exposed to the network.

    Args:
        tmp_path: Pytest temporary directory.

    """
    settings: RemoteSettings = RemoteSettings.load(
        manifest=manifest(tmp_path=tmp_path, body='[project]\nname = "x"\n'),
    )
    expect(actual=settings.bind, expected="127.0.0.1")
    expect(actual=settings.expose, expected="direct")
    expect(actual=settings.worker_host, expected="10.0.2.2")


def test_settings_are_read_from_the_remote_table(tmp_path: Path) -> None:
    """Every documented key is accepted.

    Args:
        tmp_path: Pytest temporary directory.

    """
    settings: RemoteSettings = RemoteSettings.load(
        manifest=manifest(
            tmp_path=tmp_path,
            body=(
                '[remote]\nbind = "192.0.2.1"\nport = 9000\nworker_host = '
                '"192.0.2.5"\nexpose = "tunnl"\nshort = "my-alias"\npython'
                '_version = "3.12.4"\npython_bootstrap = false\ntimeout'
                "_seconds = 60\n"
            ),
        ),
    )
    expect(
        actual=(settings.bind, settings.port, settings.expose, settings.short),
        expected=("192.0.2.1", 9000, "tunnl", "my-alias"),
    )
    expect(actual=settings.python_bootstrap, expected=False)
    expect(actual=settings.timeout_seconds, expected=60)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('[remote]\nport = "x"\n', "port"),
        ("[remote]\nport = 70000\n", "port"),
        ('[remote]\nexpose = "carrier-pigeon"\n', "expose"),
        ("[remote]\ntimeout_seconds = 0\n", "timeout_seconds"),
        ("[remote]\npython_bootstrap = 1\n", "python_bootstrap"),
        ("[remote]\nsurprise = 1\n", "surprise"),
        ('[remote]\narchitecture = "mips"\n', "architecture"),
        ("remote = 3\n", "remote"),
    ],
)
def test_invalid_settings_are_rejected(
    tmp_path: Path,
    body: str,
    message: str,
) -> None:
    """Bad types, ranges, backends and unknown keys fail loudly.

    Args:
        tmp_path: Pytest temporary directory.
        body: TOML under test.
        message: Text the error must contain.

    """
    with pytest.raises(expected_exception=BuildError, match=message):
        _ = RemoteSettings.load(
            manifest=manifest(tmp_path=tmp_path, body=body),
        )


def test_steps_install_dependencies_before_the_project(tmp_path: Path) -> None:
    """The plan creates a venv, installs, then freezes with PyInstaller.

    Args:
        tmp_path: Pytest temporary directory.

    """
    dependency: Path = tmp_path / "bowman"
    steps: tuple[RemoteStep, ...] = remote_steps(
        project=project(root=tmp_path / "p", dependencies=(dependency,)),
        target="linux",
    )
    expect(actual=len(steps), expected=7)
    expect(actual=steps[0].argv, expected=("{python}", "-m", "venv", "{venv}"))
    install: tuple[str, ...] = steps[2].argv
    check(condition="pyinstaller" in install, reason=str(object=install))
    check(condition="--no-cache-dir" in install, reason=str(object=install))
    check(
        condition=install.index(".local-deps/bowman") < install.index("."),
        reason="dependencies must precede the project",
    )
    expect(actual=steps[2].cwd, expected="source")
    freeze: tuple[str, ...] = steps[3].argv
    for needle in ("PyInstaller", "--onefile", "--name", "demo", "{dist}"):
        check(condition=needle in freeze, reason=f"{needle} in {freeze}")
    expect(actual=freeze[-1], expected="app.py")
    check(condition="--windowed" not in freeze, reason="console app")
    check(
        condition=freeze.index("--collect-submodules")
        < freeze.index("app.py"),
        reason="project PyInstaller arguments precede the script",
    )


def test_gui_projects_are_built_windowed(tmp_path: Path) -> None:
    """A non-console project adds ``--windowed``.

    Args:
        tmp_path: Pytest temporary directory.

    """
    _project: ProjectConfig = project(root=tmp_path / "p")
    gui: ProjectConfig = ProjectConfig(
        root=_project.root,
        name=_project.name,
        version=_project.version,
        entrypoint=_project.entrypoint,
        identifier=_project.identifier,
        console=False,
        pyinstaller_args=(),
        pip_args=(),
    )
    check(
        condition=any(
            "--windowed" in step.argv
            for step in remote_steps(project=gui, target="linux")
        ),
        reason="windowed",
    )


def _custom_steps(*, body: str) -> tuple[RemoteStep, ...]:
    """Build harmless steps that fabricate a build result.

    Args:
        body: Python source run by the single build step.

    Returns:
        The steps.

    """
    return (
        RemoteStep(
            message="fabricating the artifact",
            argv=("{python}", "-c", body, "{dist}"),
            cwd="source",
        ),
    )


def _ignore(message: str) -> None:
    """Discard a progress message.

    Args:
        message: Ignored text.

    """
    _ = message


@final
class FixedSteps:
    """Replace ``remote_steps`` with a fast, offline step list."""

    def __init__(self, *, body: str) -> None:
        """Remember the Python source the single step runs.

        Args:
            body: Source passed to ``python -c``.

        """
        self.body: Final[str] = body

    def __call__(
        self,
        *,
        project: ProjectConfig,
        target: str,
        packaging: RemotePackaging | None = None,
    ) -> tuple[RemoteStep, ...]:
        """Return the fixed steps.

        Args:
            project: Ignored project.
            target: Ignored target platform.
            packaging: Ignored installer metadata.

        Returns:
            The offline steps.

        """
        _ = (project, target, packaging)
        return _custom_steps(body=self.body)


@final
class StubProcess:
    """A charter backend process that records being stopped."""

    def __init__(self) -> None:
        """Create the stub."""
        self.terminated: bool = False

    @staticmethod
    def wait(timeout: float | None = None) -> int:
        """Wait for exit.

        Args:
            timeout: Ignored.

        Returns:
            Always ``0``.

        """
        _ = timeout
        return 0

    def poll(self) -> int | None:
        """Report whether the process has exited.

        Returns:
            ``None`` while running, ``0`` once terminated.

        """
        return 0 if self.terminated else None

    def terminate(self) -> None:
        """Record the stop request."""
        self.terminated = True

    def as_popen(self) -> Popen[bytes]:
        """View the stub as a process handle.

        Returns:
            This object typed as ``Popen``.

        """
        return cast(typ="Popen[bytes]", val=self)


@final
class FixedCharter:
    """Replace ``start_charter`` with a stub charter."""

    def __init__(self, *, process: StubProcess, url: str) -> None:
        """Remember what to return.

        Args:
            process: Stub backend process.
            url: Public URL the charter reports.

        """
        self.process: Final[StubProcess] = process
        self.url: Final[str] = url

    def __call__(
        self,
        *,
        backend: str,
        port: int,
        authtoken: str | None,
    ) -> Charter:
        """Return the stub charter.

        Args:
            backend: Ignored backend name.
            port: Ignored port.
            authtoken: Ignored token.

        Returns:
            A charter over the stub process.

        """
        _ = (backend, port, authtoken)
        return Charter(process=self.process.as_popen(), public_url=self.url)


@final
class FailingCharter:
    """Replace ``start_charter`` with one that cannot start."""

    def __call__(
        self,
        *,
        backend: str,
        port: int,
        authtoken: str | None,
    ) -> NoReturn:
        """Fail like a missing charter executable.

        Args:
            backend: Ignored backend name.
            port: Ignored port.
            authtoken: Ignored token.

        Raises:
            ExposureError: Always.

        """
        _ = (backend, port, authtoken)
        msg: str = "no charter"
        raise ExposureError(msg)


@final
class FixedShort:
    """Replace ``shorten_url`` with a constant."""

    def __call__(self, *, url: str, alias: str | None = None) -> str:
        """Return a fixed short URL.

        Args:
            url: Ignored target.
            alias: Ignored alias.

        Returns:
            The constant short URL.

        """
        _ = (url, alias)
        return "https://shrtr.top/q"


def run_worker_when_announced(
    *,
    messages: list[str],
    outcome: dict[str, int],
) -> Thread:
    """Run the announced POSIX worker as soon as its URL is printed.

    Args:
        messages: The builder's announcement log, appended to by ``say``.
        outcome: Receives the worker's exit status under ``status``.

    Returns:
        The started thread.

    """

    def work() -> None:
        """Wait for the announcement, fetch the script and run it."""
        deadline: float = monotonic() + 20
        url: str | None = None
        while monotonic() < deadline and url is None:
            for line in list(messages):
                found: Match[str] | None = search(
                    pattern="curl -fsSL (http://\\S+) \\| bash",
                    string=line,
                )
                if found is not None:
                    url = found.group(1)
            sleep(0.02)
        parts: SplitResult = urlsplit(url=present(value=url))
        connection: HTTPConnection = HTTPConnection(
            host=parts.hostname or "",
            port=parts.port,
            timeout=10,
        )
        connection.request(method="GET", url=parts.path)
        script: str = connection.getresponse().read().decode()
        connection.close()
        outcome["status"] = run(
            args=("bash", "-s"),
            input=script,
            text=True,
            capture_output=True,
            check=False,
        ).returncode

    thread: Thread = Thread(target=work, daemon=True)
    thread.start()
    return thread


def test_a_real_posix_worker_builds_and_returns_verified_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generated worker runs against the real server end to end.

    Only the build command is replaced by a fast, offline one; the payload,
    authentication, step interpreter, packaging and upload are all real.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setattr(
        target=remote_builder,
        name="remote_steps",
        value=FixedSteps(
            body=(
                "import pathlib, sys;"
                "assert pathlib.Path('app.py').is_file();"
                "(pathlib.Path(sys.argv[1]) / 'demo')"
                ".write_bytes(b'frozen-binary')"
            ),
        ),
    )
    messages: list[str] = []
    outcome: dict[str, int] = {}
    worker: Thread = run_worker_when_announced(
        messages=messages,
        outcome=outcome,
    )
    result: BuildResult = RemoteBuilder(say=messages.append).build(
        project=project(root=tmp_path / "proj"),
        settings=RemoteSettings(
            port=0,
            worker_host="127.0.0.1",
            timeout_seconds=60,
        ),
        target="linux",
        output=tmp_path / "build",
    )
    worker.join(timeout=20)
    expect(actual=outcome.get("status"), expected=0)
    expect(actual=result.platform, expected="linux")
    expect(actual=result.installer, expected=None)
    architecture: str = RemoteConstants.architectures.get(
        machine().lower(),
        machine().lower(),
    )
    folder: Path = ArtifactUtilities.folder(
        output=tmp_path / "build",
        platform_name="linux",
        architecture=architecture,
        project="demo",
    )
    expect(
        actual=result.executable,
        expected=(
            folder / "pyinstaller" / f"demo-1.2.3-linux-{architecture}"
        ).resolve(),
    )
    check(
        condition=not (folder / "pyinstaller" / "demo").exists(),
        reason="the executable is renamed, not duplicated",
    )
    expect(actual=result.executable.read_bytes(), expected=b"frozen-binary")
    check(condition=bool(result.executable_sha256), reason="hashed")
    check(
        condition=any("fabricating the artifact" in m for m in messages),
        reason=f"worker progress was relayed: {messages}",
    )


def test_worker_failures_surface_with_their_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing step is reported to the host and fails the build.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setattr(
        target=remote_builder,
        name="remote_steps",
        value=FixedSteps(body="raise SystemExit(7)"),
    )
    messages: list[str] = []
    outcome: dict[str, int] = {}
    worker: Thread = run_worker_when_announced(
        messages=messages,
        outcome=outcome,
    )
    with pytest.raises(expected_exception=BuildError, match="worker failed"):
        _ = RemoteBuilder(say=messages.append).build(
            project=project(root=tmp_path / "proj"),
            settings=RemoteSettings(
                port=0,
                worker_host="127.0.0.1",
                timeout_seconds=60,
            ),
            target="linux",
            output=tmp_path / "build",
        )
    worker.join(timeout=20)
    check(condition=outcome.get("status") != 0, reason="worker exits non-zero")
    check(
        condition=not (tmp_path / "build" / "linux").exists(),
        reason="nothing is materialized for a failed build",
    )


def test_a_worker_that_never_connects_times_out(tmp_path: Path) -> None:
    """The host does not wait forever.

    Args:
        tmp_path: Pytest temporary directory.

    """
    with pytest.raises(expected_exception=BuildError, match="timed out"):
        _ = RemoteBuilder(say=_ignore).build(
            project=project(root=tmp_path / "proj"),
            settings=RemoteSettings(port=0, timeout_seconds=1),
            target="linux",
            output=tmp_path / "build",
        )


def test_announcements_name_the_command_for_each_target(
    tmp_path: Path,
) -> None:
    """The printed command matches the worker's operating system.

    Args:
        tmp_path: Pytest temporary directory.

    """
    expectations: dict[str, str] = {
        "windows": "powershell.exe -NoProfile -Command -",
        "linux": "| bash",
        "macos": "| bash",
    }
    for target, needle in expectations.items():
        messages: list[str] = []
        with pytest.raises(expected_exception=BuildError):
            _ = RemoteBuilder(say=messages.append).build(
                project=project(root=tmp_path / target),
                settings=RemoteSettings(port=0, timeout_seconds=1),
                target=target,
                output=tmp_path / "out",
            )
        text: str = "\n".join(messages)
        check(condition=needle in text, reason=f"{target}: {text}")
        check(
            condition="10.0.2.2" in text,
            reason="workers reach the host at worker_host, not its bind",
        )


def test_exposure_replaces_the_public_url_and_is_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A charter's URL is announced (optionally shortened) and cleaned up.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    process: StubProcess = StubProcess()
    monkeypatch.setattr(
        target=remote_builder,
        name="start_charter",
        value=FixedCharter(process=process, url="https://abc.tunnl.gg"),
    )
    monkeypatch.setattr(
        target=remote_builder,
        name="shorten_url",
        value=FixedShort(),
    )
    messages: list[str] = []
    with pytest.raises(expected_exception=BuildError, match="timed out"):
        _ = RemoteBuilder(say=messages.append).build(
            project=project(root=tmp_path / "proj"),
            settings=RemoteSettings(
                port=0,
                timeout_seconds=1,
                expose="tunnl",
                short="",
            ),
            target="linux",
            output=tmp_path / "out",
        )
    text: str = "\n".join(messages)
    check(condition="https://shrtr.top/q" in text, reason=text)
    check(condition="abc.tunnl.gg" not in text, reason="short URL replaces it")
    check(condition=process.terminated, reason="the charter must be closed")


def test_exposure_failures_become_build_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A charter that cannot start is a build error, not a traceback.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setattr(
        target=remote_builder,
        name="start_charter",
        value=FailingCharter(),
    )
    with pytest.raises(expected_exception=BuildError, match="no charter"):
        _ = RemoteBuilder(say=_ignore).build(
            project=project(root=tmp_path / "proj"),
            settings=RemoteSettings(port=0, expose="ngrok"),
            target="linux",
            output=tmp_path / "out",
        )


def test_unknown_targets_are_rejected(tmp_path: Path) -> None:
    """Only the three native operating systems can be targeted.

    Args:
        tmp_path: Pytest temporary directory.

    """
    with pytest.raises(expected_exception=BuildError, match="target"):
        _ = RemoteBuilder(say=_ignore).build(
            project=project(root=tmp_path / "proj"),
            settings=RemoteSettings(port=0),
            target="plan9",
            output=tmp_path / "out",
        )


def test_packaging_steps_cover_every_target(tmp_path: Path) -> None:
    """Each target's plan ends with its installer steps.

    Args:
        tmp_path: Pytest temporary directory.

    """
    demo: ProjectConfig = project(root=tmp_path / "p")
    windows: tuple[RemoteStep, ...] = remote_builder.packaging_steps(
        project=demo,
        target="windows",
    )
    joined: str = " ".join(part for step in windows for part in step.argv)
    check(condition="ISCC.exe" in joined, reason=joined)
    check(condition="innosetup" in joined, reason=joined)
    linux: tuple[RemoteStep, ...] = remote_builder.packaging_steps(
        project=demo,
        target="linux",
    )
    check(
        condition=any("dpkg-deb" in step.argv for step in linux),
        reason="linux produces a deb",
    )
    macos: tuple[RemoteStep, ...] = remote_builder.packaging_steps(
        project=demo,
        target="macos",
    )
    check(
        condition=any("pkgbuild" in step.argv for step in macos),
        reason="macos produces a pkg",
    )
    expect(
        actual=remote_builder.packaging_steps(
            project=demo,
            target="amiga",
        ),
        expected=(),
    )


def test_remote_files_render_installer_inputs(tmp_path: Path) -> None:
    """Injected files carry the installer metadata a worker needs.

    Args:
        tmp_path: Pytest temporary directory.

    """
    demo: ProjectConfig = project(root=tmp_path / "p")
    packaging: remote_builder.RemotePackaging = remote_builder.RemotePackaging(
        maintainer="Jane <j@example.dev>",
        description="A demo.",
        publisher="Example Co",
    )
    windows: dict[str, str] = remote_builder.remote_files(
        project=demo,
        target="windows",
        packaging=packaging,
    )
    iss: str = windows["installer.iss"]
    check(
        condition="AppId={{dev.example.demo}}" in iss,
        reason="AppId braces survive expansion: " + iss,
    )
    check(condition="{architecture}" in iss, reason=iss)
    check(condition="AppPublisher=Example Co" in iss, reason=iss)
    linux: dict[str, str] = remote_builder.remote_files(
        project=demo,
        target="linux",
        packaging=packaging,
    )
    control: str = linux["pkg/DEBIAN/control"]
    check(condition="Package: demo" in control, reason=control)
    check(condition="Architecture: {architecture}" in control, reason=control)
    check(condition="Jane <j@example.dev>" in control, reason=control)
    expect(
        actual=remote_builder.remote_files(
            project=demo,
            target="macos",
            packaging=packaging,
        ),
        expected={},
    )


def _pyz() -> PyzPayload:
    """Describe a bundled ``.pyz`` archive.

    Returns:
        The payload.

    """
    return PyzPayload(
        archive="demo-1.2.3-linux-amd64-glibc.pyz",
        bundled="demo-1.2.3.pyz",
        bootstrap="app.py",
    )


def test_a_bundled_pyz_adds_its_build_step_to_every_target(
    tmp_path: Path,
) -> None:
    """The archive is built after the freeze and before the installer.

    Args:
        tmp_path: Pytest temporary directory.

    """
    demo: ProjectConfig = project(root=tmp_path / "p")
    packaging: remote_builder.RemotePackaging = remote_builder.RemotePackaging(
        maintainer="Jane <j@example.dev>",
        description="A demo.",
        publisher="Example Co",
        pyz=_pyz(),
    )
    for target in ("windows", "linux", "macos"):
        messages: list[str] = [
            step.message
            for step in remote_builder.remote_steps(
                project=demo,
                target=target,
                packaging=packaging,
            )
        ]
        expect(actual=messages.count("building the pyz archive"), expected=1)
        check(
            condition=messages.index("building the pyz archive")
            > messages.index("building the native executable"),
            reason=f"{target}: {messages}",
        )
    step: RemoteStep = remote_builder.pyz_step(project=demo, pyz=_pyz())
    check(condition="--bootstrap" in step.argv, reason=str(object=step.argv))
    check(
        condition="app.py" in " ".join(step.argv),
        reason=str(object=step.argv),
    )


def test_a_bundled_pyz_adds_installer_inputs_for_every_target(
    tmp_path: Path,
) -> None:
    """Each installer carries the archive, its launcher and ensure-python.

    Args:
        tmp_path: Pytest temporary directory.

    """
    demo: ProjectConfig = project(root=tmp_path / "p")
    packaging: remote_builder.RemotePackaging = remote_builder.RemotePackaging(
        maintainer="Jane <j@example.dev>",
        description="A demo.",
        publisher="Example Co",
        pyz=_pyz(),
    )
    windows: dict[str, str] = remote_builder.remote_files(
        project=demo,
        target="windows",
        packaging=packaging,
    )
    check(condition="ensure-python.ps1" in windows, reason=str(object=windows))
    check(condition="demo-pyz.cmd" in windows, reason=str(object=windows))
    check(
        condition="demo-1.2.3-linux-amd64-glibc.pyz"
        in windows["installer.iss"],
        reason=windows["installer.iss"],
    )
    linux: dict[str, str] = remote_builder.remote_files(
        project=demo,
        target="linux",
        packaging=packaging,
    )
    for name in (
        "pkg/DEBIAN/postinst",
        "pkg/usr/local/lib/demo/ensure-python.sh",
        "pkg/usr/local/bin/demo-pyz",
    ):
        check(condition=name in linux, reason=f"{name} in {sorted(linux)}")
    check(
        condition="Depends: python3" in linux["pkg/DEBIAN/control"],
        reason=linux["pkg/DEBIAN/control"],
    )
    macos: dict[str, str] = remote_builder.remote_files(
        project=demo,
        target="macos",
        packaging=packaging,
    )
    for name in (
        "scripts/postinstall",
        "pkg-root/usr/local/lib/demo/ensure-python.sh",
        "pkg-root/usr/local/bin/demo-pyz",
    ):
        check(condition=name in macos, reason=f"{name} in {sorted(macos)}")


def test_installers_are_named_the_way_local_builds_name_them() -> None:
    """Release files follow one convention on every platform."""
    names: dict[str, str] = {
        target: remote_builder.installer_name(
            target=target,
            project="demo",
            version="1.2.3",
            architecture="arm64",
        )
        for target in ("windows", "linux", "macos")
    }
    expect(
        actual=names,
        expected={
            "windows": "demo-1.2.3-windows-arm64-setup.exe",
            "linux": "demo_1.2.3_arm64.deb",
            "macos": "demo-1.2.3-macos-arm64.pkg",
        },
    )


def test_a_port_held_by_another_program_is_not_taken_over(
    tmp_path: Path,
) -> None:
    """Only a verified stationer server may be replaced on its port.

    Args:
        tmp_path: Pytest temporary directory.

    """
    with socket(family=AF_INET, type=SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        address: tuple[str, int] = holder.getsockname()
        port: int = address[1]
        with pytest.raises(
            expected_exception=BuildError,
            match=r"not a verified stationer server|could not be iden",
        ):
            _ = RemoteBuilder(say=_ignore).build(
                project=project(root=tmp_path / "proj"),
                settings=RemoteSettings(
                    port=port,
                    worker_host="127.0.0.1",
                    timeout_seconds=30,
                ),
                target="linux",
                output=tmp_path / "build",
            )


def _host_target() -> str:
    """Name this machine's operating system the way targets are named.

    Returns:
        ``windows``, ``macos`` or ``linux``.

    """
    if os_name == "nt":
        return "windows"
    return "macos" if sys_platform == "darwin" else "linux"


def test_a_local_worker_builds_without_a_vm_or_a_pasted_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``local`` the worker runs here but still talks to the server.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setattr(
        target=remote_builder,
        name="remote_steps",
        value=FixedSteps(
            body=(
                "import pathlib, sys;"
                "(pathlib.Path(sys.argv[1]) / 'demo')"
                ".write_bytes(b'frozen-binary')"
            ),
        ),
    )
    messages: list[str] = []
    result: BuildResult = RemoteBuilder(say=messages.append).build(
        project=project(root=tmp_path / "proj"),
        settings=RemoteSettings(
            port=0,
            worker_host="127.0.0.1",
            timeout_seconds=60,
            local=True,
        ),
        target=_host_target(),
        output=tmp_path / "build",
    )
    expect(actual=result.executable.read_bytes(), expected=b"frozen-binary")
    check(
        condition=any("on this machine" in text for text in messages),
        reason=str(object=messages),
    )
    check(
        condition=not any("Run this" in text for text in messages),
        reason="nothing is left for a person to paste",
    )


def test_a_local_worker_must_match_this_machine(tmp_path: Path) -> None:
    """Building another operating system locally is refused.

    Args:
        tmp_path: Pytest temporary directory.

    """
    other: str = "linux" if _host_target() == "windows" else "windows"
    with pytest.raises(expected_exception=BuildError, match="machine"):
        _ = RemoteBuilder(say=_ignore).build(
            project=project(root=tmp_path / "proj"),
            settings=RemoteSettings(
                port=0,
                worker_host="127.0.0.1",
                timeout_seconds=30,
                local=True,
            ),
            target=other,
            output=tmp_path / "build",
        )


def test_primary_requires_a_named_executable() -> None:
    """A bundle without the project's executable is rejected."""
    bundle: ResultBundle = ResultBundle(
        os_name="linux",
        architecture="arm64",
        artifacts=(),
    )
    with pytest.raises(expected_exception=BuildError, match="executable"):
        _ = remote_builder.primary_artifact(name="demo", result=bundle)


def test_a_worker_installer_lands_in_the_installer_folder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``dist/installer`` file becomes the cell's installer artifact.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setattr(
        target=remote_builder,
        name="remote_steps",
        value=FixedSteps(
            body=(
                "import pathlib, sys;"
                "d = pathlib.Path(sys.argv[1]);"
                "(d / 'installer').mkdir();"
                "(d / 'installer' / 'demo_1.2.3_arm64.deb')"
                ".write_bytes(b'deb');"
                "(d / 'demo').write_bytes(b'frozen-binary')"
            ),
        ),
    )
    messages: list[str] = []
    outcome: dict[str, int] = {}
    worker: Thread = run_worker_when_announced(
        messages=messages,
        outcome=outcome,
    )
    result: BuildResult = RemoteBuilder(say=messages.append).build(
        project=project(root=tmp_path / "proj"),
        settings=RemoteSettings(
            port=0,
            worker_host="127.0.0.1",
            timeout_seconds=60,
        ),
        target="linux",
        output=tmp_path / "build",
    )
    worker.join(timeout=20)
    expect(actual=outcome.get("status"), expected=0)
    installer: Path = present(value=result.installer)
    check(
        condition=installer.parent.name == "installer",
        reason=str(object=installer),
    )
    expect(actual=installer.suffix, expected=".deb")
    check(condition=installer.is_file(), reason=str(object=installer))
    check(
        condition=bool(result.installer_sha256),
        reason="the installer is hashed",
    )
