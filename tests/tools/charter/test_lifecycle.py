# Copyright (c) 2026 Project contributors.
"""Tests for charter lifetime: keep alive, expire, stop on command."""

from __future__ import annotations

from io import StringIO
from subprocess import DEVNULL, Popen
from time import sleep, time
from typing import TYPE_CHECKING, Final, final

import pytest

from tools.charter import backends, cli, lifecycle
from tools.charter.cli import main
from tools.charter.errors import CharterError
from tools.charter.lifecycle import (
    CharterRecord,
    CharterRegistry,
    alive,
    end_process,
    manage,
    schedule_expiry,
)
from tools.charter.models import Charter
from tools.charter.settings import Settings, SettingsLoader

from .fakes import FakeProcess, Spawned, patch_tools
from .helpers import check, expect, present

if TYPE_CHECKING:
    from pathlib import Path


@final
class Constants:
    """Group module-level constants.

    Attributes:
        sleeper: Grouped module constant.

    """

    sleeper: Final[tuple[str, ...]] = ("sleep", "60")


def _sleeper(*, detached: bool = True) -> Popen[bytes]:
    """Start a real long-lived child process.

    Args:
        detached: Whether it gets its own session, like a kept-alive charter.

    Returns:
        The running process.

    """
    return Popen(
        args=Constants.sleeper,
        stdin=DEVNULL,
        stdout=DEVNULL,
        stderr=DEVNULL,
        start_new_session=detached,
    )


def _gone(*, process: Popen[bytes]) -> bool:
    """Wait briefly for a process to be reaped.

    Args:
        process: The child to wait for.

    Returns:
        Whether it ended within a couple of seconds.

    """
    try:
        _ = process.wait(timeout=3)
    except TimeoutError:
        return False
    return True


def _record(
    *,
    pid: int,
    tmp_path: Path,
    identifier: str = "",
) -> CharterRecord:
    """Create a registry record.

    Args:
        pid: Process id the record points at.
        tmp_path: Directory for the log path.
        identifier: Registry key; the PID when empty.

    Returns:
        The record.

    """
    return CharterRecord(
        identifier=identifier or str(object=pid),
        pid=pid,
        backend="ngrok",
        port=8080,
        public_url="https://x.example",
        started_at=time(),
        expires_at=None,
        log_path=str(object=tmp_path / "t.log"),
    )


def test_keep_alive_is_refused_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows cannot probe processes safely, so keep-alive is refused.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setattr(target=lifecycle, name="os_name", value="nt")
    with pytest.raises(expected_exception=CharterError, match="POSIX"):
        backends.open_charter(
            backend="ngrok",
            port=1,
            settings=Settings(keep_alive=True),
        )


def test_a_scoped_charter_ends_on_close() -> None:
    """The default lifetime ends with ``close``."""
    process: FakeProcess = FakeProcess()
    Charter(process=process.as_popen(), public_url="https://x").close()
    check(condition=process.terminated, reason="scoped tunnels end on close")


def test_a_kept_alive_charter_survives_close_until_stopped() -> None:
    """``close`` leaves a keep-alive charter running; ``stop`` ends it."""
    process: Popen[bytes] = _sleeper()
    charter: Charter = Charter(
        process=process,
        public_url="https://x",
        keep_alive=True,
    )
    try:
        charter.close()
        expect(actual=process.poll(), expected=None)
        charter.stop()
        check(condition=_gone(process=process), reason="stop must end it")
    finally:
        if process.poll() is None:
            process.kill()
            _ = process.wait()


def test_registry_lists_stops_and_forgets(tmp_path: Path) -> None:
    """Registered tunnels can be listed and stopped on command.

    Args:
        tmp_path: Pytest temporary directory.

    """
    registry: CharterRegistry = CharterRegistry(path=tmp_path / "r" / "t.json")
    process: Popen[bytes] = _sleeper()
    try:
        registry.add(record=_record(pid=process.pid, tmp_path=tmp_path))
        records: tuple[CharterRecord, ...] = registry.records()
        expect(actual=[r.pid for r in records], expected=[process.pid])
        stopped: CharterRecord = registry.stop(
            identifier=str(object=process.pid),
            timeout_seconds=2,
        )
        expect(actual=stopped.pid, expected=process.pid)
        check(condition=_gone(process=process), reason="process ended")
        expect(actual=registry.records(), expected=())
    finally:
        if process.poll() is None:
            process.kill()
            _ = process.wait()
    with pytest.raises(expected_exception=CharterError, match="No kept-alive"):
        _ = registry.stop(identifier="404", timeout_seconds=1)


