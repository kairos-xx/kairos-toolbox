# Copyright (c) 2026 Project contributors.
"""Tests for the GitHub Wiki synchronizer."""

from __future__ import annotations

from importlib import import_module
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Final, final

import pytest

from tests.tools.stationer.fake_hosts import Opening
from tools.conjure import CommandResult
from tools.stationer.wiki import (
    WikiNamespace,
    WikiPublisher,
    WikiPublishError,
    main,
)

from .helpers import check, expect

if TYPE_CHECKING:
    from types import ModuleType


@final
class GitRunner:
    """Fabricate ``git`` results for the Wiki publisher.

    Attributes:
        commands: Recorded commands.
        dirty: Whether ``git status --porcelain`` reports changes.
        checkout_files: File names observed in the checkout at status time.

    """

    def __init__(self, *, dirty: bool = True) -> None:
        """Create the fake runner.

        Args:
            dirty: Whether the Wiki checkout has changes.

        """
        self.commands: list[tuple[str, ...]] = []
        self.dirty: Final[bool] = dirty
        self.checkout_files: tuple[str, ...] = ()

    def run(
        self,
        *,
        command: tuple[str, ...],
        cwd: Path,
    ) -> CommandResult:
        """Record the command and fabricate clone/status output.

        Args:
            command: Command arguments.
            cwd: Working directory.

        Returns:
            A successful result.

        """
        self.commands.append(command)
        if command[:2] == ("git", "clone"):
            checkout: Path = Path(command[-1])
            checkout.mkdir()
            _ = (checkout / ".git").mkdir()
            _ = (checkout / "Old.md").write_text(
                data="# old\n",
                encoding="utf-8",
            )
            stale: Path = checkout / "stale"
            stale.mkdir()
            _ = (stale / "x.md").write_text(data="x\n", encoding="utf-8")
        if command[1:] == ("status", "--porcelain"):
            self.checkout_files = tuple(
                sorted(p.name for p in cwd.iterdir()),
            )
        dirty: bool = self.dirty and command[1:] == ("status", "--porcelain")
        return CommandResult(
            command=command,
            return_code=0,
            stdout=" M Home.md\n" if dirty else "",
            stderr="",
        )


def _wiki(root: Path) -> Path:
    """Create a Wiki directory containing one page.

    Args:
        root: Directory that receives ``wiki/``.

    Returns:
        The Wiki directory.

    """
    wiki: Path = root / "wiki"
    wiki.mkdir()
    (wiki / "Home.md").write_text(data="# home\n", encoding="utf-8")
    return wiki


@pytest.mark.parametrize(
    ("repository", "expected"),
    [
        ("octo/demo", "https://github.com/octo/demo.wiki.git"),
        ("octo/demo/", "https://github.com/octo/demo.wiki.git"),
        (
            "https://github.com/octo/demo",
            "https://github.com/octo/demo.wiki.git",
        ),
        (
            "https://github.com/octo/demo.git",
            "https://github.com/octo/demo.wiki.git",
        ),
        ("git@github.com:octo/demo", "git@github.com:octo/demo.wiki.git"),
    ],
)
def test_repository_forms_become_wiki_urls(
    repository: str,
    expected: str,
) -> None:
    """Every accepted repository form maps to the ``.wiki.git`` URL.

    Args:
        repository: Repository spelling under test.
        expected: Expected Wiki clone URL.

    """
    expect(
        actual=WikiPublisher.wiki_repository_url(repository=repository),
        expected=expected,
    )


@pytest.mark.parametrize(
    "repository",
    ["https://github.com/a/b/c", "not-a-repo", "https://example.com/a/b"],
)
def test_invalid_repositories_raise(repository: str) -> None:
    """Unsupported repository spellings fail before any Git command.

    Args:
        repository: Invalid repository spelling.

    """
    with pytest.raises(expected_exception=WikiPublishError):
        _ = WikiPublisher.wiki_repository_url(repository=repository)


def test_publish_commits_and_pushes_changes(tmp_path: Path) -> None:
    """A dirty checkout is committed and pushed to ``origin HEAD``.

    Args:
        tmp_path: Pytest temporary directory.

    """
    runner: GitRunner = GitRunner()
    changed: bool = WikiPublisher(
        repository="octo/demo",
        wiki_directory=_wiki(root=tmp_path),
        open_host=Opening(answers=runner),
    ).publish()
    expect(actual=changed, expected=True)
    kinds: list[str] = [command[1] for command in runner.commands]
    expect(
        actual=kinds,
        expected=[
            "clone",
            "status",
            "status",
            "diff",
            "add",
            "commit",
            "push",
        ],
    )
    clone: tuple[str, ...] = runner.commands[0]
    expect(
        actual=clone[2],
        expected="https://github.com/octo/demo.wiki.git",
    )
    commit: tuple[str, ...] = runner.commands[5]
    check(
        condition="docs: update GitHub Wiki" in commit,
        reason=str(object=commit),
    )


def test_publish_reports_an_up_to_date_wiki(tmp_path: Path) -> None:
    """No Wiki changes means no commit and no push.

    Args:
        tmp_path: Pytest temporary directory.

    """
    runner: GitRunner = GitRunner(dirty=False)
    changed: bool = WikiPublisher(
        repository="octo/demo",
        wiki_directory=_wiki(root=tmp_path),
        open_host=Opening(answers=runner),
    ).publish()
    expect(actual=changed, expected=False)
    kinds: list[str] = [command[1] for command in runner.commands]
    expect(actual=kinds, expected=["clone", "status"])


