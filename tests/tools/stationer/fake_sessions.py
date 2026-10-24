# Copyright (c) 2026 Project contributors.
"""Recording stand-ins for the ``conjure`` runners the builders use."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol, final

from tools.conjure import CommandResult, RemoteCommandError
from tools.stationer import sessions

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest

    from tools.conjure import (
        DockerConfig,
        GuestConfig,
        QemuConfig,
        SshConfig,
    )
    from tools.stationer.builder import (
        LinuxConfig,
        PosixGuestConfig,
        WindowsConfig,
    )
    from tools.stationer.sessions import JobSession, StreamingSession


class Handler(Protocol):
    """Fabricate the side effects and output of a faked command."""

    def __call__(self, *, arguments: tuple[str, ...]) -> str | None:
        """Handle one command.

        Args:
            arguments: The command vector being faked.

        Returns:
            Standard output for the command, if any.

        """
        ...


@final
class FakeSession:
    """Record what a builder does in a session and fabricate its outputs.

    Attributes:
        events: Ordered operation names (``start``, ``run``, ``upload``,
            ``download``, ``close``).
        commands: Every command vector passed to ``run``.
        files: Uploaded files by destination, read when uploaded because the
            builders delete their scratch directories afterwards.
        trees: File names of uploaded directories by destination.
        downloads: ``(source, destination)`` pairs requested.

    """

    def __init__(
        self,
        *,
        fail_on: str | None = None,
        handler: Handler | None = None,
        commands: list[tuple[str, ...]] | None = None,
    ) -> None:
        """Create the fake.

        Args:
            fail_on: Text that makes any command containing it exit with 1.
            handler: Fabricates each command's side effects and output.
            commands: List shared with other sessions to record commands in.

        """
        self.events: Final[list[str]] = []
        self.commands: Final[list[tuple[str, ...]]] = (
            commands if commands is not None else []
        )
        self._handler: Final[Handler | None] = handler
        self.files: Final[dict[str, bytes]] = {}
        self.trees: Final[dict[str, set[str]]] = {}
        self.downloads: Final[list[tuple[str, Path]]] = []
        self._fail_on: Final[str | None] = fail_on

    def start(self) -> None:
        """Record the activation."""
        self.events.append("start")

    def run(
        self,
        *,
        command: tuple[str, ...],
        check: bool = False,
        timeout_seconds: float | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> CommandResult:
        """Record a command and optionally fail it.

        Args:
            command: Command vector.
            check: Whether to raise on a non-zero exit status.
            timeout_seconds: Ignored.
            on_output: Receives each line of the faked output.

        Returns:
            A successful result, or the failing one when not ``check``.

        Raises:
            RemoteCommandError: When ``fail_on`` matches and ``check`` is set.

        """
        _ = timeout_seconds
        self.events.append("run")
        self.commands.append(command)
        failing: bool = self._fail_on is not None and any(
            self._fail_on in part for part in command
        )
        output: str | None = (
            None
            if failing or self._handler is None
            else self._handler(arguments=command)
        )
        result: CommandResult = CommandResult(
            command=command,
            return_code=1 if failing else 0,
            stdout="out text" if self._handler is None else output or "",
            stderr="boom" if failing else "",
        )
        if on_output is not None:
            for line in result.stdout.splitlines(keepends=True):
                on_output(line)
        if failing and check:
            raise RemoteCommandError(result=result)
        return result

    def upload(
        self,
        *,
        source: Path,
        destination: str,
        recursive: bool | None = None,
    ) -> None:
        """Capture what was uploaded.

        Args:
            source: Host file or directory.
            destination: Destination inside the session.
            recursive: Ignored.

        """
        _ = recursive
        self.events.append("upload")
        if source.is_dir():
            self.trees[destination] = {
                path.relative_to(source).as_posix()
                for path in source.rglob(pattern="*")
                if path.is_file()
            }
        else:
            self.files[destination] = source.read_bytes()

    def download(
        self,
        *,
        source: str,
        destination: Path,
        recursive: bool = False,
    ) -> None:
        """Fabricate the downloaded artifact.

        Args:
            source: Path inside the session.
            destination: Host destination.
            recursive: Ignored.

        """
        _ = recursive
        self.events.append("download")
        self.downloads.append((source, destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            data="2.36\n" if source.endswith("glibc") else f"fake {source}\n",
            encoding="utf-8",
        )

    def close(self) -> None:
        """Record the release."""
        self.events.append("close")

    def as_session(self) -> StreamingSession:
        """View the fake as a builder session.

        Returns:
            This object typed as ``StreamingSession``.

        """
        return self


@final
class Opener:
    """Hand out one fake session per build and remember them."""

    def __init__(
        self,
        *,
        fail_on: str | None = None,
        handler: Handler | None = None,
    ) -> None:
        """Create the opener.

        Args:
            fail_on: Text that makes commands in every session fail.
            handler: Fabricates each command's side effects and output.

        """
        self.created: Final[list[FakeSession]] = []
        self.configs: Final[
            list[LinuxConfig | PosixGuestConfig | WindowsConfig]
        ] = []
        self._fail_on: Final[str | None] = fail_on
        self._handler: Final[Handler | None] = handler

    def __call__(
        self,
        *,
        config: LinuxConfig | PosixGuestConfig | WindowsConfig,
    ) -> StreamingSession:
        """Create and record a fake session.

        Args:
            config: The configuration the builder passed.

        Returns:
            The new session.

        """
        session: FakeSession = FakeSession(
            fail_on=self._fail_on,
            handler=self._handler,
        )
        self.created.append(session)
        self.configs.append(config)
        return session.as_session()


@final
class Hosts:
    """Hand out host sessions that record every command they run.

    Attributes:
        commands: Every command vector, across all sessions, in order.
        cwds: Working directory each session was opened with.
        created: The sessions handed out.

    """

    def __init__(
        self,
        *,
        handler: Handler | None = None,
        fail_on: str | None = None,
    ) -> None:
        """Create the opener.

        Args:
            handler: Fabricates each command's side effects and output.
            fail_on: Text that makes commands containing it fail.

        """
        self.commands: Final[list[tuple[str, ...]]] = []
        self.cwds: Final[list[Path | None]] = []
        self.created: Final[list[FakeSession]] = []
        self._handler: Final[Handler | None] = handler
        self._fail_on: Final[str | None] = fail_on

    def __call__(self, *, cwd: Path | None = None) -> JobSession:
        """Create and record a host session.

        Args:
            cwd: Working directory the session was opened with.

        Returns:
            The new session.

        """
        session: FakeSession = FakeSession(
            fail_on=self._fail_on,
            handler=self._handler,
            commands=self.commands,
        )
        self.cwds.append(cwd)
        self.created.append(session)
        return session.as_session()


@final
class RunnerFactory:
    """Stand in for ``DockerRunner`` and ``AttachedVm`` constructors."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        """Create the factory.

        Args:
            fail_on: Text that makes commands in every session fail.

        """
        self.created: Final[list[FakeSession]] = []
        self.docker: Final[list[DockerConfig]] = []
        self.ssh: Final[list[SshConfig]] = []
        self.qemu: Final[list[QemuConfig]] = []
        self.guest: Final[list[GuestConfig]] = []
        self._fail_on: Final[str | None] = fail_on

    def __call__(
        self,
        *,
        config: DockerConfig | None = None,
        ssh: SshConfig | None = None,
        qemu: QemuConfig | None = None,
        guest: GuestConfig | None = None,
    ) -> FakeSession:
        """Create a fake session and record the runner configuration.

        Args:
            config: Docker configuration, for ``DockerRunner``.
            ssh: SSH configuration, for ``AttachedVm`` and ``QemuVm``.
            qemu: QEMU configuration, for ``QemuVm``.
            guest: Guest configuration, for ``QemuVm``.

        Returns:
            The new session.

        """
        if config is not None:
            self.docker.append(config)
        if ssh is not None:
            self.ssh.append(ssh)
        if qemu is not None:
            self.qemu.append(qemu)
        if guest is not None:
            self.guest.append(guest)
        session: FakeSession = FakeSession(fail_on=self._fail_on)
        self.created.append(session)
        return session


def install_fake_runners(
    *,
    monkeypatch: pytest.MonkeyPatch,
    fail_on: str | None = None,
) -> RunnerFactory:
    """Replace the production runner classes with fakes for one test.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        fail_on: Text that makes commands in every session fail.

    Returns:
        The factory recording every session and configuration.

    """
    factory: RunnerFactory = RunnerFactory(fail_on=fail_on)
    for name in ("DockerRunner", "AttachedVm", "QemuVm"):
        monkeypatch.setattr(target=sessions, name=name, value=factory)
    return factory