def test_registry_forgets_dead_charters_and_tolerates_junk(
    tmp_path: Path,
) -> None:
    """Records of ended processes are pruned; a broken file reads as empty.

    Args:
        tmp_path: Pytest temporary directory.

    """
    process: Popen[bytes] = _sleeper()
    path: Path = tmp_path / "t.json"
    registry: CharterRegistry = CharterRegistry(path=path)
    expect(actual=registry.path, expected=path)
    registry.add(record=_record(pid=process.pid, tmp_path=tmp_path))
    process.kill()
    _ = process.wait()
    expect(actual=registry.records(), expected=())
    path.write_text(data="{not json", encoding="utf-8")
    expect(actual=registry.records(), expected=())


def test_stop_pid_ends_unregistered_processes_too(tmp_path: Path) -> None:
    """The expiry watchdog works whether or not the charter is registered.

    Args:
        tmp_path: Pytest temporary directory.

    """
    registry: CharterRegistry = CharterRegistry(path=tmp_path / "t.json")
    loose: Popen[bytes] = _sleeper()
    registry.stop_pid(pid=loose.pid, timeout_seconds=2)
    check(condition=_gone(process=loose), reason="unregistered pid ended")
    known: Popen[bytes] = _sleeper()
    registry.add(record=_record(pid=known.pid, tmp_path=tmp_path))
    registry.stop_pid(pid=known.pid, timeout_seconds=2)
    check(condition=_gone(process=known), reason="registered pid ended")
    expect(actual=registry.records(), expected=())


def test_end_process_escalates_and_ignores_missing_pids() -> None:
    """A process that ignores ``SIGTERM`` is killed; a gone pid is fine."""
    stubborn: Popen[bytes] = Popen(
        args=(
            "sh",
            "-c",
            "trap '' TERM; echo ready; while :; do sleep 1; done",
        ),
        stdout=-1,
    )
    try:
        _ = present(value=stubborn.stdout).readline()
        end_process(pid=stubborn.pid, timeout_seconds=0.2)
        check(condition=_gone(process=stubborn), reason="killed")
    finally:
        if stubborn.poll() is None:
            stubborn.kill()
            _ = stubborn.wait()
    end_process(pid=stubborn.pid, timeout_seconds=0.1)
    expect(actual=alive(pid=stubborn.pid), expected=False)
    expect(actual=alive(pid=1), expected=True)


@final
class Watchdogs:
    """Capture the detached watchdog instead of spawning it."""

    def __init__(self) -> None:
        """Create the recorder."""
        self.commands: list[tuple[str, ...]] = []

    def __call__(
        self,
        *,
        args: tuple[str, ...],
        start_new_session: bool,
        **ignored: Path | int,
    ) -> Popen[bytes]:
        """Record the command line.

        Args:
            args: Watchdog command line.
            start_new_session: Whether the watchdog is detached.
            **ignored: Working directory and streams, which are not needed.

        Returns:
            A real, already finished process, as ``Popen`` would.

        """
        _ = ignored
        self.commands.append(args)
        check(condition=start_new_session, reason="watchdog is detached")
        return Popen(args=("true",))


def test_manage_registers_and_arms_a_watchdog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A kept-alive charter with a TTL is registered and gets a watchdog.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    watchdogs: Watchdogs = Watchdogs()
    monkeypatch.setattr(target=lifecycle, name="Popen", value=watchdogs)
    process: Popen[bytes] = _sleeper()
    charter: Charter = Charter(
        process=process,
        public_url="https://x",
        keep_alive=True,
    )
    path: Path = tmp_path / "t.json"
    try:
        manage(
            charter=charter,
            backend="pinggy",
            port=9,
            settings=Settings(
                keep_alive=True,
                ttl_seconds=30,
                registry_path=str(object=path),
            ),
        )
        record: CharterRecord = CharterRegistry(path=path).records()[0]
        expect(
            actual=(record.backend, record.port, record.log_path),
            expected=("pinggy", 9, None),
        )
        check(
            condition=present(value=record.expires_at) > record.started_at,
            reason="expiry follows the start",
        )
        expect(
            actual=watchdogs.commands[0][3:],
            expected=(
                "expire",
                "--pid",
                str(object=process.pid),
                "--seconds",
                "30",
            ),
        )
    finally:
        charter.stop()
        _ = process.wait()


