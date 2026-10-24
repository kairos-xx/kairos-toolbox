"""Tests for the release providers' action planning."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tools.stationer.config import RepositoryConfig
from tools.stationer.models import ActionKind, ReleaseDetails
from tools.stationer.providers import GitHubProvider, PyPiProvider

from .helpers import check, expect

if TYPE_CHECKING:
    from pathlib import Path

    from tools.stationer.models import Action


def test_github_sync_plans_create_and_edit(tmp_path: Path) -> None:
    """Repository synchronisation creates then normalises the repo.

    Args:
        tmp_path: Pytest temporary directory.

    """
    config: RepositoryConfig = RepositoryConfig(
        provider="github",
        owner="octo",
        name="demo",
        default_branch="main",
        visibility="private",
        description="Demo repository",
    )
    actions: tuple[Action, ...] = GitHubProvider().sync_repository(
        config=config,
        root=tmp_path,
    )
    expect(actual=len(actions), expected=2)
    create, edit = actions
    expect(actual=create.kind, expected=ActionKind.command)
    expect(actual=create.command[:3], expected=("gh", "repo", "create"))
    check(condition="octo/demo" in create.command, reason=str(object=create))
    check(condition="--private" in create.command, reason=str(object=create))
    expect(actual=edit.command[:3], expected=("gh", "repo", "edit"))
    check(
        condition="--default-branch" in edit.command,
        reason=str(object=edit),
    )


def test_github_release_plans_gh_release_create(tmp_path: Path) -> None:
    """The release action carries tag, title, notes and artifacts.

    Args:
        tmp_path: Pytest temporary directory.

    """
    repository: RepositoryConfig = RepositoryConfig(
        provider="github",
        owner="octo",
        name="demo",
    )
    details: ReleaseDetails = ReleaseDetails(
        tag="demo/v1.2.3",
        title="demo 1.2.3",
        notes="notes",
    )
    actions: tuple[Action, ...] = GitHubProvider().release(
        repository=repository,
        details=details,
        artifacts=(tmp_path / "a.whl",),
        root=tmp_path,
    )
    expect(actual=len(actions), expected=1)
    command: tuple[str, ...] = actions[0].command
    expect(actual=command[:3], expected=("gh", "release", "create"))
    check(condition="demo/v1.2.3" in command, reason=str(object=command))
    check(condition="octo/demo" in command, reason=str(object=command))
    check(
        condition=str(object=tmp_path / "a.whl") in command,
        reason=str(object=command),
    )
    expect(actual=actions[0].cwd, expected=tmp_path)


def test_pypi_build_plans_python_build(tmp_path: Path) -> None:
    """Building runs ``python -m build`` inside the project.

    Args:
        tmp_path: Pytest temporary directory.

    """
    actions: tuple[Action, ...] = PyPiProvider.build(project_root=tmp_path)
    expect(actual=len(actions), expected=1)
    expect(
        actual=actions[0].command,
        expected=("python", "-m", "build"),
    )
    expect(actual=actions[0].cwd, expected=tmp_path)


def test_pypi_publish_defaults_to_pypi(tmp_path: Path) -> None:
    """Without a custom registry the upload helper gets no URL.

    Args:
        tmp_path: Pytest temporary directory.

    """
    actions: tuple[Action, ...] = PyPiProvider().publish(project_root=tmp_path)
    command: tuple[str, ...] = actions[0].command
    expect(
        actual=command[:3],
        expected=("python", "-m", "tools.stationer.upload"),
    )
    check(
        condition="--repository-url" not in command,
        reason=str(object=command),
    )
    expect(actual=command[3], expected=str(object=tmp_path / "dist"))


def test_pypi_publish_forwards_a_custom_repository(tmp_path: Path) -> None:
    """A custom registry URL is appended after the dist directory.

    Args:
        tmp_path: Pytest temporary directory.

    """
    provider: PyPiProvider = PyPiProvider(
        repository_url="https://example.test/pypi",
    )
    actions: tuple[Action, ...] = provider.publish(project_root=tmp_path)
    command: tuple[str, ...] = actions[0].command
    check(
        condition="--repository-url" in command,
        reason=str(object=command),
    )
    check(
        condition="https://example.test/pypi" in command,
        reason=str(object=command),
    )


def test_github_release_can_generate_notes_from_the_previous_tag(
    tmp_path: Path,
) -> None:
    """CI releases ask GitHub for notes since the previous release.

    Args:
        tmp_path: Pytest temporary directory.

    """
    repository: RepositoryConfig = RepositoryConfig(
        provider="github",
        owner="octo",
        name="demo",
    )
    details: ReleaseDetails = ReleaseDetails(
        tag="demo/v1.2.3",
        title="demo 1.2.3",
        notes="",
        generate_notes=True,
        notes_start_tag="demo/v1.2.2",
        prerelease=True,
    )
    command: tuple[str, ...] = (
        GitHubProvider()
        .release(
            repository=repository,
            details=details,
            artifacts=(),
            root=tmp_path,
        )[0]
        .command
    )
    check(condition="--generate-notes" in command, reason=str(object=command))
    check(condition="--prerelease" in command, reason=str(object=command))
    check(condition="--notes" not in command, reason=str(object=command))
    index: int = command.index("--notes-start-tag")
    expect(actual=command[index + 1], expected="demo/v1.2.2")
