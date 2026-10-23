# Copyright (c) 2026 Project contributors.
"""Build PyPI distributions (sdist + wheel) for a project and its siblings.

``pip install`` resolves every declared dependency from PyPI, so a project
whose manifest lists ``local_dependencies`` can only be published once those
siblings are published too. ``PypiBuilder`` builds them first, from
throwaway copies, then the project itself — the checkout stays clean.
"""

from __future__ import annotations

from pathlib import Path
from sys import executable
from sys import stdout as sys_stdout
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Final, final

from tools.release_builder.release_builder import (
    ProjectConfig,
    SourceStaging,
)

if TYPE_CHECKING:
    from tools.release_builder.release_builder import Runner


@final
class PypiBuilder:
    """Build sdist+wheel pairs for ``local_dependencies`` then the project.

    Dependencies come first so a caller publishing the directory publishes
    resolvable metadata for everything the project declares.
    """

    def __init__(self, *, runner: Runner) -> None:
        """Initialize the builder.

        Args:
            runner: External command executor.

        """
        self._runner: Final[Runner] = runner

    def build(
        self,
        *,
        project: ProjectConfig,
        output: Path,
    ) -> tuple[Path, ...]:
        """Build every distribution into ``output``.

        Each ``local_dependencies`` entry is built before the project so a
        sorted upload publishes the dependency graph in order. Builds run on
        staged copies — ``build/`` and ``*.egg-info`` never appear in the
        source checkout.

        Args:
            project: Project configuration.
            output: Directory that receives the ``.whl``/``.tar.gz`` files.

        Returns:
            The distribution paths produced, dependencies first.

        """
        output.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="release-builder-pypi-") as temporary:
            dependencies, source = SourceStaging.stage(
                project=project,
                directory=Path(temporary) / "staged",
            )
            for root in (*dependencies, source):
                _ = self._runner.execute(
                    arguments=(
                        executable,
                        "-m",
                        "build",
                        "--outdir",
                        str(object=output),
                        str(object=root),
                    ),
                )
        distributions: tuple[Path, ...] = tuple(
            sorted(
                path
                for path in output.iterdir()
                if path.suffix in {".whl", ".gz"}
            ),
        )
        for path in distributions:
            _ = sys_stdout.write(f"{path}\n")
        return distributions
