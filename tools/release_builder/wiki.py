# Copyright (c) 2026 Project contributors.
"""Synchronize a local ``wiki/`` directory with the repository's GitHub Wiki.

A GitHub Wiki is a plain git remote at ``<repository>.wiki.git``: clone it,
replace its contents with the local directory, commit and push.
"""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from pathlib import Path
from shutil import copy2, copytree, rmtree
from sys import stderr as sys_stderr
from sys import stdout as sys_stdout
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Final, cast
from urllib.parse import ParseResult, urlparse

from tools.release_builder.release_builder import BuildError
from tools.release_publish.runner import SubprocessRunner

if TYPE_CHECKING:
    from tools.release_publish.runner import CommandResult, Runner


class WikiConstants:
    """Group wiki-publisher constants.

    Attributes:
        commit_message: Default Wiki commit message.
        git_directory: Git metadata directory name.

    """

    commit_message: Final[str] = "docs: update GitHub Wiki"
    git_directory: Final[str] = ".git"


class WikiPublishError(BuildError):
    """Raised when publishing the Wiki fails."""


class WikiPublisher:
    """Synchronize a local directory with a GitHub Wiki repository."""

    def __init__(
        self,
        *,
        repository: str,
        wiki_directory: Path,
        runner: Runner | None = None,
        commit_message: str = WikiConstants.commit_message,
        dry_run: bool = False,
    ) -> None:
        """Initialize the Wiki publisher.

        Args:
            repository: GitHub repository URL or ``owner/repository``.
            wiki_directory: Directory containing the Wiki files.
            runner: Command executor; ``SubprocessRunner`` by default.
            commit_message: Git commit message used for Wiki updates.
            dry_run: Report changes without committing or pushing.

        """
        self._repository: Final[str] = repository
        self._wiki_directory: Final[Path] = wiki_directory.resolve()
        self._runner: Final[Runner] = runner or SubprocessRunner()
        self._commit_message: Final[str] = commit_message
        self._dry_run: Final[bool] = dry_run

    @classmethod
    def wiki_repository_url(cls, *, repository: str) -> str:
        """Return the GitHub Wiki Git URL.

        Args:
            repository: Repository URL or ``owner/repository``.

        Returns:
            Wiki Git URL.

        """
        normalized: str = cls._normalize_repository(repository=repository)
        return f"{normalized[:-4]}.wiki.git"

    def publish(self) -> bool:
        """Synchronize and optionally publish the Wiki.

        Returns:
            ``True`` when changes were detected, ``False`` otherwise.

        Raises:
            WikiPublishError: If the source directory does not exist or a
                Git command fails.

        """
        if not self._wiki_directory.is_dir():
            msg: str = f"Wiki directory does not exist: {self._wiki_directory}"
            raise WikiPublishError(msg)
        wiki_url: str = self.wiki_repository_url(
            repository=self._repository,
        )
        with TemporaryDirectory(prefix="github-wiki-") as temporary:
            work: Path = Path(temporary)
            checkout: Path = work / "wiki"
            _ = self._run(
                command=("git", "clone", wiki_url, str(object=checkout)),
                cwd=work,
            )
            self._clear_checkout(checkout=checkout)
            self._copy_wiki(checkout=checkout)
            if not self._has_changes(checkout=checkout):
                _ = sys_stdout.write("GitHub Wiki is already up to date.\n")
                return False
            self._show_changes(checkout=checkout)
            if self._dry_run:
                _ = sys_stdout.write(
                    "Dry run: Wiki changes were not committed or pushed.\n",
                )
                return True
            _ = self._run(command=("git", "add", "--all"), cwd=checkout)
            _ = self._run(
                command=("git", "commit", "-m", self._commit_message),
                cwd=checkout,
            )
            _ = self._run(
                command=("git", "push", "origin", "HEAD"),
                cwd=checkout,
            )
        _ = sys_stdout.write("GitHub Wiki published successfully.\n")
        return True

    @staticmethod
    def _normalize_repository(*, repository: str) -> str:
        """Normalize a repository into a Git clone URL.

        Args:
            repository: Repository URL or ``owner/repository``.

        Returns:
            Normal Git repository URL.

        Raises:
            WikiPublishError: If the repository value is invalid.

        """
        value: str = repository.strip().removesuffix("/")
        if value.endswith(".git"):
            value = value.removesuffix(".git")
        if value.startswith("git@github.com:"):
            return f"{value}.git"
        if value.startswith(("https://github.com/", "http://github.com/")):
            parsed: ParseResult = urlparse(url=value)
            path: str = parsed.path.strip("/")
            if path.count("/") != 1:
                msg: str = f"Invalid GitHub repository URL: {repository}"
                raise WikiPublishError(msg)
            return f"https://github.com/{path}.git"
        if value.count("/") == 1 and "://" not in value:
            return f"https://github.com/{value}.git"
        msg = "Repository must be 'owner/repository' or a GitHub clone URL."
        raise WikiPublishError(msg)

    @staticmethod
    def _clear_checkout(*, checkout: Path) -> None:
        """Remove existing Wiki files while preserving Git metadata.

        Args:
            checkout: Wiki checkout directory.

        """
        for path in checkout.iterdir():
            if path.name == WikiConstants.git_directory:
                continue
            if path.is_dir():
                rmtree(path=path)
            else:
                path.unlink()

    def _run(
        self,
        *,
        command: tuple[str, ...],
        cwd: Path,
    ) -> CommandResult:
        """Execute one command through the injected runner.

        Args:
            command: Command and arguments.
            cwd: Command working directory.

        Returns:
            Completed command output.

        Raises:
            WikiPublishError: If the command fails.

        """
        try:
            return self._runner.run(command=command, cwd=cwd)
        except RuntimeError as error:
            raise WikiPublishError(str(object=error)) from error

    def _copy_wiki(self, *, checkout: Path) -> None:
        """Copy the local Wiki into the checkout.

        Args:
            checkout: Wiki checkout directory.

        """
        for source in self._wiki_directory.iterdir():
            destination: Path = checkout / source.name
            if source.is_dir():
                _ = copytree(src=source, dst=destination)
            else:
                _ = copy2(src=source, dst=destination)

    def _has_changes(self, *, checkout: Path) -> bool:
        """Return whether the Wiki checkout contains changes.

        Args:
            checkout: Wiki checkout directory.

        Returns:
            Whether tracked or untracked changes exist.

        """
        result: CommandResult = self._run(
            command=("git", "status", "--porcelain"),
            cwd=checkout,
        )
        return bool(result.stdout.strip())

    def _show_changes(self, *, checkout: Path) -> None:
        """Print the pending Wiki changes.

        Args:
            checkout: Wiki checkout directory.

        """
        for command in (
            ("git", "status", "--short"),
            ("git", "diff", "--stat"),
        ):
            result: CommandResult = self._run(command=command, cwd=checkout)
            _ = sys_stdout.write(result.stdout)


