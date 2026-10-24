# Copyright (c) 2026 Project contributors.
"""Tests for the charter backends."""

from __future__ import annotations

from io import BytesIO
from json import dumps
from pathlib import Path
from secrets import token_hex
from tarfile import TarFile, TarInfo
from tarfile import open as open_tar
from typing import final

import pytest

from tools.charter import backends
from tools.charter.backends import install_ngrok, open_charter, start_ngrok
from tools.charter.errors import CharterError
from tools.charter.models import Charter, CharterConstants
from tools.charter.settings import Settings

from .fakes import (
    Answers,
    Completed,
    FakeProcess,
    FakeRun,
    OnPath,
    Spawned,
    patch_tools,
)
from .helpers import check, expect, present


@final
class _SettingsFactory:
    """Build settings with small, fast timeouts for tests."""

    @staticmethod
    def fast() -> Settings:
        """Return settings with short timeouts.

        Returns:
            Settings whose waits resolve immediately.

        """
        return Settings(
            url_timeout_seconds=0.5,
            ngrok_timeout_seconds=0.5,
            stop_timeout_seconds=0.1,
        )


@pytest.mark.parametrize(
    ("backend", "tools", "needle", "url"),
    [
        ("tunnl", {"ssh"}, "proxy.tunnl.gg", "https://abc.tunnl.gg"),
        ("runlocal", {"npx"}, "runlocal", "https://abc.runlocal.eu"),
        (
            "pinggy",
            {"ssh", "expect"},
            "free.pinggy.io",
            "https://abc.free.pinggy.net",
        ),
    ],
)
def test_log_discovered_backends(
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    tools: set[str],
    needle: str,
    url: str,
) -> None:
    """Each backend starts the right command and reads its URL from the log.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        backend: Backend name under test.
        tools: Executables present on the fake PATH.
        needle: Text the spawned command must contain.
        url: URL the fake backend prints.

    """
    patch_tools(monkeypatch=monkeypatch, tools=tools)
    spawned: Spawned = Spawned(log_text=f"noise {url}/ more")
    monkeypatch.setattr(target=backends, name="Popen", value=spawned)
    running: Charter = present(
        value=open_charter(
            backend=backend,
            port=8765,
            settings=_SettingsFactory.fast(),
        ),
    )
    expect(actual=running.public_url, expected=url)
    check(
        condition=needle in " ".join(spawned.commands[0]),
        reason=str(object=spawned.commands),
    )
    log: Path = present(value=running.log_path)
    running.close()
    check(condition=spawned.process.terminated, reason="terminated")
    check(condition=not log.exists(), reason="log removed")


def test_the_configured_backend_is_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting ``backend`` falls back to ``settings.backend``.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    patch_tools(monkeypatch=monkeypatch, tools={"ssh"})
    spawned: Spawned = Spawned(log_text="https://xyz.tunnl.gg")
    monkeypatch.setattr(target=backends, name="Popen", value=spawned)
    running: Charter = present(
        value=open_charter(
            port=8765,
            settings=Settings(
                backend="tunnl",
                url_timeout_seconds=0.5,
            ),
        ),
    )
    expect(actual=running.public_url, expected="https://xyz.tunnl.gg")
    running.close()


def test_direct_needs_no_charter() -> None:
    """The ``direct`` backend exposes nothing."""
    expect(
        actual=open_charter(
            backend="direct",
            port=1,
            settings=Settings(),
        ),
        expected=None,
    )


def test_unknown_backend_is_an_error() -> None:
    """Only the documented backends are accepted."""
    with pytest.raises(expected_exception=CharterError, match="Unknown"):
        _ = open_charter(
            backend="carrier-pigeon",
            port=1,
            settings=Settings(),
        )


@pytest.mark.parametrize(
    ("backend", "tools"),
    [
        ("pinggy", {"ssh"}),
        ("pinggy", {"expect"}),
        ("tunnl", set[str]()),
        ("runlocal", set[str]()),
    ],
)
def test_backends_require_their_tools(
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    tools: set[str],
) -> None:
    """A missing executable is reported before anything is spawned.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        backend: Backend name under test.
        tools: Executables present on the fake PATH.

    """
    patch_tools(monkeypatch=monkeypatch, tools=tools)
    spawned: Spawned = Spawned()
    monkeypatch.setattr(target=backends, name="Popen", value=spawned)
    with pytest.raises(expected_exception=CharterError, match="requires"):
        _ = open_charter(
            backend=backend,
            port=1,
            settings=Settings(),
        )
    expect(actual=spawned.commands, expected=[])


def test_a_backend_that_never_prints_a_url_is_cleaned_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the process dies without a URL, it is reported as an error.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    patch_tools(monkeypatch=monkeypatch, tools={"ssh"})
    spawned: Spawned = Spawned(log_text="nothing useful", exits=True)
    monkeypatch.setattr(target=backends, name="Popen", value=spawned)
    with pytest.raises(expected_exception=CharterError, match="HTTPS URL"):
        _ = open_charter(
            backend="tunnl",
            port=1,
            settings=_SettingsFactory.fast(),
        )


