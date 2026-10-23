"""Release execution engine with dry-run and journaling."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from json import dumps, loads
from typing import TYPE_CHECKING, cast

from tools.release_publish.git import Git
from tools.release_publish.models import (
    ActionKind,
    Bump,
    PlannedProject,
    ReleaseDetails,
    ReleasePlan,
)
from tools.release_publish.planner import Planner
from tools.release_publish.runner import Runner, SubprocessRunner
from tools.release_publish.versioning import Version

if TYPE_CHECKING:
    from pathlib import Path

    from tools.release_publish.config import Config, RepositoryConfig
    from tools.release_publish.models import Project
    from tools.release_publish.providers import GitHubProvider
type JournalEntry = dict[str, str | int]


@dataclass(frozen=True, slots=True, kw_only=True)
class ReleaseEngine:
    """High-level API for planning and executing releases.

    Attributes:
        config: Release configuration.
        runner: Command execution boundary.

    """

    config: Config
    runner: Runner = field(default_factory=SubprocessRunner)

    def plan(
        self,
        *,
        projects: tuple[str, ...],
        bump: Bump | None = None,
        version: str | None = None,
        publish: bool = True,
        skip_hooks: bool = False,
    ) -> ReleasePlan:
        """Compile a release plan for the requested projects.

        Args:
            projects: Project names to release.
            bump: Version increment to apply.
            version: Explicit target version, overriding ``bump``.
            publish: Whether publishing actions are included.
            skip_hooks: Exclude configured pre/post release hooks.

        Returns:
            The compiled release plan.

        Raises:
            ValueError: If both ``bump`` and ``version`` are provided, if a
                planned tag already exists, or if an explicit version does
                not exceed the project's latest release tag.

        """
        if bump is not None and version is not None:
            msg: str = "bump and version are mutually exclusive"
            raise ValueError(msg)
        plan: ReleasePlan = Planner(config=self.config).plan(
            names=projects,
            bump=bump,
            explicit_version=version,
            publish=publish,
            skip_hooks=skip_hooks,
        )
        git: Git = Git(root=self.config.root, runner=self.runner)
        for item in plan.projects:
            self._check_tags(
                git=git,
                item=item,
                explicit=version is not None,
            )
        return plan

    def release_exists(self, *, project: str) -> bool:
        """Return whether the hosted release for the project's tag exists.

        Args:
            project: Configured project name.

        Returns:
            ``True`` when ``gh release view`` finds it; ``False`` otherwise,
            including when ``gh`` is unavailable.

        """
        item: Project = self.config.project(name=project)
        tag: str = self._tag(project=item)
        repository: RepositoryConfig = self.config.repository
        try:
            _ = self.runner.run(
                command=(
                    "gh",
                    "release",
                    "view",
                    tag,
                    "--repo",
                    f"{repository.owner}/{repository.name}",
                ),
                cwd=self.config.root,
            )
        except (RuntimeError, OSError):
            return False
        return True

    def publish_plan(
        self,
        *,
        project: str,
        artifacts: tuple[Path, ...],
        update: bool = False,
    ) -> ReleasePlan:
        """Plan the hosted release for a project's already-pushed tag.

        Args:
            project: Configured project name.
            artifacts: Files, or directories whose files, to attach.
            update: Upload to an existing release instead of creating one.

        Returns:
            A plan holding the single create or upload action.

        Raises:
            ValueError: If the tag or a path is missing, or nothing remains
                to attach.

        """
        item: Project = self.config.project(name=project)
        tag: str = self._tag(project=item)
        git: Git = Git(root=self.config.root, runner=self.runner)
        if not git.tag_exists(tag=tag):
            msg: str = f"tag {tag} does not exist; push it first"
            raise ValueError(msg)
        files: tuple[Path, ...] = self._expand(paths=artifacts)
        if not files:
            msg = "no files to attach"
            raise ValueError(msg)
        github: GitHubProvider = Planner(config=self.config).github
        if update:
            return ReleasePlan(
                projects=(),
                actions=github.upload(
                    repository=self.config.repository,
                    tag=tag,
                    artifacts=files,
                    root=self.config.root,
                ),
            )
        details: ReleaseDetails = ReleaseDetails(
            tag=tag,
            title=f"{item.name} {item.version}",
            notes="",
            generate_notes=True,
            notes_start_tag=self._previous_tag(git=git, project=item),
            prerelease=Version.parse(value=item.version).prerelease
            is not None,
        )
        return ReleasePlan(
            projects=(),
            actions=github.release(
                repository=self.config.repository,
                details=details,
                artifacts=files,
                root=self.config.root,
            ),
        )

    def changed_projects(self) -> tuple[str, ...]:
        """Return projects changed since each project's latest release tag.

        Returns:
            Names of projects with changes since their last release.

        """
        git: Git = Git(root=self.config.root, runner=self.runner)
        changed: list[str] = []
        for project in self.config.projects:
            template: str = self.config.release.tag_template
            tags: tuple[str, ...] = git.tags(
                pattern=template.format(project=project.name, version="*"),
            )
            if not tags:
                changed.append(project.name)
                continue
            prefix: str = template.format(project=project.name, version="")
            tag: str = max(
                tags,
                key=lambda item: Version.parse(
                    value=item.removeprefix(prefix),
                ),
            )
            paths: tuple[Path, ...] = git.changed_paths(since=tag)
            if any(
                path == project.path or project.path in path.parents
                for path in paths
            ):
                changed.append(project.name)
        return tuple(changed)

    def execute(
        self,
        *,
        plan: ReleasePlan,
        dry_run: bool = False,
        resume: bool = False,
    ) -> None:
        """Execute a plan sequentially and persist a resumable journal.

        Args:
            plan: Release plan to execute.
            dry_run: Plan without executing when ``True``.
            resume: Skip actions already present in the journal.

        Raises:
            RuntimeError: If a write action lacks a path or content.

        """
        journal: Path = self.config.root / ".release-publish-journal.jsonl"
        completed: frozenset[int] = (
            self._completed_indexes(journal=journal) if resume else frozenset()
        )
        for index, action in enumerate(iterable=plan.actions):
            if index in completed:
                continue
            if dry_run:
                continue
            if action.kind is ActionKind.WRITE:
                if action.path is None or action.content is None:
                    msg: str = "Invalid write action"
                    raise RuntimeError(msg)
                action.path.write_text(
                    data=action.content,
                    encoding="utf-8",
                )
            else:
                self.runner.run(
                    command=action.command,
                    cwd=action.cwd or self.config.root,
                )
            payload: JournalEntry = {
                "index": index,
                "description": action.description,
                "fingerprint": self._fingerprint(action=action.description),
            }
            with journal.open(mode="a", encoding="utf-8") as stream:
                stream.write(dumps(obj=payload, sort_keys=True) + "\n")

    @staticmethod
    def _expand(*, paths: tuple[Path, ...]) -> tuple[Path, ...]:
        """Turn files and directories into a sorted, unique file list.

        Args:
            paths: Files, or directories whose direct files are included.

        Returns:
            Sorted unique files.

        Raises:
            ValueError: If a path does not exist.

        """
        files: set[Path] = set()
        for path in paths:
            if path.is_dir():
                files.update(p for p in path.iterdir() if p.is_file())
            elif path.is_file():
                files.add(path)
            else:
                msg: str = f"{path} does not exist"
                raise ValueError(msg)
        return tuple(sorted(files))

    @staticmethod
    def _completed_indexes(*, journal: Path) -> frozenset[int]:
        """Read previously executed action indexes from the journal.

        Args:
            journal: Journal file path.

        Returns:
            Indexes of completed actions.

        """
        if not journal.exists():
            return frozenset()
        indexes: set[int] = set()
        for line in journal.read_text(encoding="utf-8").splitlines():
            payload: JournalEntry = cast(typ="JournalEntry", val=loads(s=line))
            index: str | int | None = payload.get("index")
            if isinstance(index, int):
                indexes.add(index)
        return frozenset(indexes)

    @staticmethod
    def _fingerprint(*, action: str) -> str:
        """Compute a stable fingerprint for one action description.

        Args:
            action: Action description text.

        Returns:
            Hexadecimal SHA-256 fingerprint.

        """
        return sha256(data=action.encode()).hexdigest()

    def _tag(self, *, project: Project) -> str:
        """Return the release tag for a project's current version.

        Args:
            project: Configured project.

        Returns:
            The tag name.

        """
        return self.config.release.tag_template.format(
            project=project.name,
            version=project.version,
        )

    def _previous_tag(self, *, git: Git, project: Project) -> str | None:
        """Return the newest release tag older than the project's version.

        Args:
            git: Repository adapter.
            project: Project being released.

        Returns:
            The previous tag, or ``None`` for a first release.

        """
        template: str = self.config.release.tag_template
        if not template.endswith("{version}"):
            return None
        prefix: str = template.format(project=project.name, version="")
        current: Version = Version.parse(value=project.version)

        def order(version: Version) -> tuple[int, int, int, bool, str]:
            """Rank versions so the newest stable release sorts last.

            Args:
                version: Candidate version parsed from a tag.

            Returns:
                Sort key preferring the newest stable version.

            """
            return (
                version.major,
                version.minor,
                version.patch,
                version.prerelease is None,
                version.prerelease or "",
            )

        older: list[tuple[tuple[int, int, int, bool, str], str]] = []
        for tag in git.tags(pattern=f"{prefix}*"):
            try:
                version: Version = Version.parse(
                    value=tag.removeprefix(prefix),
                )
            except ValueError:
                continue
            if order(version=version) < order(version=current):
                older.append((order(version=version), tag))
        return max(older)[1] if older else None

    def _check_tags(
        self,
        *,
        git: Git,
        item: PlannedProject,
        explicit: bool,
    ) -> None:
        """Reject plans that collide with or regress published tags.

        Args:
            git: Git adapter bound to the repository root.
            item: Planned project release.
            explicit: Whether the target version was given explicitly.

        Raises:
            ValueError: If the planned tag exists, or an explicit version
                is not newer than the latest release tag.

        """
        if git.tag_exists(tag=item.tag):
            msg: str = f"Tag {item.tag} already exists"
            raise ValueError(msg)
        if not explicit:
            return
        template: str = self.config.release.tag_template
        tags: tuple[str, ...] = git.tags(
            pattern=template.format(project=item.project.name, version="*"),
        )
        if not tags:
            return
        prefix: str = template.format(project=item.project.name, version="")
        latest: str = max(
            tags,
            key=lambda tag: Version.parse(value=tag.removeprefix(prefix)),
        )
        if not Version.parse(value=item.new_version) > Version.parse(
            value=latest.removeprefix(prefix),
        ):
            msg = (
                f"Version {item.new_version} is not newer than the "
                f"latest tag {latest}"
            )
            raise ValueError(msg)
