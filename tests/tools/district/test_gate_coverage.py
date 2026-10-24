# Copyright (c) 2026 Project contributors.
"""Close the aggregate Python coverage gate through public behavior."""

from __future__ import annotations

from asyncio import run as asyncio_run
from ctypes import (
    POINTER,
    Structure,
    c_size_t,
    wintypes,
)
from ctypes import cast as ctypes_cast
from dataclasses import replace
from io import StringIO
from ipaddress import IPv4Address
from pathlib import Path
from sqlite3 import Connection, connect
from subprocess import CompletedProcess
from typing import TYPE_CHECKING, ClassVar, Final, NoReturn, cast

import pytest
from district import cli as cli_module
from district import conpty as conpty_module
from district import core as core_module
from district import installer as installer_module
from district import startup as startup_module
from district import tui as tui_module
from district import web as web_module
from district.cli import Cli, StatusRow
from district.conpty import (
    ConPtyClient,
    ConPtyInfo,
    ConPtyManager,
)
from district.core import (
    District,
    ExecResult,
    SessionRepository,
)
from district.exposure import Exposure
from district.hosts import RemoteHost
from district.identity import ProcessIdentity
from district.installer import (
    Component,
    Installer,
    Layout,
    ManifestData,
    Reporter,
)
from district.lifecycle import (
    CommandLifecycle,
    LifecycleInstaller,
)
from district.models import SessionEntry
from district.monitoring import SessionSnapshot
from district.platform import (
    Capabilities,
    RuntimePlatform,
)
from district.privacy import PrivacyPolicy
from district.resources import (
    ProcessResource,
    ResourceSnapshot,
)
from district.startup import (
    StartupState,
    WebStartup,
)
from district.storage import (
    Constants as StorageConstants,
)
from district.storage import (
    DistrictDatabase,
)
from district.tui import DashboardRow, WatchTui
from district.web import JsonValue, WebDashboard
from fastapi.testclient import TestClient

from tests.conftest import sibling_tree

from .helpers import check, expect

if TYPE_CHECKING:
    from collections.abc import Callable

    from _typeshed import Incomplete


class Constants:
    """Group test constants.

    Attributes:
        pid: Fake process identifier.
        min_components: Minimum installer component count.
        any_host: Wildcard bind address.
        source_root: Directory containing the ``district`` package.

    """

    source_root: Final[Path] = sibling_tree().project.parent
    pid: Final[int] = 321
    min_components: Final[int] = 8
    any_host: Final[str] = str(object=IPv4Address(address=0))


def _capture_identity(*, pid: int) -> ProcessIdentity:
    """Return a deterministic process identity.

    Args:
        pid: Process identifier.

    Returns:
        Process identity with a fixed token.

    """
    marker: str = "tok"
    return ProcessIdentity(pid=pid, started_token=marker)


def _ping_true(*, home: Path) -> bool:
    """Report the daemon as running.

    Args:
        home: Home.

    Returns:
        True.

    """
    del home
    return True


def _ping_false(*, home: Path) -> bool:
    """Report the daemon as stopped.

    Args:
        home: Home.

    Returns:
        False.

    """
    del home
    return False


def _no_sleep(value: float) -> None:
    """Skip real sleeping.

    Args:
        value: Requested delay.

    """
    del value


def _one(*args: object) -> int:
    """Return a native success value.

    Args:
        *args: Native arguments.

    Returns:
        One.

    """
    del args
    return 1


def _zero(*args: object) -> int:
    """Return a native no-op value.

    Args:
        *args: Native arguments.

    Returns:
        Zero.

    """
    del args
    return 0


class _FakeSession:
    """Provide deterministic ConPTY behavior."""

    def __init__(self, *, alive: bool = True) -> None:
        """Initialize the fake session.

        Args:
            alive: Initial liveness.

        """
        self.pid: int = Constants.pid
        self.live: bool = alive
        self.writes: list[bytes] = []
        self.terminated: bool = False

    def alive(self) -> bool:
        """Return current liveness.

        Returns:
            Current liveness.

        """
        return self.live

    def write(self, *, data: bytes) -> int:
        """Record bytes written.

        Args:
            data: Bytes written.

        Returns:
            Byte count.

        """
        self.writes.append(data)
        return len(data)

    def read(self, *, size: int = 4096) -> bytes:
        """Return no pending output.

        Args:
            size: Maximum read size.

        Returns:
            Empty output.

        """
        del size
        return b""

    def terminate(self) -> None:
        """Record termination."""
        self.terminated = True
        self.live = False


class _NoStartThread:
    """Avoid starting background drain threads in manager tests."""

    def __init__(self, **kwargs: object) -> None:
        """Accept thread construction arguments.

        Args:
            **kwargs: Ignored thread options.

        """
        del kwargs

    def start(self) -> None:
        """Accept thread start."""


def _runtime(*, os_name: str) -> RuntimePlatform:
    """Build a runtime descriptor for one operating system.

    Args:
        os_name: Runtime operating-system name.

    Returns:
        Runtime descriptor.

    """
    return RuntimePlatform(
        os=os_name,
        architecture="x86_64",
        shell_location="shell",
        backend="tmux",
        capabilities=Capabilities(
            persistent_sessions=True,
            read_only_capture=True,
            process_tree=True,
            clipboard=True,
            clickable_copy=True,
            notifications=True,
            editor_agent_bridge=True,
            interactive_reconnect=True,
        ),
    )


def _layout(*, home: Path, source_root: Path) -> Layout:
    """Build a deterministic installer layout.

    Args:
        home: Scratch home.
        source_root: Project root.

    Returns:
        Installer layout.

    """
    return Layout(
        home=home,
        bin_dir=home / "bin",
        lib_dir=home / "lib",
        alias="shared",
        mode="live",
        source_root=source_root,
        python="python3",
    )


def test_cli_value_and_remote_helpers(tmp_path: Path) -> None:
    """Exercise CLI value normalization and web-option branches.

    Args:
        tmp_path: Scratch directory.

    """
    host: RemoteHost = RemoteHost(name="remote", target="u@host")
    expect(actual=cli_module.status_value(value="x"), expected="x")
    expect(actual=cli_module.status_value(value=2), expected=2)
    expect(actual=cli_module.status_value(value=True), expected=True)
    expect(actual=cli_module.status_value(value=None), expected=None)
    expect(
        actual=Cli.parse_remote_payload(raw='{"sessions":[]}'),
        expected={"sessions": []},
    )
    expect(
        actual=Cli.parse_remote_payload(raw="bad"),
        expected={"error": "bad"},
    )
    expect(actual=Cli.json_text(value="x"), expected="x")
    expect(actual=Cli.json_text(value=[1, True]), expected="[1,true]")
    expect(actual=Cli.remote_payload_rows(host=host, payload=[]), expected=[])
    expect(actual=Cli.remote_payload_rows(host=host, payload={}), expected=[])
    rows: list[StatusRow] = Cli.remote_payload_rows(
        host=host,
        payload={"error": {"why": "no"}},
    )
    expect(actual=rows[0]["state"], expected="UNREACHABLE")
    rows = Cli.remote_payload_rows(
        host=host,
        payload={"sessions": [{"id": "abc", "bad": [1]}, "skip"]},
    )
    expect(actual=rows[0]["host"], expected="remote")
    expect(actual="bad" in rows[0], expected=False)
    expect(
        actual=Cli.web_options(args=("--no-browser",)),
        expected=("127.0.0.1", 8765, False, Exposure()),
    )
    expect(
        actual=Cli.web_options(
            args=("--host", Constants.any_host, "--port", "0"),
        ),
        expected=(Constants.any_host, 0, True, Exposure()),
    )
    expect(actual=Cli.web_options(args=("--host",)), expected=None)
    expect(actual=Cli.web_options(args=("--bad", "x")), expected=None)
    expect(actual=Cli.parse_port(value="x"), expected=None)
    expect(actual=Cli.parse_port(value="-1"), expected=None)
    expect(actual=Cli.parse_port(value="65536"), expected=None)
    expect(actual=Cli.parse_port(value="443"), expected=443)
    marker: Path = tmp_path / ".zshrc"
    expect(actual=Cli.hook_present(path=marker), expected=False)
    marker.write_text(data="# >>> district command lifecycle >>>\n")
    expect(actual=Cli.hook_present(path=marker), expected=True)


def test_installer_file_and_manifest_helpers(tmp_path: Path) -> None:
    """Exercise installer file, routing, manifest, and platform helpers.

    Args:
        tmp_path: Scratch directory.

    """
    source_root: Path = Constants.source_root
    layout: Layout = _layout(home=tmp_path, source_root=source_root)
    installer_module.write_launcher(layout=layout)
    check(
        condition=installer_module.owned_launcher(layout=layout),
        reason="owned launcher",
    )
    installer_module.remove_launcher(layout=layout)
    expect(actual=layout.launcher.exists(), expected=False)
    foreign: Path = layout.launcher
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text(data="foreign\n")
    installer_module.remove_launcher(layout=layout)
    expect(actual=foreign.exists(), expected=True)
    block_path: Path = tmp_path / "profile"
    installer_module.install_block(
        path=block_path,
        block="BEGIN\nx\nEND\n",
        begin="BEGIN",
        end="END",
    )
    installer_module.install_block(
        path=block_path,
        block="BEGIN\ny\nEND\n",
        begin="BEGIN",
        end="END",
    )
    expect(actual=block_path.read_text().count("BEGIN"), expected=1)
    installer_module.remove_block(path=block_path, begin="BEGIN", end="END")
    expect(actual="BEGIN" in block_path.read_text(), expected=False)
    installer_module.remove_block(
        path=tmp_path / "missing",
        begin="a",
        end="b",
    )
    block_path.write_text(data="unmanaged")
    installer_module.remove_block(path=block_path, begin="a", end="b")
    expect(
        actual=installer_module.routing_files(layout=layout),
        expected=(tmp_path / ".zshrc", tmp_path / ".bashrc"),
    )
    routing: str = installer_module.install_routing(layout=layout)
    check(
        condition=".zshrc" in routing and ".bashrc" in routing,
        reason="routing paths",
    )
    check(
        condition=installer_module.routing_installed(layout=layout),
        reason="routing installed",
    )
    installer_module.remove_routing(layout=layout)
    expect(
        actual=installer_module.routing_installed(layout=layout),
        expected=False,
    )
    installer_module.write_manifest(
        layout=layout,
        keys=frozenset({"launcher", "routing"}),
    )
    data: ManifestData = installer_module.read_manifest(layout=layout)
    expect(actual=data.get("alias"), expected="shared")
    rebuilt: Layout | None = installer_module.manifest_layout(home=tmp_path)
    check(condition=rebuilt is not None, reason="manifest rebuild")
    layout.manifest_path.write_text(data="bad json", encoding="utf-8")
    expect(actual=installer_module.read_manifest(layout=layout), expected={})
    layout.manifest_path.write_text(data="[]", encoding="utf-8")
    expect(actual=installer_module.read_manifest(layout=layout), expected={})
    layout.manifest_path.unlink()
    expect(actual=installer_module.read_manifest(layout=layout), expected={})
    expect(
        actual=installer_module.validated_alias(alias="good-name"),
        expected="good-name",
    )
    with pytest.raises(expected_exception=ValueError, match="Alias must be"):
        _ = installer_module.validated_alias(alias="bad/name")
    expect(
        actual=installer_module.windows_root(home=tmp_path).name,
        expected="district",
    )
    check(
        condition="DISTRICT_AUTO_AGENT_ACTIVE"
        in installer_module.routing_block(launcher=layout.launcher),
        reason="routing guard",
    )