def test_publish_dry_run_never_commits(tmp_path: Path) -> None:
    """``--dry-run`` stops after showing the pending changes.

    Args:
        tmp_path: Pytest temporary directory.

    """
    runner: GitRunner = GitRunner()
    changed: bool = WikiPublisher(
        repository="octo/demo",
        wiki_directory=_wiki(root=tmp_path),
        open_host=Opening(answers=runner),
        dry_run=True,
    ).publish()
    expect(actual=changed, expected=True)
    kinds: list[str] = [command[1] for command in runner.commands]
    expect(
        actual=kinds,
        expected=["clone", "status", "status", "diff"],
    )


def test_publish_requires_the_wiki_directory(tmp_path: Path) -> None:
    """A missing ``wiki/`` folder is an explicit error.

    Args:
        tmp_path: Pytest temporary directory.

    """
    with pytest.raises(expected_exception=WikiPublishError, match="wiki"):
        _ = WikiPublisher(
            repository="octo/demo",
            wiki_directory=tmp_path / "missing",
        ).publish()


def test_publish_replaces_files_but_keeps_git(tmp_path: Path) -> None:
    """Existing pages are replaced while ``.git`` metadata survives.

    The fake clone seeds ``Old.md`` and ``stale/``; at status time the
    checkout must contain only ``.git`` and the local Wiki pages.

    Args:
        tmp_path: Pytest temporary directory.

    """
    wiki: Path = _wiki(root=tmp_path)
    (wiki / "Extra.md").write_text(data="# extra\n", encoding="utf-8")
    runner: GitRunner = GitRunner()
    _ = WikiPublisher(
        repository="octo/demo",
        wiki_directory=wiki,
        open_host=Opening(answers=runner),
    ).publish()
    expect(
        actual=runner.checkout_files,
        expected=(".git", "Extra.md", "Home.md"),
    )


def test_command_failures_become_wiki_errors(tmp_path: Path) -> None:
    """A failed ``git`` command surfaces as ``WikiPublishError``.

    Args:
        tmp_path: Pytest temporary directory.

    """

    @final
    class FailingRunner:
        """Fail every command like a real runner would."""

        @staticmethod
        def run(
            *,
            command: tuple[str, ...],
            cwd: Path,
        ) -> CommandResult:
            """Raise like ``SubprocessRunner`` does on failure.

            Args:
                command: Command arguments.
                cwd: Working directory.

            Returns:
                Never returns.

            Raises:
                RuntimeError: Always.

            """
            _ = (command, cwd)
            msg: str = "Command failed (128): git clone"
            raise RuntimeError(msg)

    with pytest.raises(
        expected_exception=WikiPublishError,
        match="git clone",
    ):
        _ = WikiPublisher(
            repository="octo/demo",
            wiki_directory=_wiki(root=tmp_path),
            open_host=Opening(answers=FailingRunner()),
        ).publish()


def test_main_reports_failures_as_exit_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI errors print to stderr and return status 1.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture; ``sys_stderr`` is bound at
            import time, so the module attribute is replaced with a
            ``StringIO`` to observe the error message.

    """
    stream: StringIO = StringIO()
    module: ModuleType = import_module(name="tools.stationer.wiki")
    monkeypatch.setattr(
        target=module,
        name="sys_stderr",
        value=stream,
    )
    status: int = main(
        arguments=(
            "octo/demo",
            "--wiki",
            str(object=tmp_path / "missing"),
        ),
    )
    expect(actual=status, expected=1)
    check(
        condition="Wiki directory does not exist" in stream.getvalue(),
        reason="the error must name the problem",
    )


def test_main_publishes_through_the_publisher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI wires parsed arguments into ``WikiPublisher``.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    wiki: Path = _wiki(root=tmp_path)
    seen: list[tuple[str, Path, str, bool]] = []

    @final
    class Stub:
        """Stand-in publisher recording its constructor arguments."""

        def __init__(
            self,
            *,
            repository: str,
            wiki_directory: Path,
            commit_message: str,
            dry_run: bool,
        ) -> None:
            """Record the constructor arguments.

            Args:
                repository: Repository spelling.
                wiki_directory: Wiki directory.
                commit_message: Commit message.
                dry_run: Dry-run flag.

            """
            seen.append((repository, wiki_directory, commit_message, dry_run))

        @staticmethod
        def publish() -> bool:
            """Pretend changes were published.

            Returns:
                ``True``.

            """
            return True

    module: ModuleType = import_module(name="tools.stationer.wiki")
    monkeypatch.setattr(target=module, name="WikiPublisher", value=Stub)
    status: int = main(
        arguments=(
            "octo/demo",
            "--wiki",
            str(object=wiki),
            "--message",
            "docs: custom",
            "--dry-run",
        ),
    )
    expect(actual=status, expected=0)
    repository, directory, message, dry = seen[0]
    expect(actual=repository, expected="octo/demo")
    expect(actual=directory, expected=wiki.resolve())
    expect(actual=message, expected="docs: custom")
    expect(actual=dry, expected=True)


def test_namespace_declares_parsed_fields() -> None:
    """``WikiNamespace`` carries the four parsed attributes."""
    namespace: WikiNamespace = WikiNamespace(
        repository="octo/demo",
        wiki=Path("wiki"),
        message="m",
        dry_run=True,
    )
    expect(actual=namespace.repository, expected="octo/demo")
    expect(actual=namespace.dry_run, expected=True)
