# Copyright (c) 2026 Project contributors.
"""Shared fakes for the charter backend tests."""

from __future__ import annotations

from dataclasses import dataclass
from io import BufferedWriter
from subprocess import TimeoutExpired
from typing import TYPE_CHECKING, Final, cast, final

from tools.charter import backends

if TYPE_CHECKING:
    from subprocess import Popen

    import pytest


@final
class FakeProcess:
    """Stand in for a running child process."""

    def __init__(
        self,
        *,
        exits: bool = False,
        stubborn: bool = False,
        interrupt: bool = False,
    ) -> None:
        """Create the fake.

        Args:
            exits: Whether the process reports it already exited.
            stubborn: Whether it ignores a polite stop request.
            interrupt: Whether ``wait`` raises ``KeyboardInterrupt``.

        """
        self.exits: Final[bool] = exits
        self.stubborn: Final[bool] = stubborn
        self.interrupt: Final[bool] = interrupt
        self.terminated: bool = False
        self.killed: bool = False
        self.waited: bool = False
        self.pid: int = 4242

    def poll(self) -> int | None:
        """Report the exit status.

        Returns:
            ``0`` when exited, otherwise ``None``.

        """
        return 0 if self.exits and not self.terminated else None

    def terminate(self) -> None:
        """Record a polite stop request."""
        self.terminated = True

    def kill(self) -> None:
        """Record a forced stop."""
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        """Wait for exit, optionally timing out.

        Args:
            timeout: Seconds to wait (unused).

        Returns:
            The exit status.

        Raises:
            TimeoutExpired: When the process is configured to resist.
            KeyboardInterrupt: When configured to interrupt the wait.

        """
        _ = timeout
        if self.interrupt and not self.waited:
            self.waited = True
            raise KeyboardInterrupt
        self.waited = True
        if self.stubborn and not self.killed:
            raise TimeoutExpired(cmd="fake", timeout=1)
        return 0

    def as_popen(self) -> Popen[bytes]:
        """View the fake as a real process handle.

        Returns:
            This object typed as ``Popen``.

        """
        return cast(typ="Popen[bytes]", val=self)


@final
class Spawned:
    """Record spawned commands and let the log carry a public URL."""

    def __init__(self, *, log_text: str = "", exits: bool = False) -> None:
        """Create the recorder.

        Args:
            log_text: Text the fake backend writes to its log file.
            exits: Whether the fake process exits immediately.

        """
        self.log_text: Final[str] = log_text
        self.commands: list[list[str]] = []
        self.sessions: list[bool] = []
        self.environments: list[dict[str, str]] = []
        self.process: FakeProcess = FakeProcess(exits=exits)

    def __call__(
        self,
        *,
        args: list[str],
        stdout: BufferedWriter | int | None = None,
        env: dict[str, str] | None = None,
        start_new_session: bool = False,
        **ignored: BufferedWriter | int | None,
    ) -> Popen[bytes]:
        """Pretend to start a process.

        Args:
            args: Command line.
            stdout: Log stream the fake writes to, when a file.
            env: Environment the process would receive.
            start_new_session: Whether the process gets its own session.
            **ignored: Standard input and error, which the fake ignores.

        Returns:
            The fake process.

        """
        _ = ignored
        self.sessions.append(start_new_session)
        self.commands.append(list(args))
        self.environments.append(dict(env or {}))
        if isinstance(stdout, BufferedWriter):
            _ = stdout.write(self.log_text.encode())
            stdout.flush()
        return self.process.as_popen()


@final
class OnPath:
    """A ``which`` that finds only the named tools."""

    def __init__(self, *, tools: set[str]) -> None:
        """Create the lookup.

        Args:
            tools: Executable names present on the fake PATH.

        """
        self.tools: Final[set[str]] = tools
        self.seen: list[str] = []

    def __call__(self, cmd: str) -> str | None:
        """Resolve one executable name.

        Args:
            cmd: Executable name.

        Returns:
            A fake absolute path, or ``None`` when absent.

        """
        self.seen.append(cmd)
        if cmd not in self.tools:
            return None
        return cmd if cmd.startswith("/") else f"/bin/{cmd}"


@final
class NoSleep:
    """A ``sleep`` that returns immediately."""

    def __call__(self, seconds: float) -> None:
        """Skip the wait.

        Args:
            seconds: Ignored duration.

        """
        _ = seconds


@final
class Answers:
    """Scripted replacement for ``http_request``."""

    def __init__(self, *, answers: list[bytes | OSError]) -> None:
        """Create the script.

        Args:
            answers: Responses (or errors to raise) in call order; the last
                one repeats.

        """
        self.answers: Final[list[bytes | OSError]] = answers
        self.urls: list[str] = []
        self.bodies: list[bytes | None] = []

    def __call__(
        self,
        *,
        url: str,
        timeout: float,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        """Answer one request.

        Args:
            url: Requested URL.
            timeout: Ignored timeout.
            data: Request body.
            headers: Ignored headers.

        Returns:
            The scripted body.

        Raises:
            OSError: When the scripted answer is an error.

        """
        _ = (timeout, headers)
        self.urls.append(url)
        self.bodies.append(data)
        answer: bytes | OSError = (
            self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        )
        if isinstance(answer, OSError):
            raise answer
        return answer


@dataclass(frozen=True, slots=True, kw_only=True)
class Completed:
    """The result of a fake ``run``.

    Attributes:
        returncode: Exit status.

    """

    returncode: int


@final
class FakeRun:
    """A ``run`` that records commands and reports a fixed status."""

    def __init__(self, *, returncode: int = 0) -> None:
        """Create the fake.

        Args:
            returncode: Exit status to report.

        """
        self.returncode: Final[int] = returncode
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, *, args: tuple[str, ...], check: bool) -> Completed:
        """Record one command.

        Args:
            args: Command line.
            check: Ignored.

        Returns:
            A completed process with the configured status.

        """
        _ = check
        self.commands.append(args)
        return Completed(returncode=self.returncode)


def patch_tools(*, monkeypatch: pytest.MonkeyPatch, tools: set[str]) -> OnPath:
    """Make ``which`` find only the named tools and skip real sleeping.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tools: Executable names present on the fake PATH.

    Returns:
        The installed lookup, for inspecting resolved names.

    """
    on_path: OnPath = OnPath(tools=tools)
    monkeypatch.setattr(target=backends, name="which", value=on_path)
    monkeypatch.setattr(target=backends, name="sleep", value=NoSleep())
    return on_path
