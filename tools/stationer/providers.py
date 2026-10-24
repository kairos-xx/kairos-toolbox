"""Repository and package-registry adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from tools.stationer.models import Action, ActionKind, ReleaseDetails

if TYPE_CHECKING:
    from pathlib import Path

    from tools.stationer.config import RepositoryConfig


class RepositoryProvider(Protocol):
    """Remote repository provider planning boundary."""

    def sync_repository(
        self,
        *,
        config: RepositoryConfig,
        root: Path,
    ) -> tuple[Action, ...]:
        """Plan actions that create or update the remote repository.

        Args:
            config: Remote repository settings.
            root: Local repository root.

        Returns:
            Actions implementing the repository synchronisation.

        """
        ...

    def release(
        self,
        *,
        repository: RepositoryConfig,
        details: ReleaseDetails,
        artifacts: tuple[Path, ...],
        root: Path,
    ) -> tuple[Action, ...]:
        """Plan actions that publish a release on the provider.

        Args:
            repository: Remote repository settings.
            details: Tag, title, and notes for the release.
            artifacts: Files to upload to the release.
            root: Working directory for the release command.

        Returns:
            Actions implementing the release.

        """
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class GitHubProvider:
    """GitHub adapter implemented with the official gh CLI."""

    @staticmethod
    def sync_repository(
        *,
        config: RepositoryConfig,
        root: Path,
    ) -> tuple[Action, ...]:
        """Plan actions that create or update the GitHub repository.

        Args:
            config: Remote repository settings.
            root: Local repository root.

        Returns:
            Actions implementing the repository synchronisation.

        """
        slug: str = f"{config.owner}/{config.name}"
        create: Action = Action(
            kind=ActionKind.COMMAND,
            description=f"Ensure GitHub repository {slug} exists",
            command=(
                "gh",
                "repo",
                "create",
                slug,
                f"--{config.visibility}",
                "--source",
                str(object=root),
                "--remote",
                "origin",
                "--description",
                config.description,
            ),
        )
        edit: Action = Action(
            kind=ActionKind.COMMAND,
            description=f"Synchronize GitHub repository {slug}",
            command=(
                "gh",
                "repo",
                "edit",
                slug,
                "--default-branch",
                config.default_branch,
                "--description",
                config.description,
                "--visibility",
                config.visibility,
                "--accept-visibility-change-consequences",
            ),
        )
        return (create, edit)

    @staticmethod
    def upload(
        *,
        repository: RepositoryConfig,
        tag: str,
        artifacts: tuple[Path, ...],
        root: Path,
    ) -> tuple[Action, ...]:
        """Plan actions that attach files to an existing GitHub release.

        Files with the same name are replaced.

        Args:
            repository: Remote repository settings.
            tag: Tag of the existing release.
            artifacts: Files to upload.
            root: Working directory for the upload command.

        Returns:
            Actions implementing the upload.

        """
        slug: str = f"{repository.owner}/{repository.name}"
        return (
            Action(
                kind=ActionKind.COMMAND,
                description=f"Upload assets to GitHub release {tag}",
                command=(
                    "gh",
                    "release",
                    "upload",
                    tag,
                    "--repo",
                    slug,
                    "--clobber",
                    *(str(object=path) for path in artifacts),
                ),
                cwd=root,
            ),
        )

    @staticmethod
    def release(
        *,
        repository: RepositoryConfig,
        details: ReleaseDetails,
        artifacts: tuple[Path, ...],
        root: Path,
    ) -> tuple[Action, ...]:
        """Plan actions that create a GitHub release.

        Args:
            repository: Remote repository settings.
            details: Tag, title, and notes for the release.
            artifacts: Files to upload to the release.
            root: Working directory for the release command.

        Returns:
            Actions implementing the release.

        """
        slug: str = f"{repository.owner}/{repository.name}"
        command: tuple[str, ...] = (
            "gh",
            "release",
            "create",
            details.tag,
            "--repo",
            slug,
            "--title",
            details.title,
            *(
                ("--generate-notes",)
                if details.generate_notes
                else ("--notes", details.notes)
            ),
            *(
                ("--notes-start-tag", details.notes_start_tag)
                if details.notes_start_tag is not None
                else ()
            ),
            *(("--prerelease",) if details.prerelease else ()),
            *(str(object=path) for path in artifacts),
        )
        return (
            Action(
                kind=ActionKind.COMMAND,
                description=f"Create GitHub release {details.tag}",
                command=command,
                cwd=root,
            ),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class PyPiProvider:
    """PyPI-compatible registry adapter.

    Attributes:
        repository_url: Optional custom registry URL.

    """

    repository_url: str | None = None

    @staticmethod
    def build(*, project_root: Path) -> tuple[Action, ...]:
        """Plan the action that builds the Python distribution.

        Args:
            project_root: Project directory to build.

        Returns:
            Actions implementing the build.

        """
        return (
            Action(
                kind=ActionKind.COMMAND,
                description=f"Build Python distribution in {project_root}",
                command=("python", "-m", "build"),
                cwd=project_root,
            ),
        )

    def publish(self, *, project_root: Path) -> tuple[Action, ...]:
        """Plan the action that uploads the built distributions.

        Args:
            project_root: Project directory containing ``dist``.

        Returns:
            Actions implementing the upload.

        """
        command: tuple[str, ...] = (
            (
                "python",
                "-m",
                "tools.stationer.upload",
                str(object=project_root / "dist"),
            )
            if self.repository_url is None
            else (
                "python",
                "-m",
                "tools.stationer.upload",
                str(object=project_root / "dist"),
                "--repository-url",
                self.repository_url,
            )
        )
        return (
            Action(
                kind=ActionKind.COMMAND,
                description=(
                    f"Publish Python distribution from {project_root}"
                ),
                command=command,
                cwd=project_root,
            ),
        )
