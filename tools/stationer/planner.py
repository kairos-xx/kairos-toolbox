"""Release planning and dependency ordering."""

from __future__ import annotations

from dataclasses import dataclass, field
from sys import executable
from typing import TYPE_CHECKING

from tools.stationer.models import (
    Action,
    ActionKind,
    Bump,
    PlannedProject,
    Project,
    ReleaseDetails,
    ReleasePlan,
)
from tools.stationer.providers import GitHubProvider, PyPiProvider
from tools.stationer.versioning import Version, replace_version

if TYPE_CHECKING:
    from pathlib import Path

    from tools.stationer.config import Config


@dataclass(frozen=True, slots=True, kw_only=True)
class Planner:
    """Compile requested releases into deterministic actions.

    Attributes:
        config: Release configuration.
        github: GitHub release provider.
        pypi: Python package registry provider.

    """

    config: Config
    github: GitHubProvider = field(default_factory=GitHubProvider)
    pypi: PyPiProvider = field(default_factory=PyPiProvider)

    def plan(
        self,
        *,
        names: tuple[str, ...],
        bump: Bump | None,
        explicit_version: str | None,
        publish: bool,
        skip_hooks: bool = False,
    ) -> ReleasePlan:
        """Compile a release plan for the selected projects.

        Args:
            names: Project names to release.
            bump: Version increment to apply.
            explicit_version: Explicit target version, overriding ``bump``.
            publish: Whether publishing actions are included.
            skip_hooks: Exclude configured pre/post release hooks.

        Returns:
            The compiled release plan.

        """
        selected: tuple[Project, ...] = tuple(
            self.config.project(name=name) for name in names
        )
        ordered: tuple[Project, ...] = self._topological(projects=selected)
        planned: list[PlannedProject] = []
        actions: list[Action] = []
        for project in ordered:
            old: Version = Version.parse(value=project.version)
            new: Version = (
                Version.parse(value=explicit_version)
                if explicit_version is not None
                else old.bump(kind=bump or Bump.patch)
            )
            tag: str = self.config.release.tag_template.format(
                project=project.name,
                version=new,
            )
            release: PlannedProject = PlannedProject(
                project=project,
                old_version=str(object=old),
                new_version=str(object=new),
                tag=tag,
            )
            planned.append(release)
            actions.extend(
                self._project_actions(
                    release=release,
                    publish=publish,
                    skip_hooks=skip_hooks,
                ),
            )
        return ReleasePlan(projects=tuple(planned), actions=tuple(actions))

    @staticmethod
    def _topological(
        *,
        projects: tuple[Project, ...],
    ) -> tuple[Project, ...]:
        """Order selected projects so dependencies release first.

        Args:
            projects: Projects selected for release.

        Returns:
            The projects in dependency order.

        Raises:
            ValueError: If the dependency graph contains a cycle.

        """
        selected: dict[str, Project] = {
            project.name: project for project in projects
        }
        result: list[Project] = []
        pending: dict[str, Project] = dict(selected)
        while pending:
            ready: list[Project] = sorted(
                (
                    project
                    for project in pending.values()
                    if not (set(project.depends_on) & pending.keys())
                ),
                key=lambda item: item.name,
            )
            if not ready:
                msg: str = "Dependency cycle in selected projects"
                raise ValueError(msg)
            result.extend(ready)
            for project in ready:
                del pending[project.name]
        return tuple(result)

    @staticmethod
    def _hook(*, command: tuple[str, ...]) -> tuple[str, ...]:
        """Expand placeholders inside one configured hook command.

        Args:
            command: Hook command arguments.

        Returns:
            The command with ``{python}`` replaced by the interpreter.

        """
        return tuple(
            executable if part == "{python}" else part for part in command
        )

    def _project_actions(
        self,
        *,
        release: PlannedProject,
        publish: bool,
        skip_hooks: bool,
    ) -> tuple[Action, ...]:
        """Build the ordered actions for one project release.

        Args:
            release: Resolved release state for the project.
            publish: Whether publishing actions are included.
            skip_hooks: Exclude configured pre/post release hooks.

        Returns:
            The ordered actions for the project.

        Raises:
            ValueError: If a version file cannot be uniquely updated.

        """
        project: Project = release.project
        root: Path = self.config.root
        actions: list[Action] = [
            Action(
                kind=ActionKind.command,
                description=f"Preflight clean tree for {project.name}",
                command=("git", "diff", "--quiet", "--exit-code", "HEAD"),
                cwd=root,
            ),
        ]
        if not skip_hooks:
            actions.extend(
                Action(
                    kind=ActionKind.command,
                    description=f"Pre-release hook for {project.name}",
                    command=self._hook(command=hook),
                    cwd=root / project.path,
                )
                for hook in self.config.release.pre_hooks
            )
        for path in project.version_files:
            absolute: Path = root / path
            content: str = absolute.read_text(encoding="utf-8")
            substituted: tuple[str, int] = replace_version(
                text=content,
                old=release.old_version,
                new=release.new_version,
            )
            if substituted[1] != 1:
                msg: str = f"Could not uniquely update version in {path}"
                raise ValueError(msg)
            actions.append(
                Action(
                    kind=ActionKind.write,
                    description=f"Update {path} to {release.new_version}",
                    path=absolute,
                    content=substituted[0],
                ),
            )
        if project.python_distribution:
            actions.extend(self.pypi.build(project_root=root / project.path))
            actions.append(
                Action(
                    kind=ActionKind.command,
                    description=f"Checksum {project.name} artifacts",
                    command=(
                        "python",
                        "-m",
                        "tools.stationer.artifacts",
                        str(object=root / project.path / "dist"),
                    ),
                    cwd=root,
                ),
            )
        actions.extend(
            (
                Action(
                    kind=ActionKind.command,
                    description=f"Stage {project.name} release",
                    command=(
                        "git",
                        "add",
                        "--",
                        *(str(object=path) for path in project.version_files),
                    ),
                    cwd=root,
                ),
                Action(
                    kind=ActionKind.command,
                    description=(
                        f"Commit {project.name} {release.new_version}"
                    ),
                    command=(
                        "git",
                        "commit",
                        "-m",
                        f"release({project.name}): {release.new_version}",
                        "--",
                        *(str(object=path) for path in project.version_files),
                    ),
                    cwd=root,
                ),
                Action(
                    kind=ActionKind.command,
                    description=f"Tag {release.tag}",
                    command=(
                        "git",
                        "tag",
                        "-a",
                        release.tag,
                        "-m",
                        f"Release {project.name} {release.new_version}",
                    ),
                    cwd=root,
                ),
                Action(
                    kind=ActionKind.command,
                    description=f"Push {release.tag}",
                    command=("git", "push", "origin", "HEAD", release.tag),
                    cwd=root,
                ),
            ),
        )
        if (
            publish
            and project.python_distribution
            and self.config.release.publish_pypi
        ):
            actions.extend(self.pypi.publish(project_root=root / project.path))
        if publish and self.config.release.create_github_release:
            artifacts: tuple[Path, ...] = tuple(
                root / project.path / item for item in project.artifacts
            )
            actions.extend(
                self.github.release(
                    repository=self.config.repository,
                    details=ReleaseDetails(
                        tag=release.tag,
                        title=f"{project.name} {release.new_version}",
                        notes=f"Release {project.name} {release.new_version}",
                    ),
                    artifacts=artifacts,
                    root=root,
                ),
            )
        if not skip_hooks:
            actions.extend(
                Action(
                    kind=ActionKind.command,
                    description=f"Post-release hook for {project.name}",
                    command=self._hook(command=hook),
                    cwd=root / project.path,
                )
                for hook in self.config.release.post_hooks
            )
        return tuple(actions)