def test_installer_component_helpers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise package-manager and lifecycle component helpers.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    layout: Layout = _layout(
        home=tmp_path,
        source_root=Constants.source_root,
    )
    found: dict[str, str | None] = {"tmux": "/bin/tmux"}

    def fake_which(*, cmd: str) -> str | None:
        """Return recorded tool locations.

        Args:
            cmd: Command name.

        Returns:
            Tool path when recorded.

        """
        return found.get(cmd)

    monkeypatch.setattr(
        target=installer_module,
        name="which",
        value=fake_which,
    )
    expect(
        actual=installer_module.install_tmux(layout=layout),
        expected="/bin/tmux",
    )
    expect(
        actual=installer_module.tmux_installed(layout=layout),
        expected=True,
    )
    found.clear()
    expect(
        actual=installer_module.install_tmux(layout=layout),
        expected="unavailable; install tmux manually",
    )
    calls: list[tuple[str, ...]] = []

    def fake_run(
        *,
        args: tuple[str, ...],
        check: bool,
        **kwargs: object,
    ) -> CompletedProcess[str]:
        """Record subprocess calls.

        Args:
            args: Command arguments.
            check: Check flag.
            **kwargs: Other subprocess options.

        Returns:
            Successful result.

        """
        del check, kwargs
        calls.append(args)
        return CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(target=installer_module, name="run", value=fake_run)
    found["brew"] = "/brew"
    expect(
        actual=installer_module.install_tmux(layout=layout),
        expected="installed with Homebrew",
    )
    found.clear()
    found["apt-get"] = "/apt"
    expect(
        actual=installer_module.install_tmux(layout=layout),
        expected="installed with apt-get",
    )
    for install, installed, remove in (
        (
            installer_module.install_zsh,
            installer_module.zsh_installed,
            installer_module.remove_zsh,
        ),
        (
            installer_module.install_bash,
            installer_module.bash_installed,
            installer_module.remove_bash,
        ),
        (
            installer_module.install_powershell,
            installer_module.powershell_installed,
            installer_module.remove_powershell,
        ),
    ):
        _ = install(layout=layout)
        expect(actual=installed(layout=layout), expected=True)
        remove(layout=layout)
        expect(actual=installed(layout=layout), expected=False)
    expect(
        actual=installer_module.uri_handler_installed(layout=layout),
        expected=False,
    )
    expect(
        actual=installer_module.hook_installed(
            path=tmp_path / "none",
            marker="x",
        ),
        expected=False,
    )
    hook: Path = tmp_path / "hook.json"
    hook.write_text(data="marker")
    expect(
        actual=installer_module.hook_installed(path=hook, marker="marker"),
        expected=True,
    )
    components: tuple[Component, ...] = installer_module.components(
        layout=layout,
    )
    check(
        condition=len(components) >= Constants.min_components,
        reason="component catalog",
    )


def test_installer_public_status_and_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise installer selection, status, and conflict behavior.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    source_root: Path = Constants.source_root
    installer: Installer = Installer(
        source_root=source_root,
        home=tmp_path,
        reporter=Reporter(colour=False),
    )
    layout: Layout = installer.layout(
        alias="shared",
        mode="live",
        bin_dir=None,
        lib_dir=None,
    )
    components: tuple[Component, ...] = installer.components(layout=layout)
    minimal: frozenset[str] = installer_module.resolve_selection(
        components=components,
        preset="minimal",
        with_keys=frozenset({"routing"}),
        without_keys=frozenset(),
    )
    check(
        condition={"launcher", "routing"}.issubset(minimal),
        reason="minimal selection",
    )
    full: frozenset[str] = installer_module.resolve_selection(
        components=components,
        preset="full",
        with_keys=frozenset(),
        without_keys=frozenset(),
    )
    check(condition="launcher" in full, reason="full selection")
    with pytest.raises(expected_exception=ValueError, match="Unknown"):
        _ = installer_module.resolve_selection(
            components=components,
            preset="default",
            with_keys=frozenset({"missing"}),
            without_keys=frozenset(),
        )
    with pytest.raises(expected_exception=ValueError, match="required"):
        _ = installer_module.resolve_selection(
            components=components,
            preset="default",
            with_keys=frozenset(),
            without_keys=frozenset({"launcher"}),
        )
    expect(
        actual=installer_module.csv(value=" a ,, b "),
        expected=frozenset({"a", "b"}),
    )
    expect(
        actual=installer_module.csv(value=None),
        expected=frozenset[str](),
    )
    expect(actual=installer.status(layout=layout), expected=0)
    layout.launcher.parent.mkdir(parents=True, exist_ok=True)
    layout.launcher.write_text(data="foreign")

    def foreign_which(*, cmd: str) -> str:
        """Resolve every command to the foreign launcher.

        Args:
            cmd: Command name.

        Returns:
            Foreign launcher path.

        """
        del cmd
        return str(object=layout.launcher)

    monkeypatch.setattr(
        target=installer_module,
        name="which",
        value=foreign_which,
    )
    with pytest.raises(expected_exception=RuntimeError):
        installer.check_conflict(layout=layout, force=False)
    installer.check_conflict(layout=layout, force=True)


def test_startup_all_platform_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise startup enable/disable behavior for every supported OS.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    calls: list[tuple[str, ...]] = []

    def fake_run(
        *,
        args: tuple[str, ...],
        **kwargs: object,
    ) -> CompletedProcess[str]:
        """Record native startup commands.

        Args:
            args: Command arguments.
            **kwargs: Other subprocess options.

        Returns:
            Successful result.

        """
        del kwargs
        calls.append(args)
        return CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(target=startup_module, name="run", value=fake_run)

    def fake_which(*, cmd: str) -> str | None:
        """Return the systemctl location only.

        Args:
            cmd: Command name.

        Returns:
            Tool path for systemctl.

        """
        return "/bin/systemctl" if cmd == "systemctl" else None

    monkeypatch.setattr(
        target=startup_module,
        name="which",
        value=fake_which,
    )
    for os_name in ("macos", "linux", "windows", "other"):
        startup: WebStartup = WebStartup(home=tmp_path / os_name)
        startup.runtime = _runtime(os_name=os_name)
        state: StartupState = startup.enable(
            command=("shared", "--web", 'a"b'),
        )
        expect(actual=state.enabled, expected=os_name != "other")
        expect(actual=startup.disable().enabled, expected=False)
    expect(actual=WebStartup.quote(value='a"b'), expected='"a\\"b"')
    check(condition=bool(calls), reason="native commands invoked")


def test_conpty_manager_and_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise ConPTY manager and IPC client success/error branches.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    session: _FakeSession = _FakeSession()

    def fake_spawn(**kwargs: object) -> _FakeSession:
        """Return the prepared fake session.

        Args:
            **kwargs: Spawn options.

        Returns:
            Fake session.

        """
        del kwargs
        return session

    monkeypatch.setattr(
        target=conpty_module.WindowsConPty,
        name="spawn",
        value=fake_spawn,
    )
    monkeypatch.setattr(
        target=conpty_module,
        name="Thread",
        value=_NoStartThread,
    )
    manager: ConPtyManager = ConPtyManager(policy=PrivacyPolicy())
    info: ConPtyInfo = manager.create(
        session_uuid="abc",
        cwd=str(object=tmp_path),
        command=("cmd",),
    )
    expect(actual=info.pid, expected=Constants.pid)
    expect(
        actual=manager.create(
            session_uuid="abc",
            cwd=str(object=tmp_path),
            command=("cmd",),
        ).pid,
        expected=Constants.pid,
    )
    expect(actual=manager.output(session_uuid="missing"), expected="")
    expect(actual=manager.write(session_uuid="abc", text="hello"), expected=5)
    stored: ConPtyInfo | None = manager.info(session_uuid="abc")
    check(condition=stored is not None, reason="stored info")
    if stored is not None:
        expect(actual=stored.alive, expected=True)
    expect(actual=manager.kill(session_uuid="missing"), expected=False)
    expect(actual=manager.kill(session_uuid="abc"), expected=True)
    with pytest.raises(expected_exception=KeyError):
        _ = manager.write(session_uuid="abc", text="x")
    expect(actual=manager.info(session_uuid="abc"), expected=None)
    responses: list[str] = ['{"pid":321,"alive":true}']

    def request(
        *,
        home: Path,
        operation: str,
        values: dict[str, str] | None = None,
        **kwargs: object,
    ) -> str:
        """Return deterministic daemon responses.

        Args:
            home: Home directory.
            operation: Requested operation.
            values: Request values.
            **kwargs: Other request options.

        Returns:
            Response text.

        """
        del home, values, kwargs
        if responses:
            return responses.pop(0)
        return {
            "conpty-output": "out",
            "conpty-write": "3",
            "conpty-kill": "OK",
            "conpty-alive": "1",
        }.get(operation, "")

    monkeypatch.setattr(
        target=conpty_module.EventServer,
        name="request",
        value=request,
    )
    client: ConPtyClient = ConPtyClient(home=tmp_path)
    expect(
        actual=client.create(session_uuid="x", cwd=".", command=("cmd",)).pid,
        expected=Constants.pid,
    )
    expect(actual=client.output(session_uuid="x"), expected="out")
    expect(actual=client.write(session_uuid="x", text="abc"), expected=3)
    expect(actual=client.kill(session_uuid="x"), expected=True)
    expect(actual=client.alive(session_uuid="x"), expected=True)
    monkeypatch.setattr(
        target=conpty_module.stdin,
        name="isatty",
        value=lambda: False,
    )
    expect(actual=client.attach(session_uuid="x"), expected=0)


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ("[]", "Invalid ConPTY daemon response"),
        ('{"pid":true,"alive":true}', "Invalid ConPTY daemon PID"),
        ('{"pid":{},"alive":true}', "Invalid ConPTY daemon PID"),
        ('{"pid":"bad","alive":true}', "Invalid ConPTY daemon PID"),
        ('{"pid":1,"alive":1}', "Invalid ConPTY daemon alive state"),
    ],
)
def test_conpty_client_rejects_invalid_create(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response: str,
    message: str,
) -> None:
    """Reject malformed daemon create responses.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Pytest monkeypatch fixture.
        response: Daemon response.
        message: Expected error text.

    """

    def fake_request(**kwargs: object) -> str:
        """Return the parametrized daemon response.

        Args:
            **kwargs: Request options.

        Returns:
            Daemon response text.

        """
        del kwargs
        return response

    monkeypatch.setattr(
        target=conpty_module.EventServer,
        name="request",
        value=fake_request,
    )
    with pytest.raises(expected_exception=OSError, match=message):
        _ = ConPtyClient(home=tmp_path).create(
            session_uuid="x",
            cwd=".",
            command=("cmd",),
        )


