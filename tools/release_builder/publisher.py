# Copyright (c) 2026 Project contributors.
"""Publish built artifacts to a hosted release through ``release_publish``."""

from __future__ import annotations

from json import JSONDecodeError, loads
from typing import TYPE_CHECKING, Final, cast

from tools.release_builder.build_report import ReportConstants
from tools.release_builder.release_builder import BuildError
from tools.release_builder.wiki import WikiPublisher
from tools.release_publish.config import Config, load_config
from tools.release_publish.engine import ReleaseEngine

if TYPE_CHECKING:
    from pathlib import Path

    from tools.release_publish.models import ReleasePlan
    from tools.release_publish.runner import Runner


class ReleasePublisher:
    """Attach the native builds and ``BUILD.md`` to a project's release.

    Release creation, tag checks, previous-tag release notes and the
    ``gh`` commands all belong to ``tools.release_publish``; this class only
    decides *which files* a build contributes.

    ``.pyz`` archives are deliberately not published here: they are built
    per platform in CI and covered by CI's checksum manifest, so a locally
    built copy would replace the CI asset and break verification.
    """

    def __init__(
        self,
        *,
        config_path: Path,
        runner: Runner | None = None,
    ) -> None:
        """Initialize the publisher.

        Args:
            config_path: ``release.toml`` understood by ``release_publish``.
            runner: Command executor; the real subprocess runner by default.

        """
        self._config_path: Final[Path] = config_path
        self._runner: Final[Runner | None] = runner

    @staticmethod
    def files(*, output: Path) -> tuple[Path, ...]:
        """List the release assets a build directory contributes.

        Args:
            output: Build output directory.

        Returns:
            Executables, installers and ``BUILD.md`` that exist.

        Raises:
            BuildError: If the directory has no recorded builds.

        """
        manifest: Path = output / ReportConstants.manifest_name
        try:
            document: object = loads(s=manifest.read_text(encoding="utf-8"))
        except (OSError, JSONDecodeError):
            document = []
        entries: list[dict[str, str]] = (
            cast(typ="list[dict[str, str]]", val=document)
            if isinstance(document, list)
            else []
        )
        paths: list[Path] = [
            output / entry[key]
            for entry in entries
            for key in ("executable", "installer")
            if key in entry
        ]
        report: Path = output / ReportConstants.report_name
        files: list[Path] = [p for p in paths if p.is_file()]
        if not files:
            msg: str = f"No builds to publish in {output}; run a build first."
            raise BuildError(msg)
        if report.is_file():
            files.append(report)
        return tuple(sorted(files))

    def publish(
        self,
        *,
        output: Path,
        project: str,
        version: str,
        dry_run: bool,
    ) -> ReleasePlan:
        """Create the release for ``project`` or add the files to it.

        Args:
            output: Build output directory.
            project: Project name in ``release.toml``.
            version: Version the artifacts were built for.
            dry_run: Plan without running the final command.

        Returns:
            The plan that was (or, for a dry run, would be) executed.

        Raises:
            BuildError: If the version disagrees with the release
                configuration, the tag is not pushed, or nothing was built.

        """
        config: Config = load_config(path=self._config_path)
        engine: ReleaseEngine = (
            ReleaseEngine(config=config, runner=self._runner)
            if self._runner is not None
            else ReleaseEngine(config=config)
        )
        configured: str = config.project(name=project).version
        if configured != version:
            msg: str = (
                f"Built version {version} but {project} is {configured} in "
                f"{self._config_path.name}; refusing to attach it to the "
                f"wrong release."
            )
            raise BuildError(msg)
        files: tuple[Path, ...] = self.files(output=output)
        try:
            plan: ReleasePlan = engine.publish_plan(
                project=project,
                artifacts=files,
                update=engine.release_exists(project=project),
            )
        except ValueError as error:
            raise BuildError(str(object=error)) from error
        engine.execute(plan=plan, dry_run=dry_run)
        self._publish_wiki(config=config, dry_run=dry_run)
        return plan

    def _publish_wiki(self, *, config: Config, dry_run: bool) -> None:
        """Sync ``wiki/`` to the GitHub Wiki when the folder exists.

        Args:
            config: Loaded release configuration.
            dry_run: Report Wiki changes without committing or pushing.

        """
        wiki_directory: Path = config.root / "wiki"
        if not wiki_directory.is_dir():
            return
        _ = WikiPublisher(
            repository=(f"{config.repository.owner}/{config.repository.name}"),
            wiki_directory=wiki_directory,
            runner=self._runner,
            dry_run=dry_run,
        ).publish()