def test_manage_leaves_plain_charters_alone(tmp_path: Path) -> None:
    """Without keep-alive or a TTL nothing is registered or scheduled.

    Args:
        tmp_path: Pytest temporary directory.

    """
    path: Path = tmp_path / "t.json"
    manage(
        charter=Charter(
            process=FakeProcess().as_popen(),
            public_url="https://x",
        ),
        backend="ngrok",
        port=1,
        settings=Settings(registry_path=str(object=path)),
    )
    check(condition=not path.exists(), reason="nothing registered")


def test_a_scoped_ttl_stops_the_charter() -> None:
    """A TTL ends a scoped charter through a daemon timer."""
    process: FakeProcess = FakeProcess()
    schedule_expiry(
        charter=Charter(process=process.as_popen(), public_url="https://x"),
        ttl_seconds=0.05,
    )
    for _ in range(40):
        if process.terminated:
            break
        sleep(0.05)
    check(condition=process.terminated, reason="the timer stopped it")


def test_backends_detach_only_kept_alive_charters(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The backend process gets its own session only with ``keep_alive``.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory.

    """
    patch_tools(monkeypatch=monkeypatch, tools={"npx"})
    spawned: Spawned = Spawned(log_text="https://abc.runlocal.eu")
    monkeypatch.setattr(target=backends, name="Popen", value=spawned)
    base: Settings = Settings(url_timeout_seconds=1, stop_timeout_seconds=1)
    scoped: Charter = present(
        value=backends.open_charter(backend="runlocal", port=1, settings=base),
    )
    scoped.close()
    kept: Charter = present(
        value=backends.open_charter(
            backend="runlocal",
            port=1,
            settings=Settings(
                url_timeout_seconds=1,
                stop_timeout_seconds=1,
                keep_alive=True,
                registry_path=str(object=tmp_path / "t.json"),
            ),
        ),
    )
    expect(actual=spawned.sessions, expected=[False, True])
    expect(actual=(scoped.keep_alive, kept.keep_alive), expected=(False, True))


def test_settings_accept_lifetime_keys(tmp_path: Path) -> None:
    """``keep_alive``, ``ttl_seconds`` and ``registry_path`` are settings.

    Args:
        tmp_path: Pytest temporary directory.

    """
    project: Path = tmp_path / "pyproject.toml"
    registry: Path = tmp_path / "x.json"
    project.write_text(
        data=(
            "[tool.charter]\nkeep_alive = true\nttl_seconds = 90\n"
            f'registry_path = "{registry}"\n'
        ),
        encoding="utf-8",
    )
    loaded: Settings = SettingsLoader.load(pyproject=project)
    expect(
        actual=(loaded.keep_alive, loaded.ttl_seconds, loaded.registry_path),
        expected=(True, 90.0, str(object=tmp_path / "x.json")),
    )
    expect(
        actual=SettingsLoader.load(pyproject=tmp_path / "none").ttl_seconds,
        expected=None,
    )
    for body, error in (
        ('ttl_seconds = "soon"', TypeError),
        ("ttl_seconds = true", TypeError),
        ("ttl_seconds = 0", ValueError),
    ):
        project.write_text(data=f"[tool.charter]\n{body}\n", encoding="utf-8")
        with pytest.raises(expected_exception=error):
            _ = SettingsLoader.load(pyproject=project)


def _cli(
    *,
    monkeypatch: pytest.MonkeyPatch,
    arguments: tuple[str, ...],
) -> tuple[int, str, str]:
    """Run the CLI with captured streams.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        arguments: Command-line arguments.

    Returns:
        Exit status, standard output and standard error.

    """
    out: StringIO = StringIO()
    err: StringIO = StringIO()
    monkeypatch.setattr(target=cli, name="sys_stdout", value=out)
    monkeypatch.setattr(target=cli, name="sys_stderr", value=err)
    status: int = main(arguments=arguments)
    return (status, out.getvalue(), err.getvalue())


def test_cli_lists_and_stops_kept_alive_charters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``list`` and ``stop`` work on the registry.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    path: Path = tmp_path / "t.json"
    project: Path = tmp_path / "pyproject.toml"
    project.write_text(
        data=f'[tool.charter]\nregistry_path = "{path}"\n',
        encoding="utf-8",
    )
    base: tuple[str, ...] = ("--pyproject", str(object=project))
    process: Popen[bytes] = _sleeper()
    other: Popen[bytes] = _sleeper()
    try:
        registry: CharterRegistry = CharterRegistry(path=path)
        registry.add(record=_record(pid=process.pid, tmp_path=tmp_path))
        registry.add(record=_record(pid=other.pid, tmp_path=tmp_path))
        status, out, _ = _cli(
            monkeypatch=monkeypatch,
            arguments=(*base, "list"),
        )
        expect(actual=status, expected=0)
        check(condition=str(object=process.pid) in out, reason=out)
        status, out, _ = _cli(
            monkeypatch=monkeypatch,
            arguments=(*base, "stop", str(object=process.pid)),
        )
        check(condition=out.startswith("stopped "), reason=out)
        check(condition=_gone(process=process), reason="stopped by id")
        status, out, _ = _cli(
            monkeypatch=monkeypatch,
            arguments=(*base, "stop", "--all"),
        )
        check(condition=_gone(process=other), reason="stopped by --all")
        status, _, err = _cli(
            monkeypatch=monkeypatch,
            arguments=(*base, "stop"),
        )
        expect(actual=status, expected=1)
        check(condition="--all" in err, reason=err)
        status, _, err = _cli(
            monkeypatch=monkeypatch,
            arguments=(*base, "stop", "404"),
        )
        expect(actual=status, expected=1)
    finally:
        for child in (process, other):
            if child.poll() is None:
                child.kill()
                _ = child.wait()


def test_cli_expire_stops_the_process_after_the_delay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The watchdog command ends the process it was given.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    project: Path = tmp_path / "pyproject.toml"
    project.write_text(
        data=f"""[tool.charter]\nregistry_path = "{tmp_path / "t.json"}"\n""",
        encoding="utf-8",
    )
    process: Popen[bytes] = _sleeper()
    try:
        status, _, _ = _cli(
            monkeypatch=monkeypatch,
            arguments=(
                "--pyproject",
                str(object=project),
                "expire",
                "--pid",
                str(object=process.pid),
                "--seconds",
                "0.05",
            ),
        )
        expect(actual=status, expected=0)
        check(condition=_gone(process=process), reason="expired")
    finally:
        if process.poll() is None:
            process.kill()
            _ = process.wait()


def test_cli_expose_background_detaches_and_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--background`` keeps the charter and returns; ``--ttl`` is applied.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    seen: list[Settings] = []

    def fake_open(
        *,
        port: int,
        backend: str | None = None,
        settings: Settings | None = None,
    ) -> Charter:
        """Record the settings and return a fake charter.

        Args:
            port: Ignored port.
            backend: Ignored backend.
            settings: Resolved settings under test.

        Returns:
            A fake charter.

        """
        _ = (port, backend)
        seen.append(present(value=settings))
        return Charter(
            process=FakeProcess(interrupt=True).as_popen(),
            public_url="https://bg.example",
            keep_alive=True,
        )

    monkeypatch.setattr(target=cli, name="open_charter", value=fake_open)
    status, out, _ = _cli(
        monkeypatch=monkeypatch,
        arguments=("expose", "--port", "1", "--background", "--ttl", "5"),
    )
    expect(actual=status, expected=0)
    check(condition="https://bg.example" in out, reason=out)
    expect(
        actual=(seen[0].keep_alive, seen[0].ttl_seconds),
        expected=(True, 5.0),
    )