def test_lifecycle_and_tui_deep_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise command lifecycle and interactive TUI branches.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    repository: SessionRepository = SessionRepository(home=tmp_path)
    entry: SessionEntry = SessionEntry(
        id="abc123",
        uuid="uuid-1",
        command="district abc123",
        session="shared-abc123",
        cwd=str(object=tmp_path),
        agent="claude",
        surface="terminal",
    )
    repository.add(entry=entry)
    lifecycle: CommandLifecycle = CommandLifecycle(home=tmp_path)
    command_id: str = lifecycle.start(
        session_id="abc123",
        command="echo secret=abc",
        cwd=str(object=tmp_path),
    )
    lifecycle.finish(session_id="abc123", command_id=command_id, exit_code=0)
    with pytest.raises(expected_exception=KeyError):
        _ = lifecycle.start(
            session_id="missing",
            command="x",
            cwd=str(object=tmp_path),
        )
    shell: Path = tmp_path / "shellrc"
    shell.write_text(data="before\nafter\n")
    LifecycleInstaller.install_file(path=shell, command="shared path")
    LifecycleInstaller.install_file(path=shell, command="shared path")
    check(
        condition=LifecycleInstaller.begin in shell.read_text(),
        reason="shell hook",
    )
    LifecycleInstaller.remove_file(path=shell)
    LifecycleInstaller.remove_file(path=shell)
    powershell: Path = tmp_path / "profile.ps1"
    LifecycleInstaller.install_powershell(path=powershell, command='a`"b')
    LifecycleInstaller.install_powershell(path=powershell, command='a`"b')
    check(
        condition="PSConsoleHostReadLine" in powershell.read_text(),
        reason="powershell hook",
    )
    snapshot: SessionSnapshot = SessionSnapshot(
        id="abc123",
        state="RUNNING",
        clients=1,
        current_command="x" * 40,
        output="line1\nline2",
        age_seconds=3,
        reconnectable=True,
    )
    resources: ResourceSnapshot = ResourceSnapshot(
        processes=(
            ProcessResource(
                pid=1,
                parent_pid=0,
                name="python",
                command="python",
                cpu_percent=1.0,
                rss_bytes=1048576,
                vms_bytes=1048576,
                read_bytes=0,
                write_bytes=0,
                connections=(),
                listening_ports=(),
            ),
        ),
        cpu_percent=1.0,
        rss_bytes=1048576,
        vms_bytes=1048576,
        read_bytes=0,
        write_bytes=0,
        connections=(),
        listening_ports=(),
        partial=False,
    )
    frame: str = WatchTui.render(
        rows=(
            DashboardRow(
                entry=replace(entry, workspace="/" + "x" * 40),
                snapshot=snapshot,
                resources=resources,
            ),
        ),
        selected=99,
    )
    check(
        condition="Processes:" in frame and "…" in frame,
        reason="detailed frame",
    )

    class _Input:
        """Provide a TTY input stream."""

        @staticmethod
        def isatty() -> bool:
            """Return TTY state.

            Returns:
                True.

            """
            return True

    class _Output:
        """Capture TUI writes."""

        def __init__(self) -> None:
            """Initialize output capture."""
            self.values: list[str] = []

        @staticmethod
        def isatty() -> bool:
            """Return TTY state.

            Returns:
                True.

            """
            return True

        def write(self, value: str) -> int:
            """Capture output.

            Args:
                value: Output value.

            Returns:
                Character count.

            """
            self.values.append(value)
            return len(value)

        def flush(self) -> None:
            """Accept flush."""

    output: _Output = _Output()
    monkeypatch.setattr(target=tui_module, name="stdin", value=_Input())
    monkeypatch.setattr(target=tui_module, name="stdout", value=output)
    dashboard: WatchTui = WatchTui(fetch=lambda: ())
    keys: list[str] = ["j", "k", "q"]

    def fake_key(*, timeout: float) -> str:
        """Return the next queued key.

        Args:
            timeout: Read timeout.

        Returns:
            Next key.

        """
        del timeout
        return keys.pop(0)

    monkeypatch.setattr(
        target=WatchTui,
        name="key",
        value=staticmethod(fake_key),
    )
    expect(actual=dashboard.run(), expected=0)


def test_websocket_and_consumer_lifecycle(tmp_path: Path) -> None:
    """Exercise WebSocket delivery and sampler consumer lifecycle.

    Args:
        tmp_path: Scratch directory.

    """
    dashboard: WebDashboard = WebDashboard(
        service=District(repository=SessionRepository(home=tmp_path)),
        home=tmp_path,
    )
    with (
        TestClient(app=dashboard.application()) as client,
        client.websocket_connect(url="/ws") as websocket,
    ):
        payload: str = websocket.receive_text()
        check(
            condition='"sessions"' in payload,
            reason="websocket payload",
        )
    expect(actual=dashboard.consumers, expected=0)


def test_installer_wizard_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise installer wizard rendering, editing, and navigation.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    layout: Layout = _layout(
        home=tmp_path,
        source_root=Constants.source_root,
    )
    components: tuple[Component, ...] = installer_module.components(
        layout=layout,
    )
    visible: tuple[Component, ...] = tuple(
        component for component in components if component.available
    )
    frame: str = installer_module.wizard_frame(
        layout=layout,
        rows=visible,
        selected=frozenset({"launcher"}),
        cursor=0,
    )
    check(
        condition="District Terminal install" in frame,
        reason="wizard frame",
    )
    required: Component = next(
        component for component in visible if component.required
    )
    optional: Component = next(
        component for component in visible if not component.required
    )
    selected: set[str] = {required.key, optional.key}
    installer_module.wizard_toggle(component=required, selected=selected)
    check(condition=required.key in selected, reason="required retained")
    installer_module.wizard_toggle(component=optional, selected=selected)
    check(condition=optional.key not in selected, reason="optional removed")
    installer_module.wizard_toggle(component=optional, selected=selected)
    check(condition=optional.key in selected, reason="optional restored")

    def fake_alias_input(prompt: object = "") -> str:
        """Return a new alias.

        Args:
            prompt: Input prompt.

        Returns:
            Entered alias.

        """
        del prompt
        return "newalias"

    def fake_invalid_input(prompt: object = "") -> str:
        """Return an invalid alias.

        Args:
            prompt: Input prompt.

        Returns:
            Invalid alias.

        """
        del prompt
        return "bad/name"

    def fake_empty_input(prompt: object = "") -> str:
        """Return empty input.

        Args:
            prompt: Input prompt.

        Returns:
            Empty input.

        """
        del prompt
        return ""

    monkeypatch.setattr(target="builtins.input", name=fake_alias_input)
    expect(
        actual=installer_module.wizard_edit(layout=layout, key="a").alias,
        expected="newalias",
    )
    expect(
        actual=installer_module.wizard_edit(layout=layout, key="m").mode,
        expected="copy",
    )
    monkeypatch.setattr(target="builtins.input", name=fake_invalid_input)
    expect(
        actual=installer_module.prompt_alias(current="shared"),
        expected="shared",
    )
    monkeypatch.setattr(target="builtins.input", name=fake_empty_input)
    expect(
        actual=installer_module.prompt_alias(current="shared"),
        expected="shared",
    )
    keys: list[str] = ["down", "up", " ", "m", "\n"]
    monkeypatch.setattr(
        target=installer_module,
        name="read_key",
        value=lambda: keys.pop(0),
    )
    result: tuple[Layout, frozenset[str]] | None = installer_module.wizard(
        layout=layout,
        components=components,
    )
    check(condition=result is not None, reason="wizard completes")
    quit_keys: list[str] = ["q"]
    monkeypatch.setattr(
        target=installer_module,
        name="read_key",
        value=lambda: quit_keys.pop(0),
    )
    expect(
        actual=installer_module.wizard(layout=layout, components=components),
        expected=None,
    )


