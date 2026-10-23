"""Git repository adapter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.release_publish.runner import CommandResult, Runner


@dataclass(frozen=True, slots=True, kw_only=True)
class Git:
    """Typed Git operations used by the planner.

    Attributes:
        root: Repository root directory.
        runner: Command execution boundary.

    """

    root: Path
    runner: Runner

    def status_porcelain(self) -> str:
        """Return machine-readable working-tree status.

        Returns:
            The ``git status --porcelain=v1`` output.

        """
        return self.runner.run(
            command=("git", "status", "--porcelain=v1"),
            cwd=self.root,
        ).stdout

    def changed_paths(self, *, since: str) -> tuple[Path, ...]:
        """Return paths changed after a ref.

        Args:
            since: Starting ref for the diff.

        Returns:
            Changed paths relative to the repository root.

        """
        output: str = self.runner.run(
            command=("git", "diff", "--name-only", f"{since}..HEAD"),
            cwd=self.root,
        ).stdout
        return tuple(Path(line) for line in output.splitlines() if line)

    def tag_exists(self, *, tag: str) -> bool:
        """Return whether a local tag exists.

        Args:
            tag: Tag name to look up.

        Returns:
            Whether the tag exists locally.

        """
        result: CommandResult = self.runner.run(
            command=("git", "tag", "--list", tag),
            cwd=self.root,
        )
        return tag in result.stdout.splitlines()

    def tags(self, *, pattern: str) -> tuple[str, ...]:
        """Return all tags matching a glob pattern.

        Args:
            pattern: Tag glob pattern.

        Returns:
            Matching tag names.

        """
        output: str = self.runner.run(
            command=("git", "tag", "--list", pattern),
            cwd=self.root,
        ).stdout
        return tuple(line for line in output.splitlines() if line)
