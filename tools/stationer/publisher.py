# Copyright (c) 2026 Project contributors.
"""Publish built artifacts to a hosted release through ``stationer``."""

from __future__ import annotations

from json import JSONDecodeError, loads
from typing import TYPE_CHECKING, Final, cast

from tools.stationer.build_report import ReportConstants
from tools.stationer.builder import BuildError
from tools.stationer.config import Config, load_config
from tools.stationer.engine import ReleaseEngine
from tools.stationer.runner import open_host
from tools.stationer.wiki import WikiPublisher

if TYPE_CHECKING:
    from pathlib import Path

    from tools.stationer.models import ReleasePlan
    from tools.stationer.runner import HostOpener


class ReleasePublisher:
    """Attach the built artifacts and checksums to a project's release.

    Release creation, tag checks, previous-tag release notes and the
    ``gh`` commands all belong to ``tools.stationer``; this class only
    decides *which files* a build contributes: every recorded executable
    and installer, every ``.pyz`` under a ``pyz/`` artifact folder, and
    the ``SHA256SUMS``/``BUILD.md`` companions.
    """

    def __init__(
        self,
        *,
        config_path: Path,
        open_host: HostOpener = open_host,
    ) -> None:
        """Initialize the publisher.

        Args:
            config_path: ``release.toml`` understood by ``stationer``.
            open_host: Creates the host session git and gh run in.

        """
        self._config_path: Final[Path] = config_path
        self._open_host: Final[HostOpener] = open_host

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
        paths.extend(
            archive
            for archive in output.rglob(pattern="*.pyz")
            if archive.parent.name == "pyz"
        )
        pypi_directory: Path = output / "pypi"
        if pypi_directory.is_dir():
            paths.extend(
                distribution
                for distribution in pypi_directory.rglob(pattern="*")
                if distribution.suffix in {".whl", ".gz", ".zip"}
            )
        files: list[Path] = [p for p in paths if p.is_file()]
        if not files:
            msg: str = f"No builds to publish in {output}; run a build first."
            raise BuildError(msg)
        files.extend(
            companion
            for companion in (
                output / ReportConstants.report_name,
                output / ReportConstants.checksums_name,
            )
            if companion.is_file()
        )
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
        engine: ReleaseEngine = ReleaseEngine(
            config=config,
            open_host=self._open_host,
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
            open_host=self._open_host,
            dry_run=dry_run,
        ).publish()
