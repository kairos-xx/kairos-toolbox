"""Adapt recording fakes to the ``open_host`` session boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol, final

if TYPE_CHECKING:
    from pathlib import Path

    from tools.conjure import CommandResult
    from tools.stationer.runner import HostSession


class Answers(Protocol):
    """Describe a fake that answers commands for a working directory."""

    def run(self, *, command: tuple[str, ...], cwd: Path) -> CommandResult:
        """Answer one command.

        Args:
            command: Command arguments.
            cwd: Working directory for the command.

        Returns:
            The canned command output.

        """
        ...


@final
class FakeHostSession:
    """Run commands against an :class:`Answers` fake."""

    def __init__(self, *, answers: Answers, cwd: Path) -> None:
        """Bind the fake to one working directory.

        Args:
            answers: The fake that answers commands.
            cwd: Working directory the session was opened with.

        """
        self._answers: Final[Answers] = answers
        self._cwd: Final[Path] = cwd

    def start(self) -> None:
        """Activate the session (nothing to do)."""

    def run(
        self,
        *,
        command: tuple[str, ...],
        check: bool = False,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        """Delegate to the fake.

        Args:
            command: Command arguments.
            check: Ignored; fakes raise their own failures.
            timeout_seconds: Ignored.

        Returns:
            The fake's answer.

        """
        _ = (check, timeout_seconds)
        return self._answers.run(command=command, cwd=self._cwd)

    def close(self) -> None:
        """Release the session (nothing to do)."""


@final
class Opening:
    """Open :class:`FakeHostSession` objects over one fake."""

    def __init__(self, *, answers: Answers) -> None:
        """Remember the fake.

        Args:
            answers: The fake that answers commands.

        """
        self._answers: Final[Answers] = answers

    def __call__(self, *, cwd: Path) -> HostSession:
        """Open a session for ``cwd``.

        Args:
            cwd: Working directory for the session's commands.

        Returns:
            The session.

        """
        return FakeHostSession(answers=self._answers, cwd=cwd)