class _CliService:
    """Provide controllable CLI service behavior."""

    def __init__(self) -> None:
        """Initialize fake service."""
        self.entries: tuple[SessionEntry, ...] = ()
        self.fail: str = ""

    @staticmethod
    def create(*, initial_command: tuple[str, ...]) -> int:
        """Create a session.

        Args:
            initial_command: Initial command.

        Returns:
            Exit status.

        """
        return len(initial_command)

    @staticmethod
    def ensure_agent(*, initial_command: tuple[str, ...]) -> int:
        """Ensure an agent session.

        Args:
            initial_command: Initial command.

        Returns:
            Exit status.

        """
        return len(initial_command)

    def status(self, *, session_id: str) -> SessionEntry:
        """Return one status row.

        Args:
            session_id: Session identifier.

        Returns:
            Status row.

        """
        return SessionEntry(id=session_id, command="shared", session="s")

    def attach(self, *, session_id: str) -> int:
        """Attach to a session.

        Args:
            session_id: Session identifier.

        Returns:
            Exit status.

        Raises:
            KeyError: For requested failure.

        """
        if self.fail == "key":
            raise KeyError(session_id)
        return 7

    def execute(self, *, session_id: str, command: str) -> ExecResult:
        """Execute a command.

        Args:
            session_id: Session identifier.
            command: Command text.

        Returns:
            Execution result.

        Raises:
            KeyError: For missing session.
            TimeoutError: For timeout simulation.

        """
        if self.fail == "key":
            raise KeyError(session_id)
        if self.fail == "timeout":
            raise TimeoutError
        return ExecResult(exit_status=3, output=command)

    def kill(self, *, session_id: str) -> int:
        """Kill a session.

        Args:
            session_id: Session identifier.

        Returns:
            Exit status.

        Raises:
            KeyError: For missing session.

        """
        if self.fail == "key":
            raise KeyError(session_id)
        return 0

    def list_entries(self) -> tuple[SessionEntry, ...]:
        """Return configured entries.

        Returns:
            Entries.

        """
        return self.entries

    def cleanup(self, *, session_id: str) -> None:
        """Accept cleanup.

        Args:
            session_id: Session identifier.

        """
        self.fail = session_id


class _NamedService(_CliService):
    """Extend the CLI fake with recorded session-name interactions."""

    def __init__(self) -> None:
        """Initialize the named-session fake."""
        super().__init__()
        self.attached: list[str] = []
        self.killed: list[str] = []
        self.sent: list[tuple[str, str, tuple[str, ...]]] = []

    def status(self, *, session_id: str) -> SessionEntry:
        """Return the matching configured entry.

        Args:
            session_id: Session identifier.

        Returns:
            Status row.

        Raises:
            KeyError: For an unknown session.

        """
        for entry in self.entries:
            if entry.id == session_id:
                return entry
        raise KeyError(session_id)

    def attach(self, *, session_id: str) -> int:
        """Record an attach request.

        Args:
            session_id: Session identifier.

        Returns:
            Exit status.

        """
        self.attached.append(session_id)
        return 7

    def send(
        self,
        *,
        session_id: str,
        text: str,
        keys: tuple[str, ...],
    ) -> None:
        """Record a send request.

        Args:
            session_id: Session identifier.
            text: Literal text.
            keys: Key names.

        """
        self.sent.append((session_id, text, keys))

    def kill(self, *, session_id: str) -> int:
        """Record a kill request.

        Args:
            session_id: Session identifier.

        Returns:
            Exit status.

        """
        self.killed.append(session_id)
        return 0


def test_cli_dispatch_attach_and_kill_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise CLI dispatch, attach, and kill branches.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    service: _CliService = _CliService()
    cli: Cli = Cli(service=cast(typ="District", val=service))
    monkeypatch.setattr(
        target=Cli,
        name="_persistent_backend_available",
        value=staticmethod(lambda: True),
    )
    expect(actual=cli.dispatch(args=("echo",)), expected=1)
    expect(actual=cli.known(args=("--id",)), expected=1)
    expect(actual=cli.known(args=("--agent", "x")), expected=1)
    monkeypatch.setattr(
        target=Cli,
        name="_persistent_backend_available",
        value=staticmethod(lambda: False),
    )
    expect(actual=cli.known(args=("--ensure",)), expected=127)
    expect(actual=cli.dispatch(args=("echo",)), expected=127)
    monkeypatch.setattr(
        target=Cli,
        name="_persistent_backend_available",
        value=staticmethod(lambda: True),
    )
    expect(actual=cli.dispatch(args=("--wat",)), expected=2)
    expect(
        actual=cli.by_id(args=("abc123", "--other"), session_id="abc123"),
        expected=None,
    )
    expect(actual=cli.attach(session_id="abc123"), expected=7)
    service.fail = "key"
    expect(actual=cli.attach(session_id="abc123"), expected=2)
    expect(actual=cli.kill(session_id="abc123"), expected=2)
    service.fail = ""
    expect(actual=cli.kill(session_id="abc123"), expected=0)


def test_cli_exec_managed_and_cleanup() -> None:
    """Exercise CLI execute, cleanup, and managed branches."""
    service: _CliService = _CliService()
    cli: Cli = Cli(service=cast(typ="District", val=service))
    expect(actual=cli.exec(args=(), session_id="abc123"), expected=2)
    expect(
        actual=cli.exec(args=("echo", "hello"), session_id="abc123"),
        expected=3,
    )
    service.fail = "timeout"
    expect(actual=cli.exec(args=("echo",), session_id="abc123"), expected=124)
    service.fail = "key"
    expect(actual=cli.exec(args=("echo",), session_id="abc123"), expected=2)
    service.fail = ""
    expect(actual=cli.exec(args=("echo",), session_id=None), expected=2)
    service.entries = (
        SessionEntry(id="abc123", command="shared", session="s"),
    )
    expect(actual=cli.exec(args=("echo",), session_id=None), expected=3)
    expect(actual=cli.cleanup(args=("--cleanup",)), expected=2)
    expect(actual=cli.cleanup(args=("--cleanup", "abc123")), expected=0)
    expect(
        actual=cli.managed(args=("--id",), session_id="abc123"),
        expected=0,
    )
    expect(
        actual=cli.managed(args=("--status",), session_id="abc123"),
        expected=0,
    )
    expect(
        actual=cli.managed(args=("--exec", "echo"), session_id="abc123"),
        expected=3,
    )


def _named_service() -> _NamedService:
    """Build a fake service holding provider-named entries.

    Returns:
        Configured named-session fake.

    """
    service: _NamedService = _NamedService()
    service.entries = (
        SessionEntry(
            id="abc123",
            command="district abc123",
            session="s1",
            agent="devin",
            agent_session="dv-1",
            created_at="2026-01-01",
        ),
        SessionEntry(
            id="def456",
            command="district def456",
            session="s2",
            agent="devin",
            agent_session="dv-1",
            created_at="2026-01-02",
        ),
        SessionEntry(
            id="fff000",
            command="district fff000",
            session="s3",
            agent="cursor",
            agent_session="cs-1",
            created_at="2026-01-03",
        ),
    )
    return service


def test_cli_session_name_enter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve --session-name NAME to the newest matching district session.

    Args:
        tmp_path: Temporary home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.delenv(name="DISTRICT_SESSION_ID", raising=False)
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    service: _NamedService = _named_service()
    cli: Cli = Cli(service=cast(typ="District", val=service))
    expect(actual=cli.run(arguments=("--session-name",)), expected=1)
    expect(actual=cli.run(arguments=("--session-name", "nope")), expected=2)
    expect(actual=cli.run(arguments=("--session-name", "dv-1")), expected=7)
    expect(actual=service.attached, expected=["def456"])
    expect(
        actual=cli.run(
            arguments=("--session-name", "dv-1", "--exec", "echo"),
        ),
        expected=3,
    )
    expect(
        actual=cli.run(
            arguments=("--session-name", "dv-1", "--send", "hi"),
        ),
        expected=0,
    )
    expect(
        actual=service.sent,
        expected=[("def456", "hi", ("Enter",))],
    )
    expect(
        actual=cli.run(
            arguments=("--session-name", "dv-1", "--send-key", "C-c"),
        ),
        expected=0,
    )
    expect(
        actual=service.sent[-1],
        expected=("def456", "", ("C-c",)),
    )
    expect(
        actual=cli.run(
            arguments=("--session-name", "dv-1", "--kill"),
        ),
        expected=0,
    )
    expect(actual=service.killed, expected=["def456"])
    expect(actual=cli.run(arguments=("--session-name", "cs-1")), expected=7)
    expect(actual=service.attached[-1], expected="fff000")
    expect(
        actual=cli.run(
            arguments=("--session-name", "dv-1", "--bogus"),
        ),
        expected=2,
    )
    expect(actual=cli.session_by_name(args=()), expected=2)