def test_close_kills_a_stubborn_process() -> None:
    """A process that ignores terminate is killed."""
    process: FakeProcess = FakeProcess(stubborn=True)
    Charter(
        process=process.as_popen(),
        public_url="https://x",
        stop_timeout_seconds=0.01,
    ).close()
    check(condition=process.killed, reason="killed after the timeout")


def test_a_charter_is_a_context_manager() -> None:
    """Leaving a ``with`` block stops the backend."""
    process: FakeProcess = FakeProcess()
    charter: Charter = Charter(
        process=process.as_popen(),
        public_url="https://x",
        stop_timeout_seconds=0.01,
    )
    with charter as running:
        expect(actual=running, expected=charter)
    check(condition=process.terminated, reason="stopped on exit")


def test_ngrok_reads_the_https_endpoint_from_its_local_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Start ngrok with the token and poll its API for the URL.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    patch_tools(monkeypatch=monkeypatch, tools={"ngrok"})
    spawned: Spawned = Spawned()
    answers: Answers = Answers(
        answers=[
            OSError("not up yet"),
            b"[]",
            dumps(
                obj={
                    "tunnels": [
                        {"public_url": "http://plain"},
                        {"public_url": "https://abc.ngrok.app"},
                    ],
                },
            ).encode(),
        ],
    )
    monkeypatch.setattr(target=backends, name="Popen", value=spawned)
    monkeypatch.setattr(
        target=backends,
        name="http_request",
        value=answers,
    )
    token: str = token_hex(nbytes=8)
    charter: Charter = start_ngrok(
        port=9000,
        settings=_settings(token=token),
    )
    expect(actual=charter.public_url, expected="https://abc.ngrok.app")
    expect(
        actual=spawned.environments[0]["NGROK_AUTHTOKEN"],
        expected=token,
    )
    check(condition="9000" in spawned.commands[0], reason="port passed")


def test_ngrok_failure_explains_the_account_requirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no charter appears the error says how to authenticate.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    patch_tools(monkeypatch=monkeypatch, tools={"ngrok"})
    spawned: Spawned = Spawned(exits=True)
    monkeypatch.setattr(target=backends, name="Popen", value=spawned)
    with pytest.raises(expected_exception=CharterError, match="authtoken"):
        _ = start_ngrok(port=1, settings=_SettingsFactory.fast())


@pytest.mark.parametrize(
    ("system_name", "tool"),
    [("Darwin", "brew"), ("Windows", "winget")],
)
def test_ngrok_is_installed_with_the_platform_package_manager(
    monkeypatch: pytest.MonkeyPatch,
    system_name: str,
    tool: str,
) -> None:
    """Use Homebrew on macOS and winget on Windows.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        system_name: Reported operating system.
        tool: Package manager on the fake PATH.

    """
    installed: dict[str, bool] = {"done": False}
    runner: FakeRun = FakeRun()

    def which(cmd: str) -> str | None:
        """Find the package manager, and ngrok once it was installed.

        Args:
            cmd: Executable name.

        Returns:
            A fake path, or ``None``.

        """
        if cmd == "ngrok":
            return "/bin/ngrok" if installed["done"] else None
        return f"/bin/{cmd}" if cmd == tool else None

    def run(*, args: tuple[str, ...], check: bool) -> Completed:
        """Record the install and mark ngrok as installed.

        Args:
            args: Command line.
            check: Ignored.

        Returns:
            A successful completion.

        """
        installed["done"] = True
        return runner(args=args, check=check)

    monkeypatch.setattr(target=backends, name="which", value=which)
    monkeypatch.setattr(
        target=backends,
        name="system",
        value=lambda: system_name,
    )
    monkeypatch.setattr(target=backends, name="run", value=run)
    expect(
        actual=install_ngrok(settings=Settings()),
        expected=Path("/bin/ngrok"),
    )
    expect(actual=runner.commands[0][0], expected=tool)


@pytest.mark.parametrize(
    ("system_name", "has_brew", "returncode", "message"),
    [
        ("Darwin", False, 0, "no supported automatic installer"),
        ("Darwin", True, 1, "installation failed"),
        ("Plan9", False, 0, "no supported automatic installer"),
    ],
)
def test_ngrok_install_failures_are_reported(
    *,
    monkeypatch: pytest.MonkeyPatch,
    system_name: str,
    has_brew: bool,
    returncode: int,
    message: str,
) -> None:
    """Missing installers and failed installs are explained.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        system_name: Reported operating system.
        has_brew: Whether Homebrew exists.
        returncode: Exit status of the install command.
        message: Text the error must contain.

    """
    patch_tools(
        monkeypatch=monkeypatch,
        tools={"brew"} if has_brew else set(),
    )
    monkeypatch.setattr(
        target=backends,
        name="system",
        value=lambda: system_name,
    )
    monkeypatch.setattr(
        target=backends,
        name="run",
        value=FakeRun(returncode=returncode),
    )
    with pytest.raises(expected_exception=CharterError, match=message):
        _ = install_ngrok(settings=Settings())