class WikiNamespace(Namespace):
    """Parsed ``wiki`` CLI arguments.

    Attributes:
        repository: GitHub repository URL or ``owner/repository``.
        wiki: Local Wiki directory.
        message: Wiki Git commit message.
        dry_run: Report changes without pushing.

    """

    repository: str
    wiki: Path
    message: str
    dry_run: bool


def _parser() -> ArgumentParser:
    """Build the argument parser.

    Returns:
        The configured parser.

    """
    parser: ArgumentParser = ArgumentParser(
        description=(
            "Synchronize a local wiki directory with a GitHub repository Wiki."
        ),
    )
    parser.add_argument(
        "repository",
        help="GitHub repository as owner/repository or a Git clone URL.",
    )
    parser.add_argument(
        "--wiki",
        type=Path,
        default=Path("wiki"),
        help="Local Wiki directory. Default: ./wiki",
    )
    parser.add_argument(
        "--message",
        default=WikiConstants.commit_message,
        help="Wiki Git commit message.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show changes without committing or pushing.",
    )
    return parser


def main(*, arguments: tuple[str, ...] | None = None) -> int:
    """Publish a local Wiki directory to a GitHub repository Wiki.

    Args:
        arguments: Optional explicit CLI arguments.

    Returns:
        Process exit status.

    """
    parser: ArgumentParser = _parser()
    namespace: WikiNamespace = cast(
        typ="WikiNamespace",
        val=parser.parse_args(
            args=None if arguments is None else list(arguments),
        ),
    )
    try:
        _ = WikiPublisher(
            repository=namespace.repository,
            wiki_directory=namespace.wiki,
            commit_message=namespace.message,
            dry_run=namespace.dry_run,
        ).publish()
    except WikiPublishError as error:
        _ = sys_stderr.write(f"error: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