def test_cli_session_name_get_inside(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve --session-name inside a managed session by precedence.

    Args:
        tmp_path: Temporary home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    markers: tuple[str, ...] = (
        "DISTRICT_AGENT_SESSION",
        "DEVIN_SESSION_ID",
        "CURSOR_SESSION_ID",
        "CLAUDE_SESSION_ID",
        "CODEX_SESSION_ID",
        "CODEX_THREAD_ID",
    )
    for marker in markers:
        monkeypatch.delenv(name=marker, raising=False)
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    monkeypatch.setenv(name="DISTRICT_SESSION_ID", value="abc123")
    printed: StringIO = StringIO()
    monkeypatch.setattr(
        target=cli_module,
        name="stdout",
        value=printed,
    )
    service: _NamedService = _named_service()
    cli: Cli = Cli(service=cast(typ="District", val=service))
    expect(actual=cli.run(arguments=("--session-name",)), expected=0)
    expect(actual=printed.getvalue(), expected="dv-1\n")
    monkeypatch.setenv(name="DISTRICT_AGENT_SESSION", value="inner-1")
    printed.truncate(0)
    _ = printed.seek(0)
    expect(actual=cli.run(arguments=("--session-name",)), expected=0)
    expect(actual=printed.getvalue(), expected="inner-1\n")
    monkeypatch.delenv(name="DISTRICT_AGENT_SESSION")
    service.entries = (
        SessionEntry(
            id="abc123",
            command="district abc123",
            session="s1",
            agent="devin",
        ),
    )
    monkeypatch.setenv(name="DEVIN_SESSION_ID", value="env-1")
    printed.truncate(0)
    _ = printed.seek(0)
    expect(actual=cli.run(arguments=("--session-name",)), expected=0)
    expect(actual=printed.getvalue(), expected="env-1\n")
    monkeypatch.delenv(name="DEVIN_SESSION_ID")
    expect(actual=cli.run(arguments=("--session-name",)), expected=1)
    monkeypatch.setenv(name="DISTRICT_SESSION_ID", value="dead00")
    expect(actual=cli.run(arguments=("--session-name",)), expected=2)
    monkeypatch.setenv(name="DEVIN_SESSION_ID", value="env-2")
    printed.truncate(0)
    _ = printed.seek(0)
    expect(actual=cli.run(arguments=("--session-name",)), expected=0)
    expect(actual=printed.getvalue(), expected="env-2\n")


def test_installer_uri_handler_and_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise URI handler and editor hook component operations.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    layout: Layout = _layout(
        home=tmp_path,
        source_root=Constants.source_root,
    )
    calls: list[tuple[str, ...]] = []

    def fake_run(
        *,
        args: tuple[str, ...],
        **kwargs: object,
    ) -> CompletedProcess[str]:
        """Record installer subprocesses.

        Args:
            args: Command arguments.
            **kwargs: Other options.

        Returns:
            Successful result.

        """
        del kwargs
        calls.append(args)
        if args[0] == "/usr/bin/osacompile":
            app: Path = Path(args[2])
            (app / "Contents").mkdir(parents=True, exist_ok=True)
            (app / "Contents" / "Info.plist").write_text(data="")
        return CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(target=installer_module, name="run", value=fake_run)
    launch_services: Path = tmp_path / "lsregister"
    launch_services.write_text(data="")
    monkeypatch.setattr(
        target=installer_module.Constants,
        name="launch_services",
        value=launch_services,
    )
    result: str = installer_module.install_uri_handler(layout=layout)
    check(
        condition=result.endswith("DistrictURI.app"),
        reason="URI handler app path",
    )
    expect(
        actual=installer_module.uri_handler_installed(layout=layout),
        expected=True,
    )
    installer_module.remove_uri_handler(layout=layout)
    expect(
        actual=installer_module.uri_handler_installed(layout=layout),
        expected=False,
    )
    check(condition=bool(calls), reason="URI handler subprocesses")
    for install, installed, remove in (
        (
            installer_module.install_claude,
            installer_module.claude_installed,
            installer_module.remove_claude,
        ),
        (
            installer_module.install_codex,
            installer_module.codex_installed,
            installer_module.remove_codex,
        ),
        (
            installer_module.install_cursor,
            installer_module.cursor_installed,
            installer_module.remove_cursor,
        ),
        (
            installer_module.install_devin,
            installer_module.devin_installed,
            installer_module.remove_devin,
        ),
    ):
        _ = install(layout=layout)
        expect(actual=installed(layout=layout), expected=True)
        remove(layout=layout)
        expect(actual=installed(layout=layout), expected=False)


def test_core_tmux_creation_and_live_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise tmux creation and live-session discovery branches.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    runtime: RuntimePlatform = _runtime(os_name="linux")
    monkeypatch.setattr(
        target=core_module.RuntimePlatform,
        name="detect",
        value=staticmethod(lambda: runtime),
    )
    monkeypatch.setattr(
        target=core_module.RuntimePlatform,
        name="surface",
        value=staticmethod(lambda: "cli"),
    )
    monkeypatch.setattr(
        target=core_module.RuntimePlatform,
        name="editor",
        value=staticmethod(lambda: ""),
    )
    monkeypatch.setattr(
        target=core_module.ProcessIdentity,
        name="capture",
        value=staticmethod(_capture_identity),
    )
    service: District = District(
        repository=SessionRepository(home=tmp_path),
    )
    monkeypatch.setattr(target=service, name="reconcile", value=lambda: ())

    def fake_new_id(*, entries: tuple[SessionEntry, ...]) -> str:
        """Return a fixed session identifier.

        Args:
            entries: Live session entries.

        Returns:
            Fixed identifier.

        """
        del entries
        return "abc123"

    def fake_pane_pid(*, session: str) -> int:
        """Return a fixed pane process identifier.

        Args:
            session: Tmux session name.

        Returns:
            Fake process identifier.

        """
        del session
        return 123

    monkeypatch.setattr(target=service, name="new_id", value=fake_new_id)
    monkeypatch.setattr(
        target=service,
        name="pane_pid",
        value=fake_pane_pid,
    )

    def fake_run(
        *,
        args: tuple[str, ...],
        **kwargs: object,
    ) -> CompletedProcess[str]:
        """Return successful tmux operations.

        Args:
            args: Command arguments.
            **kwargs: Other subprocess options.

        Returns:
            Successful process result.

        """
        del kwargs
        return CompletedProcess(
            args=args,
            returncode=0,
            stdout="shared-a\nshared-b\n",
            stderr="",
        )

    monkeypatch.setattr(target=core_module, name="run", value=fake_run)
    expect(actual=service.create(initial_command=("echo", "x")), expected=0)
    check(
        condition=service.repository.read()[0].id == "abc123",
        reason="session persisted",
    )
    expect(
        actual=District.live_session_names(),
        expected=frozenset({"shared-a", "shared-b"}),
    )

    def failed_run(
        *,
        args: tuple[str, ...],
        **kwargs: object,
    ) -> CompletedProcess[str]:
        """Return failed tmux listing.

        Args:
            args: Command arguments.
            **kwargs: Other subprocess options.

        Returns:
            Failed process result.

        """
        del kwargs
        return CompletedProcess(args=args, returncode=1, stdout="", stderr="")

    monkeypatch.setattr(target=core_module, name="run", value=failed_run)
    expect(actual=District.live_session_names(), expected=frozenset[str]())

    def error_run(
        *,
        args: tuple[str, ...],
        **kwargs: object,
    ) -> CompletedProcess[str]:
        """Raise an OS error.

        Args:
            args: Command arguments.
            **kwargs: Other subprocess options.

        Raises:
            OSError: Always.

        Returns:
            Never returns.

        """
        del args, kwargs
        raise OSError

    monkeypatch.setattr(target=core_module, name="run", value=error_run)
    expect(actual=District.live_session_names(), expected=None)


def test_cli_durable_control_helpers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise durable CLI host, lifecycle, event, and subscription helpers.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    expect(
        actual=Cli.host_add(home=tmp_path, args=("alpha", "u@host")),
        expected=0,
    )
    expect(
        actual=Cli.host_add(home=tmp_path, args=("beta", "u@host", "custom")),
        expected=0,
    )
    expect(actual=Cli.hosts(home=tmp_path), expected=0)
    expect(actual=Cli.host_remove(home=tmp_path, args=("alpha",)), expected=0)
    expect(actual=Cli.host_remove(home=tmp_path, args=()), expected=2)
    repository: SessionRepository = SessionRepository(home=tmp_path)
    repository.add(
        entry=SessionEntry(
            id="abc123",
            uuid="uuid-1",
            command="shared",
            session="s",
            cwd=str(object=tmp_path),
        ),
    )
    monkeypatch.setenv(name="DISTRICT_SESSION_ID", value="abc123")
    expect(
        actual=Cli.lifecycle_start(home=tmp_path, args=("bad",)),
        expected=2,
    )
    expect(
        actual=Cli.lifecycle_start(home=tmp_path, args=("--", "echo", "x")),
        expected=0,
    )
    expect(
        actual=Cli.lifecycle_finish(home=tmp_path, args=("id", "bad")),
        expected=2,
    )
    expect(actual=Cli.agent_state(home=tmp_path, args=("bad",)), expected=2)
    expect(
        actual=Cli.agent_state(home=tmp_path, args=("thinking",)),
        expected=0,
    )
    expect(actual=Cli.events(home=tmp_path, filters=()), expected=0)
    expect(actual=Cli.history(home=tmp_path, filters=()), expected=0)
    expect(actual=Cli.purge(home=tmp_path), expected=0)

    class _Subscriber:
        """Provide one event then close."""

        def __init__(self, *, home: Path) -> None:
            """Initialize subscriber.

            Args:
                home: Home path.

            """
            del home
            self.closed: bool = False

        @staticmethod
        def receive(*, timeout: float) -> str:
            """Return one event.

            Args:
                timeout: Receive timeout.

            Returns:
                Event payload.

            """
            del timeout
            return "{}"

        def close(self) -> None:
            """Record closure."""
            self.closed = True

    monkeypatch.setattr(
        target=cli_module.DistrictDaemon,
        name="ping",
        value=staticmethod(_ping_true),
    )
    monkeypatch.setattr(
        target=cli_module,
        name="EventSubscriber",
        value=_Subscriber,
    )
    expect(actual=Cli.subscribe(home=tmp_path, once=True), expected=0)


def test_installer_dry_run_paths(tmp_path: Path) -> None:
    """Exercise installer dry-run install and uninstall plans.

    Args:
        tmp_path: Scratch directory.

    """
    source_root: Path = Constants.source_root
    installer: Installer = Installer(
        source_root=source_root,
        home=tmp_path,
        reporter=Reporter(colour=False),
    )
    layout: Layout = installer.layout(
        alias="shared",
        mode="live",
        bin_dir=None,
        lib_dir=None,
    )
    selected: frozenset[str] = frozenset(
        component.key
        for component in installer.components(layout=layout)
        if component.available
    )
    expect(
        actual=installer.install(
            layout=layout,
            selected=selected,
            dry_run=True,
            force=True,
        ),
        expected=0,
    )
    expect(actual=installer.uninstall(purge=True, dry_run=True), expected=0)


def test_web_run_and_health_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise web singleton startup and health-check branches.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    dashboard: WebDashboard = WebDashboard(
        service=District(repository=SessionRepository(home=tmp_path)),
        home=tmp_path,
    )
    opened: list[str] = []

    def fake_open_browser(*, url: str, new: int) -> None:
        """Record opened URLs.

        Args:
            url: Dashboard URL.
            new: Browser target.

        """
        del new
        opened.append(url)

    def fake_port(*, host: str, requested: int) -> int:
        """Return a fixed free port.

        Args:
            host: Bind host.
            requested: Requested port.

        Returns:
            Fixed port.

        """
        del host, requested
        return 9000

    monkeypatch.setattr(
        target=web_module,
        name="open_browser",
        value=fake_open_browser,
    )
    monkeypatch.setattr(
        target=web_module.ProcessIdentity,
        name="capture",
        value=staticmethod(_capture_identity),
    )
    monkeypatch.setattr(
        target=WebDashboard,
        name="port",
        value=staticmethod(fake_port),
    )

    class _Server:
        """Provide a no-op Uvicorn server."""

        def __init__(self, *, config: object) -> None:
            """Store configuration.

            Args:
                config: Uvicorn configuration.

            """
            self.config: object = config

        def run(self) -> None:
            """Accept server execution."""

    monkeypatch.setattr(target=web_module, name="Server", value=_Server)
    expect(
        actual=dashboard.run(host="127.0.0.1", port=0, browser=True),
        expected=0,
    )
    check(condition=bool(opened), reason="browser opened")
    monkeypatch.setattr(
        target=dashboard,
        name="running_url",
        value=lambda: "http://127.0.0.1:9000/",
    )
    expect(
        actual=dashboard.run(host="127.0.0.1", port=9000, browser=False),
        expected=0,
    )
    expect(
        actual=dashboard.run(host="127.0.0.1", port=9000, browser=True),
        expected=0,
    )
    expect(
        actual=WebDashboard.health_check(url="https://host/"),
        expected=False,
    )
    expect(
        actual=WebDashboard.health_check(url="http:///missing"),
        expected=False,
    )
    expect(
        actual=WebDashboard.health_check(url="http://host:bad/"),
        expected=False,
    )

    class _Response:
        """Provide a successful HTTP response."""

        status: Final[int] = 200

    class _Connection:
        """Provide a fake HTTP connection."""

        def __init__(self, **kwargs: object) -> None:
            """Accept connection options.

            Args:
                **kwargs: Connection options.

            """
            del kwargs

        @staticmethod
        def request(**kwargs: object) -> None:
            """Accept a request.

            Args:
                **kwargs: Request options.

            """
            del kwargs

        @staticmethod
        def getresponse() -> _Response:
            """Return a successful response.

            Returns:
                Successful response.

            """
            return _Response()

        def close(self) -> None:
            """Accept connection closure."""

    monkeypatch.setattr(
        target=web_module,
        name="HTTPConnection",
        value=_Connection,
    )
    expect(
        actual=WebDashboard.health_check(url="http://host:80/"),
        expected=True,
    )


def test_tui_windows_key_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise Windows dashboard keyboard decoding branches.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """

    class _Msvcrt:
        """Provide deterministic Windows key input."""

        keys: ClassVar[list[str]] = ["\xe0", "H"]

        @classmethod
        def kbhit(cls) -> bool:
            """Return whether a key remains.

            Returns:
                Whether input remains.

            """
            return bool(cls.keys)

        @classmethod
        def getwch(cls) -> str:
            """Return the next key.

            Returns:
                Next key.

            """
            return cls.keys.pop(0)

    def import_msvcrt(*, name: str) -> type[_Msvcrt]:
        """Return the fake msvcrt module.

        Args:
            name: Requested module name.

        Returns:
            Fake module.

        """
        del name
        return _Msvcrt

    monkeypatch.setattr(target=tui_module, name="os_name", value="nt")
    monkeypatch.setattr(
        target=tui_module,
        name="import_module",
        value=import_msvcrt,
    )
    expect(actual=WatchTui.key(timeout=0.1), expected="\x1b[A")
    _Msvcrt.keys = ["x"]
    expect(actual=WatchTui.key(timeout=0.1), expected="x")
    _Msvcrt.keys = []
    monkeypatch.setattr(target=tui_module, name="sleep", value=_no_sleep)
    expect(actual=WatchTui.key(timeout=0.01), expected="")


def test_storage_recovery_and_schema_mismatch(tmp_path: Path) -> None:
    """Corrupt or older-schema databases are set aside and rebuilt.

    A newer schema is left alone, and there is no in-place upgrade path.

    Args:
        tmp_path: Scratch directory.

    """
    corrupt_dir: Path = tmp_path / "corrupt" / ".district"
    corrupt_dir.mkdir(parents=True)
    corrupt_path: Path = corrupt_dir / StorageConstants.database_name
    corrupt_path.write_bytes(data=b"not sqlite")
    recovered: DistrictDatabase = DistrictDatabase(home=tmp_path / "corrupt")
    check(
        condition=bool(recovered.recovered_from),
        reason="corrupt database archived",
    )
    expect(actual=recovered.integrity(), expected="ok")
    for version, rebuilt in (
        (StorageConstants.schema_version - 1, True),
        (StorageConstants.schema_version + 1, False),
    ):
        home: Path = tmp_path / f"schema-{version}"
        state: Path = home / ".district"
        state.mkdir(parents=True)
        database: Connection = connect(
            database=state / StorageConstants.database_name,
        )
        _ = database.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)",
        )
        _ = database.execute(
            "INSERT INTO schema_meta VALUES('schema_version', ?)",
            (str(object=version),),
        )
        database.commit()
        database.close()
        opened: DistrictDatabase = DistrictDatabase(home=home)
        expect(actual=bool(opened.recovered_from), expected=rebuilt)
        expect(
            actual=opened.schema_version(),
            expected=(StorageConstants.schema_version if rebuilt else version),
        )


def test_cli_render_and_router_branches(tmp_path: Path) -> None:
    """Exercise remaining CLI rendering and early-router branches.

    Args:
        tmp_path: Scratch directory.

    """
    service: District = District(
        repository=SessionRepository(home=tmp_path),
    )
    entry: SessionEntry = SessionEntry(
        id="abc123",
        command="district abc123",
        session="s",
        agent="claude",
        cwd=str(object=tmp_path),
    )
    service.repository.add(entry=entry)
    cli: Cli = Cli(service=service)
    Cli.print_entries(entries=())
    Cli.print_entries(entries=(entry,))
    Cli.print_global_status(
        rows=[
            {
                "host": "local",
                "agent": "a",
                "surface": "cli",
                "state": "EXECUTING",
                "project": "p",
                "command": "c",
            },
            {"state": "WAITING_FOR_USER"},
            {"state": "IDLE"},
        ],
    )
    expect(actual=cli.list_sessions(args=("--list", "claude")), expected=0)
    expect(actual=cli.list_sessions(args=("--list", "other")), expected=0)
    expect(actual=cli.view(filters=()), expected=2)
    expect(actual=cli.view(filters=("missing",)), expected=2)
    expect(
        actual=cli.global_status(
            home=tmp_path,
            args=("--json", "--local-only"),
        ),
        expected=0,
    )
    expect(
        actual=cli.global_status(home=tmp_path, args=("--local-only",)),
        expected=0,
    )
    expect(
        actual=cli.early_base(args=("--unknown",), home=tmp_path),
        expected=None,
    )
    expect(
        actual=cli.early_observation(args=("--nothing",), home=tmp_path),
        expected=None,
    )
    expect(
        actual=cli.early_control(args=("--nothing",), home=tmp_path),
        expected=None,
    )
    expect(
        actual=cli.early(args=("--nothing",), home=tmp_path),
        expected=None,
    )

    class _Dashboard:
        """Provide web-info behavior."""

        @staticmethod
        def running_url() -> str:
            """Return a running URL.

            Returns:
                Running URL.

            """
            return "http://localhost/"

    class _Startup:
        """Provide startup state transitions."""

        @staticmethod
        def state() -> startup_module.StartupState:
            """Return startup state.

            Returns:
                Startup state.

            """
            return startup_module.StartupState(
                enabled=False,
                mechanism="x",
                location="y",
            )

        @staticmethod
        def enable() -> startup_module.StartupState:
            """Return enabled state.

            Returns:
                Enabled state.

            """
            return startup_module.StartupState(
                enabled=True,
                mechanism="x",
                location="y",
            )

        @staticmethod
        def disable() -> startup_module.StartupState:
            """Return disabled state.

            Returns:
                Disabled state.

            """
            return startup_module.StartupState(
                enabled=False,
                mechanism="x",
                location="y",
            )

    dashboard: WebDashboard = cast(typ="WebDashboard", val=_Dashboard())
    startup: WebStartup = cast(typ="WebStartup", val=_Startup())
    expect(
        actual=cli.web_special(
            args=("info",),
            dashboard=dashboard,
            startup=startup,
        ),
        expected=0,
    )
    expect(
        actual=cli.web_special(
            args=("--startup", "state"),
            dashboard=dashboard,
            startup=startup,
        ),
        expected=0,
    )
    expect(
        actual=cli.web_special(
            args=("--startup",),
            dashboard=dashboard,
            startup=startup,
        ),
        expected=0,
    )
    expect(
        actual=cli.web_special(
            args=("--no-startup",),
            dashboard=dashboard,
            startup=startup,
        ),
        expected=0,
    )
    expect(
        actual=cli.web_special(
            args=("other",),
            dashboard=dashboard,
            startup=startup,
        ),
        expected=None,
    )


def test_installer_windows_and_python_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise Windows launcher/default paths and interpreter probing.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    layout: Layout = _layout(
        home=tmp_path,
        source_root=Constants.source_root,
    )
    monkeypatch.setattr(
        target=installer_module,
        name="system",
        value=lambda: "Windows",
    )
    installer_module.write_launcher(layout=layout)
    check(
        condition="@echo off" in layout.launcher.read_text(),
        reason="windows launcher",
    )
    check(
        condition="district"
        in str(object=installer_module.default_bin_dir(home=tmp_path)),
        reason="windows bin",
    )
    check(
        condition="district"
        in str(object=installer_module.default_lib_dir(home=tmp_path)),
        reason="windows lib",
    )
    source_root: Path = tmp_path / "source"
    candidate: Path = source_root / ".venv" / "bin" / "python"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(data="")
    installer: Installer = Installer(
        source_root=source_root,
        home=tmp_path,
        reporter=Reporter(colour=False),
    )

    def ok_run(**kwargs: object) -> CompletedProcess[str]:
        """Return a successful process result.

        Args:
            **kwargs: Subprocess options.

        Returns:
            Successful result.

        """
        del kwargs
        return CompletedProcess(args=(), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        target=installer_module,
        name="run",
        value=ok_run,
    )
    expect(actual=installer.resolve_python(), expected=str(object=candidate))


def test_conpty_interactive_attach_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise interactive ConPTY attach output and key translation.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    client: ConPtyClient = ConPtyClient(home=tmp_path)
    monkeypatch.setattr(target=conpty_module, name="os_name", value="nt")
    monkeypatch.setattr(
        target=conpty_module.stdin,
        name="isatty",
        value=lambda: True,
    )
    outputs: list[str] = ["hello", "hello world"]
    alive_values: list[bool] = [True, True, False]
    writes: list[str] = []

    def fake_alive(*, session_uuid: str) -> bool:
        """Return the next queued liveness.

        Args:
            session_uuid: Session UUID.

        Returns:
            Queued liveness.

        """
        del session_uuid
        return alive_values.pop(0)

    def fake_output(*, session_uuid: str) -> str:
        """Return the next queued output.

        Args:
            session_uuid: Session UUID.

        Returns:
            Buffered output.

        """
        del session_uuid
        return outputs.pop(0) if outputs else "hello world"

    def fake_write(*, session_uuid: str, text: str) -> int:
        """Record written input.

        Args:
            session_uuid: Session UUID.
            text: Written text.

        Returns:
            Character count.

        """
        del session_uuid
        writes.append(text)
        return len(text)

    monkeypatch.setattr(target=client, name="alive", value=fake_alive)
    monkeypatch.setattr(target=client, name="output", value=fake_output)
    monkeypatch.setattr(target=client, name="write", value=fake_write)

    class _Msvcrt:
        """Provide bowman-key input followed by ordinary input."""

        keys: ClassVar[list[str]] = ["\xe0", "H", "x"]

        @classmethod
        def kbhit(cls) -> bool:
            """Return whether keys remain.

            Returns:
                Whether keys remain.

            """
            return bool(cls.keys)

        @classmethod
        def getwch(cls) -> str:
            """Return the next key.

            Returns:
                Next key.

            """
            return cls.keys.pop(0)

    def import_msvcrt(*, name: str) -> type[_Msvcrt]:
        """Return the fake msvcrt module.

        Args:
            name: Requested module name.

        Returns:
            Fake module.

        """
        del name
        return _Msvcrt

    monkeypatch.setattr(
        target=conpty_module,
        name="import_module",
        value=import_msvcrt,
    )
    monkeypatch.setattr(target=conpty_module, name="sleep", value=_no_sleep)
    expect(actual=client.attach(session_uuid="abc"), expected=0)
    check(
        condition="\x1b[A" in writes and "x" in writes,
        reason="keys forwarded",
    )
    alive_values[:] = [True]
    outputs[:] = [""]
    _Msvcrt.keys = ["\x1d"]
    expect(actual=client.attach(session_uuid="abc"), expected=0)


def test_installer_key_reader_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise Windows and POSIX installer key decoding.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """

    class _Msvcrt:
        """Provide Windows key sequences."""

        keys: ClassVar[list[str]] = ["\xe0", "P"]

        @classmethod
        def getwch(cls) -> str:
            """Return the next key.

            Returns:
                Next key.

            """
            return cls.keys.pop(0)

    def import_msvcrt(*, name: str) -> type[_Msvcrt]:
        """Return the fake msvcrt module.

        Args:
            name: Requested module name.

        Returns:
            Fake module.

        """
        del name
        return _Msvcrt

    monkeypatch.setattr(target=installer_module, name="os_name", value="nt")
    monkeypatch.setattr(
        target=installer_module,
        name="import_module",
        value=import_msvcrt,
    )
    expect(actual=installer_module.read_key(), expected="down")
    _Msvcrt.keys = ["x"]
    expect(actual=installer_module.read_key(), expected="x")


def test_installer_posix_key_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise POSIX installer key decoding.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """

    class _Input:
        """Provide a raw POSIX input stream."""

        chars: ClassVar[list[str]] = ["\x1b", "[", "A"]

        @staticmethod
        def isatty() -> bool:
            """Return TTY state.

            Returns:
                True.

            """
            return True

        @staticmethod
        def fileno() -> int:
            """Return a fake descriptor.

            Returns:
                Descriptor.

            """
            return 0

        @classmethod
        def read(cls, size: int) -> str:
            """Read one character.

            Args:
                size: Requested size.

            Returns:
                Next character.

            """
            del size
            return cls.chars.pop(0)

    class _Termios:
        """Provide terminal attribute operations."""

        TCSADRAIN: Final[int] = 1

        @staticmethod
        def tcgetattr(fd: int) -> list[object]:
            """Return saved terminal state.

            Args:
                fd: Descriptor.

            Returns:
                Saved state.

            """
            del fd
            return []

        @staticmethod
        def tcsetattr(fd: int, when: int, attributes: list[object]) -> None:
            """Restore terminal state.

            Args:
                fd: Descriptor.
                when: Restore mode.
                attributes: Saved attributes.

            """
            del fd, when, attributes

    class _Tty:
        """Provide cbreak activation."""

        @staticmethod
        def setcbreak(fd: int) -> None:
            """Accept cbreak activation.

            Args:
                fd: Descriptor.

            """
            del fd

    def import_terminal(*, name: str) -> type[object]:
        """Return the requested fake terminal module.

        Args:
            name: Requested module name.

        Returns:
            Fake termios or tty module.

        """
        return _Termios if name == "termios" else _Tty

    monkeypatch.setattr(target=installer_module, name="os_name", value="posix")
    monkeypatch.setattr(target=installer_module, name="stdin", value=_Input())
    monkeypatch.setattr(
        target=installer_module,
        name="import_module",
        value=import_terminal,
    )
    ready_values: list[bool] = [True, True, False]

    def fake_select(
        *args: object,
    ) -> tuple[list[int], list[object], list[object]]:
        """Report readiness from the queued sequence.

        Args:
            *args: Descriptor sets.

        Returns:
            Ready descriptor sets.

        """
        del args
        if ready_values.pop(0):
            return ([0], [], [])
        return ([], [], [])

    monkeypatch.setattr(
        target=installer_module,
        name="select",
        value=fake_select,
    )
    expect(actual=installer_module.read_key(), expected="up")
    _Input.chars = ["j"]
    expect(actual=installer_module.read_key(), expected="j")


def test_conpty_drain_and_capability_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise ConPTY drain output, respawn failure, and capability errors.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Pytest monkeypatch fixture.

    """

    class _DrainSession(_FakeSession):
        """Provide output followed by a read failure."""

        def __init__(self) -> None:
            """Initialize output sequence."""
            super().__init__()
            self.reads: int = 0

        def read(self, *, size: int = 4096) -> bytes:
            """Return output once and then fail.

            Args:
                size: Maximum read size.

            Returns:
                First output chunk.

            Raises:
                OSError: After the first chunk.

            """
            del size
            self.reads += 1
            if self.reads == 1:
                return b"hello"
            raise OSError

    session: _DrainSession = _DrainSession()
    calls: int = 0

    def spawn(**kwargs: object) -> _DrainSession:
        """Create once and reject respawn.

        Args:
            **kwargs: Spawn options.

        Returns:
            Initial fake session.

        Raises:
            OSError: On respawn.

        """
        nonlocal calls
        del kwargs
        calls += 1
        if calls > 1:
            raise OSError
        return session

    monkeypatch.setattr(
        target=conpty_module.WindowsConPty,
        name="spawn",
        value=spawn,
    )
    monkeypatch.setattr(
        target=conpty_module,
        name="Thread",
        value=_NoStartThread,
    )
    monkeypatch.setattr(target=conpty_module, name="sleep", value=_no_sleep)
    manager: ConPtyManager = ConPtyManager(policy=PrivacyPolicy())
    _ = manager.create(
        session_uuid="drain",
        cwd=str(object=tmp_path),
        command=("cmd",),
    )
    manager.drain(session_uuid="missing")
    manager.drain(session_uuid="drain")
    check(
        condition="hello" in manager.output(session_uuid="drain"),
        reason="drained output",
    )
    monkeypatch.setattr(target=conpty_module, name="os_name", value="nt")

    class _MissingKernel:
        """Reject missing native symbols."""

    def missing_kernel(**kwargs: object) -> _MissingKernel:
        """Return a kernel object without native symbols.

        Args:
            **kwargs: Library options.

        Returns:
            Empty kernel object.

        """
        del kwargs
        return _MissingKernel()

    def kernel_factory() -> Callable[..., _MissingKernel]:
        """Return the missing-kernel library factory.

        Returns:
            Kernel factory.

        """
        return missing_kernel

    monkeypatch.setattr(
        target=conpty_module,
        name="win_dll_factory",
        value=kernel_factory,
    )
    expect(
        actual=conpty_module.WindowsConPty.capabilities().available,
        expected=False,
    )


def test_cli_subscribe_failure_and_empty_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise daemon subscription startup, empty, and connection failures.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    started: list[bool] = []

    def fake_start(*, home: Path) -> bool:
        """Record daemon startup.

        Args:
            home: Home.

        Returns:
            True.

        """
        del home
        started.append(True)
        return True

    monkeypatch.setattr(
        target=cli_module.DistrictDaemon,
        name="ping",
        value=staticmethod(_ping_false),
    )
    monkeypatch.setattr(
        target=cli_module.DistrictDaemon,
        name="start",
        value=staticmethod(fake_start),
    )

    class _EmptySubscriber:
        """Return no event for one-shot subscription."""

        def __init__(self, *, home: Path) -> None:
            """Initialize subscriber.

            Args:
                home: Home path.

            """
            del home

        @staticmethod
        def receive(*, timeout: float) -> None:
            """Return no payload.

            Args:
                timeout: Receive timeout.

            Returns:
                No payload.

            """
            del timeout

        def close(self) -> None:
            """Accept closure."""

    monkeypatch.setattr(
        target=cli_module,
        name="EventSubscriber",
        value=_EmptySubscriber,
    )
    expect(actual=Cli.subscribe(home=tmp_path, once=True), expected=0)
    check(condition=bool(started), reason="daemon started")

    class _BrokenSubscriber:
        """Reject subscriber construction."""

        def __init__(self, *, home: Path) -> None:
            """Raise a connection error.

            Args:
                home: Home path.

            Raises:
                ConnectionError: Always.

            """
            del home
            raise ConnectionError

    monkeypatch.setattr(
        target=cli_module.DistrictDaemon,
        name="ping",
        value=staticmethod(_ping_true),
    )
    monkeypatch.setattr(
        target=cli_module,
        name="EventSubscriber",
        value=_BrokenSubscriber,
    )
    expect(actual=Cli.subscribe(home=tmp_path, once=True), expected=1)


def test_web_remaining_payload_kill_and_stale_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise remaining web payload, kill-action, and stale-server paths.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    repository: SessionRepository = SessionRepository(home=tmp_path)
    service: District = District(repository=repository)
    entry: SessionEntry = SessionEntry(
        id="abc123",
        command="district abc123",
        session="s",
        cwd=str(object=tmp_path),
    )
    repository.add(entry=entry)
    dashboard: WebDashboard = WebDashboard(service=service, home=tmp_path)
    payload: dict[str, JsonValue] = WebDashboard.session_payload(entry=entry)
    expect(actual=payload["id"], expected="abc123")

    class _KillService:
        """Provide one killable session."""

        def __init__(self) -> None:
            """Initialize service."""
            self.killed: bool = False

        @staticmethod
        def list_entries() -> tuple[SessionEntry, ...]:
            """Return one entry.

            Returns:
                Session entry.

            """
            return (entry,)

        def kill(self, *, session_id: str) -> None:
            """Record kill.

            Args:
                session_id: Session identifier.

            """
            self.killed = session_id == entry.id

    fake: _KillService = _KillService()
    kill_dashboard: WebDashboard = WebDashboard(
        service=cast(typ="District", val=fake),
        home=tmp_path / "kill",
    )
    result: dict[str, str] = asyncio_run(
        main=kill_dashboard.action(session_id="abc123", action="kill"),
    )
    expect(actual=result["status"], expected="killed")
    expect(actual=fake.killed, expected=True)
    marker: str = "tok"
    dashboard.database.set_web_server(
        pid=123,
        started_token=marker,
        host="127.0.0.1",
        port=8000,
        url="http://127.0.0.1:8000/",
    )

    def capture_fails(*, pid: int) -> NoReturn:
        """Reject process-identity capture.

        Args:
            pid: Process identifier.

        Returns:
            Never returns.

        Raises:
            OSError: Always.

        """
        del pid
        raise OSError

    monkeypatch.setattr(
        target=web_module.ProcessIdentity,
        name="capture",
        value=staticmethod(capture_fails),
    )
    expect(actual=dashboard.running_url(), expected=None)


def test_cli_daemon_dispatch_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise daemon start, stop, status, and passthrough dispatch.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Pytest monkeypatch fixture.

    """

    class _Daemon:
        """Provide deterministic daemon operations."""

        def __init__(self, *, home: Path) -> None:
            """Initialize daemon.

            Args:
                home: Home path.

            """
            del home

        @staticmethod
        def run() -> int:
            """Return daemon status.

            Returns:
                Exit status.

            """
            return 7

        @staticmethod
        def start(*, home: Path) -> bool:
            """Start daemon.

            Args:
                home: Home path.

            Returns:
                True.

            """
            del home
            return True

        @staticmethod
        def stop(*, home: Path) -> bool:
            """Stop daemon.

            Args:
                home: Home path.

            Returns:
                False.

            """
            del home
            return False

        @staticmethod
        def ping(*, home: Path) -> bool:
            """Report daemon liveness.

            Args:
                home: Home path.

            Returns:
                True.

            """
            del home
            return True

    monkeypatch.setattr(
        target=cli_module,
        name="DistrictDaemon",
        value=_Daemon,
    )
    expect(
        actual=Cli.early_daemon(args=("--daemon",), home=tmp_path),
        expected=7,
    )
    expect(
        actual=Cli.early_daemon(args=("--daemon-start",), home=tmp_path),
        expected=0,
    )
    expect(
        actual=Cli.early_daemon(args=("--daemon-stop",), home=tmp_path),
        expected=1,
    )
    expect(
        actual=Cli.early_daemon(args=("--daemon-status",), home=tmp_path),
        expected=0,
    )
    expect(
        actual=Cli.early_daemon(args=("other",), home=tmp_path),
        expected=None,
    )


def test_installer_copy_mode_launcher(tmp_path: Path) -> None:
    """Exercise copy-mode source installation and removal.

    Args:
        tmp_path: Scratch directory.

    """
    source_root: Path = tmp_path / "source"
    package: Path = source_root / "district"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(data='"""Fixture."""\n')
    layout: Layout = replace(
        _layout(home=tmp_path / "home", source_root=source_root),
        mode="copy",
    )
    result: str = installer_module.install_launcher(layout=layout)
    expect(actual=result, expected=str(object=layout.launcher))
    check(
        condition=(layout.lib_dir / "district" / "__init__.py").is_file(),
        reason="sources copied",
    )
    installer_module.remove_launcher(layout=layout)
    expect(
        actual=(layout.lib_dir / "district").exists(),
        expected=False,
    )


def test_conpty_spawn_success_with_fake_kernel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the complete native ConPTY creation path without Windows.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Pytest monkeypatch fixture.

    """

    class _Function:
        """Provide a ctypes-compatible mutable callable."""

        def __init__(self, *, callback: Callable[..., int]) -> None:
            """Initialize the function.

            Args:
                callback: Call implementation.

            """
            self.callback: Callable[..., int] = callback
            self.argtypes: object = None
            self.restype: object = None

        def __call__(self, *args: object) -> int:
            """Invoke the callback.

            Args:
                *args: Native arguments.

            Returns:
                Native result.

            """
            return self.callback(*args)

    class _ProcessInfo(Structure):
        """Mirror the native process-information output structure."""

        _fields_: ClassVar[Incomplete] = (
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        )

    handles: list[int] = [11, 12, 13, 14]

    def create_pipe(*args: object) -> int:
        """Assign fake pipe handles.

        Args:
            *args: Native arguments.

        Returns:
            Success.

        """
        ctypes_cast(
            obj=cast(typ="int", val=args[0]),
            typ=POINTER(wintypes.HANDLE),
        ).contents.value = handles.pop(0)
        ctypes_cast(
            obj=cast(typ="int", val=args[1]),
            typ=POINTER(wintypes.HANDLE),
        ).contents.value = handles.pop(0)
        return 1

    def create_pseudo(*args: object) -> int:
        """Assign a fake pseudo-console handle.

        Args:
            *args: Native arguments.

        Returns:
            Success HRESULT.

        """
        ctypes_cast(
            obj=cast(typ="int", val=args[4]),
            typ=POINTER(wintypes.HANDLE),
        ).contents.value = 99
        return 0

    def initialize(*args: object) -> int:
        """Size or initialize the attribute list.

        Args:
            *args: Native arguments.

        Returns:
            Success.

        """
        ctypes_cast(
            obj=cast(typ="int", val=args[3]),
            typ=POINTER(c_size_t),
        ).contents.value = 64
        return 1

    def create_process(*args: object) -> int:
        """Populate fake process information.

        Args:
            *args: Native arguments.

        Returns:
            Success.

        """
        info: _ProcessInfo = ctypes_cast(
            obj=cast(typ="int", val=args[9]),
            typ=POINTER(_ProcessInfo),
        ).contents
        info.hProcess = 77
        info.hThread = 78
        info.dwProcessId = 4321
        return 1

    class _Kernel:
        """Provide typed fake kernel32 functions."""

        def __init__(self) -> None:
            """Initialize fake native functions."""
            self.CreatePipe: _Function = _Function(callback=create_pipe)
            self.CloseHandle: _Function = _Function(callback=_one)
            self.CreatePseudoConsole: _Function = _Function(
                callback=create_pseudo,
            )
            self.InitializeProcThreadAttributeList: _Function = _Function(
                callback=initialize,
            )
            self.UpdateProcThreadAttribute: _Function = _Function(
                callback=_one,
            )
            self.DeleteProcThreadAttributeList: _Function = _Function(
                callback=_zero,
            )
            self.ClosePseudoConsole: _Function = _Function(
                callback=_zero,
            )
            self.CreateProcessW: _Function = _Function(callback=create_process)

    kernel: _Kernel = _Kernel()
    monkeypatch.setattr(
        target=conpty_module.WindowsConPty,
        name="capabilities",
        value=staticmethod(
            lambda: conpty_module.ConPtyCapabilities(available=True),
        ),
    )
    monkeypatch.setattr(
        target=conpty_module.WindowsConPty,
        name="kernel32",
        value=staticmethod(lambda: kernel),
    )
    session: conpty_module.WindowsConPty = conpty_module.WindowsConPty.spawn(
        command=("cmd", "/c", "echo"),
        cwd=str(object=tmp_path),
        columns=80,
        rows=24,
    )
    expect(actual=session.pid, expected=4321)


def test_cli_session_name_uses_claude_code_session_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Print Claude Code's native session id as the agent session name.

    Args:
        tmp_path: Temporary home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    monkeypatch.setenv(name="DISTRICT_SESSION_ID", value="abc123")
    monkeypatch.setenv(name="CLAUDE_CODE_SESSION_ID", value="cc-9")
    printed: StringIO = StringIO()
    monkeypatch.setattr(target=cli_module, name="stdout", value=printed)
    service: _NamedService = _named_service()
    service.entries = (
        SessionEntry(id="abc123", command="district abc123", session="s1"),
    )
    cli: Cli = Cli(service=cast(typ="District", val=service))
    expect(actual=cli.run(arguments=("--session-name",)), expected=0)
    expect(actual=printed.getvalue(), expected="cc-9\n")