def test_ngrok_executable_can_be_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured ngrok path is resolved instead of ``ngrok``.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    on_path: OnPath = patch_tools(
        monkeypatch=monkeypatch,
        tools={"/opt/ngrok/bin/ngrok"},
    )
    executable: Path = install_ngrok(
        settings=Settings(ngrok_executable="/opt/ngrok/bin/ngrok"),
    )
    expect(actual=on_path.seen[0], expected="/opt/ngrok/bin/ngrok")
    expect(actual=executable, expected=Path("/opt/ngrok/bin/ngrok"))


def _tarball() -> bytes:
    """Build a tarball holding an ``ngrok`` file.

    Returns:
        The gzip tar bytes.

    """
    buffer: BytesIO = BytesIO()
    archive: TarFile
    with open_tar(fileobj=buffer, mode="w:gz") as archive:
        info: TarInfo = TarInfo(name="ngrok")
        info.size = 3
        archive.addfile(tarinfo=info, fileobj=BytesIO(initial_bytes=b"bin"))
    return buffer.getvalue()


def test_linux_ngrok_is_downloaded_for_the_architecture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The official tarball for this CPU is unpacked into the cache.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory.

    """
    patch_tools(monkeypatch=monkeypatch, tools=set())
    answers: Answers = Answers(answers=[_tarball()])
    monkeypatch.setattr(
        target=backends,
        name="system",
        value=lambda: "Linux",
    )
    monkeypatch.setattr(
        target=backends,
        name="machine",
        value=lambda: "aarch64",
    )
    monkeypatch.setattr(
        target=backends,
        name="http_request",
        value=answers,
    )
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    monkeypatch.setenv(name="USERPROFILE", value=str(object=tmp_path))
    installed: Path = install_ngrok(settings=Settings())
    expect(actual=installed.read_bytes(), expected=b"bin")
    check(condition=answers.urls[0].endswith("linux-arm64.tgz"), reason="arch")
    check(
        condition=bool(installed.stat().st_mode & 0o100),
        reason="executable",
    )


def test_linux_ngrok_rejects_unknown_architectures_and_bad_downloads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An unsupported CPU or a failed download is a charter error.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory.

    """
    patch_tools(monkeypatch=monkeypatch, tools=set())
    monkeypatch.setattr(
        target=backends,
        name="system",
        value=lambda: "Linux",
    )
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    monkeypatch.setenv(name="USERPROFILE", value=str(object=tmp_path))
    monkeypatch.setattr(
        target=backends,
        name="machine",
        value=lambda: "riscv64",
    )
    with pytest.raises(expected_exception=CharterError, match="riscv64"):
        _ = install_ngrok(settings=Settings())
    monkeypatch.setattr(
        target=backends,
        name="machine",
        value=lambda: "x86_64",
    )
    monkeypatch.setattr(
        target=backends,
        name="http_request",
        value=Answers(answers=[OSError("offline")]),
    )
    with pytest.raises(expected_exception=CharterError, match="download"):
        _ = install_ngrok(settings=Settings())


def test_existing_ngrok_is_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ngrok already on PATH is never reinstalled.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    patch_tools(monkeypatch=monkeypatch, tools={"ngrok"})
    expect(
        actual=install_ngrok(settings=Settings()),
        expected=Path("/bin/ngrok"),
    )


def test_ngrok_url_requires_a_charter_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed ngrok API payloads never produce a URL.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    patch_tools(monkeypatch=monkeypatch, tools={"ngrok"})
    spawned: Spawned = Spawned()
    monkeypatch.setattr(target=backends, name="Popen", value=spawned)
    answers: Answers = Answers(
        answers=[
            dumps(obj={"tunnels": {"bad": True}}).encode(),
            dumps(obj={"tunnels": ["not-a-dict"]}).encode(),
            dumps(
                obj={
                    "tunnels": [
                        {"public_url": "https://good.ngrok.app"},
                    ],
                },
            ).encode(),
        ],
    )
    monkeypatch.setattr(
        target=backends,
        name="http_request",
        value=answers,
    )
    charter: Charter = start_ngrok(
        port=1,
        settings=_SettingsFactory.fast(),
    )
    expect(actual=charter.public_url, expected="https://good.ngrok.app")


def _settings(*, token: str) -> Settings:
    """Build fast settings carrying an ngrok authtoken.

    Args:
        token: ngrok authtoken.

    Returns:
        Settings for the tests.

    """
    return Settings(
        ngrok_authtoken=token,
        url_timeout_seconds=0.5,
        ngrok_timeout_seconds=0.5,
        stop_timeout_seconds=0.1,
    )


def test_backend_names_match_the_constants() -> None:
    """The public constants list every implemented backend."""
    expect(
        actual=CharterConstants.backends,
        expected=frozenset(
            {"direct", "ngrok", "pinggy", "tunnl", "runlocal"},
        ),
    )
