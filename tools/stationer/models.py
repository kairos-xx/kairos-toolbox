"""Immutable domain models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final, final

if TYPE_CHECKING:
    from pathlib import Path


class Bump(StrEnum):
    """Supported semantic-version increments.

    Attributes:
        major: Increment the major component.
        minor: Increment the minor component.
        patch: Increment the patch component.

    """

    major = "major"
    minor = "minor"
    patch = "patch"


class ActionKind(StrEnum):
    """Kinds of release action.

    Attributes:
        command: Execute a subprocess command.
        write: Write file content.

    """

    command = "command"
    write = "write"


@dataclass(frozen=True, slots=True, kw_only=True)
class Project:
    """One independently versioned project.

    Attributes:
        name: Project name.
        path: Project directory relative to the repository root.
        version: Current semantic version text.
        version_files: Files whose version literal is rewritten.
        python_distribution: Whether the project builds a Python package.
        distribution_name: Distribution name, when it differs from ``name``.
        depends_on: Names of projects that must release first.
        artifacts: Files uploaded to the GitHub release.

    """

    name: str
    path: Path
    version: str
    version_files: tuple[Path, ...] = ()
    python_distribution: bool = False
    distribution_name: str | None = None
    depends_on: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Action:
    """One deterministic plan action.

    Attributes:
        kind: Whether the action runs a command or writes a file.
        description: Human-readable summary of the action.
        command: Command arguments for ``COMMAND`` actions.
        path: Target path for ``WRITE`` actions.
        content: File content for ``WRITE`` actions.
        cwd: Working directory for ``COMMAND`` actions.
        secret_indexes: Command positions that carry secret values.

    """

    kind: ActionKind
    description: str
    command: tuple[str, ...] = ()
    path: Path | None = None
    content: str | None = None
    cwd: Path | None = None
    secret_indexes: frozenset[int] = frozenset()


@dataclass(frozen=True, slots=True, kw_only=True)
class ReleaseDetails:
    """Parameters for a GitHub release.

    Attributes:
        tag: Release tag name.
        title: Release title.
        notes: Release notes text.
        generate_notes: Ask GitHub to generate the notes.
        notes_start_tag: Tag the generated notes start after.
        prerelease: Mark the release as a prerelease.

    """

    tag: str
    title: str
    notes: str
    generate_notes: bool = False
    notes_start_tag: str | None = None
    prerelease: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class PlannedProject:
    """Resolved release state for a project.

    Attributes:
        project: The configured project.
        old_version: Version before the release.
        new_version: Version after the release.
        tag: Git tag created for the release.

    """

    project: Project
    old_version: str
    new_version: str
    tag: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ReleasePlan:
    """Immutable release execution plan.

    Attributes:
        projects: Projects selected for release.
        actions: Ordered actions that implement the release.

    """

    projects: tuple[PlannedProject, ...]
    actions: tuple[Action, ...]


@final
class Constants:
    """Group module-level constants.

    Attributes:
        config_name: Grouped module constant.

    """

    config_name: Final[str] = "release.toml"
